import calendar
from datetime import datetime, time, timedelta

from django.utils import timezone

from app.models import CalendarEvent, CalendarReminder, ConversationMember
from app.services.user_preferences import (
    format_user_date,
    format_user_time,
    get_user_month_name,
    get_user_weekday_name,
    get_user_zoneinfo,
    get_user_timezone_name,
    localtime_for_user,
)
from app.services.weather_service import get_weather_context


def get_dashboard_context(user=None):
    now = localtime_for_user(profile_or_user=user)
    dashboard_weather = _dashboard_weather_context()
    unread_messages_total = _dashboard_unread_message_count(user)

    return {
        "active_page": "home",
        "today_label": format_user_date(now, user),
        "time_label": format_user_time(now, user),
        "dashboard_weather": dashboard_weather,
        "clock": {
            "time": format_user_time(now, user),
            "weekday": get_user_weekday_name(now, user),
            "day": now.strftime("%d"),
            "month": get_user_month_name(now, user),
            "year": now.strftime("%Y"),
            "timezone": get_user_timezone_name(user),
        },
        "nav_tiles": [
            {"label": "Dashboard", "icon": "fa-table-cells-large", "url_name": "home"},
            {"label": "Wetter", "icon": "fa-cloud-sun", "url_name": "weather"},
            {"label": "Kalender", "icon": "fa-calendar-days", "url_name": "calendar"},
            {"label": "Projekte", "icon": "fa-folder", "url_name": "home"},
            {
                "label": "Nachrichten",
                "icon": "fa-message",
                "url_name": "messages",
                "badge_key": "messages_unread",
                "badge_count": unread_messages_total,
            },
        ],
        "recent_tools": [
            {"title": "Notizen", "subtitle": "Weiter schreiben", "icon": "fa-note-sticky"},
            {"title": "Planer", "subtitle": "Termine ansehen", "icon": "fa-calendar-check"},
            {"title": "Dateien", "subtitle": "Zuletzt geöffnet", "icon": "fa-folder-open"},
            {"title": "Analysen", "subtitle": "Einblicke ansehen", "icon": "fa-chart-simple"},
        ],
        "upcoming_dashboard_events": _dashboard_upcoming_events(user, now) if user else [],
        "legacy_notes": [
            {"text": "Projektbrief prüfen", "done": True},
            {"text": "Landingpage designen", "done": False},
            {"text": "Präsentation vorbereiten", "done": False},
        ],
    }


def _dashboard_unread_message_count(user):
    if not user or not getattr(user, "is_authenticated", False):
        return 0

    memberships = (
        ConversationMember.objects.filter(user=user, is_archived=False)
        .select_related("conversation")
        .prefetch_related("conversation__messages")
    )
    return sum(member.unread_count() for member in memberships)

def _dashboard_weather_context():
    weather_context = get_weather_context({})
    current = weather_context.get("current", {})
    daily_forecast = weather_context.get("daily_forecast") or []
    tomorrow = daily_forecast[0] if daily_forecast else {}

    return {
        "today": {
            "temperature": current.get("temperature", 24),
            "feels_like": current.get("feels_like", current.get("temperature", 24)),
            "description": current.get("description", "Teilweise bewölkt"),
            "icon": current.get("icon", "fa-cloud-sun"),
        },
        "tomorrow": {
            "day": tomorrow.get("day", "Morgen"),
            "high": tomorrow.get("high", current.get("high", current.get("temperature", 24))),
            "low": tomorrow.get("low", current.get("low", current.get("temperature", 18))),
            "rain": tomorrow.get("rain", 10),
            "description": tomorrow.get("description", "Teilweise bewölkt"),
            "icon": tomorrow.get("icon", "fa-cloud-sun"),
        },
    }


def get_settings_context():
    return {
        "active_page": "settings",
        "accent_colors": ["#c2a276", "#7f916b", "#a5aa74", "#9eb1b6", "#aaa2be", "#c1a09a"],
        "region_rows": [
            {"label": "Sprache", "value": "Deutsch"},
            {"label": "Datumsformat", "value": "25. Juni 2026"},
            {"label": "Zeitformat", "value": "24-Stunden"},
            {"label": "Zeitzone", "value": "Europe/Berlin"},
        ],
        "notification_rows": [
            {"label": "E-Mail Benachrichtigungen", "hint": "Wichtige Updates erhalten"},
            {"label": "Erinnerungen", "hint": "Aufgaben und Termine im Blick behalten"},
            {"label": "Desktop Hinweise", "hint": "Benachrichtigungen auf diesem Gerät"},
            {"label": "Wöchentliche Zusammenfassung", "hint": "Kurzer Rückblick per E-Mail"},
        ],
    }


def _dashboard_upcoming_events(user, now):
    events = CalendarEvent.objects.filter(user=user, end_at__gte=now).select_related("source").order_by("start_at")[:5]
    return [
        {
            "title": event.title,
            "date": format_user_date(event.start_at, user),
            "time": "Ganztägig" if event.is_all_day else format_user_time(event.start_at, user),
            "tone": event.tone,
        }
        for event in events
    ]


def get_calendar_context(user, *, year=None, month=None):
    user_timezone = get_user_zoneinfo(user)
    now = localtime_for_user(profile_or_user=user)
    try:
        year = int(year or now.year)
        month = int(month or now.month)
        if month < 1 or month > 12:
            raise ValueError
    except (TypeError, ValueError):
        year = now.year
        month = now.month

    month_date = now.date().replace(year=year, month=month, day=1)
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    visible_start = weeks[0][0]
    visible_end = weeks[-1][-1] + timedelta(days=1)
    range_start = timezone.make_aware(datetime.combine(visible_start, time.min), user_timezone)
    range_end = timezone.make_aware(datetime.combine(visible_end, time.min), user_timezone)
    events = list(
        CalendarEvent.objects.filter(user=user, start_at__lt=range_end, end_at__gt=range_start)
        .select_related("source")
        .order_by("start_at", "title")
    )
    events_by_date = _group_calendar_events_by_date(events, visible_start, visible_end, user)

    rows = []
    for week in weeks:
        row = []
        for day in week:
            day_events = events_by_date.get(day, [])
            row.append(
                {
                    "number": str(day.day),
                    "muted": day.month != month,
                    "today": day == now.date(),
                    "events": [
                        {
                            "label": event.title,
                            "tone": event.tone,
                            "time": _calendar_event_time_label(event, user),
                        }
                        for event in day_events[:3]
                    ],
                    "overflow": max(0, len(day_events) - 3),
                }
            )
        rows.append(row)

    today_events = [
        {
            "time": _calendar_event_time_label(event, user),
            "title": event.title,
            "icon": "fa-calendar-day",
            "tone": event.tone,
        }
        for event in events_by_date.get(now.date(), [])
    ]
    upcoming_events = _upcoming_calendar_events(user, now)
    month_events = [
        event
        for event in events
        if localtime_for_user(event.start_at, user).date().year == year
        and localtime_for_user(event.start_at, user).date().month == month
    ]
    days_in_month = [month_date.replace(day=day) for day in range(1, calendar.monthrange(year, month)[1] + 1)]
    busy_days = {localtime_for_user(event.start_at, user).date() for event in month_events}
    reminder_items = CalendarReminder.objects.filter(user=user)[:8]
    chart_bars = _calendar_chart_bars(month_events, year, month, user)
    prev_month = _shift_month(year, month, -1)
    next_month = _shift_month(year, month, 1)

    return {
        "active_page": "calendar",
        "calendar_rows": rows,
        "month_label": f"{get_user_month_name(month_date, user)} {year}",
        "today_label": f"{get_user_weekday_name(now, user)}, {format_user_date(now, user)}",
        "today_events": today_events,
        "upcoming_events": upcoming_events,
        "prev_month": {"year": prev_month[0], "month": prev_month[1]},
        "next_month": {"year": next_month[0], "month": next_month[1]},
        "month_stats": {
            "events": len(month_events),
            "free_days": len(days_in_month) - len(busy_days),
            "synced_days": len(busy_days),
            "chart_bars": chart_bars,
        },
        "reminders": reminder_items,
    }


def _group_calendar_events_by_date(events, visible_start, visible_end, user):
    grouped = {}
    for event in events:
        start_date = max(localtime_for_user(event.start_at, user).date(), visible_start)
        event_end = localtime_for_user(event.end_at, user)
        end_date = event_end.date()
        if event.is_all_day:
            end_date = (event_end - timedelta(seconds=1)).date()
        end_date = min(end_date, visible_end - timedelta(days=1))

        current = start_date
        while current <= end_date:
            grouped.setdefault(current, []).append(event)
            current += timedelta(days=1)
    return grouped


def _calendar_event_time_label(event, user):
    if event.is_all_day:
        return "Ganztägig"
    return format_user_time(event.start_at, user)


def _upcoming_calendar_events(user, now):
    events = CalendarEvent.objects.filter(user=user, end_at__gte=now).select_related("source").order_by("start_at")[:6]
    return [
        {
            "date": format_user_date(event.start_at, user),
            "title": event.title,
            "category": event.source.name,
            "icon": "fa-calendar-day",
        }
        for event in events
    ]


def _shift_month(year, month, direction):
    shifted_month = month + direction
    if shifted_month < 1:
        return year - 1, 12
    if shifted_month > 12:
        return year + 1, 1
    return year, shifted_month


def _calendar_chart_bars(events, year, month, user):
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    counts = []
    for week in weeks:
        week_dates = {day for day in week if day.month == month}
        count = sum(1 for event in events if localtime_for_user(event.start_at, user).date() in week_dates)
        counts.append(count)

    max_count = max(counts, default=0)
    if max_count == 0:
        return [{"height": 18, "count": 0} for _count in counts]

    return [
        {
            "height": 18 + round((count / max_count) * 48),
            "count": count,
        }
        for count in counts
    ]
