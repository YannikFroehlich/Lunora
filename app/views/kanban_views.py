from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from app.forms import KanbanCardForm
from app.models import KanbanBoard, KanbanCard, KanbanColumn
from app.services.kanban import (
    accessible_boards,
    add_card,
    add_column,
    add_lane,
    assign_card,
    board_grid,
    board_members,
    board_version,
    create_board,
    delete_card,
    delete_column,
    delete_lane,
    end_board_share,
    move_card,
    share_board,
    shift,
    touch_board,
)
from app.services.notes import display_name
from app.services.system_settings import disabled_feature_response, feature_enabled
from app.services.user_preferences import format_user_datetime
from app.views.navigation import posted_id

BOARD_TITLE_MAX_LENGTH = KanbanBoard._meta.get_field("title").max_length
COLUMN_TITLE_MAX_LENGTH = KanbanColumn._meta.get_field("title").max_length
CARD_TITLE_MAX_LENGTH = KanbanCard._meta.get_field("title").max_length


def _posted_title(request, max_length):
    return request.POST.get("title", "").strip()[:max_length]


@login_required
def kanban_boards(request):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools")

    if request.method == "POST" and request.POST.get("form_name") == "kanban_board_create":
        title = _posted_title(request, BOARD_TITLE_MAX_LENGTH)
        if not title:
            django_messages.error(request, "Der Titel darf nicht leer sein.")
            return redirect("kanban_boards")
        board = create_board(request.user, title)
        return redirect("kanban_board", board_id=board.pk)

    boards = (
        accessible_boards(request.user)
        .select_related("owner__profile")
        .annotate(card_count=Count("cards", distinct=True), member_count=Count("shares", distinct=True))
    )
    items = [
        {
            "id": board.pk,
            "title": board.title,
            "card_count": board.card_count,
            "member_count": board.member_count,
            "updated_label": format_user_datetime(board.updated_at, request.user),
            "is_own": board.owner_id == request.user.pk,
            "owner_name": display_name(board.owner),
        }
        for board in boards
    ]
    return render(
        request,
        "app/kanban_boards.html",
        {"active_page": "tools", "boards": items, "title_max_length": BOARD_TITLE_MAX_LENGTH},
    )


def _wip_limit(value):
    value = (value or "").strip()
    return int(value) if value.isdigit() and 0 < int(value) < 1000 else None


def _handle_column_or_lane(request, board, form_name):
    is_column = form_name.startswith("kanban_column_")
    items = board.columns if is_column else board.lanes
    action = form_name.rsplit("_", 1)[1]
    if action == "add":
        title = _posted_title(request, COLUMN_TITLE_MAX_LENGTH)
        if not title:
            return "Der Name darf nicht leer sein."
        return add_column(board, title) if is_column else add_lane(board, title)
    item = items.filter(pk=posted_id(request, "item_id")).first()
    if item is None:
        raise Http404
    if action == "rename":
        title = _posted_title(request, COLUMN_TITLE_MAX_LENGTH)
        if not title:
            return "Der Name darf nicht leer sein."
        item.title = title
        fields = ["title"]
        if is_column:
            item.wip_limit = _wip_limit(request.POST.get("wip_limit"))
            fields.append("wip_limit")
        item.save(update_fields=fields)
    elif action == "move":
        shift(items.all(), item.pk, -1 if request.POST.get("direction") == "back" else 1)
    elif action == "delete":
        return delete_column(board, item.pk) if is_column else delete_lane(board, item.pk)
    return None


def _handle_card(request, board, form_name):
    if form_name == "kanban_card_add":
        column = get_object_or_404(board.columns, pk=posted_id(request, "column_id"))
        lane = get_object_or_404(board.lanes, pk=posted_id(request, "lane_id"))
        title = _posted_title(request, CARD_TITLE_MAX_LENGTH)
        return (
            add_card(board, request.user, column, lane, title) if title else "Der Titel darf nicht leer sein."
        )

    card = get_object_or_404(board.cards, pk=posted_id(request, "card_id"))
    if form_name == "kanban_card_delete":
        delete_card(card)
    elif form_name == "kanban_card_move":
        column = get_object_or_404(board.columns, pk=posted_id(request, "column_id"))
        lane = get_object_or_404(board.lanes, pk=posted_id(request, "lane_id"))
        move_card(card, column, lane, posted_id(request, "before_id"))
    elif form_name == "kanban_card_update":
        cell = (card.column_id, card.lane_id)
        assignee_id = card.assignee_id
        form = KanbanCardForm(request.POST, instance=card, board=board)
        if not form.is_valid():
            return "Bitte prüf Titel, Datum und Auswahl der Karte."
        card = form.save(commit=False)
        assignee = card.assignee
        card.assignee_id = assignee_id
        assign_card(card, assignee, request.user)
        card.save()
        if (card.column_id, card.lane_id) != cell:
            move_card(card, card.column, card.lane)
    return None


def _handle_membership(request, board, form_name):
    is_owner = board.owner_id == request.user.pk
    if form_name == "kanban_leave" and not is_owner:
        end_board_share(board.pk, request.user.pk)
        return redirect("kanban_boards")
    if not is_owner:
        raise Http404
    if form_name == "kanban_delete":
        board.delete()
        django_messages.success(request, "Board gelöscht.")
        return redirect("kanban_boards")
    if form_name == "kanban_share":
        error = share_board(board, request.POST.get("identifier", "")[:254])
        if error:
            django_messages.error(request, error)
        else:
            django_messages.success(request, "Das Board ist jetzt geteilt.")
    elif form_name == "kanban_unshare" and (user_id := posted_id(request, "user_id")) is not None:
        end_board_share(board.pk, user_id)
    touch_board(board)
    return redirect("kanban_board", board_id=board.pk)


@login_required
def kanban_board(request, board_id):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools", json_response="version" in request.GET)
    board = get_object_or_404(accessible_boards(request.user).select_related("owner__profile"), pk=board_id)
    if request.method == "GET" and "version" in request.GET:
        return JsonResponse({"version": board_version(board)}, headers={"Cache-Control": "no-store"})

    if request.method == "POST":
        form_name = request.POST.get("form_name", "")
        if form_name in {"kanban_delete", "kanban_share", "kanban_unshare", "kanban_leave"}:
            return _handle_membership(request, board, form_name)
        error = None
        if form_name == "kanban_rename":
            title = _posted_title(request, BOARD_TITLE_MAX_LENGTH)
            if title:
                KanbanBoard.objects.filter(pk=board.pk).update(title=title)
            else:
                error = "Der Titel darf nicht leer sein."
        elif form_name.startswith(("kanban_column_", "kanban_lane_")):
            error = _handle_column_or_lane(request, board, form_name)
        elif form_name.startswith("kanban_card_"):
            error = _handle_card(request, board, form_name)
        if error:
            django_messages.error(request, error)
        touch_board(board)
        return redirect(reverse("kanban_board", args=[board.pk]))

    members = board_members(board)
    return render(
        request,
        "app/kanban_board.html",
        {
            "active_page": "tools",
            "board": board,
            "grid": board_grid(board, request.user),
            "is_owner": board.owner_id == request.user.pk,
            "owner_name": display_name(board.owner),
            "members": [{"id": user.pk, "name": display_name(user)} for user in members[1:]],
            "is_shared": len(members) > 1,
            "version": board_version(board),
            "card_form": KanbanCardForm(board=board),
            "color_choices": KanbanCard.COLOR_CHOICES,
            "title_max_length": BOARD_TITLE_MAX_LENGTH,
            "column_title_max_length": COLUMN_TITLE_MAX_LENGTH,
            "card_title_max_length": CARD_TITLE_MAX_LENGTH,
        },
    )
