"""Retrospectives: boards, participants (Lunora users and guests), cards, votes and actions.

Guests have no account: joining through the invite link stores their participant id in the
session (``retro_guests``), which is what identifies them on later requests."""

import secrets
from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from app.models import RetroAction, RetroBoard, RetroCard, RetroColumn, RetroParticipant, RetroVote, Task
from app.services.data_import import create_markdown_note
from app.services.notes import display_name

TEMPLATES = {
    "start_stop": (
        "Start / Stop / Weitermachen",
        [("Start", "green"), ("Stop", "red"), ("Weitermachen", "sand")],
    ),
    "good_bad": ("Gut / Schlecht / Ideen", [("Gut", "green"), ("Schlecht", "red"), ("Ideen", "blue")]),
    "mad_sad_glad": ("Wütend / Traurig / Froh", [("Wütend", "red"), ("Traurig", "blue"), ("Froh", "green")]),
    "four_l": (
        "4L",
        [("Gemocht", "green"), ("Gelernt", "blue"), ("Gefehlt", "red"), ("Gewünscht", "violet")],
    ),
    "blank": ("Leer", [("Notizen", "sand")]),
}
PHASES = [key for key, _label in RetroBoard.PHASE_CHOICES]
MAX_PARTICIPANTS = 50
GUEST_NAME_MAX_LENGTH = 40
SESSION_KEY = "retro_guests"


def accessible_retros(user):
    joined = RetroParticipant.objects.filter(user=user).values("board_id")
    return RetroBoard.objects.filter(Q(owner=user) | Q(pk__in=joined))


def touch_board(board):
    RetroBoard.objects.filter(pk=board.pk).update(updated_at=timezone.now())


def board_version(board):
    board.refresh_from_db(fields=["updated_at"])
    return board.updated_at.isoformat()


def new_join_token():
    return secrets.token_urlsafe(32)


def previous_choices(user, exclude=None):
    """The user's own retros a new one can follow up on, newest first. Only own ones, so a board's
    participants never see actions from a retro its owner merely joined."""
    boards = RetroBoard.objects.filter(owner=user).order_by("-created_at", "-id")
    return boards.exclude(pk=exclude.pk) if exclude else boards


@transaction.atomic
def create_retro(user, title, template, previous=None):
    _label, columns = TEMPLATES.get(template, TEMPLATES["start_stop"])
    board = RetroBoard.objects.create(owner=user, title=title, join_token=new_join_token(), previous=previous)
    RetroColumn.objects.bulk_create(
        RetroColumn(board=board, title=name, tone=tone, position=index)
        for index, (name, tone) in enumerate(columns)
    )
    RetroParticipant.objects.create(board=board, user=user)
    return board


# ---- participants ---------------------------------------------------------------------------------


def participant_for(request, board):
    """The participant this request acts as, or None. Signed-in users are matched by account (the
    owner always is one), guests by the id their session remembers for this board."""
    if request.user.is_authenticated:
        if board.owner_id == request.user.pk:
            return join_as_user(board, request.user)
        return board.participants.filter(user=request.user).first()
    participant_id = request.session.get(SESSION_KEY, {}).get(str(board.pk))
    if participant_id is None:
        return None
    return board.participants.filter(pk=participant_id, user=None).first()


def join_as_user(board, user):
    participant = board.participants.filter(user=user).first()
    if participant is None and (board.owner_id == user.pk or board.participants.count() < MAX_PARTICIPANTS):
        participant, _created = RetroParticipant.objects.get_or_create(board=board, user=user)
    return participant


def join_as_guest(request, board, name):
    """Returns (participant, error). Same names get a number, so "Tom" and "Tom (2)" stay apart."""
    name = " ".join(name.split())[:GUEST_NAME_MAX_LENGTH]
    if not name:
        return None, "Bitte gib einen Namen ein."
    if board.participants.count() >= MAX_PARTICIPANTS:
        return None, "Diese Retro ist voll."
    taken = {
        participant_name(participant) for participant in board.participants.select_related("user__profile")
    }
    unique, number = name, 2
    while unique in taken:
        unique, number = f"{name} ({number})", number + 1
    participant = RetroParticipant.objects.create(board=board, guest_name=unique)
    guests = request.session.get(SESSION_KEY, {})
    guests[str(board.pk)] = participant.pk
    request.session[SESSION_KEY] = guests
    return participant, None


def participant_name(participant):
    return display_name(participant.user) if participant.user_id else participant.guest_name


def initials(name):
    parts = name.split()
    return "".join(part[0] for part in parts[:2]).upper() or "?"


# ---- cards ------------------------------------------------------------------------------------------


def cards_left(board, participant):
    if not board.max_cards:
        return None
    return max(board.max_cards - board.cards.filter(author=participant).count(), 0)


def add_card(board, participant, column, text):
    if board.phase != RetroBoard.PHASE_COLLECT:
        return "Karten kommen nur beim Sammeln dazu."
    if cards_left(board, participant) == 0:
        return "Du hast alle deine Karten geschrieben."
    RetroCard.objects.create(board=board, column=column, author=participant, text=text)
    return None


def can_change_card(board, participant, card):
    """Authors change their own cards; the owner others' only once they are revealed, so a hidden
    card is never overwritten unseen. Which phase allows changes is the caller's check."""
    if card.author_id == participant.pk:
        return True
    hidden = board.phase == RetroBoard.PHASE_COLLECT and board.hide_cards
    return board.owner_id == participant.user_id and not hidden


@transaction.atomic
def group_card(card, target):
    """Stack ``card`` (and whatever was stacked under it) under ``target``'s group; their votes move
    to the group's top card, which is what gets voted on."""
    root = target.group or target
    if root.pk == card.pk:
        return
    members = RetroCard.objects.filter(Q(pk=card.pk) | Q(group=card))
    RetroVote.objects.filter(card__in=members).update(card=root)
    members.update(group=root)


@transaction.atomic
def ungroup_card(card):
    """Takes a card out of its group; a group's top card hands the group to its oldest member."""
    if card.group_id:
        RetroCard.objects.filter(pk=card.pk).update(group=None)
        return
    members = list(card.grouped_cards.order_by("created_at", "id"))
    if members:
        new_root, rest = members[0], members[1:]
        RetroCard.objects.filter(pk__in=[member.pk for member in rest]).update(group=new_root)
        RetroCard.objects.filter(pk=new_root.pk).update(group=None)
        RetroVote.objects.filter(card=card).update(card=new_root)


# ---- votes --------------------------------------------------------------------------------------------


def votes_left(board, participant):
    return max(board.votes_per_person - participant.votes.filter(card__board=board).count(), 0)


@transaction.atomic
def vote(board, participant, card, delta):
    RetroParticipant.objects.select_for_update().filter(pk=participant.pk).first()
    if board.phase != RetroBoard.PHASE_VOTE:
        return "Abstimmen geht nur in der Abstimmungsphase."
    card = card.group or card
    own = participant.votes.filter(card=card)
    if delta < 0:
        first = own.first()
        if first:
            first.delete()
        return None
    if votes_left(board, participant) == 0:
        return "Du hast alle Stimmen vergeben."
    if own.count() >= board.votes_per_card:
        return "Mehr Stimmen gehen auf diese Karte nicht."
    RetroVote.objects.create(card=card, participant=participant)
    return None


# ---- owner actions ------------------------------------------------------------------------------------


def set_phase(board, phase):
    if phase in PHASES:
        board.phase = phase
        board.presented_card = None
        board.timer_ends_at = None
        board.save(update_fields=["phase", "presented_card", "timer_ends_at", "updated_at"])


def start_timer(board, minutes):
    board.timer_minutes = minutes
    board.timer_ends_at = timezone.now() + timedelta(minutes=minutes)
    board.save(update_fields=["timer_minutes", "timer_ends_at", "updated_at"])


def stop_timer(board):
    board.timer_ends_at = None
    board.save(update_fields=["timer_ends_at", "updated_at"])


def present_card(board, card):
    board.presented_card = (card.group or card) if card else None
    board.save(update_fields=["presented_card", "updated_at"])


def renew_join_token(board):
    board.join_token = new_join_token()
    board.save(update_fields=["join_token", "updated_at"])


# ---- actions -----------------------------------------------------------------------------------------


def take_action_as_task(action, user):
    """A Lunora user turns an action into their own task; it then counts as theirs."""
    task = Task.objects.create(
        user=user,
        title=action.title[: Task._meta.get_field("title").max_length],
        description=action.description,
    )
    action.task = task
    if action.assignee_id is None:
        action.assignee = action.board.participants.filter(user=user).first()
    action.save(update_fields=["task", "assignee"])
    return task


def reviewable_actions(board):
    """Actions a board's participants may tick off: its own and those of the retro it follows up on."""
    return RetroAction.objects.filter(board_id__in=[board.pk, board.previous_id]).select_related("board")


def set_action_done(action, done):
    """An action taken over as a task gets its state from the task, so only the others change."""
    if action.task_id:
        return "Der Stand kommt aus der Aufgabe."
    action.done = done
    action.save(update_fields=["done"])
    touch_board(action.board)
    return None


def action_done(action):
    return action.task.is_done if action.task_id else action.done


def save_as_note(board, user):
    topics = ranked_topics(board)
    lines = [f"# Retro: {board.title}", ""]
    for column in board.columns.all():
        lines.append(f"## {column.title}")
        for topic in (topic for topic in topics if topic["column_id"] == column.pk):
            votes = f" ({topic['votes']} Stimmen)" if topic["votes"] else ""
            lines.append(f"- {topic['text']}{votes}")
            lines.extend(f"  - {child}" for child in topic["children"])
        lines.append("")
    actions = list(board.actions.select_related("assignee__user__profile", "task"))
    if actions:
        lines.append("## Actions")
        for action in actions:
            mark = "x" if action_done(action) else " "
            owner = f" ({participant_name(action.assignee)})" if action.assignee else ""
            details = f" – {' '.join(action.description.split())}" if action.description else ""
            lines.append(f"- [{mark}] {action.title}{owner}{details}")
    return create_markdown_note(user, "\n".join(lines), f"Retro: {board.title}")


# ---- view model --------------------------------------------------------------------------------------


def ranked_topics(board):
    """Top-level cards with their grouped cards' texts and vote totals, most votes first."""
    cards = list(board.cards.annotate(vote_count=Count("votes")).order_by("created_at", "id"))
    children = defaultdict(list)
    for card in cards:
        if card.group_id:
            children[card.group_id].append(card.text)
    topics = [
        {
            "id": card.pk,
            "text": card.text,
            "children": children[card.pk],
            "votes": card.vote_count,
            "column_id": card.column_id,
        }
        for card in cards
        if not card.group_id
    ]
    topics.sort(key=lambda topic: -topic["votes"])
    return topics


def board_context(board, participant):
    is_owner = board.owner_id == participant.user_id
    phase = board.phase
    revealed = phase != RetroBoard.PHASE_COLLECT or not board.hide_cards
    votes_shown = phase in {RetroBoard.PHASE_DISCUSS, RetroBoard.PHASE_DONE} or not board.hide_votes
    participants = list(board.participants.select_related("user__profile"))
    names = {item.pk: participant_name(item) for item in participants}
    cards = list(board.cards.annotate(vote_count=Count("votes")).order_by("created_at", "id"))
    my_votes = defaultdict(int)
    for card_id in participant.votes.filter(card__board=board).values_list("card_id", flat=True):
        my_votes[card_id] += 1
    # Who voted how often per card; only named once votes are shown and the board isn't anonymous.
    voters = defaultdict(lambda: defaultdict(int))
    vote_counts = defaultdict(int)
    for card_id, voter_id in RetroVote.objects.filter(card__board=board).values_list(
        "card_id", "participant_id"
    ):
        voters[card_id][voter_id] += 1
        vote_counts[voter_id] += 1
    show_voters = votes_shown and not board.anonymous
    show_authors = phase != RetroBoard.PHASE_COLLECT and not board.anonymous
    children = defaultdict(list)
    for card in cards:
        if card.group_id:
            children[card.group_id].append(card)

    def card_item(card):
        own = card.author_id == participant.pk
        visible = own or revealed
        return {
            "id": card.pk,
            "text": card.text if visible else "",
            "hidden": not visible,
            "own": own,
            "can_change": can_change_card(board, participant, card),
            # Authors show from grouping on, and never on anonymous boards.
            "author": names.get(card.author_id, "") if show_authors else "",
            "votes": card.vote_count if votes_shown else None,
            "my_votes": my_votes[card.pk],
            "voters": ", ".join(
                f"{names.get(voter_id, '?')} ×{count}" if count > 1 else names.get(voter_id, "?")
                for voter_id, count in voters[card.pk].items()
            )
            if show_voters
            else "",
            "children": [card_item(child) for child in children[card.pk]],
            "column_id": card.column_id,
        }

    tops = [card_item(card) for card in cards if not card.group_id]
    columns = []
    for column in board.columns.all():
        column_cards = [item for item in tops if item["column_id"] == column.pk]
        if votes_shown and phase in {RetroBoard.PHASE_DISCUSS, RetroBoard.PHASE_DONE}:
            column_cards.sort(key=lambda item: -item["votes"])
        columns.append({"id": column.pk, "title": column.title, "tone": column.tone, "cards": column_cards})

    ranked = sorted(tops, key=lambda item: -(item["votes"] or 0)) if votes_shown else tops
    presented = None
    if board.presented_card_id and phase == RetroBoard.PHASE_DISCUSS:
        order = [item["id"] for item in ranked]
        if board.presented_card_id in order:
            index = order.index(board.presented_card_id)
            column = next(column for column in columns if column["id"] == ranked[index]["column_id"])
            presented = {
                **ranked[index],
                "column": column["title"],
                "column_tone": column["tone"],
                "number": index + 1,
                "total": len(order),
                "previous_id": order[index - 1] if index > 0 else None,
                "next_id": order[index + 1] if index + 1 < len(order) else None,
            }
    card_counts = defaultdict(int)
    for card in cards:
        card_counts[card.author_id] += 1
    actions = [
        {
            "id": action.pk,
            "title": action.title,
            "description": action.description,
            "assignee_id": action.assignee_id,
            "assignee": names.get(action.assignee_id, ""),
            "has_task": action.task_id is not None,
            "done": action_done(action),
            "can_take": (
                participant.user_id is not None
                and action.task_id is None
                and action.assignee_id in {None, participant.pk}
            ),
        }
        for action in board.actions.select_related("task")
    ]
    # Reviewed while collecting: what the retro this one follows up on decided.
    previous_actions = None
    if board.previous_id and phase == RetroBoard.PHASE_COLLECT:
        previous_actions = [
            {
                "id": action.pk,
                "title": action.title,
                "description": action.description,
                "assignee": participant_name(action.assignee) if action.assignee else "",
                "has_task": action.task_id is not None,
                "done": action_done(action),
            }
            for action in board.previous.actions.select_related("task", "assignee__user__profile")
        ]
    left_cards = cards_left(board, participant)
    left_votes = votes_left(board, participant)
    return {
        "board": board,
        "is_owner": is_owner,
        "me": participant,
        "phases": [
            {
                "key": key,
                "label": label,
                "current": key == phase,
                "done": PHASES.index(key) < PHASES.index(phase),
            }
            for key, label in RetroBoard.PHASE_CHOICES
        ],
        "next_phase": PHASES[PHASES.index(phase) + 1] if phase != RetroBoard.PHASE_DONE else None,
        "columns": columns,
        "ranked": ranked,
        "top_three": ranked[:3] if votes_shown else [],
        "presented": presented,
        "cards_left": left_cards,
        "votes_left": left_votes,
        "participants": [
            {
                "id": item.pk,
                "name": names[item.pk],
                "initials": initials(names[item.pk]),
                "is_guest": item.user_id is None,
                "is_me": item.pk == participant.pk,
                # Progress without content: how many cards someone wrote and votes they gave.
                "cards": card_counts[item.pk],
                "votes": vote_counts[item.pk],
            }
            for item in participants
        ],
        "actions": actions,
        "previous_actions": previous_actions,
        "card_count": len(cards),
        "vote_count": sum(vote_counts.values()),
        "topic_count": len(tops),
    }
