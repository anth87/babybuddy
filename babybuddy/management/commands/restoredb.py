# -*- coding: utf-8 -*-
"""
Management utility to restore a database backup from off-site storage.

The backup is always written to a new local file, never over the database in
use, so this is safe to run at any time. Inspect the result, then swap it in
by hand if you decide to.

Example usage:

  manage.py restoredb --list
  manage.py restoredb
  manage.py restoredb --key backups/db-2026-07-28T03-30-00Z.sqlite3.gz
"""

import os

from django.core.management.base import BaseCommand, CommandError

from babybuddy import backup


class Command(BaseCommand):
    help = "Download a database backup from off-site storage to a local file"

    def add_arguments(self, parser):
        parser.add_argument(
            "--list",
            action="store_true",
            default=False,
            help="List the available backups and exit.",
        )
        parser.add_argument(
            "--key",
            help="Backup to restore. Defaults to the most recent one.",
        )
        parser.add_argument(
            "--output",
            help="Where to write the restored database. Defaults to "
            "restored-<timestamp>.sqlite3 in the current directory.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help="Overwrite the output file if it already exists. Default is False.",
        )

    def handle(self, *args, **options):
        if not backup.is_configured():
            raise CommandError(
                "Backups are not configured. Set BACKUP_S3_BUCKET, "
                "BACKUP_S3_ENDPOINT_URL, BACKUP_S3_ACCESS_KEY_ID and "
                "BACKUP_S3_SECRET_ACCESS_KEY."
            )

        try:
            available = backup.list_backups()
        except Exception as error:
            raise CommandError(f"Could not list backups: {error}")

        if not available:
            raise CommandError("There are no backups in the bucket.")

        if options.get("list"):
            self.stdout.write(f"{len(available)} backup(s), newest first:")
            for entry in available:
                self.stdout.write(
                    f"  {entry['LastModified']:%Y-%m-%d %H:%M} UTC  "
                    f"{entry['Size']:>12,} bytes  {entry['Key']}"
                )
            return

        key = options.get("key") or available[0]["Key"]
        if key not in {entry["Key"] for entry in available}:
            raise CommandError(
                f"No backup named {key}. Run with --list to see what exists."
            )

        output = options.get("output") or os.path.join(
            os.getcwd(), os.path.basename(key).replace(".sqlite3.gz", "") + ".sqlite3"
        )
        output = os.path.abspath(output)

        # Refuse to write over the database in use. Restoring is a decision that
        # should be made deliberately, by moving the file into place.
        live = os.path.abspath(backup._database_path())
        if output == live:
            raise CommandError(
                f"Refusing to overwrite the database in use ({live}). Restore to "
                "another path, check it, then move it into place yourself."
            )

        if os.path.exists(output) and not options.get("force"):
            raise CommandError(f"{output} already exists. Use --force to overwrite.")

        self.stdout.write(f"Downloading {key}...")
        try:
            size = backup.download_backup(key, output)
        except Exception as error:
            raise CommandError(f"Restore failed: {error}")

        integrity, counts = backup.inspect_database(output)
        if integrity != "ok":
            raise CommandError(
                f"Restored file failed its integrity check: {integrity}. "
                "Do not use this backup."
            )

        self.stdout.write(
            self.style.SUCCESS(f"Restored to {output} ({size:,} bytes, integrity ok).")
        )

        interesting = (
            ("children", "core_child"),
            ("feedings", "core_feeding"),
            ("sleep entries", "core_sleep"),
            ("diaper changes", "core_diaperchange"),
            ("notes", "core_note"),
        )
        self.stdout.write(f"Contains {len(counts)} tables:")
        for label, table in interesting:
            if table in counts:
                self.stdout.write(f"  {counts[table]:>7,} {label}")

        self.stdout.write(
            "\nThis file is a copy; nothing in use was modified. To run the app "
            "against it, set DB_NAME to this path in .env."
        )
