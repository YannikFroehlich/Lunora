"""Free kanban boards: own columns, swimlanes (rows across the columns) and cards.

The owner and everyone the board is shared with have the same rights on columns, lanes and cards;
only the owner shares, unshares and deletes the board. Every change bumps the board's
``updated_at`` (`touch_board`), which shared pages poll to reload when someone else changed it.
"""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from app.models import (
    Conversation,
    KanbanBoard,
    KanbanBoardShare,
    KanbanCard,
    KanbanColumn,
    KanbanLane,
    UserNotification,
)
from app.services.notes import display_name
from app.services.notifications import (
    materialize_kanban_assignment_notification,
    materialize_kanban_share_notification,
)
from app.services.user_preferences import format_user_date, localtime_for_user
from app.services.vocabulary import resolve_share_recipient

DEFAULT_COLUMNS = ("Offen", "In Arbeit", "Erledigt")
DEFAULT_LANE = "Allgemein"
MAX_COLUMNS = 12
MAX_LANES = 10
MAX_CARDS = 500
MAX_KANBAN_SHARES = 10


def accessible_boards(user):
    """Own boards plus those shared with `user`; a subquery, so no duplicates."""
    shared_ids = KanbanBoardShare.objects.filter(user=user).values("board_id")
    return KanbanBoard.objects.filter(Q(owner=user) | Q(pk__in=shared_ids, owner__is_active=True))


def board_member_ids(board):
    return {board.owner_id, *board.shares.values_list("user_id", flat=True)}


def touch_board(board):
    # .update() so a bump never overwrites a title someone else saved meanwhile.
    board.updated_at = timezone.now()
    KanbanBoard.objects.filter(pk=board.pk).update(updated_at=board.updated_at)


def board_version(board):
    return str(board.updated_at.timestamp())


@transaction.atomic
def create_board(user, title):
    board = KanbanBoard.objects.create(owner=user, title=title)
    KanbanColumn.objects.bulk_create(
        KanbanColumn(board=board, title=name, position=index) for index, name in enumerate(DEFAULT_COLUMNS)
    )
    KanbanLane.objects.create(board=board, title=DEFAULT_LANE, position=0)
    return board


def _reindex(items):
    for index, item in enumerate(items):
        item.position = index
    if items:
        type(items[0]).objects.bulk_update(items, ["position"])


def shift(items, item_id, step):
    """Moves the item one place to the left (-1) or right (+1) among `items` and renumbers them."""
    items = list(items)
    index = next((i for i, item in enumerate(items) if item.pk == item_id), None)
    target = None if index is None else index + step
    if target is None or not 0 <= target < len(items):
        return False
    items[index], items[target] = items[target], items[index]
    _reindex(items)
    return True


def add_column(board, title):
    if board.columns.count() >= MAX_COLUMNS:
        return f"Ein Board hat höchstens {MAX_COLUMNS} Spalten."
    KanbanColumn.objects.create(board=board, title=title, position=board.columns.count())
    return None


def add_lane(board, title):
    if board.lanes.count() >= MAX_LANES:
        return f"Ein Board hat höchstens {MAX_LANES} Swimlanes."
    KanbanLane.objects.create(board=board, title=title, position=board.lanes.count())
    return None


def delete_column(board, column_id):
    if board.columns.count() <= 1:
        return "Die letzte Spalte lässt sich nicht löschen."
    board.columns.filter(pk=column_id).delete()
    _reindex(list(board.columns.all()))
    return None


def delete_lane(board, lane_id):
    if board.lanes.count() <= 1:
        return "Die letzte Swimlane lässt sich nicht löschen."
    board.lanes.filter(pk=lane_id).delete()
    _reindex(list(board.lanes.all()))
    return None


def add_card(board, user, column, lane, title):
    if board.cards.count() >= MAX_CARDS:
        return f"Ein Board hat höchstens {MAX_CARDS} Karten."
    last = board.cards.filter(column=column, lane=lane).order_by("-position").first()
    KanbanCard.objects.create(
        board=board,
        column=column,
        lane=lane,
        title=title,
        created_by=user,
        position=last.position + 1 if last else 0,
    )
    return None


@transaction.atomic
def move_card(card, column, lane, before_id=None):
    """Puts the card into the column/lane cell, in front of card `before_id` or at its end."""
    cell = list(card.board.cards.filter(column=column, lane=lane).exclude(pk=card.pk).select_for_update())
    index = next((i for i, other in enumerate(cell) if other.pk == before_id), len(cell))
    cell.insert(index, card)
    card.column = column
    card.lane = lane
    card.save(update_fields=["column", "lane", "updated_at"])
    _reindex(cell)


def assign_card(card, assignee, actor):
    """Stores the assignee; a new one other than `actor` gets an inbox row, the old one's goes."""
    previous_id = card.assignee_id
    card.assignee = assignee
    if previous_id == card.assignee_id:
        return
    UserNotification.objects.filter(
        kind=UserNotification.KIND_KANBAN_ASSIGNMENT, source_key__startswith=f"kanban-card:{card.pk}:"
    ).delete()
    if assignee is not None and assignee.pk != actor.pk:
        materialize_kanban_assignment_notification(card, actor)


def delete_card(card):
    UserNotification.objects.filter(
        kind=UserNotification.KIND_KANBAN_ASSIGNMENT, source_key__startswith=f"kanban-card:{card.pk}:"
    ).delete()
    card.delete()


def share_board(board, identifier):
    """Shares the owner's board with the user named by e-mail or username; returns an error or None."""
    user, error = resolve_share_recipient(board.owner, identifier)
    if error:
        return error
    if board.shares.count() >= MAX_KANBAN_SHARES:
        return f"Ein Board lässt sich mit höchstens {MAX_KANBAN_SHARES} Personen teilen."
    share, created = KanbanBoardShare.objects.get_or_create(board=board, user=user)
    if not created:
        return "Das Board ist mit diesem Konto bereits geteilt."
    materialize_kanban_share_notification(share)
    return None


def end_board_share(board_id, user_id):
    """Ends a share (owner removes a member, or a member leaves): their cards lose the assignee."""
    for share in KanbanBoardShare.objects.filter(board_id=board_id, user_id=user_id):
        cards = KanbanCard.objects.filter(board_id=board_id, assignee_id=user_id)
        keys = [
            f"kanban-share:{share.pk}",
            *(f"kanban-card:{pk}:{user_id}" for pk in cards.values_list("pk", flat=True)),
        ]
        UserNotification.objects.filter(recipient_id=user_id, source_key__in=keys).delete()
        cards.update(assignee=None)
        share.delete()


def board_members(board):
    """Owner first, then the members in the order they were added."""
    shares = board.shares.select_related("user__profile")
    return [board.owner, *(share.user for share in shares)]


def board_grid(board, user):
    """Columns and lanes with the cards of every cell, ready for the template."""
    columns = list(board.columns.all())
    lanes = list(board.lanes.all())
    cards = list(board.cards.select_related("assignee__profile"))
    today = localtime_for_user(timezone.now(), user).date()
    # The last column is where finished cards go, so nothing there counts as overdue.
    done_column_id = columns[-1].pk if columns else None
    cells = {}
    counts = {column.pk: 0 for column in columns}
    for card in cards:
        card.due_label = format_user_date(card.due_date, user) if card.due_date else ""
        card.is_overdue = bool(card.due_date and card.due_date < today and card.column_id != done_column_id)
        card.assignee_name = display_name(card.assignee) if card.assignee else ""
        card.assignee_initials = Conversation.initials_for_user(card.assignee) if card.assignee else ""
        cells.setdefault((card.lane_id, card.column_id), []).append(card)
        counts[card.column_id] = counts.get(card.column_id, 0) + 1
    for column in columns:
        column.card_count = counts[column.pk]
        column.over_limit = column.wip_limit is not None and column.card_count > column.wip_limit
    return {
        "columns": columns,
        "lanes": [
            {
                "lane": lane,
                "cells": [
                    {"column": column, "cards": cells.get((lane.pk, column.pk), [])} for column in columns
                ],
                "card_count": sum(len(cells.get((lane.pk, column.pk), [])) for column in columns),
            }
            for lane in lanes
        ],
        "card_count": len(cards),
    }
