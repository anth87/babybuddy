# -*- coding: utf-8 -*-
"""
Management utility to back up the database to off-site object storage.

Example usage:

  manage.py backupdb --dry-run
  manage.py backupdb
"""

from django.core.management.base import BaseCommand, CommandError

from babybuddy import backup


class Command(BaseCommand):
    help = "Back up the database to off-site storage and remove expired copies"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Snapshot the database and report what would be uploaded and "
            "removed, without changing anything in the bucket.",
        )
        parser.add_argument(
            "--no-prune",
            action="store_true",
            default=False,
            help="Upload a backup but keep expired copies. Default is False.",
        )

    def handle(self, *args, **options):
        if not backup.is_configured():
            raise CommandError(
                "Backups are not configured. Set BACKUP_S3_BUCKET, "
                "BACKUP_S3_ENDPOINT_URL, BACKUP_S3_ACCESS_KEY_ID and "
                "BACKUP_S3_SECRET_ACCESS_KEY."
            )

        dry_run = options.get("dry_run")

        try:
            result = backup.run_backup(
                dry_run=dry_run, prune_expired=not options.get("no_prune")
            )
        except Exception as error:
            raise CommandError(f"Backup failed: {error}")

        if options.get("verbosity") == 0:
            return

        verb = "Would upload" if dry_run else "Uploaded"
        self.stdout.write(
            self.style.SUCCESS(f"{verb} {result['key']} ({result['size']:,} bytes).")
        )

        pruned = result["pruned"]
        if pruned:
            verb = "Would remove" if dry_run else "Removed"
            self.stdout.write(f"{verb} {len(pruned)} expired backup(s):")
            for key in pruned:
                self.stdout.write(f"  {key}")
        else:
            self.stdout.write("No expired backups to remove.")
