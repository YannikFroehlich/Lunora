"""Time tracking for tasks: one running timer per user, finished spans, manual entries, a weekly report.

Entries are private to the user who tracked them, also on shared or assigned tasks; every function
here filters on ``user``. A task can only be tracked when it is in ``visible_tasks(user)``.
"""

import csv
import io
from datetime import date, datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from app.models import TaskTimeEntry
from app.services.tasks import visible_tasks
from app.services.user_preferences import (
    format_user_date,
    format_user_time,
    get_user_weekday_name,
    get_user_zoneinfo,
    localtime_for_user,
)

MANUAL_ENTRY_MAX_MINUTES = 12 * 60
# Spans shorter than this (a timer started by mistake) are dropped when the timer stops.
MIN_ENTRY_SECONDS = 5


def format_duration(seconds):
    """ "1 h 05 min", "12 min" or "< 1 min"."""
    minutes = int(seconds) // 60
    if minutes < 1:
        return "< 1 min"
    hours, minutes = divmod(minutes, 60)
    if not hours:
        return f"{minutes} min"
    return f"{hours} h {minutes:02d} min"


def running_entry(user):
    return TaskTimeEntry.objects.filter(user=user, ended_at__isnull=True).select_related("task").first()


def stop_timer(user, *, now=None):
    """Stop the user's running timer; returns the finished entry, or None (none running / too short)."""
    current_time = now or timezone.now()
    entry = running_entry(user)
    if entry is None:
        return None
    if (current_time - entry.started_at).total_seconds() < MIN_ENTRY_SECONDS:
        entry.delete()
        return None
    entry.ended_at = max(current_time, entry.started_at)
    entry.save(update_fields=["ended_at"])
    return entry


def start_timer(user, task, *, now=None):
    """Start tracking ``task``; a timer running on another task is stopped first."""
    if not visible_tasks(user).filter(pk=task.pk).exists():
        raise ValueError("Diese Aufgabe wurde nicht gefunden.")
    if task.is_done:
        raise ValueError("Für erledigte Aufgaben läuft kein Timer.")
    current_time = now or timezone.now()
    try:
        with transaction.atomic():
            stop_timer(user, now=current_time)
            return TaskTimeEntry.objects.create(task=task, user=user, started_at=current_time)
    except IntegrityError:
        # Another request started a timer at the same moment; that one wins.
        return running_entry(user)


def toggle_timer(user, task, *, now=None):
    """Stop the timer when it runs on ``task``, otherwise start it there. Returns "started"/"stopped"."""
    entry = running_entry(user)
    if entry is not None and entry.task_id == task.pk:
        stop_timer(user, now=now)
        return "stopped"
    start_timer(user, task, now=now)
    return "started"


def stop_timers_for_task(task, *, now=None):
    """A task that gets done stops every timer running on it."""
    current_time = now or timezone.now()
    TaskTimeEntry.objects.filter(task=task, ended_at__isnull=True).update(ended_at=current_time)


def add_manual_entry(user, task, day, minutes, *, now=None):
    """Book ``minutes`` on ``day`` (the user's local date): ending now for today, else at 18:00."""
    if not visible_tasks(user).filter(pk=task.pk).exists():
        raise ValueError("Diese Aufgabe wurde nicht gefunden.")
    if not 1 <= minutes <= MANUAL_ENTRY_MAX_MINUTES:
        raise ValueError(f"Bitte zwischen 1 und {MANUAL_ENTRY_MAX_MINUTES} Minuten eintragen.")
    current_time = now or timezone.now()
    local_now = localtime_for_user(current_time, user)
    if day > local_now.date():
        raise ValueError("Zeit lässt sich nicht für die Zukunft nachtragen.")
    if day == local_now.date():
        ended_at = current_time
    else:
        ended_at = timezone.make_aware(datetime.combine(day, time(18, 0)), get_user_zoneinfo(user))
    return TaskTimeEntry.objects.create(
        task=task, user=user, started_at=ended_at - timedelta(minutes=minutes), ended_at=ended_at
    )


def delete_entry(user, entry_id):
    deleted, _details = TaskTimeEntry.objects.filter(pk=entry_id, user=user).delete()
    return bool(deleted)


def task_totals(user, task_ids, *, now=None):
    """Seconds the user tracked per task, the running timer counted up to ``now``."""
    current_time = now or timezone.now()
    totals = {}
    for entry in TaskTimeEntry.objects.filter(user=user, task_id__in=task_ids).only(
        "task_id", "started_at", "ended_at"
    ):
        totals[entry.task_id] = totals.get(entry.task_id, 0) + entry.duration_seconds(current_time)
    return totals


def annotate_task_items(items, user, *, now=None):
    """Add ``time_label``/``timer_running`` to the task page's items (and their subtasks)."""
    flat = [item for item in items for item in [item, *item.get("subtasks", [])]]
    totals = task_totals(user, [item["task"].pk for item in flat], now=now)
    running = running_entry(user)
    for item in flat:
        seconds = totals.get(item["task"].pk, 0)
        item["time_label"] = format_duration(seconds) if seconds else ""
        item["timer_running"] = running is not None and running.task_id == item["task"].pk
    return running


def running_timer_context(user, *, now=None):
    """The banner above the task list: which task runs since when (ISO for the ticking clock)."""
    entry = running_entry(user)
    if entry is None:
        return None
    current_time = now or timezone.now()
    return {
        "task_id": entry.task_id,
        "title": entry.task.title,
        "started_iso": entry.started_at.isoformat(),
        "started_label": format_user_time(entry.started_at, user),
        "elapsed_seconds": entry.duration_seconds(current_time),
    }


def week_start_for(value, user):
    """Monday of the ISO week in ``value`` ("2026-W45") or of today."""
    today = localtime_for_user(profile_or_user=user).date()
    if value:
        try:
            year, week = value.split("-W")
            return date.fromisocalendar(int(year), int(week), 1)
        except ValueError:
            pass
    return today - timedelta(days=today.weekday())


def week_report(user, week_start, *, now=None):
    """Per day and per task totals for one week, plus its entries (newest first)."""
    current_time = now or timezone.now()
    user_timezone = get_user_zoneinfo(user)
    range_start = timezone.make_aware(datetime.combine(week_start, time.min), user_timezone)
    range_end = timezone.make_aware(datetime.combine(week_start + timedelta(days=7), time.min), user_timezone)
    entries = list(
        TaskTimeEntry.objects.filter(user=user, started_at__gte=range_start, started_at__lt=range_end)
        .select_related("task", "task__task_list")
        .order_by("-started_at", "-id")
    )
    day_seconds = {week_start + timedelta(days=offset): 0 for offset in range(7)}
    task_seconds = {}
    for entry in entries:
        seconds = entry.duration_seconds(current_time)
        day_seconds[localtime_for_user(entry.started_at, user).date()] += seconds
        task_seconds.setdefault(entry.task, 0)
        task_seconds[entry.task] += seconds
    total = sum(day_seconds.values())
    busiest = max(day_seconds.values()) or 1
    iso_year, iso_week, _weekday = week_start.isocalendar()
    previous_start = week_start - timedelta(days=7)
    next_start = week_start + timedelta(days=7)
    return {
        "week_value": f"{iso_year}-W{iso_week:02d}",
        "label": f"KW {iso_week} · {format_user_date(week_start, user)} – "
        f"{format_user_date(week_start + timedelta(days=6), user)}",
        "previous_value": "{}-W{:02d}".format(*previous_start.isocalendar()[:2]),
        "next_value": "{}-W{:02d}".format(*next_start.isocalendar()[:2]),
        "is_current": week_start <= localtime_for_user(current_time, user).date() < next_start,
        "total_seconds": total,
        "total_label": format_duration(total) if total else "0 min",
        "days": [
            {
                "weekday": get_user_weekday_name(day, user),
                "date_label": format_user_date(day, user),
                "seconds": seconds,
                "label": format_duration(seconds) if seconds else "–",
                "percent": round(100 * seconds / busiest),
            }
            for day, seconds in day_seconds.items()
        ],
        "tasks": [
            {
                "title": task.title,
                "list_name": task.task_list.name if task.task_list else "Eingang",
                "seconds": seconds,
                "label": format_duration(seconds),
                "percent": round(100 * seconds / total) if total else 0,
            }
            for task, seconds in sorted(task_seconds.items(), key=lambda item: item[1], reverse=True)
        ],
        "entries": [
            {
                "id": entry.pk,
                "title": entry.task.title,
                "day_label": format_user_date(entry.started_at, user),
                "time_label": f"{format_user_time(entry.started_at, user)}–"
                + (format_user_time(entry.ended_at, user) if entry.ended_at else "läuft"),
                "duration_label": format_duration(entry.duration_seconds(current_time)),
                "is_running": entry.is_running,
            }
            for entry in entries
        ],
    }


def week_report_csv(report):
    """The week's entries as semicolon-separated CSV (opens directly in German Excel)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["Datum", "Zeit", "Aufgabe", "Dauer"])
    for entry in reversed(report["entries"]):
        writer.writerow([entry["day_label"], entry["time_label"], entry["title"], entry["duration_label"]])
    writer.writerow([])
    writer.writerow(["Summe", "", "", report["total_label"]])
    return "﻿" + buffer.getvalue()


def trackable_tasks(user):
    """Tasks the manual-entry form offers: the user's visible open tasks, then recently done ones."""
    return list(visible_tasks(user).filter(is_done=False).order_by("title")[:200]) + list(
        visible_tasks(user).filter(is_done=True).order_by("-updated_at")[:50]
    )
