from copy import deepcopy
from dataclasses import dataclass

DASHBOARD_LAYOUT_VERSION = 1


@dataclass(frozen=True)
class DashboardWidget:
    id: str
    label: str
    template: str
    class_name: str
    feature_flag: str | None = None


DASHBOARD_WIDGETS = (
    DashboardWidget(
        id="welcome",
        label="Willkommen",
        template="app/partials/dashboard_welcome.html",
        class_name="welcome-panel",
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
        class_name="notes-panel",
    ),
    DashboardWidget(
        id="weather",
        label="Wetter",
        template="app/partials/dashboard_weather.html",
        class_name="today-panel weather-overview-panel",
        feature_flag="weather",
    ),
    DashboardWidget(
        id="upcoming_events",
        label="Nächste Termine",
        template="app/partials/dashboard_upcoming_events.html",
        class_name="notes-panel",
    ),
    DashboardWidget(
        id="tasks",
        label="Aufgaben",
        template="app/partials/dashboard_tasks.html",
        class_name="notes-panel",
        feature_flag="tasks",
    ),
    DashboardWidget(
        id="quick_actions",
        label="Schnellzugriff",
        template="app/partials/dashboard_quick_actions.html",
        class_name="quick-actions",
    ),
    DashboardWidget(
        id="recent_tools",
        label="Letzte Tools",
        template="app/partials/dashboard_recent_tools.html",
        class_name="recent-panel",
    ),
)


DASHBOARD_WIDGET_BY_ID = {widget.id: widget for widget in DASHBOARD_WIDGETS}
DASHBOARD_WIDGET_IDS = [widget.id for widget in DASHBOARD_WIDGETS]


def default_dashboard_layout():
    return {
        "version": DASHBOARD_LAYOUT_VERSION,
        "order": list(DASHBOARD_WIDGET_IDS),
        "hidden": [],
    }


def normalize_dashboard_layout(layout):
    if not isinstance(layout, dict):
        return default_dashboard_layout()

    order = layout.get("order")
    hidden = layout.get("hidden")
    if not isinstance(order, list) or not isinstance(hidden, list):
        return default_dashboard_layout()

    normalized_order = []
    seen = set()
    for widget_id in order:
        if not isinstance(widget_id, str) or widget_id not in DASHBOARD_WIDGET_BY_ID or widget_id in seen:
            continue
        normalized_order.append(widget_id)
        seen.add(widget_id)

    normalized_order.extend(widget_id for widget_id in DASHBOARD_WIDGET_IDS if widget_id not in seen)
    if not normalized_order:
        normalized_order = list(DASHBOARD_WIDGET_IDS)

    normalized_hidden = []
    hidden_seen = set()
    for widget_id in hidden:
        if (
            isinstance(widget_id, str)
            and widget_id in DASHBOARD_WIDGET_BY_ID
            and widget_id not in hidden_seen
        ):
            normalized_hidden.append(widget_id)
            hidden_seen.add(widget_id)

    return {
        "version": DASHBOARD_LAYOUT_VERSION,
        "order": normalized_order,
        "hidden": normalized_hidden,
    }


def validate_dashboard_layout(layout):
    if not isinstance(layout, dict):
        return False, "Das Layout muss ein Objekt sein."
    if layout.get("version") != DASHBOARD_LAYOUT_VERSION:
        return False, "Die Layout-Version wird nicht unterstützt."

    order = layout.get("order")
    hidden = layout.get("hidden")
    if not isinstance(order, list) or not all(isinstance(widget_id, str) for widget_id in order):
        return False, "Die Reihenfolge muss eine Liste aus Widget-IDs sein."
    if not isinstance(hidden, list) or not all(isinstance(widget_id, str) for widget_id in hidden):
        return False, "Die ausgeblendeten Widgets müssen eine Liste aus Widget-IDs sein."

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

    return True, ""


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
        widget = deepcopy(DASHBOARD_WIDGET_BY_ID[widget_id])
        widgets.append(
            {
                "id": widget.id,
                "label": widget.label,
                "template": widget.template,
                "class_name": widget.class_name,
                "hidden": widget.id in hidden_ids,
            }
        )

    return widgets
