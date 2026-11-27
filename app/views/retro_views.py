from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.db.models import Count
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from app.forms import RetroSettingsForm
from app.models import RetroAction, RetroBoard, RetroCard, RetroColumn
from app.services import retro
from app.services.audit_log import client_ip
from app.services.notes import display_name
from app.services.system_settings import disabled_feature_response, feature_enabled
from app.services.throttle import count_hit
from app.services.user_preferences import format_user_datetime
from app.views.navigation import posted_id

TITLE_MAX_LENGTH = RetroBoard._meta.get_field("title").max_length
CARD_MAX_LENGTH = RetroCard._meta.get_field("text").max_length
COLUMN_MAX_LENGTH = RetroColumn._meta.get_field("title").max_length
ACTION_MAX_LENGTH = RetroAction._meta.get_field("title").max_length
ACTION_DESCRIPTION_MAX_LENGTH = RetroAction._meta.get_field("description").max_length
# Per participant and minute; generous for clicking votes, tight enough to stop a script.
ACTIONS_PER_MINUTE = 120
JOINS_PER_HOUR = 30
OWNER_FORMS = {
    "retro_phase",
    "retro_settings",
    "retro_timer_start",
    "retro_timer_extend",
    "retro_timer_stop",
    "retro_present",
    "retro_discussed",
    "retro_column_add",
    "retro_column_edit",
    "retro_column_move",
    "retro_column_delete",
    "retro_participants_remove",
    "retro_hand_over",
    "retro_link_renew",
    "retro_duplicate",
    "retro_delete",
    "retro_note",
}


def _private(response):
    # The invite token and guest pages stay out of referrers, search engines and shared caches.
    response["Referrer-Policy"] = "same-origin"
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Cache-Control"] = "private, no-cache"
    return response


def _text(request, name, max_length):
    return " ".join(request.POST.get(name, "").split())[:max_length]


def _multiline(request, name, max_length):
    """Like `_text`, but keeps line breaks."""
    lines = (" ".join(line.split()) for line in request.POST.get(name, "").splitlines())
    return "\n".join(lines).strip()[:max_length]


def _action_fields(request, board):
    """(title, description, assignee) of an action form; the assignee must be on the board."""
    return (
        _text(request, "title", ACTION_MAX_LENGTH),
        _multiline(request, "description", ACTION_DESCRIPTION_MAX_LENGTH),
        board.participants.filter(pk=posted_id(request, "assignee_id")).first(),
    )


@login_required
def retro_boards(request):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools")

    if request.method == "POST" and request.POST.get("form_name") == "retro_create":
        title = _text(request, "title", TITLE_MAX_LENGTH)
        if not title:
            django_messages.error(request, "Der Titel darf nicht leer sein.")
            return redirect("retro_boards")
        previous = retro.previous_choices(request.user).filter(pk=posted_id(request, "previous_id")).first()
        board = retro.create_retro(request.user, title, request.POST.get("template", ""), previous)
        return redirect("retro_board", board_id=board.pk)

    boards = (
        retro.accessible_retros(request.user)
        .select_related("owner__profile")
        .annotate(
            card_count=Count("cards", distinct=True), participant_count=Count("participants", distinct=True)
        )
    )
    items = [
        {
            "id": board.pk,
            "title": board.title,
            "phase": board.get_phase_display(),
            "is_done": board.phase == RetroBoard.PHASE_DONE,
            "card_count": board.card_count,
            "participant_count": board.participant_count,
            "updated_label": format_user_datetime(board.updated_at, request.user),
            "is_own": board.owner_id == request.user.pk,
            "owner_name": display_name(board.owner),
        }
        for board in boards
    ]
    # Status chips; every count comes from the same list, so they always add up.
    status = request.GET.get("status", "")
    filters = [
        ("", "Alle", items),
        ("open", "Laufend", [item for item in items if not item["is_done"]]),
        ("done", "Abgeschlossen", [item for item in items if item["is_done"]]),
        ("others", "Von anderen", [item for item in items if not item["is_own"]]),
    ]
    shown = next((matches for key, _label, matches in filters if key == status), items)
    return render(
        request,
        "app/retro_boards.html",
        {
            "active_page": "tools",
            "boards": shown,
            "has_boards": bool(items),
            "status": status if any(key == status for key, _label, _matches in filters) else "",
            "status_filters": [
                {"key": key, "label": label, "count": len(matches)} for key, label, matches in filters
            ],
            "templates": [
                {"key": key, "label": label, "columns": [name for name, _tone in columns]}
                for key, (label, columns) in retro.TEMPLATES.items()
            ],
            "title_max_length": TITLE_MAX_LENGTH,
            # Preselects the newest, since retros usually follow up on the last one.
            "previous_choices": list(retro.previous_choices(request.user).values("id", "title")),
        },
    )


@require_http_methods(["GET", "POST"])
def retro_join(request, token):
    """The invite link: signed-in users join with their account, guests with a name."""
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools")
    board = RetroBoard.objects.filter(join_token=token, join_enabled=True).first()
    if board is None:
        raise Http404
    if request.user.is_authenticated:
        if retro.join_as_user(board, request.user) is None:
            django_messages.error(request, "Diese Retro ist voll.")
            return redirect("retro_boards")
        return redirect("retro_board", board_id=board.pk)
    if retro.participant_for(request, board):
        return redirect("retro_board", board_id=board.pk)

    # Errors answer 200: Django logs 4xx answers with their path, and the path holds the token.
    error = None
    if request.method == "POST":
        if count_hit(f"retro-join:{client_ip(request)}", 3600) > JOINS_PER_HOUR:
            error = "Zu viele Versuche. Bitte warte etwas."
        else:
            participant, error = retro.join_as_guest(request, board, request.POST.get("name", ""))
            if participant:
                return redirect("retro_board", board_id=board.pk)
    response = render(
        request,
        "app/retro_join.html",
        {
            "board": board,
            "error": error,
            "name": request.POST.get("name", "")[: retro.GUEST_NAME_MAX_LENGTH],
            "name_max_length": retro.GUEST_NAME_MAX_LENGTH,
            "login_url": f"{reverse('login')}?next={request.path}",
        },
    )
    return _private(response)


def _handle_owner(request, board, form_name):
    """Returns an error text or a response that replaces the usual redirect."""
    if form_name == "retro_phase":
        retro.set_phase(board, request.POST.get("phase", ""))
    elif form_name == "retro_settings":
        form = RetroSettingsForm(request.POST, instance=board)
        if not form.is_valid():
            return "Bitte prüf die Einstellungen."
        form.save()
    elif form_name == "retro_timer_start":
        minutes = posted_id(request, "minutes") or board.timer_minutes
        retro.start_timer(board, min(max(minutes, 1), retro.MAX_TIMER_MINUTES))
    elif form_name == "retro_timer_extend":
        retro.extend_timer(board)
    elif form_name == "retro_timer_stop":
        retro.stop_timer(board)
    elif form_name == "retro_present":
        card_id = posted_id(request, "card_id")
        card = board.cards.filter(pk=card_id).first() if card_id else None
        if board.phase == RetroBoard.PHASE_DISCUSS:
            retro.present_card(board, card)
    elif form_name == "retro_discussed":
        card = get_object_or_404(board.cards, pk=posted_id(request, "card_id"))
        if board.phase != RetroBoard.PHASE_DISCUSS:
            return "Abhaken geht nur beim Besprechen."
        retro.set_discussed(card, request.POST.get("discussed") == "1")
    elif form_name.startswith("retro_column_"):
        column = get_object_or_404(board.columns, pk=posted_id(request, "column_id"))
        if form_name == "retro_column_move":
            retro.move_column(board, column, -1 if request.POST.get("direction") == "left" else 1)
        elif form_name == "retro_column_delete":
            return retro.delete_column(board, column)
        else:
            title = _text(request, "title", COLUMN_MAX_LENGTH)
            if not title:
                return "Die Spalte braucht einen Namen."
            tone = request.POST.get("tone", "")
            if form_name == "retro_column_add":
                return retro.add_column(board, column, title, tone)
            retro.edit_column(column, title, tone)
    elif form_name == "retro_participants_remove":
        ids = [int(value) for value in request.POST.getlist("participant_id") if value.isdigit()]
        removed = retro.remove_participants(board, ids)
        if removed:
            django_messages.success(request, f"{removed} Teilnehmende entfernt.")
    elif form_name == "retro_hand_over":
        new_owner = get_object_or_404(board.participants, pk=posted_id(request, "participant_id"))
        error = retro.hand_over(board, new_owner)
        if error:
            return error
        django_messages.success(
            request, f"Die Moderation liegt jetzt bei {retro.participant_name(new_owner)}."
        )
    elif form_name == "retro_link_renew":
        retro.renew_join_token(board)
        django_messages.success(request, "Der alte Einladungslink gilt nicht mehr.")
    elif form_name == "retro_duplicate":
        title = _text(request, "title", TITLE_MAX_LENGTH) or retro.next_title(board.title)
        copy = retro.duplicate_retro(board, title)
        return redirect("retro_board", board_id=copy.pk)
    elif form_name == "retro_delete":
        board.delete()
        django_messages.success(request, "Retro gelöscht.")
        return redirect("retro_boards")
    elif form_name == "retro_note":
        if not feature_enabled("notes"):
            return "Notizen sind gerade abgeschaltet."
        note = retro.save_as_note(board, request.user)
        return redirect("note_detail", note_id=note.id)
    return None


def _handle_participant(request, board, participant, form_name):
    """Returns an error text or None."""
    if form_name == "retro_card_add":
        column = get_object_or_404(board.columns, pk=posted_id(request, "column_id"))
        text = _text(request, "text", CARD_MAX_LENGTH)
        return retro.add_card(board, participant, column, text) if text else "Die Karte ist leer."
    if form_name == "retro_action_add":
        title, description, assignee = _action_fields(request, board)
        if not title:
            return "Die Action braucht einen Titel."
        RetroAction.objects.create(board=board, title=title, description=description, assignee=assignee)
        return None
    if form_name == "retro_rate":
        return retro.rate(board, participant, posted_id(request, "score"))
    if form_name == "retro_action_done":
        action = get_object_or_404(retro.reviewable_actions(board), pk=posted_id(request, "action_id"))
        return retro.set_action_done(action, request.POST.get("done") == "1")
    if form_name in {"retro_action_edit", "retro_action_delete", "retro_action_task"}:
        action = get_object_or_404(board.actions, pk=posted_id(request, "action_id"))
        if form_name == "retro_action_edit":
            title, description, assignee = _action_fields(request, board)
            if not title:
                return "Die Action braucht einen Titel."
            action.title, action.description, action.assignee = title, description, assignee
            action.save(update_fields=["title", "description", "assignee"])
        elif form_name == "retro_action_delete":
            action.delete()
        elif participant.user_id and action.task_id is None and action.assignee_id in {None, participant.pk}:
            if not feature_enabled("tasks"):
                return "Aufgaben sind gerade abgeschaltet."
            retro.take_action_as_task(action, request.user)
            django_messages.success(request, "Als Aufgabe übernommen.")
        return None

    card = get_object_or_404(board.cards.select_related("group"), pk=posted_id(request, "card_id"))
    if form_name == "retro_vote":
        return retro.vote(board, participant, card, -1 if request.POST.get("direction") == "down" else 1)
    # Phases are checked here, not just hidden in the template: a stale page or a hand-made POST
    # must not move votes between groups or change a card people already voted on.
    if form_name in {"retro_group", "retro_ungroup"}:
        if board.phase != RetroBoard.PHASE_GROUP:
            return "Gruppieren geht nur in der Gruppierphase."
        if form_name == "retro_group":
            target = get_object_or_404(
                board.cards.select_related("group"), pk=posted_id(request, "target_id")
            )
            retro.group_card(card, target)
        else:
            retro.ungroup_card(card)
        return None
    if board.phase != RetroBoard.PHASE_COLLECT:
        return "Karten lassen sich nur beim Sammeln ändern."
    if not retro.can_change_card(board, participant, card):
        raise Http404
    if form_name == "retro_card_edit":
        text = _text(request, "text", CARD_MAX_LENGTH)
        if not text:
            return "Die Karte ist leer."
        card.text = text
        card.save(update_fields=["text"])
    elif form_name == "retro_card_delete":
        card.delete()
    return None


@require_http_methods(["GET", "POST"])
def retro_board(request, board_id):
    """Open to the board's participants, guests included; who they are comes from participant_for."""
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools", json_response="version" in request.GET)
    board = RetroBoard.objects.filter(pk=board_id).first()
    participant = retro.participant_for(request, board) if board else None
    if participant is None:
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        raise Http404
    if request.method == "GET" and "version" in request.GET:
        return JsonResponse({"version": retro.board_version(board)}, headers={"Cache-Control": "no-store"})

    if request.method == "POST":
        form_name = request.POST.get("form_name", "")
        if count_hit(f"retro-post:{participant.pk}", 60) > ACTIONS_PER_MINUTE:
            django_messages.error(request, "Zu viele Aktionen. Bitte warte kurz.")
            return redirect("retro_board", board_id=board.pk)
        if form_name in OWNER_FORMS:
            if board.owner_id != participant.user_id:
                raise Http404
            result = _handle_owner(request, board, form_name)
        else:
            result = _handle_participant(request, board, participant, form_name)
        if result is not None and not isinstance(result, str):
            return result
        if result:
            django_messages.error(request, result)
        if RetroBoard.objects.filter(pk=board.pk).exists():
            retro.touch_board(board)
        return redirect("retro_board", board_id=board.pk)

    context = retro.board_context(board, participant)
    context.update(
        {
            "active_page": "tools",
            "version": retro.board_version(board),
            "card_max_length": CARD_MAX_LENGTH,
            "column_max_length": COLUMN_MAX_LENGTH,
            "max_columns": retro.MAX_COLUMNS,
            "tone_choices": RetroColumn.TONE_CHOICES,
            "action_max_length": ACTION_MAX_LENGTH,
            "action_description_max_length": ACTION_DESCRIPTION_MAX_LENGTH,
            "is_guest": participant.user_id is None,
        }
    )
    if context["is_owner"]:
        context["settings_form"] = RetroSettingsForm(instance=board)
        context["join_url"] = request.build_absolute_uri(reverse("retro_join", args=[board.join_token]))
        context["next_title"] = retro.next_title(board.title)
        context["title_max_length"] = TITLE_MAX_LENGTH
    return _private(render(request, "app/retro_board.html", context))
