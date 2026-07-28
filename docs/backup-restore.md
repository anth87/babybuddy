# Backup and restore

The database is backed up automatically once a day to S3-compatible object
storage (Cloudflare R2), and copies are kept for seven days.

This is the only backup layer. Railway's own volume snapshots are not enabled,
so R2 holds the sole copy of the data outside the live volume.

That single layer covers both of the cases that matter. A backup can be opened
locally to recover a few deleted records without touching production, and it
lives off the hosting platform, so it survives the volume or the Railway
account itself going away.

If you later want a faster whole-volume rollback as well, enable scheduled
volume backups in the Railway dashboard; the two are independent and nothing
here needs to change.

## Configuration

Backups are inactive until all four of these environment variables are set:

| Variable                      | Value                                           |
| ----------------------------- | ----------------------------------------------- |
| `BACKUP_S3_BUCKET`            | Bucket name, e.g. `baby-saliba-backups`         |
| `BACKUP_S3_ENDPOINT_URL`      | `https://<account-id>.r2.cloudflarestorage.com` |
| `BACKUP_S3_ACCESS_KEY_ID`     | R2 access key ID                                |
| `BACKUP_S3_SECRET_ACCESS_KEY` | R2 secret access key                            |

These are separate from the `AWS_*` settings, which django-storages uses for
uploaded photos.

Objects are written to `backups/db-<timestamp>.sqlite3.gz`. The retention
period and the time of day are constants at the top of `babybuddy/backup.py`.

## How it runs

A daily thread inside the web process handles it. It lives there rather than in
a separate Railway cron service because a Railway volume attaches to only one
service, so nothing else can read the database file.

The backup uses SQLite's online backup API, which is safe to run against a
live database. Do not replace it with a file copy or `rclone` sync: copying a
database that is being written to can capture a torn page and produce an
archive that will not restore.

If backups lapse (the service was asleep or down at the scheduled time), the
next boot notices the newest copy is over a day old and runs one immediately.
Pruning always keeps the most recent backup regardless of age, so an outage
cannot empty the bucket.

Two caveats:

- If **app sleeping / serverless** is enabled on the Railway service, the
  thread will not fire while the app is asleep. Leave it disabled.
- Backups cover the database only. **Uploaded photos in `MEDIA_ROOT` are not
  backed up anywhere**, and with volume snapshots disabled there is no second
  copy of them. Losing the volume would lose the photos even though the records
  themselves would restore cleanly.

## Taking a backup by hand

Before anything risky, such as a migration or a bulk import:

```
python manage.py backupdb
```

To see what would happen without touching the bucket:

```
python manage.py backupdb --dry-run
```

## Restoring

`manage.py restoredb` downloads a backup to a **new local file**. It never
writes over the database in use and never touches production, so it is safe to
run at any time, including against production backups from a laptop.

See what is available:

```
python manage.py restoredb --list
```

Restore the most recent backup:

```
python manage.py restoredb
```

Or a specific one, to a chosen path:

```
python manage.py restoredb --key backups/db-2026-07-28T03-30-00Z.sqlite3.gz --output check.sqlite3
```

The command runs `PRAGMA integrity_check` on the result and refuses to report
success if the archive is damaged, so a clean run is also proof the backup is
good. It then prints row counts for the main tables so you can confirm the
records you are looking for are present.

### Recovering a few deleted records (the usual case)

1. `python manage.py restoredb` to pull down the most recent backup that still
   contains the records.
2. Point a local checkout at it by setting `DB_NAME` to that path in `.env`,
   then run the app and read the values you need.
3. Re-enter those records in production through the UI or API.

Production is untouched throughout, which is why this is preferred over a full
restore for anything short of total loss.

### Full restore

1. Take a fresh backup first if the current database still has any value:
   `python manage.py backupdb`.
2. `python manage.py restoredb` to fetch the copy you want, and check the row
   counts it prints.
3. Upload that file to the Railway volume as `db.sqlite3`, replacing the
   existing one.
4. Restart the service and confirm the data is present.

Anything written after the backup timestamp is lost, so prefer record-level
recovery where possible.

## A caution about local credentials

Keeping the `BACKUP_S3_*` values in a local `.env` is useful: it is what lets
you run `restoredb` from your machine. The scheduler will not run when `DEBUG`
is true, so a development server cannot upload local data to the bucket.

Running `manage.py backupdb` by hand locally _will_, though, and that copy is
indistinguishable from a production backup. Only run `backupdb` locally when
you mean to.
