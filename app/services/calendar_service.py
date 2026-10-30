import calendar
import secrets
from datetime import UTC, timedelta

from django.db.models import Q
from django.utils import timezone

from app.models import CalendarEvent, CalendarEventAttendee, VacationPeriod
from app.services.system_settings import feature_enabled
from app.services.user_preferences import localtime_for_user

# Subscribers only need recent history; everything ahead is always included.
FEED_LOOKBACK_DAYS = 180
ICS_LINE_LIMIT = 75


def _add_months(value, months):
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


MANUAL_RECURRENCE_MAX_INSTANCES = 366


def expand_manual_recurrence(start_at, frequency, until):
    """Step start_at forward by frequency (DAILY/WEEKLY/MONTHLY/YEARLY) through until.

    Capped at MANUAL_RECURRENCE_MAX_INSTANCES so a manually created series can't
    generate an unbounded number of CalendarEvent rows.
    """
    occurrences = [start_at]
    current = start_at
    while len(occurrences) < MANUAL_RECURRENCE_MAX_INSTANCES:
        if frequency == "DAILY":
            current += timedelta(days=1)
        elif frequency == "WEEKLY":
            current += timedelta(weeks=1)
        elif frequency == "MONTHLY":
            current = _add_months(current, 1)
        elif frequency == "YEARLY":
            current = _add_months(current, 12)
        else:
            break
        if current > until:
            break
        occurrences.append(current)
    return occurrences


SERIES_SCOPE_THIS = "this"
SERIES_SCOPE_FOLLOWING = "following"
SERIES_SCOPE_ALL = "all"
SERIES_SCOPE_CHOICES = [
    (SERIES_SCOPE_THIS, "Nur dieser Termin"),
    (SERIES_SCOPE_FOLLOWING, "Dieser und alle folgenden"),
    (SERIES_SCOPE_ALL, "Alle Termine der Serie"),
]


def series_scope_events(event, scope):
    """The events an edit or delete of `event` applies to: the event alone, it and the later
    occurrences of its series, or the whole series. Events outside a series only ever cover themselves."""
    if not event.recurrence_id or scope not in {SERIES_SCOPE_FOLLOWING, SERIES_SCOPE_ALL}:
        return CalendarEvent.objects.filter(pk=event.pk)
    events = CalendarEvent.objects.filter(user_id=event.user_id, recurrence_id=event.recurrence_id)
    if scope == SERIES_SCOPE_FOLLOWING:
        events = events.filter(start_at__gte=event.start_at)
    return events


def reset_calendar_feed_token(profile):
    """Create or rotate the secret subscription token; any previously shared link stops working."""
    profile.calendar_feed_token = secrets.token_urlsafe(32)
    profile.save(update_fields=["calendar_feed_token", "updated_at"])
    return profile.calendar_feed_token


def disable_calendar_feed(profile):
    profile.calendar_feed_token = None
    profile.save(update_fields=["calendar_feed_token", "updated_at"])


def calendar_feed_events(user, *, now=None):
    """Own events plus invitations the user accepted, from FEED_LOOKBACK_DAYS ago onwards."""
    current_time = now or timezone.now()
    accepted = Q(attendees__user=user, attendees__status=CalendarEventAttendee.STATUS_ACCEPTED)
    return (
        CalendarEvent.objects.filter(Q(user=user) | accepted)
        .filter(end_at__gte=current_time - timedelta(days=FEED_LOOKBACK_DAYS))
        .select_related("user", "user__profile")
        .distinct()
        .order_by("start_at", "id")
    )


def build_calendar_feed(user, *, calendar_name, now=None):
    """Render the user's events as an RFC 5545 iCalendar document (CRLF line endings, folded lines)."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Lunora//Kalender//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape(calendar_name)}",
        # Refresh hints; Apple Calendar and Outlook honour them, Google Calendar ignores them.
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    for event in calendar_feed_events(user, now=now):
        lines.extend(_ics_event_lines(event))
    if feature_enabled("vacation_planner"):
        current_date = localtime_for_user(now or timezone.now(), user).date()
        for period in VacationPeriod.objects.filter(
            user=user, end_date__gte=current_date - timedelta(days=FEED_LOOKBACK_DAYS)
        ).order_by("start_date", "id"):
            lines.extend(_ics_vacation_lines(period))
    lines.append("END:VCALENDAR")
    return "".join(f"{_ics_fold(line)}\r\n" for line in lines)


def _ics_event_lines(event):
    category_label = event.get_category_display()
    summary = event.title
    # Plain appointments and "other" need no label; Google Calendar ignores CATEGORIES.
    if event.category not in (CalendarEvent.CATEGORY_APPOINTMENT, CalendarEvent.CATEGORY_OTHER):
        summary = f"[{category_label}] {summary}"
    lines = [
        "BEGIN:VEVENT",
        f"UID:lunora-event-{event.pk}",
        f"DTSTAMP:{_ics_utc(event.updated_at)}",
        f"LAST-MODIFIED:{_ics_utc(event.updated_at)}",
    ]
    if event.is_all_day:
        # All-day events are stored as local midnights of the creator; end_at is exclusive.
        start_date = localtime_for_user(event.start_at, event.user).date()
        end_date = localtime_for_user(event.end_at, event.user).date()
        if end_date <= start_date:
            end_date = start_date + timedelta(days=1)
        lines.append(f"DTSTART;VALUE=DATE:{start_date:%Y%m%d}")
        lines.append(f"DTEND;VALUE=DATE:{end_date:%Y%m%d}")
    else:
        lines.append(f"DTSTART:{_ics_utc(event.start_at)}")
        lines.append(f"DTEND:{_ics_utc(event.end_at)}")
    lines.append(f"SUMMARY:{_ics_escape(summary)}")
    lines.append(f"CATEGORIES:{_ics_escape(category_label)}")
    if event.location:
        lines.append(f"LOCATION:{_ics_escape(event.location)}")
    if event.description:
        lines.append(f"DESCRIPTION:{_ics_escape(event.description)}")
    lines.append("END:VEVENT")
    return lines


def _ics_vacation_lines(period):
    """A vacation-planner period as an all-day event; DTEND is exclusive, so it is the day after."""
    lines = [
        "BEGIN:VEVENT",
        f"UID:lunora-vacation-{period.pk}",
        f"DTSTAMP:{_ics_utc(period.updated_at)}",
        f"LAST-MODIFIED:{_ics_utc(period.updated_at)}",
        f"DTSTART;VALUE=DATE:{period.start_date:%Y%m%d}",
        f"DTEND;VALUE=DATE:{period.end_date + timedelta(days=1):%Y%m%d}",
        f"SUMMARY:{_ics_escape(period.get_name_display())}",
        "CATEGORIES:Urlaub",
    ]
    if period.notes:
        lines.append(f"DESCRIPTION:{_ics_escape(period.notes)}")
    lines.append("END:VEVENT")
    return lines


def _ics_utc(value):
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _ics_escape(value):
    value = value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return value.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def _ics_fold(line):
    """Split a content line into chunks of at most 75 UTF-8 octets without breaking a character."""
    if len(line.encode("utf-8")) <= ICS_LINE_LIMIT:
        return line
    chunks = []
    current = ""
    current_size = 0
    limit = ICS_LINE_LIMIT
    for char in line:
        char_size = len(char.encode("utf-8"))
        if current_size + char_size > limit:
            chunks.append(current)
            current = ""
            current_size = 0
            limit = ICS_LINE_LIMIT - 1  # continuation lines start with one space
        current += char
        current_size += char_size
    chunks.append(current)
    return "\r\n ".join(chunks)
