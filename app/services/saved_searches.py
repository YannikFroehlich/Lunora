"""Search areas and the saved searches built on them, shared by /search/ and the dashboard."""

from django.urls import reverse
from django.utils.http import urlencode

from app.models import SavedSearch
from app.services.system_settings import feature_flags

MAX_SAVED_SEARCHES = 20

# (key, label, icon, feature flag or None). The order is the order on the page.
SEARCH_AREAS = (
    ("notes", "Notizen", "fa-regular fa-note-sticky", "notes"),
    ("messages", "Nachrichten", "fa-regular fa-comments", "messages"),
    ("events", "Termine", "fa-regular fa-calendar", None),
    ("tasks", "Aufgaben", "fa-regular fa-square-check", "tasks"),
    ("bookmarks", "Lesezeichen", "fa-solid fa-bookmark", "tools"),
    ("recipes", "Rezepte", "fa-solid fa-utensils", "tools"),
    ("kanban", "Kanban", "fa-solid fa-table-columns", "tools"),
    ("wishlists", "Wunschlisten", "fa-solid fa-gift", "tools"),
    ("contracts", "Verträge", "fa-solid fa-file-signature", "tools"),
    ("expenses", "Ausgaben", "fa-solid fa-scale-balanced", "tools"),
    ("tools", "Weitere Werkzeuge", "fa-solid fa-toolbox", "tools"),
    ("vacation", "Urlaub", "fa-solid fa-umbrella-beach", "vacation_planner"),
)


def enabled_search_areas():
    flags = feature_flags()
    return flags, [area for area in SEARCH_AREAS if area[3] is None or flags[area[3]]]


def search_url(query, area=""):
    params = {"q": query, **({"in": area} if area else {})}
    return f"{reverse('global_search')}?{urlencode(params)}"


def saved_search_items(user, query="", selected=""):
    """The user's saved searches as chips; an area that is switched off now searches everything."""
    area_labels = {area[0]: area[1] for area in enabled_search_areas()[1]}
    return [
        {
            "id": item.pk,
            "query": item.query,
            "area_label": area_labels.get(item.area, ""),
            "url": search_url(item.query, item.area if item.area in area_labels else ""),
            "is_current": item.query == query and item.area == selected,
        }
        for item in SavedSearch.objects.filter(owner=user)
    ]
