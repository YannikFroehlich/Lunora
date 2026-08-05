import calendar
from datetime import datetime, time, timedelta

from django.db.models import F, Q
from django.utils import timezone

from app.models import CalendarEvent, CalendarReminder
from app.services.message_queries import unread_total_for_user
from app.services.system_settings import feature_enabled, feature_flags
from app.services.user_preferences import (
    format_user_date,
    format_user_datetime,
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
    flags = feature_flags()
    dashboard_weather = _dashboard_weather_context(user) if flags["weather"] else {}
    unread_messages_total = _dashboard_unread_message_count(user) if flags["messages"] else 0
    upcoming_dashboard_events = _dashboard_upcoming_events(user, now) if user else []
    nav_tiles = _dashboard_nav_tiles(user, unread_messages_total, flags)

    return {
        "active_page": "home",
        "today_label": format_user_date(now, user),
        "time_label": format_user_time(now, user),
        "dashboard_weather": dashboard_weather,
        "dashboard_weather_enabled": flags["weather"],
        "dashboard_messages_enabled": flags["messages"],
        "clock": {
            "time": format_user_time(now, user),
            "weekday": get_user_weekday_name(now, user),
            "day": now.strftime("%d"),
            "month": get_user_month_name(now, user),
            "year": now.strftime("%Y"),
            "timezone": get_user_timezone_name(user),
        },
        "nav_tiles": nav_tiles,
        "recent_tools": _dashboard_tool_shortcuts(
            upcoming_dashboard_events,
            unread_messages_total,
            dashboard_weather,
            flags,
        ),
        "upcoming_dashboard_events": upcoming_dashboard_events,
    }


def _preference_row(preferences_form, field_name, label, hint):
    return {
        "field": preferences_form[field_name] if preferences_form else None,
        "label": label,
        "hint": hint,
    }


def _dashboard_unread_message_count(user):
    return unread_total_for_user(user)


def _dashboard_nav_tiles(user, unread_messages_total, flags):
    tiles = [
        {"label": "Dashboard", "icon": "fa-table-cells-large", "url_name": "home"},
        {"label": "Kalender", "icon": "fa-calendar-days", "url_name": "calendar"},
        {"label": "Einstellungen", "icon": "fa-gear", "url_name": "settings"},
    ]
    if flags["weather"]:
        tiles.insert(1, {"label": "Wetter", "icon": "fa-cloud-sun", "url_name": "weather"})
    if flags["messages"]:
        tiles.insert(
            -1,
            {
                "label": "Nachrichten",
                "icon": "fa-message",
                "url_name": "messages",
                "badge_key": "messages_unread",
                "badge_count": unread_messages_total,
            },
        )
    if user and getattr(user, "is_superuser", False):
        tiles.append({"label": "Administration", "icon": "fa-shield-halved", "url_name": "administration"})
    return tiles


def _dashboard_tool_shortcuts(upcoming_events, unread_messages_total, dashboard_weather):
    event_count = len(upcoming_events)
    event_subtitle = f"{event_count} kommende Termine" if event_count else "Kalender oeffnen"
    unread_subtitle = f"{unread_messages_total} ungelesen" if unread_messages_total else "Inbox oeffnen"
    weather_city = dashboard_weather.get("today", {}).get("city", "Standardort")

    return [
        {"title": "Kalender", "subtitle": event_subtitle, "icon": "fa-calendar-check", "url_name": "calendar"},
        {"title": "Wetter", "subtitle": weather_city, "icon": "fa-cloud-sun", "url_name": "weather"},
        {"title": "Nachrichten", "subtitle": unread_subtitle, "icon": "fa-message", "url_name": "messages"},
        {"title": "Einstellungen", "subtitle": "Profil & Präferenzen", "icon": "fa-gear", "url_name": "settings"},
    ]


def _dashboard_tool_shortcuts(upcoming_events, unread_messages_total, dashboard_weather, flags):
    event_count = len(upcoming_events)
    event_subtitle = f"{event_count} kommende Termine" if event_count else "Kalender oeffnen"
    unread_subtitle = f"{unread_messages_total} ungelesen" if unread_messages_total else "Inbox oeffnen"
    weather_city = dashboard_weather.get("today", {}).get("city", "Standardort")
    tools = [
        {"title": "Kalender", "subtitle": event_subtitle, "icon": "fa-calendar-check", "url_name": "calendar"},
        {"title": "Einstellungen", "subtitle": "Profil & Praeferenzen", "icon": "fa-gear", "url_name": "settings"},
    ]
    if flags["weather"]:
        tools.insert(1, {"title": "Wetter", "subtitle": weather_city, "icon": "fa-cloud-sun", "url_name": "weather"})
    if flags["messages"]:
        tools.insert(2, {"title": "Nachrichten", "subtitle": unread_subtitle, "icon": "fa-message", "url_name": "messages"})
    return tools


def _dashboard_weather_context(user=None):
    weather_context = get_weather_context({}, user=user)
    current = weather_context.get("current", {})
    daily_forecast = weather_context.get("daily_forecast") or []
    tomorrow = daily_forecast[0] if daily_forecast else {}

    return {
        "today": {
            "city": current.get("city", "Bünde"),
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


def get_settings_context(preferences_form=None):
    context = {
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
    context["notification_rows"] = [
        _preference_row(preferences_form, "notify_email", "E-Mail Benachrichtigungen", "Wichtige Updates erhalten"),
        _preference_row(preferences_form, "notify_reminders", "Erinnerungen", "Aufgaben und Termine im Blick behalten"),
        _preference_row(preferences_form, "notify_desktop", "Desktop Hinweise", "Benachrichtigungen auf diesem Gerät"),
        _preference_row(preferences_form, "weekly_summary", "Wöchentliche Zusammenfassung", "Kurzer Rückblick per E-Mail"),
    ]
    context["privacy_rows"] = [
        _preference_row(preferences_form, "analytics_enabled", "Analysen", "Hilft, Lunora besser zu machen"),
        _preference_row(preferences_form, "usage_data_enabled", "Nutzungsdaten", "Anonyme Nutzung erfassen"),
    ]
    return context


def _dashboard_upcoming_events(user, now):
    events = (
        CalendarEvent.objects.filter(
            Q(source__isnull=True) | Q(source__is_visible=True),
            user=user,
            end_at__gte=now,
        )
        .select_related("source")
        .order_by("start_at")[:5]
    )
    return [
        {
            "title": event.title,
            "date": format_user_date(event.start_at, user),
            "time": "Ganztägig" if event.is_all_day else format_user_time(event.start_at, user),
            "tone": _calendar_event_tone(event),
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
        CalendarEvent.objects.filter(
            Q(source__isnull=True) | Q(source__is_visible=True),
            user=user,
            start_at__lt=range_end,
            end_at__gt=range_start,
        )
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
                    "date_input": day.isoformat(),
                    "date_label": format_user_date(day, user),
                    "muted": day.month != month,
                    "today": day == now.date(),
                    "events": [
                        {
                            "label": event.title,
                            "tone": _calendar_event_tone(event),
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
            "tone": _calendar_event_tone(event),
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
    calendar_reminders_enabled = feature_enabled("calendar_reminders")
    reminder_items = _calendar_reminder_items(user, now) if calendar_reminders_enabled else []
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
        "calendar_reminders_enabled": calendar_reminders_enabled,
        "calendar_event_creation_enabled": feature_enabled("calendar_event_creation"),
        "calendar_sync_enabled": feature_enabled("calendar_sync"),
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


def _calendar_event_tone(event):
    return event.source.color if event.source_id else "sand"


def _upcoming_calendar_events(user, now):
    events = (
        CalendarEvent.objects.filter(
            Q(source__isnull=True) | Q(source__is_visible=True),
            user=user,
            end_at__gte=now,
        )
        .select_related("source")
        .order_by("start_at")[:6]
    )
    return [
        {
            "date": format_user_date(event.start_at, user),
            "title": event.title,
            "category": event.source.name if event.source_id else "Eigener Termin",
            "icon": "fa-calendar-day",
            "tone": _calendar_event_tone(event),
        }
        for event in events
    ]


def _calendar_reminder_items(user, now):
    reminders = CalendarReminder.objects.filter(user=user).order_by(
        "is_done",
        F("due_at").asc(nulls_last=True),
        "-created_at",
    )[:8]
    return [
        {
            "reminder": reminder,
            "title": reminder.title,
            "is_done": reminder.is_done,
            "due_label": _calendar_reminder_due_label(reminder, now, user),
            "due_state": _calendar_reminder_due_state(reminder, now, user),
        }
        for reminder in reminders
    ]


def _calendar_reminder_due_label(reminder, now, user):
    if reminder.is_done:
        return "Erledigt"
    if not reminder.due_at:
        return "Ohne Faelligkeitsdatum"

    due_at = localtime_for_user(reminder.due_at, user)
    today = now.date()
    if due_at < now:
        return f"Ueberfaellig seit {format_user_datetime(due_at, user)}"
    if due_at.date() == today:
        return f"Heute {format_user_time(due_at, user)}"
    if due_at.date() == today + timedelta(days=1):
        return f"Morgen {format_user_time(due_at, user)}"
    return format_user_datetime(due_at, user)


def _calendar_reminder_due_state(reminder, now, user):
    if reminder.is_done:
        return "is-done"
    if not reminder.due_at:
        return ""
    due_at = localtime_for_user(reminder.due_at, user)
    if due_at < now:
        return "is-overdue"
    if due_at.date() == now.date():
        return "is-due-today"
    return ""


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
