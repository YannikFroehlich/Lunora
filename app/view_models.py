import calendar
from datetime import date, datetime, time, timedelta
from urllib.parse import urlparse

from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone

from app.forms import BookmarkForm, event_last_date
from app.models import (
    TASK_COLOR_CHOICES,
    Bookmark,
    CalendarEvent,
    CalendarEventAttendee,
    CalendarReminder,
    CalendarSubscription,
    Conversation,
    ExpenseGroupMember,
    Habit,
    Note,
    NoteShare,
    Profile,
    Recipe,
    ShoppingItem,
    Task,
    UmlDiagram,
    VacationYear,
    VocabularyCard,
    VocabularyList,
)
from app.services.calendar_service import location_link
from app.services.dashboard import (
    ITEM_LIMITS,
    MAX_WIDGET_ITEMS,
    WIDGET_SIZE_LABELS,
    WIDGET_SIZES,
    WIDGET_TONE_LABELS,
    WIDGET_TONES,
    available_dashboard_widgets,
    dashboard_presets,
    dashboard_widgets_for_layout,
    default_dashboard_layout,
    normalize_dashboard_layout,
)
from app.services.habits import habit_items
from app.services.message_queries import unread_total_for_user
from app.services.notifications import dashboard_latest_notifications
from app.services.shopping import group_items
from app.services.system_settings import feature_enabled, feature_flags
from app.services.tasks import dashboard_open_tasks, dashboard_today_tasks, visible_tasks
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
from app.services.vacation_planner import annual_summary, calendar_day_markers, upcoming_vacations
from app.services.vocabulary import due_filter

# Calendar views: the month grid plus time-grid views showing this many days side by side.
CALENDAR_TIME_GRID_DAYS = {"week": 7, "three_days": 3, "day": 1}
# The agenda lists this many days from its anchor day, skipping days without entries.
CALENDAR_AGENDA_DAYS = 28
# Years the year view accepts in the URL.
CALENDAR_YEAR_RANGE = (1900, 2200)
# Time grid: the shortest height an event gets, and where the grid starts scrolled for ranges
# that don't contain today.
WEEK_MIN_EVENT_MINUTES = 20
WEEK_DEFAULT_SCROLL_MINUTE = 7 * 60


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
    # Lists load the largest selectable limit; the template hides rows beyond the widget's own
    # limit so the customizer can preview a different limit without a reload.
    upcoming_dashboard_events = _dashboard_upcoming_events(user, now) if user else []
    dashboard_tasks = (
        dashboard_open_tasks(user, now, limit=MAX_WIDGET_ITEMS) if flags["tasks"] and user else []
    )
    open_tasks_count = visible_tasks(user).filter(is_done=False).count() if flags["tasks"] and user else 0
    nav_tiles = _dashboard_nav_tiles(user, unread_messages_total, new_note_shares, open_tasks_count, flags)
    if user and "notifications" in visible_widget_ids:
        dashboard_notifications = dashboard_latest_notifications(user, limit=MAX_WIDGET_ITEMS)
    else:
        dashboard_notifications = []
    dashboard_stats = _dashboard_stats(user, now, flags) if user and "stats" in visible_widget_ids else {}
    dashboard_habits = habit_items(user, now.date()) if user and "habits" in visible_widget_ids else []
    dashboard_shopping = _dashboard_shopping(user) if user and "shopping" in visible_widget_ids else {}
    dashboard_bookmarks = (
        [
            {"id": item.pk, "title": item.title, "url": item.url, "host": urlparse(item.url).hostname or ""}
            for item in Bookmark.objects.filter(owner=user)
        ]
        if user and "bookmarks" in visible_widget_ids
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
        "dashboard_presets": dashboard_presets(),
        "dashboard_size_choices": [(size, WIDGET_SIZE_LABELS[size]) for size in WIDGET_SIZES],
        "dashboard_tone_choices": [(tone, WIDGET_TONE_LABELS[tone]) for tone in WIDGET_TONES],
        "dashboard_limit_choices": ITEM_LIMITS,
        "dashboard_new_note_shares": new_note_shares,
        "dashboard_tasks": dashboard_tasks,
        "dashboard_tasks_enabled": flags["tasks"],
        "dashboard_notifications": dashboard_notifications,
        "dashboard_stats": dashboard_stats,
        "dashboard_habits": dashboard_habits,
        "dashboard_bookmarks": dashboard_bookmarks,
        "dashboard_shopping": dashboard_shopping,
        "bookmark_form": BookmarkForm(),
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


DASHBOARD_STATS_PERIODS = ("week", "month")


def _dashboard_stats(user, now, flags):
    return {period: _dashboard_stat_tiles(user, now, flags, period) for period in DASHBOARD_STATS_PERIODS}


def _dashboard_stat_tiles(user, now, flags, period):
    if period == "month":
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        lookahead_days = 30
        period_label = "Monat"
    else:
        period_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        lookahead_days = 7
        period_label = "Woche"

    tiles = []

    if flags["tasks"]:
        tasks_done = Task.objects.filter(user=user, is_done=True, updated_at__gte=period_start).count()
        open_tasks = visible_tasks(user).filter(is_done=False).count()
        tiles.append(
            {
                "icon": "fa-check-double",
                "label": f"Erledigt ({period_label})",
                "value": str(tasks_done),
                "url_name": "tasks",
            }
        )
        tiles.append(
            {
                "icon": "fa-list-check",
                "label": "Offene Aufgaben",
                "value": str(open_tasks),
                "url_name": "tasks",
            }
        )

    if flags["notes"]:
        notes_count = Note.objects.filter(
            owner=user, deleted_at__isnull=True, updated_at__gte=period_start
        ).count()
        tiles.append(
            {
                "icon": "fa-note-sticky",
                "label": f"Notizen ({period_label})",
                "value": str(notes_count),
                "url_name": "notes",
            }
        )

    upcoming_event_count = (
        _calendar_visible_events_query(user)
        .filter(start_at__gte=now, start_at__lte=now + timedelta(days=lookahead_days))
        .count()
    )
    tiles.append(
        {
            "icon": "fa-calendar-week",
            "label": f"Termine ({period_label})",
            "value": str(upcoming_event_count),
            "url_name": "calendar",
        }
    )

    if flags["vacation_planner"] and VacationYear.objects.filter(user=user, year=now.year).exists():
        remaining_label = annual_summary(user, now.year)["remaining_label"]
        tiles.append(
            {
                "icon": "fa-umbrella-beach",
                "label": f"Resturlaub {now.year}",
                "value": f"{remaining_label} Tage",
                "url_name": "vacation_planner",
            }
        )

    return tiles


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
    if flags.get("tools"):
        tiles.insert(-1, {"label": "Werkzeuge", "icon": "fa-toolbox", "url_name": "tools"})
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
    if flags.get("tools"):
        tools.insert(
            -1,
            {
                "title": "Werkzeuge",
                "subtitle": "Gewohnheiten, Timer & mehr",
                "icon": "fa-toolbox",
                "url_name": "tools",
            },
        )
    return tools


# Catalog for the /tools/ hub. An entry without url_name renders as "coming soon".
TOOLS = (
    {
        "id": "habits",
        "title": "Gewohnheiten",
        "description": "Tägliche und wöchentliche Gewohnheiten abhaken und deine Serien im Blick behalten.",
        "icon": "fa-seedling",
        "url_name": "habits",
    },
    {
        "id": "shopping",
        "title": "Einkaufszettel",
        "description": "Aufschreiben, was fehlt, und im Laden abhaken.",
        "icon": "fa-basket-shopping",
        "url_name": "shopping_list",
    },
    {
        "id": "recipes",
        "title": "Rezepte",
        "description": "Lieblingsrezepte sammeln und die Zutaten mit einem Klick auf den Einkaufszettel setzen.",
        "icon": "fa-utensils",
        "url_name": "recipes",
    },
    {
        "id": "expenses",
        "title": "Geteilte Ausgaben",
        "description": "Gemeinsame Ausgaben eintragen, sehen, wer wem was schuldet, und ausgleichen.",
        "icon": "fa-scale-balanced",
        "url_name": "expense_groups",
    },
    {
        "id": "focus",
        "title": "Fokus-Timer",
        "description": "Konzentriert arbeiten in festen Intervallen mit kurzen und langen Pausen.",
        "icon": "fa-hourglass-half",
        "url_name": "focus_timer",
    },
    {
        "id": "uml",
        "title": "UML-Klassendiagramm",
        "description": "Klassen, Interfaces, Enums und Beziehungen zeichnen, als Bild, PlantUML oder Java-Code exportieren.",
        "icon": "fa-diagram-project",
        "url_name": "uml_diagrams",
    },
    {
        "id": "vocabulary",
        "title": "Vokabeltrainer",
        "description": "Vokabellisten anlegen, per Karteikarte, Eintippen oder Multiple Choice abfragen – mit Lernkartei.",
        "icon": "fa-language",
        "url_name": "vocabulary_lists",
    },
)


def get_tools_context(user):
    diagram_count = UmlDiagram.objects.filter(owner=user).count()
    vocabulary_count = VocabularyList.objects.filter(owner=user).count()
    due_cards = (
        VocabularyCard.objects.filter(vocabulary_list__owner=user).filter(due_filter(timezone.now())).count()
    )
    habit_count = Habit.objects.filter(owner=user).count()
    open_shopping_items = ShoppingItem.objects.filter(owner=user, is_done=False).count()
    recipe_count = Recipe.objects.filter(owner=user).count()
    expense_group_count = ExpenseGroupMember.objects.filter(user=user).count()
    meta = {
        "habits": f"{habit_count} Gewohnheit(en)" if habit_count else "Noch keine Gewohnheiten",
        "shopping": f"{open_shopping_items} offen" if open_shopping_items else "Nichts offen",
        "recipes": f"{recipe_count} Rezept(e)" if recipe_count else "Noch keine Rezepte",
        "expenses": f"{expense_group_count} Gruppe(n)" if expense_group_count else "Noch keine Gruppen",
        "focus": "Läuft direkt im Browser",
        "uml": f"{diagram_count} Diagramm(e)" if diagram_count else "Noch keine Diagramme",
        "vocabulary": (
            f"{vocabulary_count} Liste(n) · {due_cards} fällig"
            if vocabulary_count
            else "Noch keine Vokabellisten"
        ),
    }
    tools = [{**tool, "meta": meta.get(tool["id"])} for tool in TOOLS]
    return {"active_page": "tools", "tools": tools}


def _dashboard_shopping(user):
    """The user's own open shopping items in store-aisle order, as on the shopping list itself."""
    groups, _done = group_items(list(ShoppingItem.objects.filter(owner=user, is_done=False)))
    items = [item for group in groups for item in group["items"]]
    return {"items": items[:MAX_WIDGET_ITEMS], "meta": f"{len(items)} offen" if items else ""}


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
    """Events the user owns plus events they were invited to; subscribed calendars only while that
    feature is on."""
    events = CalendarEvent.objects.filter(Q(user=user) | Q(attendees__user=user))
    if not feature_enabled("calendar_subscriptions"):
        events = events.filter(subscription=None)
    return events.select_related("subscription").distinct()


def _dashboard_upcoming_events(user, now):
    events = (
        _calendar_visible_events_query(user).filter(end_at__gte=now).order_by("start_at")[:MAX_WIDGET_ITEMS]
    )
    items = [
        {
            "title": event.title,
            "date": format_user_date(event.start_at, user),
            "time": "Ganztägig" if event.is_all_day else format_user_time(event.start_at, user),
            "tone": _calendar_event_tone(event),
            "sort_key": _event_sort_key(event, user),
        }
        for event in events
    ]
    for item in _upcoming_vacation_items(user, now, MAX_WIDGET_ITEMS):
        items.append({**item, "time": f"Urlaub bis {item['end_date']}"})
    return _soonest(items, MAX_WIDGET_ITEMS)


def _event_sort_key(event, user):
    return localtime_for_user(event.start_at, user).replace(tzinfo=None)


def _soonest(items, limit):
    items.sort(key=lambda item: item["sort_key"])
    return items[:limit]


def _vacation_calendar_url(day):
    # `day` makes the planner flash that day once (vacation_planner.js).
    return f"{reverse('vacation_planner')}?year={day.year}&month={day.month}&day={day.isoformat()}#vacation-calendar"


def _upcoming_vacation_items(user, now, limit):
    """Running and upcoming vacations as list rows next to events (empty when the planner is off)."""
    if not feature_enabled("vacation_planner"):
        return []
    today = now.date()
    items = []
    for period in upcoming_vacations(user, today, limit):
        items.append(
            {
                "title": period.get_name_display(),
                "date": format_user_date(period.start_date, user),
                "end_date": format_user_date(period.end_date, user),
                "tone": "green",
                "url": _vacation_calendar_url(period.start_date),
                # A running vacation sorts as today, ahead of later events.
                "sort_key": datetime.combine(max(period.start_date, today), time.min),
            }
        )
    return items


def get_calendar_context(user, *, year=None, month=None, view=None, day=None):
    user_timezone = get_user_zoneinfo(user)
    now = localtime_for_user(profile_or_user=user)
    view = view if view in CALENDAR_TIME_GRID_DAYS or view in {"year", "agenda"} else "month"
    view_days = CALENDAR_AGENDA_DAYS if view == "agenda" else CALENDAR_TIME_GRID_DAYS.get(view)
    anchor = None
    grid_start = None
    if view == "year":
        # The sidebar stats show the current month in the current year, January otherwise.
        try:
            year = int(year or now.year)
            if not CALENDAR_YEAR_RANGE[0] <= year <= CALENDAR_YEAR_RANGE[1]:
                raise ValueError
        except (TypeError, ValueError):
            year = now.year
        month = now.month if year == now.year else 1
    elif view != "month":
        # Time-grid views and the agenda are anchored on a day (the week view on any day of its
        # week); the stats below then belong to that day's month, and the queried range covers both.
        try:
            anchor = date.fromisoformat(day) if day else now.date()
        except (TypeError, ValueError):
            anchor = now.date()
        year, month = anchor.year, anchor.month
        grid_start = anchor - timedelta(days=anchor.weekday()) if view == "week" else anchor
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
    if grid_start is not None:
        visible_start = min(visible_start, grid_start)
        visible_end = max(visible_end, grid_start + timedelta(days=view_days))
    range_start = timezone.make_aware(datetime.combine(visible_start, time.min), user_timezone)
    range_end = timezone.make_aware(datetime.combine(visible_end, time.min), user_timezone)
    events = list(
        _calendar_visible_events_query(user)
        .filter(start_at__lt=range_end, end_at__gt=range_start)
        .order_by("start_at", "title")
    )
    events_by_date = _group_calendar_events_by_date(events, visible_start, visible_end, user)
    tasks_enabled = feature_enabled("tasks")
    tasks_by_date = {}
    if tasks_enabled:
        # Includes tasks assigned to the user; only their own can be dragged to another day.
        for task in (
            visible_tasks(user)
            .filter(is_done=False, due_at__gte=range_start, due_at__lt=range_end)
            .select_related("user__profile")
            .order_by("due_at", "id")
        ):
            tasks_by_date.setdefault(localtime_for_user(task.due_at, user).date(), []).append(task)

    markers = {}
    if feature_enabled("vacation_planner"):
        markers = calendar_day_markers(user, visible_start, visible_end - timedelta(days=1))

    rows = []
    for week in weeks if view == "month" else []:
        row = []
        for day in week:
            day_events = events_by_date.get(day, [])
            day_tasks = tasks_by_date.get(day, [])
            # Events and tasks share the three visible slots per day; tasks fill what events leave.
            shown_tasks = day_tasks[: max(0, 3 - len(day_events))]
            row.append(
                {
                    "number": str(day.day),
                    "date_input": day.isoformat(),
                    "date_label": format_user_date(day, user),
                    "muted": day.month != month,
                    "today": day == now.date(),
                    # Holidays and vacations from the vacation planner; a thin line that takes no slot.
                    "markers": [
                        {
                            "label": marker["label"],
                            "tone": "red" if marker["kind"] == "holiday" else "green",
                            "url": _vacation_calendar_url(day),
                        }
                        for marker in markers.get(day, [])
                    ],
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
                    "tasks": [_calendar_task_item(task, user) for task in shown_tasks],
                    "overflow": len(day_events) + len(day_tasks) - min(len(day_events), 3) - len(shown_tasks),
                }
            )
        rows.append(row)

    today_events = [
        {
            "time": _calendar_event_time_label(event, user),
            "title": event.title,
            "category": _calendar_event_category(event),
            "tone": _calendar_event_tone(event),
            "is_invited": event.user_id != user.id,
            **_calendar_event_manage_fields(event, user),
        }
        for event in events_by_date.get(now.date(), [])
    ]
    upcoming_events = _upcoming_calendar_events(user, now)
    calendar_reminders_enabled = feature_enabled("calendar_reminders")
    subscriptions_enabled = feature_enabled("calendar_subscriptions")
    reminder_items = _calendar_reminder_items(user, now) if calendar_reminders_enabled else []
    due_tasks = dashboard_today_tasks(user, now, limit=8) if tasks_enabled else []
    prev_month = _shift_month(year, month, -1)
    next_month = _shift_month(year, month, 1)
    month_label = f"{get_user_month_name(month_date, user)} {year}"
    calendar_url = reverse("calendar")
    time_grid = None
    year_grid = None
    agenda = None
    if view == "agenda":
        agenda = _calendar_agenda(user, anchor, now, events_by_date, tasks_by_date, markers)
        period_label = "Agenda"
        step = timedelta(days=CALENDAR_AGENDA_DAYS)
        prev_url = f"{calendar_url}?view=agenda&date={(anchor - step).isoformat()}"
        next_url = f"{calendar_url}?view=agenda&date={(anchor + step).isoformat()}"
    elif view == "year":
        year_grid = _calendar_year_grid(user, year, now, tasks_enabled)
        period_label = str(year)
        prev_url = f"{calendar_url}?view=year&year={year - 1}"
        next_url = f"{calendar_url}?view=year&year={year + 1}"
    elif view != "month":
        day_count = CALENDAR_TIME_GRID_DAYS[view]
        time_grid = _calendar_time_grid(
            user, view, grid_start, day_count, now, events_by_date, tasks_by_date, markers
        )
        period_label = time_grid["label"]
        step = timedelta(days=day_count)
        prev_url = f"{calendar_url}?view={view}&date={(anchor - step).isoformat()}"
        next_url = f"{calendar_url}?view={view}&date={(anchor + step).isoformat()}"
    else:
        period_label = month_label
        prev_url = f"{calendar_url}?year={prev_month[0]}&month={prev_month[1]}"
        next_url = f"{calendar_url}?year={next_month[0]}&month={next_month[1]}"
    # Switching views keeps the period: the anchor day, or today in the current month and the
    # first of any other month. Every switch link names its view, so the view gets remembered.
    switch_anchor = anchor or (now.date() if (year, month) == (now.year, now.month) else month_date)
    view_options = [
        {
            "key": key,
            "label": label,
            "active": key == view,
            "url": (
                f"{calendar_url}?view=month&year={year}&month={month}"
                if key == "month"
                else f"{calendar_url}?view=year&year={year}"
                if key == "year"
                else f"{calendar_url}?view={key}&date={switch_anchor.isoformat()}"
            ),
        }
        for key, label in Profile.CALENDAR_VIEW_CHOICES
    ]

    return {
        "active_page": "calendar",
        "calendar_view": view,
        "calendar_view_options": view_options,
        "calendar_rows": rows,
        "calendar_grid": time_grid,
        "calendar_year": year_grid,
        "calendar_agenda": agenda,
        "period_label": period_label,
        "prev_url": prev_url,
        "next_url": next_url,
        "today_url": f"{calendar_url}?view={view}",
        "month_label": month_label,
        "today_label": f"{get_user_weekday_name(now, user)}, {format_user_date(now, user)}",
        "today_events": today_events,
        "upcoming_events": upcoming_events,
        "prev_month": {"year": prev_month[0], "month": prev_month[1]},
        "next_month": {"year": next_month[0], "month": next_month[1]},
        "reminders": reminder_items,
        "pending_invitations": _pending_invitation_items(user),
        "calendar_reminders_enabled": calendar_reminders_enabled,
        "calendar_event_creation_enabled": feature_enabled("calendar_event_creation"),
        "calendar_feed_enabled": feature_enabled("calendar_feed"),
        "calendar_subscriptions_enabled": subscriptions_enabled,
        "calendar_subscriptions": calendar_subscription_items(user) if subscriptions_enabled else [],
        "subscription_color_choices": TASK_COLOR_CHOICES,
        "subscription_icon_choices": CalendarSubscription.ICON_CHOICES,
        "tasks_enabled": tasks_enabled,
        "due_tasks": due_tasks,
    }


def _calendar_agenda(user, start, now, events_by_date, tasks_by_date, markers):
    """The agenda: the days from ``start`` that have events, tasks or planner markers, in order."""
    days = []
    for offset in range(CALENDAR_AGENDA_DAYS):
        day = start + timedelta(days=offset)
        day_events = events_by_date.get(day, [])
        day_tasks = tasks_by_date.get(day, [])
        day_markers = markers.get(day, [])
        if not (day_events or day_tasks or day_markers):
            continue
        days.append(
            {
                "date_input": day.isoformat(),
                "weekday": get_user_weekday_name(day, user),
                "date_label": format_user_date(day, user),
                "today": day == now.date(),
                "markers": [
                    {
                        "label": marker["label"],
                        "tone": "red" if marker["kind"] == "holiday" else "green",
                        "url": _vacation_calendar_url(day),
                    }
                    for marker in day_markers
                ],
                "events": [
                    {
                        "label": event.title,
                        "tone": _calendar_event_tone(event),
                        "time": _agenda_time_label(event, day, user),
                        "category": _calendar_event_category(event),
                        "is_invited": event.user_id != user.id,
                        "day_url": f"{reverse('calendar')}?view=day&date={day.isoformat()}",
                        **_calendar_event_manage_fields(event, user),
                    }
                    for event in day_events
                ],
                "tasks": [_calendar_task_item(task, user) for task in day_tasks],
            }
        )
    last_day = start + timedelta(days=CALENDAR_AGENDA_DAYS - 1)
    return {
        "days": days,
        "label": f"{format_user_date(start, user)} – {format_user_date(last_day, user)}",
        "day_count": CALENDAR_AGENDA_DAYS,
    }


def _agenda_time_label(event, day, user):
    """Time of an event on one agenda day; multi-day events say where they start or end."""
    if event.is_all_day:
        return "Ganztägig"
    start = localtime_for_user(event.start_at, user)
    # An event ending exactly at midnight belongs to the day before.
    end = localtime_for_user(event.end_at - timedelta(microseconds=1), user)
    starts_today = start.date() == day
    ends_today = end.date() == day
    if starts_today and ends_today:
        return f"{format_user_time(event.start_at, user)}–{format_user_time(event.end_at, user)}"
    if starts_today:
        return f"ab {format_user_time(event.start_at, user)}"
    if ends_today:
        return f"bis {format_user_time(event.end_at, user)}"
    return "Ganztägig"


def _calendar_year_grid(user, year, now, tasks_enabled):
    """Twelve small month grids for the year view: per day the number of events and open tasks
    (as a busy level) plus holiday/vacation markers; every day links to its day view."""
    user_timezone = get_user_zoneinfo(user)
    first_day, last_day = date(year, 1, 1), date(year, 12, 31)
    range_start = timezone.make_aware(datetime.combine(first_day, time.min), user_timezone)
    range_end = timezone.make_aware(datetime.combine(last_day + timedelta(days=1), time.min), user_timezone)
    events = (
        _calendar_visible_events_query(user)
        .filter(start_at__lt=range_end, end_at__gt=range_start)
        .select_related(None)
        .only("id", "start_at", "end_at", "is_all_day")
    )
    events_by_date = _group_calendar_events_by_date(events, first_day, last_day + timedelta(days=1), user)
    task_counts = {}
    if tasks_enabled:
        for due_at in (
            visible_tasks(user)
            .filter(is_done=False, due_at__gte=range_start, due_at__lt=range_end)
            .values_list("due_at", flat=True)
        ):
            due_day = localtime_for_user(due_at, user).date()
            task_counts[due_day] = task_counts.get(due_day, 0) + 1
    markers = calendar_day_markers(user, first_day, last_day) if feature_enabled("vacation_planner") else {}
    calendar_url = reverse("calendar")
    months = []
    for month in range(1, 13):
        month_date = date(year, month, 1)
        weeks = []
        for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
            row = []
            for day in week:
                if day.month != month:
                    row.append(None)
                    continue
                event_count = len(events_by_date.get(day, []))
                task_count = task_counts.get(day, 0)
                day_markers = markers.get(day, [])
                details = []
                if event_count:
                    details.append(f"{event_count} Termin" + ("e" if event_count != 1 else ""))
                if task_count:
                    details.append(f"{task_count} Aufgabe" + ("n" if task_count != 1 else ""))
                details.extend(marker["label"] for marker in day_markers)
                total = event_count + task_count
                row.append(
                    {
                        "number": day.day,
                        "url": f"{calendar_url}?view=day&date={day.isoformat()}",
                        "today": day == now.date(),
                        "weekend": day.weekday() >= 5,
                        "level": min(total, 3),
                        "holiday": any(marker["kind"] == "holiday" for marker in day_markers),
                        "vacation": any(marker["kind"] == "vacation" for marker in day_markers),
                        "label": format_user_date(day, user) + (f": {', '.join(details)}" if details else ""),
                    }
                )
            weeks.append(row)
        months.append(
            {
                "label": get_user_month_name(month_date, user),
                "url": f"{calendar_url}?view=month&year={year}&month={month}",
                "is_current": (year, month) == (now.year, now.month),
                "weeks": weeks,
            }
        )
    return {"year": year, "months": months}


def _calendar_task_item(task, user):
    is_own = task.user_id == user.pk
    return {
        "id": task.id,
        "label": task.title,
        "time": format_user_time(task.due_at, user),
        "can_move": is_own,
        "owner_name": "" if is_own else _display_name(task.user),
    }


def _calendar_time_grid(user, view, grid_start, day_count, now, events_by_date, tasks_by_date, markers):
    """Day columns for the week, three-day and day views: all-day items on top, timed events placed
    on a 24 h grid."""
    grid_end = grid_start + timedelta(days=day_count - 1)
    manage_fields = {}

    def event_item(event):
        if event.id not in manage_fields:
            manage_fields[event.id] = _calendar_event_manage_fields(event, user)
        return {
            "label": event.title,
            "tone": _calendar_event_tone(event),
            "location": event.location,
            "is_invited": event.user_id != user.id,
            **manage_fields[event.id],
        }

    days = []
    for offset in range(day_count):
        day = grid_start + timedelta(days=offset)
        all_day = []
        timed = []
        for event in events_by_date.get(day, []):
            start = localtime_for_user(event.start_at, user)
            end = localtime_for_user(event.end_at, user)
            # Like other calendars, events of a day or longer sit in the all-day row instead of the grid.
            if event.is_all_day or end - start >= timedelta(days=1):
                all_day.append({**event_item(event), "time": _calendar_event_time_label(event, user)})
                continue
            if start.date() < day:
                start_minute = 0
            else:
                start_minute = start.hour * 60 + start.minute
            if end.date() > day:
                end_minute = 24 * 60
            else:
                end_minute = end.hour * 60 + end.minute
            if end_minute <= start_minute and start != end:
                # A timed event that ends exactly at midnight leaves nothing on the following day.
                continue
            start_minute = min(start_minute, 24 * 60 - WEEK_MIN_EVENT_MINUTES)
            time_label = format_user_time(start, user)
            if end != start:
                time_label = f"{time_label} – {format_user_time(end, user)}"
            timed.append(
                {
                    **event_item(event),
                    "time": time_label,
                    # Only the part where the event ends can be stretched to change its end.
                    "can_resize": end.date() == day and start != end,
                    "start_minute": start_minute,
                    "duration": max(end_minute - start_minute, WEEK_MIN_EVENT_MINUTES),
                    # Too short for stacked lines: title and time share one line.
                    "is_short": end_minute - start_minute < 45,
                }
            )
        _assign_week_lanes(timed)
        days.append(
            {
                "date_input": day.isoformat(),
                "date_label": format_user_date(day, user),
                "weekday": get_user_weekday_name(day, user)[:2],
                "number": str(day.day),
                "today": day == now.date(),
                "markers": [
                    {
                        "label": marker["label"],
                        "tone": "red" if marker["kind"] == "holiday" else "green",
                        "url": _vacation_calendar_url(day),
                    }
                    for marker in markers.get(day, [])
                ],
                "all_day": all_day,
                "tasks": [_calendar_task_item(task, user) for task in tasks_by_date.get(day, [])],
                "timed": timed,
                "now_minute": now.hour * 60 + now.minute if day == now.date() else None,
            }
        )

    range_label = (
        f"{format_user_date(grid_start, user, include_year=grid_start.year != grid_end.year)}"
        f" – {format_user_date(grid_end, user)}"
    )
    if view == "week":
        label = f"KW {grid_start.isocalendar().week}"
    elif view == "day":
        label = get_user_weekday_name(grid_start, user)
        range_label = format_user_date(grid_start, user)
    else:
        label = "3 Tage"
    return {
        "label": label,
        "range_label": range_label,
        "day_count": day_count,
        "days": days,
        "hours": [format_user_time(time(hour), user) for hour in range(24)],
        "scroll_minute": (
            max(now.hour - 1, 0) * 60 if grid_start <= now.date() <= grid_end else WEEK_DEFAULT_SCROLL_MINUTE
        ),
    }


def _assign_week_lanes(items):
    """Place overlapping timed events side by side: each gets a lane and the lane count of its cluster."""
    items.sort(key=lambda item: (item["start_minute"], -item["duration"]))
    cluster = []
    lane_ends = []
    cluster_end = -1

    def close_cluster():
        for item in cluster:
            item["lanes"] = len(lane_ends)

    for item in items:
        start = item["start_minute"]
        end = start + item["duration"]
        if cluster and start >= cluster_end:
            close_cluster()
            cluster, lane_ends = [], []
        lane = next((index for index, lane_end in enumerate(lane_ends) if lane_end <= start), None)
        if lane is None:
            lane = len(lane_ends)
            lane_ends.append(end)
        else:
            lane_ends[lane] = end
        item["lane"] = lane
        cluster.append(item)
        cluster_end = max(cluster_end, end) if len(cluster) > 1 else end
    close_cluster()


def _group_calendar_events_by_date(events, visible_start, visible_end, user):
    grouped = {}
    for event in events:
        start_date = max(localtime_for_user(event.start_at, user).date(), visible_start)
        event_end = localtime_for_user(event.end_at, user)
        end_date = event_end.date()
        if event.is_all_day:
            end_date = (event_end - timedelta(seconds=1)).date()
        elif event.end_at > event.start_at:
            # A timed event ending exactly at midnight doesn't continue into the next day.
            end_date = max(
                start_date, localtime_for_user(event.end_at - timedelta(microseconds=1), user).date()
            )
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
    if event.subscription_id:
        return event.subscription.color
    return CalendarEvent.CATEGORY_TONES.get(event.category, "sand")


def _calendar_event_category(event):
    """The category label, or for a subscribed calendar's event that calendar's name."""
    return event.subscription.name if event.subscription_id else event.get_category_display()


def _calendar_event_manage_fields(event, user):
    # Events of a subscribed calendar are read-only mirrors; the next sync would undo any change.
    can_manage = event.user_id == user.id and not event.subscription_id
    fields = {
        "event_id": event.id,
        "detail_url": reverse("calendar_event_detail", args=[event.id]),
        "subscription_name": event.subscription.name if event.subscription_id else "",
        "subscription_icon": event.subscription.icon if event.subscription_id else "",
        "recurrence_id": event.recurrence_id,
        "can_delete": can_manage,
        "location": event.location,
        "location_link": location_link(event.location),
        "has_reminder": event.reminder_minutes is not None,
    }
    if can_manage:
        event_start = localtime_for_user(event.start_at, user)
        event_end = localtime_for_user(event.end_at, user)
        last_date = event_last_date(event, event_start, event_end)
        fields.update(
            {
                "edit_title": event.title,
                "edit_date": event_start.date().isoformat(),
                "edit_end_date": last_date.isoformat() if last_date else "",
                "edit_start_time": event_start.strftime("%H:%M"),
                "edit_end_time": event_end.strftime("%H:%M"),
                "edit_is_all_day": event.is_all_day,
                "edit_category": event.category,
                "edit_location": event.location,
                "edit_reminder_minutes": "" if event.reminder_minutes is None else event.reminder_minutes,
                "edit_attendee_ids": ",".join(
                    str(user_id) for user_id in event.attendees.values_list("user_id", flat=True)
                ),
            }
        )
    return fields


def get_calendar_event_detail_context(event, user):
    """The event detail page: when and where, who is invited with their answers, and the comments."""
    start = localtime_for_user(event.start_at, user)
    end = localtime_for_user(event.end_at, user)
    last_date = event_last_date(event, start, end)
    if event.is_all_day:
        when = format_user_date(start, user)
        if last_date and last_date != start.date():
            when = f"{when} – {format_user_date(last_date, user)}"
        when = f"{when} · ganztägig"
    elif start.date() == (end - timedelta(microseconds=1)).date() or end == start:
        when = (
            f"{format_user_date(start, user)}, {format_user_time(start, user)}–{format_user_time(end, user)}"
        )
    else:
        when = (
            f"{format_user_date(start, user)}, {format_user_time(start, user)} – "
            f"{format_user_date(end, user)}, {format_user_time(end, user)}"
        )
    attendees = list(event.attendees.select_related("user__profile").order_by("created_at", "id"))
    own_attendee = next((row for row in attendees if row.user_id == user.pk), None)
    comments = event.comments.select_related("author__profile")
    return {
        "active_page": "calendar",
        "event": event,
        "title": event.title,
        "category": _calendar_event_category(event),
        "is_subscribed": bool(event.subscription_id),
        "tone": _calendar_event_tone(event),
        "when": f"{get_user_weekday_name(start, user)}, {when}",
        "location": event.location,
        "location_link": location_link(event.location),
        "has_reminder": event.reminder_minutes is not None,
        "is_series": bool(event.recurrence_id),
        "is_owner": event.user_id == user.pk,
        "organizer_name": _display_name(event.user),
        "day_url": f"{reverse('calendar')}?view=day&date={start.date().isoformat()}",
        "attendees": [
            {
                "name": _display_name(row.user),
                "status": row.status,
                "status_label": row.get_status_display(),
                "is_self": row.user_id == user.pk,
            }
            for row in attendees
        ],
        "own_attendee": {"id": own_attendee.pk, "status": own_attendee.status} if own_attendee else None,
        "can_comment": bool(attendees),
        "comments": [
            {
                "id": comment.pk,
                "author_name": _display_name(comment.author),
                "initials": Conversation.initials_for_user(comment.author),
                "body": comment.body,
                "created_label": format_user_datetime(comment.created_at, user),
                "created_iso": comment.created_at.isoformat(),
                "is_own": comment.author_id == user.pk,
            }
            for comment in comments
        ],
    }


def _upcoming_calendar_events(user, now):
    events = _calendar_visible_events_query(user).filter(end_at__gte=now).order_by("start_at")[:6]
    items = [
        {
            "date": format_user_date(event.start_at, user),
            "time": "Ganztägig" if event.is_all_day else format_user_time(event.start_at, user),
            "title": event.title,
            "category": _calendar_event_category(event),
            "icon": "fa-calendar-day",
            "tone": _calendar_event_tone(event),
            "is_invited": event.user_id != user.id,
            "sort_key": _event_sort_key(event, user),
            **_calendar_event_manage_fields(event, user),
        }
        for event in events
    ]
    for item in items:
        item["url"] = item["detail_url"]
    for item in _upcoming_vacation_items(user, now, 6):
        items.append({**item, "category": f"Urlaub bis {item['end_date']}", "icon": "fa-umbrella-beach"})
    items = _soonest(items, 6)
    for item in items:
        # A running vacation sorts (and shows) as today.
        day = item["sort_key"].date()
        item["badge"] = {
            "weekday": get_user_weekday_name(day)[:2],
            "day": day.day,
            "month": get_user_month_name(day)[:3],
            "is_today": day == now.date(),
        }
    return items


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
            "detail_url": reverse("calendar_event_detail", args=[row.event_id]),
            "organizer_name": _display_name(row.event.user),
            "when": format_user_datetime(row.event.start_at, user),
            "location": row.event.location,
            "location_link": location_link(row.event.location),
        }
        for row in attendee_rows
    ]


def _calendar_reminder_items(user, now):
    # Reminders created for events ("15 Minuten vorher") belong to the event, not this list.
    reminders = CalendarReminder.objects.filter(user=user, event__isnull=True).order_by(
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


def calendar_subscription_items(user):
    """The user's subscriptions for the settings page and the calendar's panel (never the URL)."""
    return [
        {
            "id": subscription.pk,
            "name": subscription.name,
            "color": subscription.color,
            "icon": subscription.icon,
            "event_count": subscription.event_count,
            "last_synced": format_user_datetime(subscription.last_synced_at, user)
            if subscription.last_synced_at
            else "",
            "last_error": subscription.last_error,
        }
        for subscription in user.calendar_subscriptions.all()
    ]
