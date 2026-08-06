# -*- coding: utf-8 -*-
import datetime
import os
import tempfile

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core import models
from core.management.commands.import_huckleberry import IMPORT_TAG

CSV_HEADER = (
    '"Type","Start","End","Duration","Start Condition","Start Location",'
    '"End Condition","Notes"\n'
)


class ImportHuckleberryBathTestCase(TestCase):
    def setUp(self):
        call_command("migrate", verbosity=0)
        self.child = models.Child.objects.create(
            first_name="Child",
            last_name="One",
            birth_date=datetime.date(2026, 7, 1),
        )

    def _run(self, rows, **options):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, encoding="utf-8", newline=""
        )
        try:
            handle.write(CSV_HEADER)
            handle.writelines(rows)
            handle.close()
            call_command(
                "import_huckleberry",
                handle.name,
                child=self.child.slug,
                verbosity=0,
                **options,
            )
        finally:
            os.unlink(handle.name)

    def test_bath_rows_are_imported(self):
        self._run(
            [
                '"Bath","2026-07-24 09:23",,,,,,\n',
                '"Bath","2026-07-20 09:20",,,,,,"Loved it"\n',
            ]
        )

        self.assertEqual(models.Bath.objects.count(), 2)

        # Ordering is newest first, matching the model's Meta.
        latest = models.Bath.objects.first()
        self.assertEqual(latest.child, self.child)
        self.assertEqual(
            timezone.localtime(latest.time).strftime("%Y-%m-%d %H:%M"),
            "2026-07-24 09:23",
        )
        self.assertEqual(latest.notes, None)
        self.assertEqual([t.name for t in latest.tags.all()], [IMPORT_TAG])

        oldest = models.Bath.objects.last()
        self.assertEqual(oldest.notes, "Loved it")

    def test_dry_run_writes_nothing(self):
        self._run(['"Bath","2026-07-24 09:23",,,,,,\n'], dry_run=True)
        self.assertEqual(models.Bath.objects.count(), 0)

    def test_wipe_removes_existing_baths(self):
        models.Bath.objects.create(
            child=self.child,
            time=timezone.make_aware(datetime.datetime(2026, 7, 2, 8, 0)),
        )

        self._run(['"Bath","2026-07-24 09:23",,,,,,\n'], wipe=True)

        self.assertEqual(models.Bath.objects.count(), 1)
        self.assertEqual(
            timezone.localtime(models.Bath.objects.first().time).strftime("%Y-%m-%d"),
            "2026-07-24",
        )
