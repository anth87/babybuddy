# -*- coding: utf-8 -*-
"""
Import a Huckleberry CSV export into Baby Buddy.

Huckleberry exports one row per event with the columns:

    Type, Start, End, Duration, Start Condition, Start Location,
    End Condition, Notes

The meaning of the "condition"/"location" columns changes per Type, so each
type is parsed separately. Mapping to Baby Buddy models:

    Feed (Bottle)  -> Feeding  (type from Start Condition, method=bottle,
                                amount from the "60ml" End Condition field)
    Feed (Breast)  -> Feeding  (method from the R/L markers, start/end/duration)
    Diaper         -> DiaperChange
    Growth         -> Weight AND Height
    Pump           -> Pumping
    Bath           -> skipped (no Baby Buddy model)

Every created record is tagged with ``huckleberry-import`` so a run can be
undone with a single query. Use ``--dry-run`` to preview, and ``--wipe`` to
delete the child's existing tracking data first (a full replace).

Example:
    python manage.py import_huckleberry huckleberry-export.csv \\
        --child my-childs-slug --wipe --dry-run
"""

import csv
import datetime
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core import models

IMPORT_TAG = "huckleberry-import"

# Models wiped by --wipe, i.e. everything the importer could plausibly touch.
WIPEABLE_MODELS = [
    models.Feeding,
    models.DiaperChange,
    models.Pumping,
    models.Weight,
    models.Height,
    models.BMI,
    models.HeadCircumference,
    models.Temperature,
    models.Sleep,
    models.TummyTime,
    models.Note,
    models.Medication,
]

DATETIME_FORMAT = "%Y-%m-%d %H:%M"


class _Rollback(Exception):
    """Raised to unwind the transaction after a --dry-run."""


def parse_dt(value, tz=None):
    """Parse a Huckleberry 'YYYY-MM-DD HH:MM' string into an aware datetime.

    The Huckleberry export has no timezone, so the naive wall-clock time is
    interpreted in ``tz`` (a tzinfo). When ``tz`` is None the instance's active
    timezone is used (settings.TIME_ZONE outside a request).
    """
    value = (value or "").strip()
    if not value:
        return None
    naive = datetime.datetime.strptime(value, DATETIME_FORMAT)
    return timezone.make_aware(naive, tz)


def parse_number(value):
    """Extract the first number from strings like '60ml', '3.45kg', '53cm'."""
    if not value:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(match.group()) if match else None


class Command(BaseCommand):
    help = "Import a Huckleberry CSV export into Baby Buddy."

    def add_arguments(self, parser):
        parser.add_argument("csv_file", help="Path to the Huckleberry export CSV.")
        parser.add_argument(
            "--child",
            help=(
                "Child slug or id to attach records to. Optional if exactly one "
                "child exists."
            ),
        )
        parser.add_argument(
            "--wipe",
            action="store_true",
            help="Delete the child's existing tracking data before importing.",
        )
        parser.add_argument(
            "--timezone",
            dest="tz_name",
            help=(
                "IANA timezone the Huckleberry data was recorded in, e.g. "
                "'Australia/Sydney'. The export has no timezone, so this is how "
                "the wall-clock times are interpreted. Defaults to the app's "
                "TIME_ZONE setting (UTC)."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and report without writing anything (rolls back).",
        )

    def handle(self, *args, **options):
        child = self._resolve_child(options.get("child"))
        self.tz = self._resolve_tz(options.get("tz_name"))
        dry_run = options["dry_run"]
        rows = self._read_rows(options["csv_file"])

        try:
            with transaction.atomic():
                if options["wipe"]:
                    self._wipe(child)
                counts = self._import(child, rows)
                self._report(counts, child, dry_run)
                if dry_run:
                    raise _Rollback()
        except _Rollback:
            self.stdout.write(self.style.WARNING("Dry run: no changes committed."))

    # -- setup ---------------------------------------------------------------

    def _resolve_child(self, child_arg):
        if child_arg:
            child = models.Child.objects.filter(slug=child_arg).first()
            if not child and str(child_arg).isdigit():
                child = models.Child.objects.filter(pk=int(child_arg)).first()
            if not child:
                raise CommandError(f"No child matches '{child_arg}'.")
            return child
        children = list(models.Child.objects.all()[:2])
        if len(children) == 1:
            return children[0]
        if not children:
            raise CommandError("No children exist. Create one first.")
        raise CommandError("Multiple children exist; specify one with --child <slug>.")

    def _resolve_tz(self, tz_name):
        if not tz_name:
            self.stdout.write(
                self.style.WARNING(
                    "No --timezone given; interpreting times as "
                    f"{timezone.get_current_timezone_name()}."
                )
            )
            return None
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise CommandError(f"Unknown timezone '{tz_name}'.")
        self.stdout.write(f"Interpreting Huckleberry times as {tz_name}.")
        return tz

    def _read_rows(self, path):
        try:
            with open(path, newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                return list(reader)
        except FileNotFoundError:
            raise CommandError(f"CSV file not found: {path}")

    def _wipe(self, child):
        self.stdout.write("Wiping existing tracking data:")
        for model in WIPEABLE_MODELS:
            deleted, _ = model.objects.filter(child=child).delete()
            self.stdout.write(f"  {model.__name__}: {deleted} deleted")

    # -- import --------------------------------------------------------------

    def _import(self, child, rows):
        counts = {
            "feeding": 0,
            "diaper": 0,
            "weight": 0,
            "height": 0,
            "pumping": 0,
            "bath_skipped": 0,
            "skipped": 0,
        }
        for line, row in enumerate(rows, start=2):
            event_type = (row.get("Type") or "").strip()
            try:
                if event_type == "Feed":
                    self._feed(child, row)
                    counts["feeding"] += 1
                elif event_type == "Diaper":
                    self._diaper(child, row)
                    counts["diaper"] += 1
                elif event_type == "Growth":
                    made = self._growth(child, row)
                    counts["weight"] += "weight" in made
                    counts["height"] += "height" in made
                elif event_type == "Pump":
                    self._pump(child, row)
                    counts["pumping"] += 1
                elif event_type == "Bath":
                    counts["bath_skipped"] += 1
                else:
                    counts["skipped"] += 1
                    self.stderr.write(
                        f"  line {line}: unknown type '{event_type}', skipped"
                    )
            except Exception as exc:  # noqa: BLE001 - surface the row, keep going
                counts["skipped"] += 1
                self.stderr.write(f"  line {line}: error ({exc}), skipped")
        return counts

    def _feed(self, child, row):
        start = parse_dt(row["Start"], self.tz)
        end = parse_dt(row["End"], self.tz) or start
        location = (row.get("Start Location") or "").strip().lower()

        if location == "breast":
            method = self._breast_method(row)
            feed_type = "breast milk"
            amount = None
        else:  # Bottle (or unspecified) feed.
            condition = (row.get("Start Condition") or "").strip().lower()
            feed_type = "formula" if "formula" in condition else "breast milk"
            method = "bottle"
            amount = parse_number(row.get("End Condition"))

        feeding = models.Feeding(
            child=child,
            start=start,
            end=end,
            type=feed_type,
            method=method,
            amount=amount,
            notes=self._clean_notes(row.get("Notes")),
        )
        feeding.save()
        feeding.tags.add(IMPORT_TAG)

    def _breast_method(self, row):
        """Determine breast method from the R/L markers in cols 5 and 7."""
        blob = f"{row.get('Start Condition', '')} {row.get('End Condition', '')}"
        has_right = "R" in blob
        has_left = "L" in blob
        if has_right and has_left:
            return "both breasts"
        if has_left:
            return "left breast"
        # Default to right breast when only an R marker (or nothing) is present;
        # Baby Buddy requires a concrete method value.
        return "right breast"

    def _diaper(self, child, row):
        color = (row.get("Duration") or "").strip().lower()
        if color not in {"black", "brown", "green", "yellow"}:
            color = ""
        content = (row.get("End Condition") or "").lower()
        consistency = (row.get("Start Condition") or "").strip()

        notes_parts = []
        if consistency:
            notes_parts.append(consistency)
        raw = (row.get("End Condition") or "").strip()
        if raw:
            notes_parts.append(raw)

        diaper = models.DiaperChange(
            child=child,
            time=parse_dt(row["Start"], self.tz),
            wet="pee" in content,
            solid="poo" in content,
            color=color,
            notes="; ".join(notes_parts) or None,
        )
        diaper.save()
        diaper.tags.add(IMPORT_TAG)

    def _growth(self, child, row):
        made = set()
        date = parse_dt(row["Start"], self.tz).date()
        weight = parse_number(row.get("Start Condition"))  # e.g. "3.45kg"
        height = parse_number(row.get("Start Location"))  # e.g. "53cm"
        if weight is not None:
            record = models.Weight(child=child, weight=weight, date=date)
            record.save()
            record.tags.add(IMPORT_TAG)
            made.add("weight")
        if height is not None:
            record = models.Height(child=child, height=height, date=date)
            record.save()
            record.tags.add(IMPORT_TAG)
            made.add("height")
        return made

    def _pump(self, child, row):
        start = parse_dt(row["Start"], self.tz)
        end = parse_dt(row["End"], self.tz) or start
        # Amount may appear in Start Condition, End Condition, or both (L/R).
        amount = sum(
            v
            for v in (
                parse_number(row.get("Start Condition")),
                parse_number(row.get("End Condition")),
            )
            if v is not None
        )
        pumping = models.Pumping(
            child=child,
            start=start,
            end=end,
            amount=amount,
            notes=self._clean_notes(row.get("Notes")),
        )
        pumping.save()
        pumping.tags.add(IMPORT_TAG)

    @staticmethod
    def _clean_notes(value):
        value = (value or "").strip()
        return value or None

    # -- reporting -----------------------------------------------------------

    def _report(self, counts, child, dry_run):
        verb = "Would import" if dry_run else "Imported"
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{verb} for child '{child}':"))
        self.stdout.write(f"  Feedings:        {counts['feeding']}")
        self.stdout.write(f"  Diaper changes:  {counts['diaper']}")
        self.stdout.write(f"  Weights:         {counts['weight']}")
        self.stdout.write(f"  Heights:         {counts['height']}")
        self.stdout.write(f"  Pumpings:        {counts['pumping']}")
        self.stdout.write(f"  Baths skipped:   {counts['bath_skipped']}")
        if counts["skipped"]:
            self.stdout.write(
                self.style.WARNING(f"  Rows skipped:    {counts['skipped']}")
            )
        self.stdout.write(f"  (all tagged '{IMPORT_TAG}')")
