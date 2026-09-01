from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render

from app.models import Conversation
from app.services.note_search import build_snippet, highlight_text, parse_search_query, search_notes
from app.services.notes import accessible_notes
from app.services.system_settings import feature_flags
from app.services.user_preferences import format_user_date, format_user_time, localtime_for_user
from app.view_models import _calendar_visible_events_query
from app.views.message_views import (
    _build_inbox_items,
    _conversation_ids_matching_message_query,
    _filter_inbox_items,
)

SEARCH_RESULT_LIMIT = 20


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
    shortcuts.append(
        {
            "label": "Kalender öffnen",
            "description": "Termine, Orte und Erinnerungen",
            "icon": "fa-regular fa-calendar",
            "url_name": "calendar",
        }
    )
    return shortcuts


@login_required
def global_search(request):
    query = request.GET.get("q", "").strip()
    flags = feature_flags()

    notes_results = []
    if query and flags["notes"]:
        parsed = parse_search_query(query)
        matches = search_notes(accessible_notes(request.user), query).order_by(
            "-search_rank", "-updated_at", "-id"
        )[:SEARCH_RESULT_LIMIT]
        notes_results = [
            {
                "id": note.id,
                "title_segments": highlight_text(note.title or "Unbenannte Notiz", parsed),
                "snippet": build_snippet(note.plain_text, parsed),
            }
            for note in matches
        ]

    message_results = []
    if query and flags["messages"]:
        conversations = list(Conversation.visible_for(request.user))
        message_match_ids = _conversation_ids_matching_message_query(conversations, query)
        message_results = _filter_inbox_items(
            _build_inbox_items(conversations, request.user), query, "all", message_match_ids
        )[:SEARCH_RESULT_LIMIT]

    event_results = []
    if query:
        events = (
            _calendar_visible_events_query(request.user)
            .filter(
                Q(title__icontains=query) | Q(description__icontains=query) | Q(location__icontains=query)
            )
            .select_related("source")
            .order_by("-start_at")[:SEARCH_RESULT_LIMIT]
        )
        event_results = [
            {
                "title": event.title,
                "date": format_user_date(event.start_at, request.user),
                "time": "Ganztägig" if event.is_all_day else format_user_time(event.start_at, request.user),
                "location": event.location,
                "year": localtime_for_user(event.start_at, request.user).year,
                "month": localtime_for_user(event.start_at, request.user).month,
            }
            for event in events
        ]

    has_search_results = bool(notes_results or message_results or event_results)

    return render(
        request,
        "app/search.html",
        {
            "active_page": "search",
            "query": query,
            "has_search_results": has_search_results,
            "search_shortcuts": _search_shortcuts(flags),
            "notes_results": notes_results,
            "message_results": message_results,
            "event_results": event_results,
            "notes_enabled": flags["notes"],
            "messages_enabled": flags["messages"],
        },
    )
