from dataclasses import dataclass, field

DASHBOARD_LAYOUT_VERSION = 3

# Grid spans on the 12-column desktop grid (see home.css): small 4, medium 6, large 8, full 12.
# Defaults stick to small/large/full so widths line up on the 4-column rhythm without holes.
WIDGET_SIZES = ("small", "medium", "large", "full")
WIDGET_SIZE_LABELS = {"small": "Klein", "medium": "Mittel", "large": "Groß", "full": "Volle Breite"}
# "" follows the profile accent; the rest are the shared .tone-* classes from base.css.
WIDGET_TONES = ("", "sand", "blue", "green", "violet", "red")
WIDGET_TONE_LABELS = {
    "": "Akzent",
    "sand": "Sand",
    "blue": "Blau",
    "green": "Grün",
    "violet": "Violett",
    "red": "Rosé",
}
ITEM_LIMITS = (3, 5, 8)
MAX_WIDGET_ITEMS = max(ITEM_LIMITS)
STATS_PERIODS = ("week", "month")
# Quick-action tiles are addressed by their URL name (see view_models._dashboard_nav_tiles).
QUICK_ACTION_TILE_IDS = (
    "home",
    "weather",
    "calendar",
    "messages",
    "notes",
    "tasks",
    "vacation_planner",
    "tools",
    "settings",
    "administration",
)


@dataclass(frozen=True)
class DashboardWidget:
    id: str
    label: str
    template: str
    class_name: str
    default_size: str = "small"
    options: dict = field(default_factory=dict)
    feature_flag: str | None = None


DASHBOARD_WIDGETS = (
    DashboardWidget(
        id="welcome",
        label="Willkommen",
        template="app/partials/dashboard_welcome.html",
        class_name="welcome-panel",
        default_size="large",
    ),
    DashboardWidget(
        id="clock",
        label="Uhr",
        template="app/partials/dashboard_clock.html",
        class_name="clock-panel",
    ),
    DashboardWidget(
        id="notifications",
        label="Benachrichtigungen",
        template="app/partials/dashboard_notifications.html",
        class_name="list-panel",
        options={"limit": 5},
    ),
    DashboardWidget(
        id="upcoming_events",
        label="Nächste Termine",
        template="app/partials/dashboard_upcoming_events.html",
        class_name="list-panel",
        options={"limit": 5},
    ),
    DashboardWidget(
        id="tasks",
        label="Aufgaben",
        template="app/partials/dashboard_tasks.html",
        class_name="list-panel",
        options={"limit": 5},
        feature_flag="tasks",
    ),
    DashboardWidget(
        id="habits",
        label="Gewohnheiten",
        template="app/partials/dashboard_habits.html",
        class_name="list-panel",
        options={"limit": 5},
        feature_flag="tools",
    ),
    DashboardWidget(
        id="weather",
        label="Wetter",
        template="app/partials/dashboard_weather.html",
        class_name="weather-overview-panel",
        options={"show_tomorrow": True},
        feature_flag="weather",
    ),
    DashboardWidget(
        id="stats",
        label="Statistik",
        template="app/partials/dashboard_stats.html",
        class_name="stats-panel",
        options={"period": "week"},
    ),
    DashboardWidget(
        id="quick_actions",
        label="Schnellzugriff",
        template="app/partials/dashboard_quick_actions.html",
        class_name="quick-actions",
        default_size="large",
        options={"hidden_tiles": []},
    ),
    DashboardWidget(
        id="recent_tools",
        label="Zuletzt genutzt",
        template="app/partials/dashboard_recent_tools.html",
        class_name="recent-panel",
    ),
    DashboardWidget(
        id="bookmarks",
        label="Lesezeichen",
        template="app/partials/dashboard_bookmarks.html",
        class_name="bookmarks-panel",
    ),
)


DASHBOARD_WIDGET_BY_ID = {widget.id: widget for widget in DASHBOARD_WIDGETS}
DASHBOARD_WIDGET_IDS = [widget.id for widget in DASHBOARD_WIDGETS]


def _default_widget_settings(widget):
    return {
        "size": widget.default_size,
        "tone": "",
        **{key: _copy(value) for key, value in widget.options.items()},
    }


def _copy(value):
    return list(value) if isinstance(value, list) else value


def default_dashboard_layout():
    return {
        "version": DASHBOARD_LAYOUT_VERSION,
        "order": list(DASHBOARD_WIDGET_IDS),
        "hidden": [],
        "widgets": {widget.id: _default_widget_settings(widget) for widget in DASHBOARD_WIDGETS},
    }


def _setting_error(widget, key, value):
    """Return an error message when value is not allowed for this widget setting, else ""."""
    if key == "size":
        return "" if value in WIDGET_SIZES else "Unbekannte Widget-Größe."
    if key == "tone":
        return "" if value in WIDGET_TONES else "Unbekannte Widget-Farbe."
    if key not in widget.options:
        return f"Unbekannte Einstellung „{key}“ für {widget.label}."
    if key == "limit":
        return "" if type(value) is int and value in ITEM_LIMITS else "Die Anzahl muss 3, 5 oder 8 sein."
    if key == "show_tomorrow":
        return "" if isinstance(value, bool) else "„Morgen anzeigen“ muss wahr oder falsch sein."
    if key == "period":
        return "" if value in STATS_PERIODS else "Unbekannter Zeitraum."
    if key == "hidden_tiles":
        if (
            not isinstance(value, list)
            or not all(isinstance(tile, str) for tile in value)
            or len(set(value)) != len(value)
            or not set(value).issubset(QUICK_ACTION_TILE_IDS)
        ):
            return "Die ausgeblendeten Kacheln müssen eine Liste bekannter Kacheln sein."
        return ""
    return f"Unbekannte Einstellung „{key}“ für {widget.label}."


def _clean_known_ids(values):
    cleaned = []
    seen = set()
    for widget_id in values:
        if isinstance(widget_id, str) and widget_id in DASHBOARD_WIDGET_BY_ID and widget_id not in seen:
            cleaned.append(widget_id)
            seen.add(widget_id)
    return cleaned


def normalize_dashboard_layout(layout):
    """Upgrade any stored layout (v1/v2/v3) to a complete v3 layout, dropping invalid parts."""
    if not isinstance(layout, dict):
        return default_dashboard_layout()

    order = layout.get("order")
    hidden = layout.get("hidden")
    if not isinstance(order, list) or not isinstance(hidden, list):
        return default_dashboard_layout()

    normalized_order = _clean_known_ids(order)
    normalized_order.extend(
        widget_id for widget_id in DASHBOARD_WIDGET_IDS if widget_id not in normalized_order
    )

    # v2 only knew a "wide" list, which spanned the whole two-column grid.
    legacy_wide = set(
        _clean_known_ids(layout.get("wide", []) if isinstance(layout.get("wide"), list) else [])
    )
    stored_widgets = layout.get("widgets") if isinstance(layout.get("widgets"), dict) else {}
    widgets = {}
    for widget in DASHBOARD_WIDGETS:
        settings = _default_widget_settings(widget)
        if widget.id in legacy_wide:
            settings["size"] = "full"
        stored = stored_widgets.get(widget.id)
        if isinstance(stored, dict):
            for key, value in stored.items():
                if key in settings and not _setting_error(widget, key, value):
                    settings[key] = _copy(value)
        widgets[widget.id] = settings

    return {
        "version": DASHBOARD_LAYOUT_VERSION,
        "order": normalized_order,
        "hidden": _clean_known_ids(hidden),
        "widgets": widgets,
    }


def validate_dashboard_layout(layout):
    if not isinstance(layout, dict):
        return False, "Das Layout muss ein Objekt sein."
    if layout.get("version") != DASHBOARD_LAYOUT_VERSION:
        return False, "Die Layout-Version wird nicht unterstützt."

    order = layout.get("order")
    hidden = layout.get("hidden")
    widgets = layout.get("widgets")
    if not isinstance(order, list) or not all(isinstance(widget_id, str) for widget_id in order):
        return False, "Die Reihenfolge muss eine Liste aus Widget-IDs sein."
    if not isinstance(hidden, list) or not all(isinstance(widget_id, str) for widget_id in hidden):
        return False, "Die ausgeblendeten Widgets müssen eine Liste aus Widget-IDs sein."
    if not isinstance(widgets, dict):
        return False, "Die Widget-Einstellungen müssen ein Objekt sein."

    if len(order) != len(DASHBOARD_WIDGET_IDS):
        return False, "Die Reihenfolge muss alle Dashboard-Widgets enthalten."
    if len(set(order)) != len(order):
        return False, "Die Reihenfolge enthält doppelte Widget-IDs."
    if set(order) != set(DASHBOARD_WIDGET_IDS):
        return False, "Die Reihenfolge enthält unbekannte oder fehlende Widget-IDs."
    if len(set(hidden)) != len(hidden):
        return False, "Die ausgeblendeten Widgets enthalten doppelte Widget-IDs."
    if not set(hidden).issubset(DASHBOARD_WIDGET_BY_ID):
        return False, "Die ausgeblendeten Widgets enthalten unbekannte Widget-IDs."

    for widget_id, settings in widgets.items():
        widget = DASHBOARD_WIDGET_BY_ID.get(widget_id)
        if widget is None:
            return False, "Die Widget-Einstellungen enthalten unbekannte Widget-IDs."
        if not isinstance(settings, dict):
            return False, f"Die Einstellungen für {widget.label} müssen ein Objekt sein."
        for key, value in settings.items():
            error = _setting_error(widget, key, value)
            if error:
                return False, error

    return True, ""


def _preset(order, hidden, overrides):
    layout = default_dashboard_layout()
    layout["order"] = order + [widget_id for widget_id in DASHBOARD_WIDGET_IDS if widget_id not in order]
    layout["hidden"] = hidden
    for widget_id, settings in overrides.items():
        layout["widgets"][widget_id].update(settings)
    return layout


def dashboard_presets():
    """One-click starting points for the customizer; every preset is a complete, valid v3 layout."""
    return [
        {
            "id": "overview",
            "label": "Übersicht",
            "icon": "fa-table-cells-large",
            "layout": default_dashboard_layout(),
        },
        {
            "id": "focus",
            "label": "Fokus",
            "icon": "fa-bullseye",
            "layout": _preset(
                ["tasks", "habits", "clock", "upcoming_events", "notifications", "weather"],
                ["welcome", "stats", "quick_actions", "recent_tools", "bookmarks"],
                {
                    "tasks": {"size": "large", "limit": 8},
                    "habits": {"size": "small", "limit": 5},
                    "clock": {"size": "small"},
                    "upcoming_events": {"size": "medium", "limit": 5},
                    "notifications": {"size": "medium", "limit": 5},
                    "weather": {"size": "full", "show_tomorrow": True},
                },
            ),
        },
        {
            "id": "compact",
            "label": "Kompakt",
            "icon": "fa-compress",
            "layout": _preset(
                ["clock", "tasks", "upcoming_events", "notifications", "weather", "quick_actions"],
                ["welcome", "stats", "recent_tools", "habits", "bookmarks"],
                {
                    "clock": {"size": "small"},
                    "tasks": {"size": "small", "limit": 3},
                    "upcoming_events": {"size": "small", "limit": 3},
                    "notifications": {"size": "small", "limit": 3},
                    "weather": {"size": "small", "show_tomorrow": False},
                    "quick_actions": {"size": "full"},
                },
            ),
        },
    ]


def available_dashboard_widgets(flags):
    return [
        widget
        for widget in DASHBOARD_WIDGETS
        if not widget.feature_flag or flags.get(widget.feature_flag, False)
    ]


def dashboard_widgets_for_layout(layout, flags, *, include_hidden=False):
    normalized_layout = normalize_dashboard_layout(layout)
    available_ids = {widget.id for widget in available_dashboard_widgets(flags)}
    hidden_ids = set(normalized_layout["hidden"])
    widgets = []

    for widget_id in normalized_layout["order"]:
        if widget_id not in available_ids:
            continue
        if widget_id in hidden_ids and not include_hidden:
            continue
        widget = DASHBOARD_WIDGET_BY_ID[widget_id]
        settings = normalized_layout["widgets"][widget_id]
        widgets.append(
            {
                "id": widget.id,
                "label": widget.label,
                "template": widget.template,
                "class_name": widget.class_name,
                "hidden": widget.id in hidden_ids,
                "size": settings["size"],
                "tone": settings["tone"],
                "options": {key: settings[key] for key in widget.options},
            }
        )

    return widgets
