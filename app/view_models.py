import calendar
from datetime import datetime, time, timedelta

from django.db.models import F, Q
from django.utils import timezone

from app.models import CalendarEvent, CalendarEventAttendee, CalendarReminder, NoteShare, Task
from app.services.dashboard import (
    available_dashboard_widgets,
    dashboard_widgets_for_layout,
    default_dashboard_layout,
    normalize_dashboard_layout,
)
from app.services.message_queries import unread_total_for_user
from app.services.notifications import dashboard_latest_notifications
from app.services.system_settings import feature_enabled, feature_flags
from app.services.tasks import dashboard_open_tasks, dashboard_today_tasks
from app.services.user_preferences import (
    format_user_date,
    format_user_datetime,
    format_user_time,
    get_user_date_format,
    get_user_month_name,
    get_user_time_format,
    get_user_timezone_name,
    get_user_weekday_name,
    get_user_zoneinfo,
    localtime_for_user,
)


def get_dashboard_context(user=None):
    now = localtime_for_user(profile_or_user=user)
    flags = feature_flags()
    profile = getattr(user, "profile", None) if user else None
    stored_layout = getattr(profile, "dashboard_layout", None) if profile else None
    dashboard_customization_enabled = flags["dashboard_customization"]
    dashboard_layout = normalize_dashboard_layout(stored_layout)
    rendered_dashboard_layout = (
        dashboard_layout if dashboard_customization_enabled else default_dashboard_layout()
    )
    dashboard_widgets = dashboard_widgets_for_layout(
        rendered_dashboard_layout,
        flags,
        include_hidden=dashboard_customization_enabled,
    )
    dashboard_visible_widgets = [widget for widget in dashboard_widgets if not widget["hidden"]]
    visible_widget_ids = {widget["id"] for widget in dashboard_visible_widgets}
    dashboard_weather = _dashboard_weather_placeholder(user) if flags["weather"] else {}
    unread_messages_total = _dashboard_unread_message_count(user) if flags["messages"] else 0
    new_note_shares = _dashboard_new_note_share_count(user) if flags["notes"] else 0
    upcoming_dashboard_events = _dashboard_upcoming_events(user, now) if user else []
    dashboard_tasks = dashboard_open_tasks(user, now) if flags["tasks"] and user else []
    open_tasks_count = Task.objects.filter(user=user, is_done=False).count() if flags["tasks"] and user else 0
    nav_tiles = _dashboard_nav_tiles(user, unread_messages_total, new_note_shares, open_tasks_count, flags)
    if user and "notifications" in visible_widget_ids:
        dashboard_notifications = dashboard_latest_notifications(user)
    else:
        dashboard_notifications = []
    dashboard_today_open_tasks = (
        dashboard_today_tasks(user, now)
        if flags["tasks"] and user and "notifications" in visible_widget_ids
        else []
    )

    return {
        "active_page": "home",
        "dashboard_greeting": _dashboard_greeting(now),
        "dashboard_moment_icon": _dashboard_moment_icon(now),
        "dashboard_moment_label": _dashboard_moment_label(now),
        "today_label": format_user_date(now, user),
        "time_label": format_user_time(now, user),
        "dashboard_weather": dashboard_weather,
        "dashboard_weather_enabled": flags["weather"],
        "dashboard_messages_enabled": flags["messages"],
        "dashboard_notes_enabled": flags["notes"],
        "dashboard_customization_enabled": dashboard_customization_enabled,
        "dashboard_layout": dashboard_layout,
        "dashboard_default_layout": default_dashboard_layout(),
        "dashboard_widgets": dashboard_widgets,
        "dashboard_visible_widgets": dashboard_visible_widgets,
        "dashboard_available_widgets": available_dashboard_widgets(flags),
        "dashboard_new_note_shares": new_note_shares,
        "dashboard_tasks": dashboard_tasks,
        "dashboard_tasks_enabled": flags["tasks"],
        "dashboard_notifications": dashboard_notifications,
        "dashboard_today_tasks": dashboard_today_open_tasks,
        "clock": {
            "time": format_user_time(now, user),
            "weekday": get_user_weekday_name(now, user),
            "day": now.strftime("%d"),
            "month": get_user_month_name(now, user),
            "year": now.strftime("%Y"),
            "timezone": get_user_timezone_name(user),
            "time_format": get_user_time_format(user),
            "date_format": get_user_date_format(user),
        },
        "nav_tiles": nav_tiles,
        "recent_tools": _dashboard_tool_shortcuts(
            upcoming_dashboard_events,
            unread_messages_total,
            dashboard_weather,
            new_note_shares,
            flags,
        ),
        "upcoming_dashboard_events": upcoming_dashboard_events,
    }


def _dashboard_greeting(now):
    hour = now.hour
    if 5 <= hour < 11:
        return "Guten Morgen"
    if 11 <= hour < 17:
        return "Guten Tag"
    if 17 <= hour < 22:
        return "Guten Abend"
    return "Gute Nacht"


def _dashboard_moment_label(now):
    hour = now.hour
    if 5 <= hour < 11:
        return "Ruhiger Start"
    if 11 <= hour < 17:
        return "Fokussierter Tag"
    if 17 <= hour < 22:
        return "Ruhiger Abend"
    return "Nachtruhe"


def _dashboard_moment_icon(now):
    hour = now.hour
    if 5 <= hour < 17:
        return "fa-regular fa-sun"
    if 17 <= hour < 22:
        return "fa-solid fa-cloud-sun"
    return "fa-regular fa-moon"


def _preference_row(preferences_form, field_name, label, hint):
    return {
        "field": preferences_form[field_name] if preferences_form else None,
        "label": label,
        "hint": hint,
    }


def _dashboard_unread_message_count(user):
    return unread_total_for_user(user)


def _dashboard_new_note_share_count(user):
    if not user:
        return 0
    return NoteShare.objects.filter(
        user=user, first_opened_at__isnull=True, note__deleted_at__isnull=True
    ).count()


def _dashboard_nav_tiles(user, unread_messages_total, new_note_shares, open_tasks_count, flags):
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
    if flags["notes"]:
        tiles.insert(
            -1,
            {
                "label": "Notizen",
                "icon": "fa-note-sticky",
                "url_name": "notes",
                "badge_key": "notes_new",
                "badge_count": new_note_shares,
            },
        )
    if flags["tasks"]:
        tiles.insert(
            -1,
            {
                "label": "Aufgaben",
                "icon": "fa-list-check",
                "url_name": "tasks",
                "badge_key": "tasks_due",
                "badge_count": open_tasks_count,
            },
        )
    if flags["vacation_planner"]:
        tiles.insert(
            -1, {"label": "Urlaubsplaner", "icon": "fa-umbrella-beach", "url_name": "vacation_planner"}
        )
    if user and getattr(user, "is_superuser", False):
        tiles.append({"label": "Administration", "icon": "fa-shield-halved", "url_name": "administration"})
    return tiles


def _dashboard_tool_shortcuts(
    upcoming_events, unread_messages_total, dashboard_weather, new_note_shares, flags
):
    event_count = len(upcoming_events)
    event_subtitle = f"{event_count} kommende Termine" if event_count else "Kalender öffnen"
    unread_subtitle = f"{unread_messages_total} ungelesen" if unread_messages_total else "Inbox öffnen"
    weather_city = dashboard_weather.get("today", {}).get("city", "Standardort")
    tools = [
        {
            "title": "Kalender",
            "subtitle": event_subtitle,
            "icon": "fa-calendar-check",
            "url_name": "calendar",
        },
        {
            "title": "Einstellungen",
            "subtitle": "Profil & Präferenzen",
            "icon": "fa-gear",
            "url_name": "settings",
        },
    ]
    if flags["weather"]:
        tools.insert(
            1, {"title": "Wetter", "subtitle": weather_city, "icon": "fa-cloud-sun", "url_name": "weather"}
        )
    if flags["messages"]:
        tools.insert(
            2,
            {
                "title": "Nachrichten",
                "subtitle": unread_subtitle,
                "icon": "fa-message",
                "url_name": "messages",
            },
        )
    if flags["notes"]:
        note_subtitle = f"{new_note_shares} neue Freigabe(n)" if new_note_shares else "Notizen öffnen"
        tools.insert(
            -1, {"title": "Notizen", "subtitle": note_subtitle, "icon": "fa-note-sticky", "url_name": "notes"}
        )
    if flags["vacation_planner"]:
        tools.insert(
            -1,
            {
                "title": "Urlaubsplaner",
                "subtitle": "Tage & Feiertage",
                "icon": "fa-umbrella-beach",
                "url_name": "vacation_planner",
            },
        )
    return tools


def _dashboard_weather_placeholder(user=None):
    city = "Standardort"
    if user:
        try:
            default_city = user.profile.weather_default_city.strip()
        except Exception:
            default_city = ""
        city = default_city.partition(",")[0].strip() or city
    return {
        "today": {
            "city": city,
            "temperature": "–",
            "feels_like": "–",
            "description": "Wetter wird geladen …",
            "icon": "fa-cloud",
        },
        "tomorrow": {
            "day": "Morgen",
            "high": "–",
            "low": "–",
            "rain": "–",
            "description": "Vorhersage wird geladen …",
            "icon": "fa-cloud",
        },
    }


def get_settings_context(notification_form=None, notification_preferences_form=None):
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
        _preference_row(
            notification_form,
            "notify_reminders",
            "Erinnerungszustellung",
            "Fällige Erinnerungen automatisch zustellen",
        ),
        _preference_row(
            notification_form, "notify_email", "E-Mail-Versand", "Fällige Erinnerungen per E-Mail erhalten"
        ),
        _preference_row(
            notification_form,
            "notify_desktop",
            "Web-Push-Zustellung",
            "Auch bei geschlossener App auf registrierten Geräten anzeigen",
        ),
        _preference_row(
            notification_form,
            "weekly_summary",
            "Wöchentliche Zusammenfassung",
            "Montags einen Überblick per E-Mail erhalten",
        ),
    ]
    context["notification_category_rows"] = (
        notification_preferences_form.category_rows if notification_preferences_form else []
    )
    return context


def _display_name(user):
    profile_name = getattr(getattr(user, "profile", None), "display_name", "")
    return profile_name or user.get_full_name() or user.email or user.get_username()


def _calendar_visible_events_query(user):
    """Events the user owns (from a visible source or manually created) plus events they were invited to."""
    return CalendarEvent.objects.filter(
        (Q(user=user) & (Q(source__isnull=True) | Q(source__is_visible=True))) | Q(attendees__user=user)
    ).distinct()


def _dashboard_upcoming_events(user, now):
    events = (
        _calendar_visible_events_query(user)
        .filter(end_at__gte=now)
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
        _calendar_visible_events_query(user)
        .filter(start_at__lt=range_end, end_at__gt=range_start)
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
                            "is_invited": event.user_id != user.id,
                            **_calendar_event_manage_fields(event, user),
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
            "is_invited": event.user_id != user.id,
            **_calendar_event_manage_fields(event, user),
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
    tasks_enabled = feature_enabled("tasks")
    due_tasks = dashboard_today_tasks(user, now, limit=8) if tasks_enabled else []
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
        "pending_invitations": _pending_invitation_items(user),
        "calendar_reminders_enabled": calendar_reminders_enabled,
        "calendar_event_creation_enabled": feature_enabled("calendar_event_creation"),
        "calendar_sync_enabled": feature_enabled("calendar_sync"),
        "tasks_enabled": tasks_enabled,
        "due_tasks": due_tasks,
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


def _calendar_event_manage_fields(event, user):
    can_manage = event.user_id == user.id and event.source_id is None
    fields = {
        "event_id": event.id,
        "recurrence_id": event.recurrence_id,
        "can_delete": can_manage,
    }
    if can_manage:
        event_start = localtime_for_user(event.start_at, user)
        event_end = localtime_for_user(event.end_at, user)
        fields.update(
            {
                "edit_title": event.title,
                "edit_date": event_start.date().isoformat(),
                "edit_start_time": event_start.strftime("%H:%M"),
                "edit_end_time": event_end.strftime("%H:%M"),
                "edit_is_all_day": event.is_all_day,
                "edit_location": event.location,
                "edit_attendee_ids": ",".join(
                    str(user_id) for user_id in event.attendees.values_list("user_id", flat=True)
                ),
            }
        )
    return fields


def _upcoming_calendar_events(user, now):
    events = (
        _calendar_visible_events_query(user)
        .filter(end_at__gte=now)
        .select_related("source")
        .order_by("start_at")[:6]
    )
    return [
        {
            "date": format_user_date(event.start_at, user),
            "title": event.title,
            "category": _calendar_event_category(event, user),
            "icon": "fa-calendar-day",
            "tone": _calendar_event_tone(event),
            "is_invited": event.user_id != user.id,
            **_calendar_event_manage_fields(event, user),
        }
        for event in events
    ]


def _calendar_event_category(event, user):
    if event.source_id:
        return event.source.name
    if event.user_id != user.id:
        return "Einladung"
    return "Eigener Termin"


def _pending_invitation_items(user):
    attendee_rows = (
        CalendarEventAttendee.objects.filter(user=user, status=CalendarEventAttendee.STATUS_INVITED)
        .select_related("event", "event__user", "event__user__profile")
        .order_by("event__start_at")[:10]
    )
    return [
        {
            "attendee_id": row.id,
            "event_title": row.event.title,
            "organizer_name": _display_name(row.event.user),
            "when": format_user_datetime(row.event.start_at, user),
        }
        for row in attendee_rows
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
        return "Ohne Fälligkeitsdatum"

    due_at = localtime_for_user(reminder.due_at, user)
    today = now.date()
    if due_at < now:
        return f"Überfällig seit {format_user_datetime(due_at, user)}"
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
