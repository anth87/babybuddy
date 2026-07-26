# -*- coding: utf-8 -*-
from django import template
from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncDate
from django.template.defaultfilters import floatformat
from django.utils import formats, timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

import collections

from core import models
from core.templatetags.misc import feeding_time_diff_base
from core.utils import duration_parts

register = template.Library()

# Number of days shown by the Recent Feedings chart, today included.
FEEDING_RECENT_DAYS = 7

# The Last Pumping card is dropped from the dashboard once the most recent
# entry is older than this.
PUMPING_LAST_MAX_AGE = timezone.timedelta(days=7)


def _amount_display(total, count):
    """Formats a day's total, matching the units used elsewhere on the cards."""
    if not count:
        return _("None")
    return _("%(amount)s mL") % {"amount": floatformat(total, "0g")}


def _count_display(count):
    """Formats a day's number of feedings, or nothing at all for a quiet day."""
    if not count:
        return ""
    return ngettext("%(counter)s feeding", "%(counter)s feedings", count) % {
        "counter": count
    }


def _hide_empty(context):
    return context["request"].user.settings.dashboard_hide_empty


def _elapsed(duration):
    """Format a timedelta compactly for a status tile, e.g. "1h 09m" or "12m"."""
    if not isinstance(duration, timezone.timedelta):
        return ""
    h, m, s = duration_parts(duration)
    if h >= 24:
        return "%dd %dh" % (h // 24, h % 24)
    if h > 0:
        return "%dh %02dm" % (h, m)
    return "%dm" % m


def _stale(instance, model, child, field):
    """
    Whether an activity should drop off the dashboard entirely.

    An activity earns its place only by having been logged in the past seven
    days, which is the same rule the trends chart uses to decide its tabs.

    :param instance: the most recent instance, or None.
    :param model: the model to look for recent entries in.
    :param field: the name of that model's timestamp field.
    :returns: True when the activity should be hidden.
    """
    if not instance:
        return True
    return not _logged_recently(model, child, field)


def _logged_recently(model, child, field):
    """Whether a child has any entry of this type in the past seven days."""
    cutoff = timezone.localtime() - timezone.timedelta(days=7)
    return model.objects.filter(child=child, **{field + "__gte": cutoff}).exists()


def _status(last_time, interval):
    """
    Progress of the current wait against how long the wait usually is.

    Both values come from data the cards already compute, so this only shapes
    them for the status tile: how long it has been, how far through a typical
    gap that is, and when the next one is roughly due.

    :param last_time: when the activity last happened, or None.
    :param interval: the typical gap between occurrences, or a falsy value.
    :returns: a dictionary for the status tile.
    """
    if not last_time:
        return {
            "since": None,
            "elapsed": "",
            "pct": 0,
            "due": "",
            "due_soon": False,
            "has_meter": False,
        }

    since = timezone.localtime() - timezone.localtime(last_time)
    # Without a known interval there is nothing to measure progress against, so
    # the tile shows the elapsed time only rather than a meter reading "full".
    status = {
        "since": since,
        "elapsed": _elapsed(since),
        "pct": 0,
        "due": "",
        "due_soon": False,
        "has_meter": False,
    }

    if isinstance(interval, timezone.timedelta) and interval:
        status["has_meter"] = True
        status["pct"] = max(0, min(100, round(since / interval * 100)))
        remaining = interval - since
        if remaining > timezone.timedelta():
            status["due"] = _("Due in ~%(time)s") % {"time": _elapsed(remaining)}
            status["due_soon"] = remaining < timezone.timedelta(minutes=30)
        else:
            status["due"] = _("Due now")
            status["due_soon"] = True
        status["interval"] = _elapsed(interval)

    return status


def _filter_data_age(context, keyword="end"):
    filter = {}
    if context["request"].user.settings.dashboard_hide_age:
        now = timezone.localtime()
        start_time = now - context["request"].user.settings.dashboard_hide_age
        filter[keyword + "__range"] = (start_time, now)
    return filter


@register.inclusion_tag("cards/diaperchange_last.html", takes_context=True)
def card_diaperchange_last(context, child):
    """
    Information about the most recent diaper change.
    :param child: an instance of the Child model.
    :returns: a dictionary with the most recent Diaper Change instance.
    """
    instance = (
        models.DiaperChange.objects.filter(child=child)
        .filter(**_filter_data_age(context, "time"))
        .order_by("-time")
        .first()
    )
    empty = not instance

    # Reuse the frequency already computed for the statistics card (past 3 days).
    statistics = _diaperchange_statistics(child) if instance else None
    interval = statistics[0]["btwn_average"] if statistics else None

    return {
        "type": "diaperchange",
        "change": instance,
        "status": _status(instance.time if instance else None, interval),
        "stale": _stale(instance, models.DiaperChange, child, "time"),
        "empty": empty,
        "hide_empty": _hide_empty(context),
    }


@register.inclusion_tag("cards/diaperchange_types.html", takes_context=True)
def card_diaperchange_types(context, child, date=None):
    """
    Creates a break down of wet and solid Diaper Change instances for the past
    seven days.
    :param child: an instance of the Child model.
    :param date: a datetime object for the day to filter.
    :returns: a dictionary with the wet/solid/empty statistics.
    """
    if not date:
        date = timezone.localtime()
    else:
        date = timezone.datetime.combine(date, timezone.localtime().min.time())
        date = timezone.make_aware(date)
    max_date = (date + timezone.timedelta(days=1)).replace(hour=0, minute=0, second=0)
    min_date = (max_date - timezone.timedelta(days=7)).replace(
        hour=0, minute=0, second=0
    )

    stats = {}
    for x in range(7):
        stats[x] = {"wet": 0.0, "solid": 0.0, "empty": 0.0, "changes": 0.0}

    instances = (
        models.DiaperChange.objects.filter(child=child)
        .filter(time__gt=min_date)
        .filter(time__lt=max_date)
        .order_by("-time")
    )
    empty = len(instances) == 0

    for instance in instances:
        key = (max_date - timezone.localtime(instance.time)).days
        stats[key]["changes"] += 1
        if instance.wet:
            stats[key]["wet"] += 1
        if instance.solid:
            stats[key]["solid"] += 1
        if not instance.wet and not instance.solid:
            stats[key]["empty"] += 1

    week_total = 0
    for key, info in stats.items():
        total = info["wet"] + info["solid"] + info["empty"]
        week_total += total
        if total > 0:
            stats[key]["wet_pct"] = info["wet"] / total * 100
            stats[key]["solid_pct"] = info["solid"] / total * 100
            stats[key]["empty_pct"] = info["empty"] / total * 100

    return {
        "type": "diaperchange",
        "stats": stats,
        "total": week_total,
        "empty": empty,
        "hide_empty": _hide_empty(context),
    }


@register.inclusion_tag("cards/breastfeeding.html", takes_context=True)
def card_breastfeeding(context, child, date=None):
    """
    Creates a break down of breasts used for breastfeeding, for the past
    seven days.
    :param child: an instance of the Child model.
    :param date: a datetime object for the day to filter.
    :returns: a dictionary with the statistics.
    """
    if date:
        date = timezone.datetime.combine(date, timezone.localtime().min.time())
        date = timezone.make_aware(date)
    else:
        date = timezone.localtime()

    max_date = (date + timezone.timedelta(days=1)).replace(hour=0, minute=0, second=0)
    min_date = (max_date - timezone.timedelta(days=7)).replace(
        hour=0, minute=0, second=0
    )

    instances = (
        models.Feeding.objects.filter(child=child)
        .filter(start__gt=min_date)
        .filter(start__lt=max_date)
        .filter(method__in=("left breast", "right breast", "both breasts"))
        .order_by("-start")
    )

    # Create a `stats` dictionary, keyed by day for the past 7 days.
    stats = {}
    for x in range(7):
        stats[x] = {}

    # Group feedings per day.
    per_day = collections.defaultdict(list)
    for instance in instances:
        key = (max_date - timezone.localtime(instance.start)).days
        per_day[key].append(instance)

    # Go through each day, set the stats dictionary for that day.
    for key, day_instances in per_day.items():
        left_count = 0
        right_count = 0
        for instance in day_instances:
            if instance.method in ("left breast", "both breasts"):
                left_count += 1
            if instance.method in ("right breast", "both breasts"):
                right_count += 1

        stats[key] = {
            "count": len(day_instances),
            "duration": sum(
                (instance.duration for instance in day_instances),
                start=timezone.timedelta(),
            ),
            "left_count": left_count,
            "right_count": right_count,
            "left_pct": 100 * left_count // (left_count + right_count),
            "right_pct": 100 * right_count // (left_count + right_count),
        }

    return {
        "type": "feeding",
        "stats": stats,
        "total": len(instances),
        "empty": len(instances) == 0,
        "hide_empty": _hide_empty(context),
    }


@register.inclusion_tag("cards/feeding_recent.html", takes_context=True)
def card_feeding_recent(context, child, end_date=None):
    """
    Totals Feeding instances by day over the past week for the card's bar chart.
    :param child: an instance of the Child model.
    :param end_date: a Date object for the last day of the window.
    :returns: a dict with per-day totals, today's figures and the daily average.
    """
    if not end_date:
        end_date = timezone.localtime()

    last_day = end_date.date()
    first_day = last_day - timezone.timedelta(days=FEEDING_RECENT_DAYS - 1)

    instances = models.Feeding.objects.filter(
        child=child, start__date__range=(first_day, last_day)
    )

    # One bucket per day in the window, oldest first, so days without any
    # feeding still get a column in the chart.
    days = collections.OrderedDict(
        (first_day + timezone.timedelta(days=i), {"total": 0, "count": 0})
        for i in range(FEEDING_RECENT_DAYS)
    )
    for instance in instances:
        # The query and this lookup both resolve dates in the active timezone,
        # so a miss should not happen -- but a dropped feeding beats a
        # dashboard that raises.
        day = days.get(timezone.localtime(instance.start).date())
        if day is None:
            continue
        day["total"] += instance.amount or 0
        day["count"] += 1

    # Bars are drawn as a percentage of the tallest day. A window with feedings
    # but no recorded amounts has no tallest day; every bar falls back to the
    # minimum height set in CSS.
    scale = max(day["total"] for day in days.values()) or 1

    results = [
        {
            "date": date,
            "total": day["total"],
            "count": day["count"],
            "percent": round(day["total"] / scale * 100),
            "today": date == last_day,
            # Rendered here rather than in the template so that the readout and
            # the bar that fills it in cannot drift apart.
            "amount_display": _amount_display(day["total"], day["count"]),
            "count_display": _count_display(day["count"]),
            "label": ngettext(
                "%(date)s: %(amount)s mL over %(count)s feeding",
                "%(date)s: %(amount)s mL over %(count)s feedings",
                day["count"],
            )
            % {
                "date": formats.date_format(date),
                "amount": floatformat(day["total"], "0g"),
                "count": day["count"],
            },
        }
        for date, day in days.items()
    ]

    # Today is excluded from the average so that a part-finished day cannot
    # drag it down.
    completed = results[:-1]
    average = round(sum(day["total"] for day in completed) / len(completed))

    return {
        "days": results,
        "today": results[-1],
        "average": average,
        "average_percent": round(average / scale * 100),
        "type": "feeding",
        "empty": len(instances) == 0,
        "hide_empty": _hide_empty(context),
    }


@register.inclusion_tag("cards/feeding_last.html", takes_context=True)
def card_feeding_last(context, child):
    """
    Information about the most recent feeding.
    :param child: an instance of the Child model.
    :returns: a dictionary with the most recent Feeding instance.
    """
    instance = (
        models.Feeding.objects.filter(child=child)
        .filter(**_filter_data_age(context))
        .order_by("-end")
        .first()
    )
    empty = not instance
    diff_base = feeding_time_diff_base(context, instance)

    # Reuse the frequency already computed for the statistics card (past 3 days).
    statistics = _feeding_statistics(child) if instance else None
    interval = statistics[0]["btwn_average"] if statistics else None

    return {
        "type": "feeding",
        "feeding": instance,
        "feeding_diff_base": diff_base,
        "status": _status(diff_base, interval),
        "stale": _stale(instance, models.Feeding, child, "start"),
        "empty": empty,
        "hide_empty": _hide_empty(context),
    }


@register.inclusion_tag("cards/feeding_last_method.html", takes_context=True)
def card_feeding_last_method(context, child):
    """
    Information about the three most recent feeding methods.
    :param child: an instance of the Child model.
    :returns: a dictionary with the most recent Feeding instances.
    """
    instances = (
        models.Feeding.objects.filter(child=child)
        .filter(**_filter_data_age(context))
        .order_by("-end")[:3]
    )
    num_unique_methods = len({i.method for i in instances})
    empty = num_unique_methods <= 1

    # Results are reversed for carousel forward/back behavior.
    return {
        "type": "feeding",
        "feedings": list(reversed(instances)),
        "empty": empty,
        "hide_empty": _hide_empty(context),
    }


@register.inclusion_tag("cards/pumping_last.html", takes_context=True)
def card_pumping_last(context, child):
    """
    Information about the most recent pumping.
    :param child: an instance of the Child model.
    :returns: a dictionary with the most recent Pumping instance.
    """
    instance = (
        models.Pumping.objects.filter(child=child)
        .filter(end__gte=timezone.localtime() - PUMPING_LAST_MAX_AGE)
        .filter(**_filter_data_age(context))
        .order_by("-end")
        .first()
    )

    return {
        "type": "pumping",
        "pumping": instance,
        "status": _status(instance.end if instance else None, None),
        "stale": _stale(instance, models.Pumping, child, "start"),
        "empty": not instance,
        # Nothing pumped in the past week means there is nothing worth a slot on
        # the dashboard, so this card is always dropped when empty rather than
        # deferring to the user's "hide empty" preference.
        "hide_empty": True,
    }


@register.inclusion_tag("cards/pumping_recent.html", takes_context=True)
def card_pumping_recent(context, child, end_date=None):
    """
    Filters Pumping instances to get total amount for a specific date and for 7 days before.
    :param child: an instance of the Child model.
    :param end_date: a Date object for the day to filter.
    :returns: a dict with count and total amount for the Pumping instances.
    """
    if not end_date:
        end_date = timezone.localtime()

    end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=9999)
    start_date = end_date - timezone.timedelta(days=8)

    instances = models.Pumping.objects.filter(child=child).filter(
        start__range=[start_date, end_date]
    )

    dates = [end_date - timezone.timedelta(days=i) for i in range(8)]
    results = [{"date": d, "total": 0, "count": 0} for d in dates]

    for instance in instances:
        pump_date = timezone.localtime(instance.end).replace(
            hour=23, minute=59, second=59, microsecond=9999
        )
        idx = (end_date - pump_date).days
        result = results[idx]
        result["total"] += instance.amount if instance.amount is not None else 0
        result["count"] += 1

    return {
        "pumpings": results,
        "type": "pumping",
        "empty": len(instances) == 0,
        "hide_empty": _hide_empty(context),
    }


@register.inclusion_tag("cards/sleep_last.html", takes_context=True)
def card_sleep_last(context, child):
    """
    Information about the most recent sleep entry.
    :param child: an instance of the Child model.
    :returns: a dictionary with the most recent Sleep instance.
    """
    instance = (
        models.Sleep.objects.filter(child=child)
        .filter(**_filter_data_age(context))
        .order_by("-end")
        .first()
    )
    empty = not instance

    # Reuse the average awake duration already computed for the statistics card.
    statistics = _sleep_statistics(child) if instance else None
    interval = statistics["btwn_average"] if statistics else None

    return {
        "type": "sleep",
        "sleep": instance,
        "status": _status(instance.end if instance else None, interval),
        "stale": _stale(instance, models.Sleep, child, "end"),
        "empty": empty,
        "hide_empty": _hide_empty(context),
    }


@register.inclusion_tag("cards/sleep_recent.html", takes_context=True)
def card_sleep_recent(context, child, end_date=None):
    """
    Filters sleeping instances to get total amount for a specific date and for 7 days before
    :param child: an instance of the Child model.
    :param end_date: a Date object for the day to filter.
    :returns: a dict with count and total amount for the sleeping instances.
    """
    if not end_date:
        end_date = timezone.localtime()

    # push end_date to very end of that day
    end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=9999)
    # we need a datetime to use the range helper in the model
    start_date = end_date - timezone.timedelta(
        days=8
    )  # end of the -8th day so we get the FULL 7th day

    instances = models.Sleep.objects.filter(child=child).filter(
        start__range=[start_date, end_date]
    ) | models.Sleep.objects.filter(child=child).filter(
        end__range=[start_date, end_date]
    )

    # prepare the result list for the last 7 days
    dates = [end_date - timezone.timedelta(days=i) for i in range(8)]
    results = [{"date": d, "total": timezone.timedelta(), "count": 0} for d in dates]

    # do one pass over the data and add it to the appropriate day
    for instance in instances:
        # convert to local tz and push feed_date to end so we're comparing apples to apples for the date
        start = timezone.localtime(instance.start)
        end = timezone.localtime(instance.end)
        sleep_start_date = start.replace(
            hour=23, minute=59, second=59, microsecond=9999
        )
        sleep_end_date = end.replace(hour=23, minute=59, second=59, microsecond=9999)
        start_idx = (end_date - sleep_start_date).days
        end_idx = (end_date - sleep_end_date).days
        # this is more complicated than feedings because we only want to capture the PORTION of sleep
        # that is a part of this day (e.g. starts sleep at 7PM and finished at 7AM = 5 hrs yesterday 7 hrs today)
        # (Assuming you have a unicorn sleeper. Congratulations)
        if start_idx == end_idx:  # if we're in the same day it's simple
            result = results[start_idx]
            result["total"] += end - start
            result["count"] += 1
        else:  # otherwise we need to split the time up
            midnight = end.replace(hour=0, minute=0, second=0)

            if 0 <= start_idx < len(results):
                result = results[start_idx]
                # only the portion that is today
                result["total"] += midnight - start
                result["count"] += 1

            if 0 <= end_idx < len(results):
                result = results[end_idx]
                # only the portion that is tomorrow
                result["total"] += end - midnight
                result["count"] += 1

    return {
        "sleeps": results,
        "type": "sleep",
        "empty": len(instances) == 0,
        "hide_empty": _hide_empty(context),
    }


@register.inclusion_tag("cards/sleep_naps_day.html", takes_context=True)
def card_sleep_naps_day(context, child, date=None):
    """
    Filters Sleep instances categorized as naps and generates statistics for a
    specific date.
    :param child: an instance of the Child model.
    :param date: a Date object for the day to filter.
    :returns: a dictionary of nap data statistics.
    """
    if not date:
        date = timezone.localtime().date()
    instances = models.Sleep.objects.filter(child=child, nap=True).filter(
        start__year=date.year, start__month=date.month, start__day=date.day
    ) | models.Sleep.objects.filter(child=child, nap=True).filter(
        end__year=date.year, end__month=date.month, end__day=date.day
    )
    empty = len(instances) == 0

    return {
        "type": "sleep",
        "total": instances.aggregate(Sum("duration"))["duration__sum"],
        "count": len(instances),
        "empty": empty,
        "hide_empty": _hide_empty(context),
    }


@register.inclusion_tag("cards/statistics.html", takes_context=True)
def card_statistics(context, child):
    """
    Statistics data for all models.
    :param child: an instance of the Child model.
    :returns: a list of dictionaries with "type", "stat" and "title" entries.
    """
    stats = []

    changes = _diaperchange_statistics(child)
    if changes:
        for item in changes:
            stats.append(
                {
                    "type": "duration",
                    "stat": item["btwn_average"],
                    "title": item["title"],
                }
            )

    feedings = _feeding_statistics(child)
    if feedings:
        for item in feedings:
            stats.append(
                {
                    "type": "duration",
                    "stat": item["btwn_average"],
                    "title": item["title"],
                }
            )

    naps = _nap_statistics(child)
    if naps:
        stats.append(
            {
                "type": "duration",
                "stat": naps["average"],
                "title": _("Average nap duration"),
            }
        )
        stats.append(
            {
                "type": "float",
                "stat": naps["avg_per_day"],
                "title": _("Average naps per day"),
            }
        )

    sleep = _sleep_statistics(child)
    if sleep:
        stats.append(
            {
                "type": "duration",
                "stat": sleep["average"],
                "title": _("Average sleep duration"),
            }
        )
        stats.append(
            {
                "type": "duration",
                "stat": sleep["btwn_average"],
                "title": _("Average awake duration"),
            }
        )

    weight = _weight_statistics(child)
    if weight:
        stats.append(
            {
                "type": "float",
                "stat": weight["change_weekly"],
                "title": _("Weight change per week"),
            }
        )

    height = _height_statistics(child)
    if height:
        stats.append(
            {
                "type": "float",
                "stat": height["change_weekly"],
                "title": _("Height change per week"),
            }
        )

    head_circumference = _head_circumference_statistics(child)
    if head_circumference:
        stats.append(
            {
                "type": "float",
                "stat": head_circumference["change_weekly"],
                "title": _("Head circumference change per week"),
            }
        )

    bmi = _bmi_statistics(child)
    if bmi:
        stats.append(
            {
                "type": "float",
                "stat": bmi["change_weekly"],
                "title": _("BMI change per week"),
            }
        )

    empty = len(stats) == 0

    return {"stats": stats, "empty": empty, "hide_empty": _hide_empty(context)}


def _diaperchange_statistics(child):
    """
    Averaged Diaper Change data.
    :param child: an instance of the Child model.
    :returns: a dictionary of statistics.
    """
    changes = [
        {
            "start": timezone.localtime() - timezone.timedelta(days=3),
            "title": _("Diaper change frequency (past 3 days)"),
        },
        {
            "start": timezone.localtime() - timezone.timedelta(weeks=2),
            "title": _("Diaper change frequency (past 2 weeks)"),
        },
        {
            "start": None,
            "title": _("Diaper change frequency"),
        },
    ]
    for timespan in changes:
        timespan["btwn_total"] = timezone.timedelta(0)
        timespan["btwn_count"] = 0
        timespan["btwn_average"] = 0.0

    instances = models.DiaperChange.objects.filter(child=child).order_by("time")
    if len(instances) == 0:
        return False
    last_instance = None

    for instance in instances:
        if last_instance:
            for timespan in changes:
                last_time = timezone.localtime(last_instance.time)
                if timespan["start"] is None or last_time > timespan["start"]:
                    timespan["btwn_total"] += (
                        timezone.localtime(instance.time) - last_time
                    )
                    timespan["btwn_count"] += 1
        last_instance = instance

    for timespan in changes:
        if timespan["btwn_count"] > 0:
            timespan["btwn_average"] = timespan["btwn_total"] / timespan["btwn_count"]
    return changes


def _feeding_statistics(child):
    """
    Averaged Feeding data.
    :param child: an instance of the Child model.
    :returns: a dictionary of statistics.
    """
    feedings = [
        {
            "start": timezone.localtime() - timezone.timedelta(days=3),
            "title": _("Feeding frequency (past 3 days)"),
        },
        {
            "start": timezone.localtime() - timezone.timedelta(weeks=2),
            "title": _("Feeding frequency (past 2 weeks)"),
        },
        {
            "start": None,
            "title": _("Feeding frequency"),
        },
    ]
    for timespan in feedings:
        timespan["btwn_total"] = timezone.timedelta(0)
        timespan["btwn_count"] = 0
        timespan["btwn_average"] = 0.0

    instances = models.Feeding.objects.filter(child=child).order_by("start")
    if len(instances) == 0:
        return False
    last_instance = None

    for instance in instances:
        if last_instance:
            for timespan in feedings:
                start = timezone.localtime(instance.start)
                last_start = timezone.localtime(last_instance.start)
                last_end = timezone.localtime(last_instance.end)
                if timespan["start"] is None or last_start > timespan["start"]:
                    timespan["btwn_total"] += start - last_end
                    timespan["btwn_count"] += 1
        last_instance = instance

    for timespan in feedings:
        if timespan["btwn_count"] > 0:
            timespan["btwn_average"] = timespan["btwn_total"] / timespan["btwn_count"]
    return feedings


def _nap_statistics(child):
    """
    Averaged nap data.
    :param child: an instance of the Child model.
    :returns: a dictionary of statistics.
    """
    instances = models.Sleep.objects.filter(child=child, nap=True).order_by("start")
    if len(instances) == 0:
        return False
    naps = {
        "total": instances.aggregate(Sum("duration"))["duration__sum"],
        "count": instances.count(),
        "average": 0.0,
        "avg_per_day": 0.0,
    }
    if naps["count"] > 0:
        naps["average"] = naps["total"] / naps["count"]

    naps_avg = (
        instances.annotate(date=TruncDate("start"))
        .values("date")
        .annotate(naps_count=Count("id"))
        .order_by()
        .aggregate(Avg("naps_count"))
    )
    naps["avg_per_day"] = naps_avg["naps_count__avg"]

    return naps


def _sleep_statistics(child):
    """
    Averaged Sleep data.
    :param child: an instance of the Child model.
    :returns: a dictionary of statistics.
    """
    instances = models.Sleep.objects.filter(child=child).order_by("start")
    if len(instances) == 0:
        return False

    sleep = {
        "total": instances.aggregate(Sum("duration"))["duration__sum"],
        "count": instances.count(),
        "average": 0.0,
        "btwn_total": timezone.timedelta(0),
        "btwn_count": instances.count() - 1,
        "btwn_average": 0.0,
    }

    last_instance = None
    for instance in instances:
        if last_instance:
            start = timezone.localtime(instance.start)
            last_end = timezone.localtime(last_instance.end)
            sleep["btwn_total"] += start - last_end
        last_instance = instance

    if sleep["count"] > 0:
        sleep["average"] = sleep["total"] / sleep["count"]
    if sleep["btwn_count"] > 0:
        sleep["btwn_average"] = sleep["btwn_total"] / sleep["btwn_count"]

    return sleep


def _weight_statistics(child):
    """
    Statistical weight data.
    :param child: an instance of the Child model.
    :returns: a dictionary of statistics.
    """
    weight = {"change_weekly": 0.0}

    instances = models.Weight.objects.filter(child=child).order_by("-date")
    if len(instances) == 0:
        return False

    newest = instances.first()
    oldest = instances.last()

    if newest != oldest:
        weight_change = newest.weight - oldest.weight
        weeks = (newest.date - oldest.date).days / 7
        weight["change_weekly"] = weight_change / weeks

    return weight


def _height_statistics(child):
    """
    Statistical height data.
    :param child: an instance of the Child model.
    :returns: a dictionary of statistics.
    """
    height = {"change_weekly": 0.0}

    instances = models.Height.objects.filter(child=child).order_by("-date")
    if len(instances) == 0:
        return False

    newest = instances.first()
    oldest = instances.last()

    if newest != oldest:
        height_change = newest.height - oldest.height
        weeks = (newest.date - oldest.date).days / 7
        height["change_weekly"] = height_change / weeks

    return height


def _head_circumference_statistics(child):
    """
    Statistical head circumference data.
    :param child: an instance of the Child model.
    :returns: a dictionary of statistics.
    """
    head_circumference = {"change_weekly": 0.0}

    instances = models.HeadCircumference.objects.filter(child=child).order_by("-date")
    if len(instances) == 0:
        return False

    newest = instances.first()
    oldest = instances.last()

    if newest != oldest:
        hc_change = newest.head_circumference - oldest.head_circumference
        weeks = (newest.date - oldest.date).days / 7
        head_circumference["change_weekly"] = hc_change / weeks

    return head_circumference


def _bmi_statistics(child):
    """
    Statistical BMI data.
    :param child: an instance of the Child model.
    :returns: a dictionary of statistics.
    """
    bmi = {"change_weekly": 0.0}

    instances = models.BMI.objects.filter(child=child).order_by("-date")
    if len(instances) == 0:
        return False

    newest = instances.first()
    oldest = instances.last()

    if newest != oldest:
        bmi_change = newest.bmi - oldest.bmi
        weeks = (newest.date - oldest.date).days / 7
        bmi["change_weekly"] = bmi_change / weeks

    return bmi


@register.inclusion_tag("cards/timer_list.html", takes_context=True)
def card_timer_list(context, child=None):
    """
    Filters for currently active Timer instances, optionally by child.
    :param child: an instance of the Child model.
    :returns: a dictionary with a list of active Timer instances.
    """
    if child:
        # Get active instances for the selected child _or_ None (no child).
        instances = models.Timer.objects.filter(
            Q(child=child) | Q(child=None)
        ).order_by("-start")
    else:
        instances = models.Timer.objects.order_by("-start")
    empty = len(instances) == 0

    return {
        "type": "timer",
        "instances": list(instances),
        "empty": empty,
        "hide_empty": _hide_empty(context),
    }


@register.inclusion_tag("cards/tummytime_last.html", takes_context=True)
def card_tummytime_last(context, child):
    """
    Filters the most recent tummy time.
    :param child: an instance of the Child model.
    :returns: a dictionary with the most recent Tummy Time instance.
    """
    instance = (
        models.TummyTime.objects.filter(child=child)
        .filter(**_filter_data_age(context))
        .order_by("-end")
        .first()
    )
    empty = not instance

    return {
        "type": "tummytime",
        "tummytime": instance,
        "empty": empty,
        "hide_empty": _hide_empty(context),
    }


@register.inclusion_tag("cards/tummytime_day.html", takes_context=True)
def card_tummytime_day(context, child, date=None):
    """
    Filters Tummy Time instances and generates statistics for a specific date.
    :param child: an instance of the Child model.
    :param date: a Date object for the day to filter.
    :returns: a dictionary of all Tummy Time instances and stats for date.
    """
    if not date:
        date = timezone.localtime().date()
    instances = models.TummyTime.objects.filter(
        child=child, end__year=date.year, end__month=date.month, end__day=date.day
    ).order_by("-end")
    empty = len(instances) == 0

    stats = {"total": timezone.timedelta(seconds=0), "count": instances.count()}
    for instance in instances:
        stats["total"] += timezone.timedelta(seconds=instance.duration.seconds)

    last = instances.first()

    return {
        "type": "tummytime",
        "stats": stats,
        "instances": instances,
        "last": last,
        # Progress toward the day's tummy time rather than a wait between events.
        "status": {
            "elapsed": _elapsed(stats["total"]) or "0m",
            "pct": max(
                0,
                min(100, round(stats["total"] / timezone.timedelta(minutes=20) * 100)),
            ),
            "has_meter": True,
            "goal_met": stats["total"] >= timezone.timedelta(minutes=20),
            "last_time": last.end if last else None,
        },
        # Today can legitimately be empty while the week still has sessions, so
        # this looks at the week rather than at today's instances.
        "stale": not _logged_recently(models.TummyTime, child, "start"),
        "empty": empty,
        "hide_empty": _hide_empty(context),
    }


# Minimum bar height, as a percentage of the chart, so a low-but-nonzero day
# stays visible next to a tall one.
MIN_BAR_PCT = 6


def _trend_days(values, displays, headlines, sublines, lower=None):
    """
    Turn a seven-day series into bar geometry for the trends chart.
    :param values: seven numbers, oldest first, driving the bar heights.
    :param lower: optional seven numbers for the darker stacked segment.
    :returns: a (days, average_pct) tuple.
    """
    peak = max(values) or 1
    days = []
    today = timezone.localtime().date()
    for index, value in enumerate(values):
        date = today - timezone.timedelta(days=6 - index)
        height = MIN_BAR_PCT if not value else max(MIN_BAR_PCT, value / peak * 100)
        days.append(
            {
                "index": index,
                "letter": formats.date_format(date, "D")[0],
                "name": _("Today") if index == 6 else formats.date_format(date, "l"),
                "today": index == 6,
                "display": displays[index],
                "headline": headlines[index],
                "subline": sublines[index],
                "height": round(height, 2),
                # Percentage of *this bar* taken by the darker lower segment.
                "lower": round(lower[index] / value * 100, 2) if lower and value else 0,
            }
        )
    average_pct = round(min(100, (sum(values) / 7) / peak * 100), 2)
    return days, average_pct


def _amount(value):
    """Format a millilitre amount the way the rest of the app does."""
    return formats.number_format(round(value), decimal_pos=0, force_grouping=True)


@register.inclusion_tag("cards/trends.html", takes_context=True)
def card_trends(context, child):
    """
    A tabbed seven-day chart, assembled from the existing "recent" cards so the
    dashboard does not query the same data twice.
    :param child: an instance of the Child model.
    :returns: a dictionary with one entry per activity that has recent data.
    """
    metrics = []

    # Feeding: amounts when they are recorded, counts for breastfed children.
    feeding = card_feeding_recent(context, child)
    if not feeding["empty"]:
        # Already one bucket per day, oldest first, which is the order the
        # chart draws in.
        recent = feeding["days"]
        by_amount = sum(day["total"] for day in recent) > 0
        values = [day["total"] if by_amount else day["count"] for day in recent]
        if by_amount:
            displays = [_amount(day["total"]) for day in recent]
            headlines = [
                _("%(amount)s mL") % {"amount": _amount(day["total"])} for day in recent
            ]
        else:
            displays = [str(day["count"]) for day in recent]
            headlines = [
                ngettext("%(count)s feeding", "%(count)s feedings", day["count"])
                % {"count": day["count"]}
                for day in recent
            ]
        sublines = [
            ngettext("%(count)s feeding", "%(count)s feedings", day["count"])
            % {"count": day["count"]}
            for day in recent
        ]
        days, average_pct = _trend_days(values, displays, headlines, sublines)
        statistics = _feeding_statistics(child)
        metrics.append(
            {
                "key": "feeding",
                "label": _("Feeding"),
                "days": days,
                "average_pct": average_pct,
                "average_label": (
                    _("Daily average: %(amount)s mL")
                    % {"amount": _amount(sum(values) / 7)}
                    if by_amount
                    else _("Daily average: %(count)s feedings")
                    % {"count": formats.number_format(sum(values) / 7, decimal_pos=1)}
                ),
                "interval": (
                    _elapsed(statistics[0]["btwn_average"]) if statistics else ""
                ),
                "total": (
                    _("%(amount)s mL") % {"amount": _amount(sum(values))}
                    if by_amount
                    else str(int(sum(values)))
                ),
                "split": False,
            }
        )

    # Nappies: total changes per day, with the solid share as the lower segment.
    nappies = card_diaperchange_types(context, child)
    if not nappies["empty"]:
        recent = [nappies["stats"][index] for index in range(6, -1, -1)]
        values = [day["changes"] for day in recent]
        solids = [day["solid"] for day in recent]
        displays = [str(int(day["changes"])) for day in recent]
        headlines = [
            ngettext("%(count)s change", "%(count)s changes", int(day["changes"]))
            % {"count": int(day["changes"])}
            for day in recent
        ]
        sublines = [
            _("%(wet)d wet · %(dirty)d dirty")
            % {"wet": day["changes"] - day["solid"], "dirty": day["solid"]}
            for day in recent
        ]
        days, average_pct = _trend_days(values, displays, headlines, sublines, solids)
        statistics = _diaperchange_statistics(child)
        metrics.append(
            {
                "key": "diaperchange",
                "label": _("Nappies"),
                "days": days,
                "average_pct": average_pct,
                "average_label": _("Daily average: %(count)s changes")
                % {"count": formats.number_format(sum(values) / 7, decimal_pos=1)},
                "interval": (
                    _elapsed(statistics[0]["btwn_average"]) if statistics else ""
                ),
                "total": str(int(sum(values))),
                "split": True,
            }
        )

    # Sleep: hours per day, already split across midnight by the recent card.
    sleep = card_sleep_recent(context, child)
    if not sleep["empty"]:
        recent = list(reversed(sleep["sleeps"][:7]))
        values = [day["total"].total_seconds() / 3600 for day in recent]
        displays = [
            "%dh" % round(value) if value >= 1 else "%dm" % round(value * 60)
            for value in values
        ]
        headlines = [_elapsed(day["total"]) or "0m" for day in recent]
        sublines = [
            ngettext("%(count)s sleep", "%(count)s sleeps", day["count"])
            % {"count": day["count"]}
            for day in recent
        ]
        days, average_pct = _trend_days(values, displays, headlines, sublines)
        statistics = _sleep_statistics(child)
        metrics.append(
            {
                "key": "sleep",
                "label": _("Sleep"),
                "days": days,
                "average_pct": average_pct,
                "average_label": _("Daily average: %(total)s")
                % {"total": _elapsed(timezone.timedelta(hours=sum(values) / 7))},
                "interval": (
                    _elapsed(statistics["btwn_average"]) + " " + _("awake")
                    if statistics
                    else ""
                ),
                "total": _elapsed(timezone.timedelta(hours=sum(values))),
                "split": False,
            }
        )

    # Pumping: millilitres per day.
    pumping = card_pumping_recent(context, child)
    if not pumping["empty"]:
        recent = list(reversed(pumping["pumpings"][:7]))
        values = [day["total"] for day in recent]
        displays = [_amount(day["total"]) for day in recent]
        headlines = [
            _("%(amount)s mL") % {"amount": _amount(day["total"])} for day in recent
        ]
        sublines = [
            ngettext("%(count)s session", "%(count)s sessions", day["count"])
            % {"count": day["count"]}
            for day in recent
        ]
        days, average_pct = _trend_days(values, displays, headlines, sublines)
        metrics.append(
            {
                "key": "pumping",
                "label": _("Pumping"),
                "days": days,
                "average_pct": average_pct,
                "average_label": _("Daily average: %(amount)s mL")
                % {"amount": _amount(sum(values) / 7)},
                "interval": "",
                "total": _("%(amount)s mL") % {"amount": _amount(sum(values))},
                "split": False,
            }
        )

    return {
        "child": child,
        "metrics": metrics,
        "default": metrics[0] if metrics else None,
        "empty": not metrics,
        "hide_empty": _hide_empty(context),
    }


@register.inclusion_tag("cards/medication_last.html", takes_context=True)
def card_medication_last(context, child):
    """
    Information about the most recent medication administration.
    :param child: an instance of the Child model.
    :returns: a dictionary with the most recent Medication instance.
    """
    instance = (
        models.Medication.objects.filter(child=child)
        .filter(**_filter_data_age(context, "time"))
        .select_related("child")
        .order_by("-time")
        .first()
    )

    return {
        "type": "medication",
        "medication": instance,
        "status": _status(
            instance.time if instance else None,
            instance.next_dose_interval if instance else None,
        ),
        "stale": _stale(instance, models.Medication, child, "time"),
        "empty": not instance,
        "hide_empty": _hide_empty(context),
    }
