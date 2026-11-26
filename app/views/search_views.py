from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from app.models import SavedSearch
from app.services.global_search import GlobalSearch, toggle_saved_search_alert
from app.services.saved_searches import (
    MAX_SAVED_SEARCHES,
    SEARCH_AREAS,
    enabled_search_areas,
    saved_search_items,
    search_url,
)
from app.views.navigation import posted_id

SEARCH_RESULT_LIMIT = 20
# With one area picked (?in=…), that area shows more of its hits.
SEARCH_FILTERED_LIMIT = 60
# The command palette's live preview: a few hits per area, a handful in total.
QUICK_SEARCH_PER_AREA = 2
QUICK_SEARCH_LIMIT = 8
QUICK_SEARCH_MIN_LENGTH = 2


def _search_shortcuts(flags):
    shortcuts = []
    if flags["notes"]:
        shortcuts.append(
            {
                "label": "Notizen öffnen",
                "description": "Gedanken, Listen und Anhänge",
                "icon": "fa-regular fa-note-sticky",
                "url_name": "notes",
            }
        )
    if flags["messages"]:
        shortcuts.append(
            {
                "label": "Nachrichten öffnen",
                "description": "Chats, Gruppen und Antworten",
                "icon": "fa-regular fa-comments",
                "url_name": "messages",
            }
        )
    if flags["tasks"]:
        shortcuts.append(
            {
                "label": "Aufgaben öffnen",
                "description": "Listen, Fälligkeiten und Prioritäten",
                "icon": "fa-regular fa-square-check",
                "url_name": "tasks",
            }
        )
    shortcuts.append(
        {
            "label": "Kalender öffnen",
            "description": "Termine, Orte und Erinnerungen",
            "icon": "fa-regular fa-calendar",
            "url_name": "calendar",
        }
    )
    if flags["tools"]:
        shortcuts.append(
            {
                "label": "Werkzeuge öffnen",
                "description": "Lesezeichen, Rezepte, Boards und mehr",
                "icon": "fa-solid fa-toolbox",
                "url_name": "tools",
            }
        )
    return shortcuts


@login_required
def global_search(request):
    query = request.GET.get("q", "").strip()
    flags, areas = enabled_search_areas()
    area_keys = {area[0] for area in areas}
    selected = request.GET.get("in", "")
    if selected not in area_keys:
        selected = ""

    sections = []
    if query:
        search = GlobalSearch(request.user, query)
        for key, label, icon, _flag in areas:
            limit = SEARCH_FILTERED_LIMIT if key == selected else SEARCH_RESULT_LIMIT
            results = search.results(key, limit)
            sections.append(
                {
                    "key": key,
                    "label": label,
                    "icon": icon,
                    "results": results,
                    "has_more": len(results) >= limit,
                    "count_label": f"{len(results)}+" if len(results) >= limit else str(len(results)),
                }
            )
    results_by_key = {section["key"]: section["results"] for section in sections}
    hit_sections = [section for section in sections if section["results"]]
    shown_sections = [section for section in hit_sections if not selected or section["key"] == selected]

    saved = saved_search_items(request.user, query, selected)

    return render(
        request,
        "app/search.html",
        {
            "active_page": "search",
            "query": query,
            "saved_searches": saved,
            "is_saved": any(item["is_current"] for item in saved),
            "saved_search_limit_reached": len(saved) >= MAX_SAVED_SEARCHES,
            "selected_area": selected,
            "has_search_results": bool(hit_sections),
            "hit_sections": hit_sections,
            "shown_sections": shown_sections,
            "search_shortcuts": _search_shortcuts(flags),
            # Per-area result lists, also used by the tests.
            "notes_results": results_by_key.get("notes", []),
            "message_results": results_by_key.get("messages", []),
            "event_results": results_by_key.get("events", []),
            "task_results": results_by_key.get("tasks", []),
            "vacation_results": results_by_key.get("vacation", []),
            "tool_results": results_by_key.get("tools", []),
            "bookmark_results": results_by_key.get("bookmarks", []),
            "recipe_results": results_by_key.get("recipes", []),
            "kanban_results": results_by_key.get("kanban", []),
            "wishlist_results": results_by_key.get("wishlists", []),
            "contract_results": results_by_key.get("contracts", []),
            "expense_results": results_by_key.get("expenses", []),
        },
    )


@login_required
@require_POST
def saved_searches(request):
    query = request.POST.get("q", "").strip()[: SavedSearch._meta.get_field("query").max_length]
    area = request.POST.get("in", "")
    if area not in {key for key, *_rest in SEARCH_AREAS}:
        area = ""
    if request.POST.get("form_name") == "saved_search_delete":
        SavedSearch.objects.filter(owner=request.user, pk=posted_id(request, "saved_search_id")).delete()
    elif request.POST.get("form_name") == "saved_search_notify":
        saved = SavedSearch.objects.filter(
            owner=request.user, pk=posted_id(request, "saved_search_id")
        ).first()
        if saved is not None:
            toggle_saved_search_alert(saved)
    elif query:
        if SavedSearch.objects.filter(owner=request.user).count() >= MAX_SAVED_SEARCHES:
            django_messages.error(request, f"Du kannst höchstens {MAX_SAVED_SEARCHES} Suchen speichern.")
        else:
            SavedSearch.objects.get_or_create(owner=request.user, query=query, area=area)
    return redirect(search_url(query, area) if query else "global_search")


@never_cache
@login_required
@require_GET
def quick_search(request):
    """The command palette's live preview: the best few hits of every enabled area as JSON."""
    query = request.GET.get("q", "").strip()
    results = []
    if len(query) >= QUICK_SEARCH_MIN_LENGTH:
        search = GlobalSearch(request.user, query)
        for key, label, _icon, _flag in enabled_search_areas()[1]:
            for row in search.results(key, QUICK_SEARCH_PER_AREA):
                results.append(
                    {
                        "title": row["title"] or "Ohne Titel",
                        "url": row["url"],
                        "icon": row["icon"],
                        "area": label,
                        "external": bool(row.get("external")),
                    }
                )
            if len(results) >= QUICK_SEARCH_LIMIT:
                break
    return JsonResponse({"results": results[:QUICK_SEARCH_LIMIT]})
