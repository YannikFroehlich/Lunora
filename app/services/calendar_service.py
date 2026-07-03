from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.error import URLError
from urllib.request import Request, urlopen

from django.db import transaction
from django.utils import timezone

from app.models import CalendarEvent, CalendarSource

SYNC_LOOKBACK_DAYS = 45
SYNC_LOOKAHEAD_DAYS = 370
TONE_SEQUENCE = ["blue", "green", "sand", "violet", "red"]


@dataclass(frozen=True)
class ParsedEvent:
    external_id: str
    title: str
    description: str
    location: str
    start_at: datetime
    end_at: datetime
    is_all_day: bool
    tone: str


def sync_calendar_source(source, *, force=False):
    if not source.enabled:
        return {"synced": False, "message": "Synchronisierung ist deaktiviert."}

    now = timezone.now()
    if not force and source.last_synced_at:
        next_sync_at = source.last_synced_at + timedelta(minutes=source.sync_interval_minutes)
        if next_sync_at > now:
            return {"synced": False, "message": "Kalender ist aktuell."}

    try:
        ical_text = fetch_ical(source.ical_url)
        parsed_events = parse_ical_events(
            ical_text,
            window_start=now - timedelta(days=SYNC_LOOKBACK_DAYS),
            window_end=now + timedelta(days=SYNC_LOOKAHEAD_DAYS),
        )
        saved_count = save_events(source, parsed_events)
    except (ValueError, URLError, TimeoutError, OSError) as error:
        source.last_error = str(error)
        source.save(update_fields=["last_error", "updated_at"])
        return {"synced": False, "message": str(error)}

    source.last_synced_at = now
    source.last_error = ""
    source.save(update_fields=["last_synced_at", "last_error", "updated_at"])
    return {"synced": True, "message": f"{saved_count} Termine synchronisiert."}


def fetch_ical(url):
    request = Request(url, headers={"User-Agent": "Lunora Calendar Sync/1.0"})
    with urlopen(request, timeout=12) as response:
        content_type = response.headers.get("content-type", "")
        data = response.read(5_000_000)

    text = data.decode("utf-8-sig", errors="replace")
    if "BEGIN:VCALENDAR" not in text:
        raise ValueError("Der Kalenderlink liefert keine gueltige iCal-Datei.")
    if content_type and "text/html" in content_type:
        raise ValueError("Der Kalenderlink zeigt auf eine Webseite, nicht auf eine iCal-Datei.")
    return text


def save_events(source, parsed_events):
    incoming_ids = {event.external_id for event in parsed_events}

    with transaction.atomic():
        for event in parsed_events:
            CalendarEvent.objects.update_or_create(
                source=source,
                external_id=event.external_id,
                defaults={
                    "user": source.user,
                    "title": event.title,
                    "description": event.description,
                    "location": event.location,
                    "start_at": event.start_at,
                    "end_at": event.end_at,
                    "is_all_day": event.is_all_day,
                    "tone": event.tone,
                },
            )

        source.events.exclude(external_id__in=incoming_ids).delete()

    return len(parsed_events)


def parse_ical_events(ical_text, *, window_start, window_end):
    events = []
    for index, raw_event in enumerate(_extract_vevents(ical_text)):
        events.extend(_event_instances(raw_event, index, window_start, window_end))
    return sorted(events, key=lambda event: (event.start_at, event.title))


def _extract_vevents(ical_text):
    lines = _unfold_lines(ical_text)
    current = []
    in_event = False

    for line in lines:
        if line == "BEGIN:VEVENT":
            current = []
            in_event = True
            continue
        if line == "END:VEVENT" and in_event:
            yield _parse_properties(current)
            current = []
            in_event = False
            continue
        if in_event:
            current.append(line)


def _unfold_lines(ical_text):
    unfolded = []
    for raw_line in ical_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw_line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += raw_line[1:]
        elif raw_line:
            unfolded.append(raw_line)
    return unfolded


def _parse_properties(lines):
    properties = {}
    for line in lines:
        if ":" not in line:
            continue
        name_part, value = line.split(":", 1)
        pieces = name_part.split(";")
        name = pieces[0].upper()
        params = {}
        for piece in pieces[1:]:
            if "=" in piece:
                key, param_value = piece.split("=", 1)
                params[key.upper()] = param_value.strip('"')
        properties.setdefault(name, []).append({"value": _decode_ical_text(value), "params": params})
    return properties


def _decode_ical_text(value):
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
        .strip()
    )


def _event_instances(raw_event, index, window_start, window_end):
    if _first_value(raw_event, "STATUS", "").upper() == "CANCELLED":
        return []

    start_prop = _first(raw_event, "DTSTART")
    if not start_prop:
        return []

    start_at, is_all_day = _parse_datetime(start_prop)
    end_prop = _first(raw_event, "DTEND")
    if end_prop:
        end_at, _end_is_all_day = _parse_datetime(end_prop)
    else:
        end_at = start_at + (timedelta(days=1) if is_all_day else timedelta(hours=1))

    if end_at <= start_at:
        end_at = start_at + timedelta(days=1 if is_all_day else 1 / 24)

    uid = _first_value(raw_event, "UID", f"event-{index}")
    title = _first_value(raw_event, "SUMMARY", "Ohne Titel")
    description = _first_value(raw_event, "DESCRIPTION", "")
    location = _first_value(raw_event, "LOCATION", "")
    tone = TONE_SEQUENCE[index % len(TONE_SEQUENCE)]
    duration = end_at - start_at
    exclusions = _parse_exdates(raw_event)
    rrule = _first_value(raw_event, "RRULE", "")

    starts = _expand_starts(start_at, rrule, window_start, window_end, exclusions)
    return [
        ParsedEvent(
            external_id=f"{uid}|{event_start.isoformat()}",
            title=title,
            description=description,
            location=location,
            start_at=event_start,
            end_at=event_start + duration,
            is_all_day=is_all_day,
            tone=tone,
        )
        for event_start in starts
        if event_start < window_end and event_start + duration > window_start
    ]


def _first(properties, name):
    values = properties.get(name, [])
    return values[0] if values else None


def _first_value(properties, name, default=None):
    value = _first(properties, name)
    return value["value"] if value else default


def _parse_datetime(prop):
    value = prop["value"]
    params = prop["params"]

    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        parsed_date = datetime.strptime(value[:8], "%Y%m%d").date()
        return timezone.make_aware(datetime.combine(parsed_date, time.min), timezone.get_current_timezone()), True

    tzinfo = timezone.get_current_timezone()
    if value.endswith("Z"):
        tzinfo = datetime_timezone.utc
        value = value[:-1]
    elif params.get("TZID"):
        try:
            tzinfo = ZoneInfo(params["TZID"])
        except ZoneInfoNotFoundError:
            tzinfo = timezone.get_current_timezone()

    fmt = "%Y%m%dT%H%M%S" if len(value) >= 15 else "%Y%m%dT%H%M"
    parsed = datetime.strptime(value[:15] if fmt.endswith("%S") else value[:13], fmt)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tzinfo)
    return timezone.localtime(parsed), False


def _parse_exdates(raw_event):
    exclusions = set()
    for prop in raw_event.get("EXDATE", []):
        for value in prop["value"].split(","):
            try:
                excluded, _is_all_day = _parse_datetime({"value": value, "params": prop["params"]})
            except ValueError:
                continue
            exclusions.add(excluded)
    return exclusions


def _expand_starts(start_at, rrule, window_start, window_end, exclusions):
    if not rrule:
        return [] if start_at in exclusions else [start_at]

    rule = _parse_rrule(rrule)
    freq = rule.get("FREQ", "").upper()
    interval = max(1, int(rule.get("INTERVAL", "1")))
    count = int(rule.get("COUNT", "0") or "0")
    until = _parse_rrule_until(rule.get("UNTIL"), start_at.tzinfo) if rule.get("UNTIL") else window_end
    byday = [item.strip() for item in rule.get("BYDAY", "").split(",") if item.strip()]
    starts = []
    occurrence = start_at
    generated = 0
    limit = min(until, window_end + timedelta(days=31))

    while occurrence <= limit and (not count or generated < count):
        candidates = _recurrence_candidates(occurrence, start_at, freq, byday)
        for candidate in candidates:
            if candidate < start_at:
                continue
            generated += 1
            if count and generated > count:
                break
            if candidate not in exclusions and candidate < window_end and candidate >= window_start - timedelta(days=1):
                starts.append(candidate)

        if freq == "DAILY":
            occurrence += timedelta(days=interval)
        elif freq == "WEEKLY":
            occurrence += timedelta(weeks=interval)
        elif freq == "MONTHLY":
            occurrence = _add_months(occurrence, interval)
        else:
            break

    return sorted(set(starts))


def _parse_rrule(rrule):
    result = {}
    for piece in rrule.split(";"):
        if "=" in piece:
            key, value = piece.split("=", 1)
            result[key.upper()] = value
    return result


def _parse_rrule_until(value, tzinfo):
    if value.endswith("Z"):
        parsed = datetime.strptime(value[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=datetime_timezone.utc)
    elif "T" in value:
        parsed = datetime.strptime(value[:15], "%Y%m%dT%H%M%S").replace(tzinfo=tzinfo)
    else:
        parsed = datetime.strptime(value[:8], "%Y%m%d").replace(tzinfo=tzinfo)
    return timezone.localtime(parsed)


def _recurrence_candidates(occurrence, start_at, freq, byday):
    if freq != "WEEKLY" or not byday:
        return [occurrence]

    weekday_map = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
    week_start = occurrence - timedelta(days=occurrence.weekday())
    return [
        datetime.combine((week_start + timedelta(days=weekday_map[item[-2:]])).date(), start_at.timetz())
        for item in byday
        if item[-2:] in weekday_map
    ]


def _add_months(value, months):
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)
