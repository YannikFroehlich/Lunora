"""The global search over every area (/search/, the palette's live list, saved-search alerts).

Each area method returns at most `limit` result rows the user may see, built with `row`."""

from datetime import timedelta

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from app.models import (
    Bookmark,
    Contract,
    Conversation,
    Recipe,
    SavedSearch,
    UmlDiagram,
    VacationPeriod,
    VocabularyList,
)
from app.services.bookmarks import host_of, search_bookmarks
from app.services.expenses import user_groups
from app.services.kanban import accessible_boards
from app.services.note_search import (
    build_snippet,
    filter_by_query,
    highlight_text,
    parse_search_query,
    search_notes,
    split_date_filters,
)
from app.services.notes import accessible_notes, filter_by_tags, split_tag_filters
from app.services.notifications import materialize_saved_search_alert
from app.services.saved_searches import enabled_search_areas, search_url
from app.services.tasks import visible_tasks
from app.services.user_preferences import (
    format_user_date,
    format_user_datetime,
    format_user_time,
)
from app.services.whiteboards import accessible_whiteboards
from app.services.wishlist import accessible_wish_lists
from app.view_models import _calendar_visible_events_query

# The date each area's nach:/vor: bounds apply to; areas without one show no hits under a date filter.
DATE_FIELDS = {
    "notes": "updated_at__date",
    "events": "start_at__date",
    "tasks": "due_at__date",
    "bookmarks": "created_at__date",
    "vacation": "start_date",
}


class GlobalSearch:
    """One search request: the parsed query plus a row builder shared by every area."""

    def __init__(self, user, query):
        self.user = user
        self.after, self.before, self.query = split_date_filters(query)
        self.has_dates = bool(self.after or self.before)
        self.tags, self.text_query = split_tag_filters(self.query)
        self.parsed = parse_search_query(self.text_query)

    def results(self, area, limit):
        if self.has_dates and area not in DATE_FIELDS:
            return []
        return getattr(self, area)(limit)

    def dated(self, queryset, area):
        """`queryset` within the query's nach:/vor: bounds, both days included."""
        field = DATE_FIELDS[area]
        if self.after:
            queryset = queryset.filter(**{f"{field}__gte": self.after})
        if self.before:
            queryset = queryset.filter(**{f"{field}__lte": self.before})
        return queryset

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
        """`queryset` filtered by the text part of the query: none at all for a tags-only query, all
        of it for a dates-only one (the area narrows it with `dated`)."""
        if not self.parsed:
            return queryset if self.has_dates and not self.tags else queryset.none()
        return filter_by_query(queryset, self.parsed, fields)

    # Areas. Each returns at most `limit` rows the user may see.

    def notes(self, limit):
        candidates = self.dated(filter_by_tags(accessible_notes(self.user), self.user, self.tags), "notes")
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
        # The inbox helpers live with the messages page; imported here, as that module imports views.
        from app.views.message_views import (
            _build_inbox_items,
            _conversation_ids_matching_message_query,
            _filter_inbox_items,
        )

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
            self.dated(_calendar_visible_events_query(self.user), "events"),
            ["title", "description", "location"],
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
            self.dated(visible_tasks(self.user), "tasks"),
            ["title", "description", "labels__name", "comments__body"],
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
        bookmarks = self.dated(Bookmark.objects.filter(owner=self.user), "bookmarks")
        for tag in self.tags:
            bookmarks = search_bookmarks(bookmarks, tag=tag)
        if self.parsed:
            bookmarks = filter_by_query(bookmarks, self.parsed, ["title", "url", "description", "tags"])
        elif not self.tags and not self.has_dates:
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
            self.dated(VacationPeriod.objects.filter(user=self.user), "vacation"), ["name", "notes"]
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


# Saved-search alerts: a saved search with `notify` on is re-run by the automations at most once per
# SAVED_SEARCH_CHECK_INTERVAL and reports hits it hasn't seen before in the inbox.
SAVED_SEARCH_CHECK_INTERVAL = timedelta(hours=1)
SAVED_SEARCH_HITS_PER_AREA = 20
MAX_KNOWN_HITS = 300


def saved_search_hits(saved):
    """{key: title} of the saved search's current hits. Rows carry no id, so a hit is keyed by area,
    link and title (a renamed task counts as new)."""
    search = GlobalSearch(saved.owner, saved.query)
    hits = {}
    for key, *_rest in enabled_search_areas()[1]:
        if saved.area and key != saved.area:
            continue
        for row in search.results(key, SAVED_SEARCH_HITS_PER_AREA):
            hits[f"{key}|{row['url']}|{row['title']}"[:400]] = row["title"] or "Ohne Titel"
    return hits


def toggle_saved_search_alert(saved, *, now=None):
    """Switch the alert on or off. Switching on takes today's hits as known, so only later ones count."""
    saved.notify = not saved.notify
    saved.known_hits = list(saved_search_hits(saved))[:MAX_KNOWN_HITS] if saved.notify else []
    saved.checked_at = (now or timezone.now()) if saved.notify else None
    saved.save(update_fields=["notify", "known_hits", "checked_at"])


def check_saved_searches(*, now=None):
    current_time = now or timezone.now()
    due = SavedSearch.objects.filter(notify=True, owner__is_active=True).filter(
        Q(checked_at__isnull=True) | Q(checked_at__lte=current_time - SAVED_SEARCH_CHECK_INTERVAL)
    )
    checked = alerted = 0
    for saved in due.select_related("owner"):
        hits = saved_search_hits(saved)
        known = set(saved.known_hits)
        new_titles = [title for key, title in hits.items() if key not in known]
        if new_titles:
            alerted += materialize_saved_search_alert(
                saved, new_titles, url=search_url(saved.query, saved.area), now=current_time
            )
        # Hits that dropped out are forgotten, so one coming back is reported again.
        saved.known_hits = list(hits)[:MAX_KNOWN_HITS]
        saved.checked_at = current_time
        saved.save(update_fields=["known_hits", "checked_at"])
        checked += 1
    return {"checked": checked, "alerted": alerted}
