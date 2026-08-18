from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render

from app.models import Conversation
from app.services.notes import accessible_notes
from app.services.system_settings import feature_flags
from app.services.user_preferences import format_user_date, format_user_time, localtime_for_user
from app.view_models import _calendar_visible_events_query
from app.views.message_views import _build_inbox_items, _conversation_ids_matching_message_query, _filter_inbox_items


SEARCH_RESULT_LIMIT = 20


@login_required
def global_search(request):
    query = request.GET.get("q", "").strip()
    flags = feature_flags()

    notes_results = []
    if query and flags["notes"]:
        notes_results = list(
            accessible_notes(request.user)
            .filter(
                Q(title__icontains=query)
                | Q(plain_text__icontains=query)
                | Q(tags__display_name__icontains=query)
            )
            .distinct()
            .order_by("-updated_at")[:SEARCH_RESULT_LIMIT]
        )

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
            .filter(Q(title__icontains=query) | Q(description__icontains=query) | Q(location__icontains=query))
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

    return render(
        request,
        "app/search.html",
        {
            "active_page": "search",
            "query": query,
            "notes_results": notes_results,
            "message_results": message_results,
            "event_results": event_results,
            "notes_enabled": flags["notes"],
            "messages_enabled": flags["messages"],
        },
    )
