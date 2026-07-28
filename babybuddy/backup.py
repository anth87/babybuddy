# -*- coding: utf-8 -*-
"""
Off-site backups of the Baby Buddy database.

Once a day the SQLite database is snapshotted, gzipped and uploaded to
S3-compatible object storage (Cloudflare R2), and copies older than
`RETENTION_DAYS` are removed.

Nothing happens unless the BACKUP_S3_* settings are populated, so local
development and tests are unaffected.

The schedule runs on a thread inside the web process rather than as a separate
cron service because a Railway volume attaches to only one service: no other
service can read the database file.
"""

import datetime
import gzip
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("babybuddy.backup")

# Where backups are kept within the bucket, and for how long.
KEY_PREFIX = "backups/"
RETENTION_DAYS = 7

# Local time of day at which the daily backup runs.
BACKUP_HOUR = 3
BACKUP_MINUTE = 30

_scheduler_lock = threading.Lock()
_scheduler_started = False


def is_configured():
    """True when a backup destination and credentials are all present."""
    return all(
        (
            settings.BACKUP_S3_BUCKET,
            settings.BACKUP_S3_ENDPOINT_URL,
            settings.BACKUP_S3_ACCESS_KEY_ID,
            settings.BACKUP_S3_SECRET_ACCESS_KEY,
        )
    )


def _client():
    # Imported lazily so that an unconfigured install never pays for boto3.
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=settings.BACKUP_S3_ENDPOINT_URL,
        aws_access_key_id=settings.BACKUP_S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.BACKUP_S3_SECRET_ACCESS_KEY,
        # R2 ignores regions but botocore insists on one.
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            # Recent boto3 versions add trailing checksums by default, which
            # non-AWS S3 implementations reject.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_supported",
        ),
    )


def _database_path():
    database = settings.DATABASES["default"]
    if "sqlite3" not in database["ENGINE"]:
        raise RuntimeError(
            f"Only SQLite databases can be backed up (found {database['ENGINE']})."
        )
    return database["NAME"]


def _write_snapshot(destination):
    """
    Write a gzipped, internally consistent copy of the database to `destination`.

    This uses SQLite's online backup API rather than copying the file. A plain
    copy of a database that is being written to can capture a torn page or an
    inconsistent WAL state, producing an archive that cannot be restored.
    """
    source = sqlite3.connect(f"file:{_database_path()}?mode=ro", uri=True)
    try:
        with tempfile.TemporaryDirectory() as workspace:
            snapshot = os.path.join(workspace, "snapshot.sqlite3")
            target = sqlite3.connect(snapshot)
            try:
                source.backup(target)
            finally:
                target.close()
            with (
                open(snapshot, "rb") as plain,
                gzip.open(destination, "wb") as compressed,
            ):
                shutil.copyfileobj(plain, compressed)
    finally:
        source.close()


def _key_for(moment):
    return f"{KEY_PREFIX}db-{moment.strftime('%Y-%m-%dT%H-%M-%SZ')}.sqlite3.gz"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def list_backups(client=None):
    """Return the existing backup objects, newest first."""
    client = client or _client()
    paginator = client.get_paginator("list_objects_v2")
    objects = []
    for page in paginator.paginate(Bucket=settings.BACKUP_S3_BUCKET, Prefix=KEY_PREFIX):
        objects.extend(page.get("Contents", []))
    return sorted(objects, key=lambda entry: entry["LastModified"], reverse=True)


def prune(client=None, dry_run=False):
    """
    Delete backups older than `RETENTION_DAYS` and return the keys removed.

    The newest backup is always kept, whatever its age, so that a long outage
    of the backup job cannot leave the bucket empty.
    """
    client = client or _client()
    cutoff = _now() - datetime.timedelta(days=RETENTION_DAYS)
    expired = [
        entry["Key"]
        for entry in list_backups(client)[1:]
        if entry["LastModified"] < cutoff
    ]
    if expired and not dry_run:
        for start in range(0, len(expired), 1000):
            client.delete_objects(
                Bucket=settings.BACKUP_S3_BUCKET,
                Delete={
                    "Objects": [{"Key": key} for key in expired[start : start + 1000]]
                },
            )
    return expired


def run_backup(dry_run=False, prune_expired=True):
    """Upload a fresh backup, prune expired ones, and return a summary."""
    if not is_configured():
        raise RuntimeError(
            "Backups are not configured; set BACKUP_S3_BUCKET and credentials."
        )

    client = _client()
    key = _key_for(_now())

    with tempfile.TemporaryDirectory() as workspace:
        archive = os.path.join(workspace, "backup.sqlite3.gz")
        _write_snapshot(archive)
        size = os.path.getsize(archive)
        if not dry_run:
            client.upload_file(archive, settings.BACKUP_S3_BUCKET, key)

    pruned = prune(client=client, dry_run=dry_run) if prune_expired else []
    return {"key": key, "size": size, "pruned": pruned, "dry_run": dry_run}


def download_backup(key, destination, client=None):
    """
    Download `key` and write the decompressed database to `destination`.

    This only ever reads from storage and writes to a local path, so it is safe
    to run against production backups from a developer machine.
    """
    client = client or _client()
    with tempfile.TemporaryDirectory() as workspace:
        archive = os.path.join(workspace, "backup.sqlite3.gz")
        client.download_file(settings.BACKUP_S3_BUCKET, key, archive)
        with (
            gzip.open(archive, "rb") as compressed,
            open(destination, "wb") as plain,
        ):
            shutil.copyfileobj(compressed, plain)
    return os.path.getsize(destination)


def inspect_database(path):
    """Return SQLite's integrity verdict and per-table row counts for `path`."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        counts = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in tables
        }
        return integrity, counts
    finally:
        connection.close()


def _configure_logger():
    """
    Send backup messages to stdout.

    Baby Buddy defines no LOGGING config, so without a handler here anything
    below WARNING would be swallowed and the daily run would be invisible.
    """
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("[%(asctime)s] %(levelname)s %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False


def _seconds_until_next_run():
    now = timezone.localtime()
    target = now.replace(
        hour=BACKUP_HOUR, minute=BACKUP_MINUTE, second=0, microsecond=0
    )
    if target <= now:
        target += datetime.timedelta(days=1)
    return (target - now).total_seconds()


def _is_overdue():
    """True when there is no backup yet, or the newest is over a day old."""
    backups = list_backups()
    if not backups:
        return True
    return _now() - backups[0]["LastModified"] > datetime.timedelta(days=1)


def _run_once(reason):
    try:
        result = run_backup()
        logger.info(
            "Backup uploaded (%s): %s, %s bytes, %d old copies removed.",
            reason,
            result["key"],
            f"{result['size']:,}",
            len(result["pruned"]),
        )
    except Exception:
        # A failed backup must never be able to take the site down.
        logger.exception("Backup failed (%s).", reason)


def _scheduler_loop():
    # Catch up straight away if backups have lapsed, so that a service which
    # restarts often (or was down at the scheduled time) still gets covered.
    try:
        overdue = _is_overdue()
    except Exception:
        logger.exception("Could not list existing backups.")
        overdue = False
    if overdue:
        _run_once("catch-up")

    while True:
        time.sleep(_seconds_until_next_run())
        _run_once("scheduled")


def start_scheduler():
    """Start the daily backup thread. A no-op when backups are unconfigured."""
    global _scheduler_started

    if not is_configured():
        return

    # Never schedule from a development server. Credentials in a local .env are
    # there so that `manage.py backupdb` can be run by hand; without this guard
    # `runserver` would quietly upload the local database into the production
    # bucket, where it would be indistinguishable from a real backup.
    if settings.DEBUG:
        return

    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    _configure_logger()
    threading.Thread(
        target=_scheduler_loop, name="babybuddy-backup", daemon=True
    ).start()
    logger.info(
        "Daily backup to %s scheduled for %02d:%02d local time, kept for %d days.",
        settings.BACKUP_S3_BUCKET,
        BACKUP_HOUR,
        BACKUP_MINUTE,
        RETENTION_DAYS,
    )
