from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render
from django.urls import reverse

from app.models import Bookmark, Contract, Conversation, Recipe, UmlDiagram, VacationPeriod, VocabularyList
from app.services.bookmarks import host_of, search_bookmarks
from app.services.expenses import user_groups
from app.services.kanban import accessible_boards
from app.services.note_search import (
    build_snippet,
    filter_by_query,
    highlight_text,
    parse_search_query,
    search_notes,
)
from app.services.notes import accessible_notes, filter_by_tags, split_tag_filters
from app.services.system_settings import feature_flags
from app.services.tasks import visible_tasks
from app.services.user_preferences import (
    format_user_date,
    format_user_datetime,
    format_user_time,
)
from app.services.whiteboards import accessible_whiteboards
from app.services.wishlist import accessible_wish_lists
from app.view_models import _calendar_visible_events_query
from app.views.message_views import (
    _build_inbox_items,
    _conversation_ids_matching_message_query,
    _filter_inbox_items,
)

SEARCH_RESULT_LIMIT = 20
# With one area picked (?in=…), that area shows more of its hits.
SEARCH_FILTERED_LIMIT = 60

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


class _Search:
    """One search request: the parsed query plus a row builder shared by every area."""

    def __init__(self, user, query):
        self.user = user
        self.query = query
        self.tags, self.text_query = split_tag_filters(query)
        self.parsed = parse_search_query(self.text_query)

    def row(self, title, url, *, icon, subtitle="", text="", placeholder=""):
        """A result row; `text` adds a snippet when the hit isn't already visible in the title."""
        title_segments = highlight_text(title or placeholder, self.parsed)
        title_hit = any(part["match"] for part in title_segments)
        return {
            "title": title,
            "title_segments": title_segments,
            "url": url,
            "icon": icon,
            "subtitle": subtitle,
            "snippet": build_snippet(text, self.parsed) if text and not title_hit else [],
        }

    def text_matches(self, queryset, fields):
        """`queryset` filtered by the text part of the query; none at all for a tags-only query."""
        if not self.parsed:
            return queryset.none()
        return filter_by_query(queryset, self.parsed, fields)

    # Areas. Each returns at most `limit` rows the user may see.

    def notes(self, limit):
        candidates = filter_by_tags(accessible_notes(self.user), self.user, self.tags)
        matches = search_notes(candidates, self.text_query).order_by("-search_rank", "-updated_at", "-id")
        rows = []
        for note in matches[:limit]:
            row = self.row(
                note.title, reverse("note_detail", args=[note.pk]), icon="fa-regular fa-note-sticky"
            )
            row["title_segments"] = highlight_text(note.title or "Unbenannte Notiz", self.parsed)
            # Notes always show their text, "Kein Inhalt" when there is none.
            row["snippet"] = build_snippet(note.plain_text, self.parsed) or [
                {"text": "Kein Inhalt", "match": False}
            ]
            row["id"] = note.pk
            rows.append(row)
        return rows

    def messages(self, limit):
        conversations = list(Conversation.visible_for(self.user))
        match_ids = _conversation_ids_matching_message_query(conversations, self.query)
        items = _filter_inbox_items(
            _build_inbox_items(conversations, self.user), self.query, "all", match_ids
        )
        return [
            {
                **self.row(
                    item["title"],
                    reverse("messages_detail", args=[item["conversation"].id]),
                    icon="fa-solid fa-user-group" if item["is_group"] else "fa-solid fa-comment",
                ),
                "subtitle": (item["preview"] or item["last_message_body"])[:120],
            }
            for item in items[:limit]
        ]

    def events(self, limit):
        events = self.text_matches(
            _calendar_visible_events_query(self.user), ["title", "description", "location"]
        ).order_by("-start_at")[:limit]
        rows = []
        for event in events:
            when = "Ganztägig" if event.is_all_day else format_user_time(event.start_at, self.user)
            parts = [format_user_date(event.start_at, self.user), when, event.location]
            rows.append(
                self.row(
                    event.title,
                    reverse("calendar_event_detail", args=[event.pk]),
                    icon="fa-regular fa-calendar-days",
                    subtitle=" · ".join(part for part in parts if part),
                    text=event.description,
                )
            )
        return rows

    def tasks(self, limit):
        tasks = self.text_matches(
            visible_tasks(self.user), ["title", "labels__name", "comments__body"]
        ).order_by("is_done", "due_at", "-id")[:limit]
        return [
            {
                **self.row(
                    task.title,
                    reverse("tasks"),
                    icon="fa-regular fa-square-check" if task.is_done else "fa-regular fa-square",
                    subtitle=f"Fällig {format_user_datetime(task.due_at, self.user)}" if task.due_at else "",
                ),
                "is_done": task.is_done,
            }
            for task in tasks
        ]

    def bookmarks(self, limit):
        bookmarks = Bookmark.objects.filter(owner=self.user)
        for tag in self.tags:
            bookmarks = search_bookmarks(bookmarks, tag=tag)
        if self.parsed:
            bookmarks = filter_by_query(bookmarks, self.parsed, ["title", "url", "description", "tags"])
        elif not self.tags:
            return []
        return [
            {
                **self.row(
                    bookmark.title,
                    bookmark.url,
                    icon="fa-solid fa-arrow-up-right-from-square",
                    subtitle=" · ".join([host_of(bookmark.url), *(f"#{tag}" for tag in bookmark.tag_list)]),
                    text=bookmark.description,
                ),
                "external": True,
            }
            for bookmark in bookmarks.order_by("-created_at", "-id")[:limit]
        ]

    def recipes(self, limit):
        recipes = self.text_matches(
            Recipe.objects.filter(owner=self.user), ["title", "ingredients", "instructions"]
        ).order_by("title", "id")[:limit]
        return [
            self.row(
                recipe.title,
                reverse("recipe_detail", args=[recipe.pk]),
                icon="fa-solid fa-utensils",
                subtitle="Rezept",
                text=f"{recipe.ingredients}\n{recipe.instructions}",
            )
            for recipe in recipes
        ]

    def kanban(self, limit):
        boards = self.text_matches(
            accessible_boards(self.user), ["title", "cards__title", "cards__description"]
        ).order_by("-updated_at", "-id")[:limit]
        rows = []
        for board in boards:
            # Name the matching cards, so a hit inside a big board can be found there.
            cards = self.text_matches(board.cards.all(), ["title", "description"]).values_list(
                "title", flat=True
            )[:3]
            rows.append(
                self.row(
                    board.title,
                    reverse("kanban_board", args=[board.pk]),
                    icon="fa-solid fa-table-columns",
                    subtitle="Kanban-Board" if board.owner_id == self.user.pk else "Geteiltes Kanban-Board",
                    text=" · ".join(cards),
                )
            )
        return rows

    def wishlists(self, limit):
        lists = self.text_matches(accessible_wish_lists(self.user), ["title", "items__title"]).order_by(
            "title", "id"
        )[:limit]
        rows = []
        for wish_list in lists:
            items = self.text_matches(wish_list.items.all(), ["title"]).values_list("title", flat=True)[:3]
            rows.append(
                self.row(
                    wish_list.title,
                    reverse("wish_list_detail", args=[wish_list.pk]),
                    icon="fa-solid fa-gift",
                    subtitle="Wunschliste" if wish_list.owner_id == self.user.pk else "Geteilte Wunschliste",
                    text=" · ".join(items),
                )
            )
        return rows

    def contracts(self, limit):
        contracts = self.text_matches(
            Contract.objects.filter(owner=self.user), ["name", "provider", "notes"]
        ).order_by("name", "id")[:limit]
        return [
            self.row(
                contract.name,
                f"{reverse('contracts')}#contract-{contract.pk}",
                icon="fa-solid fa-file-signature",
                subtitle=contract.provider or contract.get_category_display(),
                text=contract.notes,
            )
            for contract in contracts
        ]

    def expenses(self, limit):
        groups = self.text_matches(user_groups(self.user), ["name", "expenses__title"])[:limit]
        rows = []
        for group in groups:
            expenses = self.text_matches(group.expenses.all(), ["title"]).values_list("title", flat=True)[:3]
            rows.append(
                self.row(
                    group.name,
                    reverse("expense_group_detail", args=[group.pk]),
                    icon="fa-solid fa-scale-balanced",
                    subtitle="Ausgabengruppe",
                    text=" · ".join(expenses),
                )
            )
        return rows

    def tools(self, limit):
        vocabulary_lists = self.text_matches(
            VocabularyList.objects.filter(Q(owner=self.user) | Q(shares__user=self.user)),
            ["title", "cards__term", "cards__translation"],
        ).order_by("-updated_at", "-id")[:limit]
        rows = [
            self.row(
                item.title,
                # Shared lists have no detail page for recipients; they are copied from the overview.
                reverse("vocabulary_list_detail", args=[item.pk])
                if item.owner_id == self.user.pk
                else reverse("vocabulary_lists"),
                icon="fa-solid fa-language",
                subtitle="Vokabelliste" if item.owner_id == self.user.pk else "Geteilte Vokabelliste",
            )
            for item in vocabulary_lists
        ]
        diagrams = self.text_matches(UmlDiagram.objects.filter(owner=self.user), ["title"]).order_by(
            "-updated_at", "-id"
        )[:limit]
        rows += [
            self.row(
                diagram.title,
                reverse("uml_diagram_editor", args=[diagram.pk]),
                icon="fa-solid fa-diagram-project",
                subtitle="UML-Klassendiagramm",
            )
            for diagram in diagrams
        ]
        # Texts on a board count too; the snippet shows where the hit is when it isn't in the title.
        boards = self.text_matches(accessible_whiteboards(self.user), ["title", "plain_text"]).order_by(
            "-updated_at", "-id"
        )[:limit]
        rows += [
            self.row(
                board.title,
                reverse("whiteboard_editor", args=[board.pk]),
                icon="fa-solid fa-pen-ruler",
                subtitle="Whiteboard" if board.owner_id == self.user.pk else "Geteiltes Whiteboard",
                text=board.plain_text,
            )
            for board in boards
        ]
        return rows[:limit]

    def vacation(self, limit):
        periods = self.text_matches(
            VacationPeriod.objects.filter(user=self.user), ["name", "notes"]
        ).order_by("-start_date")[:limit]
        return [
            self.row(
                period.get_name_display(),
                f"{reverse('vacation_planner')}?year={period.start_date.year}",
                icon="fa-solid fa-umbrella-beach",
                subtitle=f"{format_user_date(period.start_date, self.user)} – "
                f"{format_user_date(period.end_date, self.user)}",
                text=period.notes,
            )
            for period in periods
        ]


@login_required
def global_search(request):
    query = request.GET.get("q", "").strip()
    flags = feature_flags()
    areas = [area for area in SEARCH_AREAS if area[3] is None or flags[area[3]]]
    area_keys = {area[0] for area in areas}
    selected = request.GET.get("in", "")
    if selected not in area_keys:
        selected = ""

    sections = []
    if query:
        search = _Search(request.user, query)
        for key, label, icon, _flag in areas:
            limit = SEARCH_FILTERED_LIMIT if key == selected else SEARCH_RESULT_LIMIT
            results = getattr(search, key)(limit)
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

    return render(
        request,
        "app/search.html",
        {
            "active_page": "search",
            "query": query,
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
