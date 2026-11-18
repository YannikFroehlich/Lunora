"""Comments pinned to spots on a whiteboard.

Every member of a board (owner and those it is shared with) can open threads, reply and mark a
thread done or open again; authors delete their own comments, and deleting a thread's first
comment deletes the thread. Each new comment notifies the other members in the inbox.
"""

import math

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from app.models import UserNotification, WhiteboardComment
from app.services.notes import display_name
from app.services.notifications import materialize_whiteboard_comment_notification
from app.services.uml_content import MAX_COORDINATE
from app.services.user_preferences import format_user_datetime

MAX_COMMENT_LENGTH = 2000
MAX_THREADS_PER_BOARD = 200
MAX_COMMENTS_PER_THREAD = 100


def board_members(board):
    return {board.owner_id, *board.shares.values_list("user_id", flat=True)}


def comments_signature(board):
    """Changes whenever a comment is added, deleted, resolved or reopened; the live poll sends it."""
    stats = board.comments.aggregate(
        count=Count("id"), last=Max("id"), done=Count("id", filter=Q(resolved_at__isnull=False))
    )
    return f"{stats['count']}:{stats['last'] or 0}:{stats['done']}"


def comment_threads(board, user):
    comments = list(board.comments.select_related("author__profile"))
    threads = {}
    for comment in comments:
        if comment.parent_id is None:
            threads[comment.pk] = {
                "id": comment.pk,
                "x": comment.x,
                "y": comment.y,
                "resolved": comment.resolved_at is not None,
                "comments": [],
            }
    for comment in comments:
        thread = threads.get(comment.parent_id or comment.pk)
        if thread is None:
            continue
        thread["comments"].append(
            {
                "id": comment.pk,
                "author": display_name(comment.author) if comment.author else "Gelöschtes Konto",
                "author_id": comment.author_id,
                "body": comment.body,
                "created_label": format_user_datetime(comment.created_at, user),
                "can_delete": comment.author_id == user.pk,
            }
        )
    return list(threads.values())


def _clean_body(value):
    body = str(value or "").strip()
    if not body:
        raise ValidationError("Der Kommentar ist leer.")
    if len(body) > MAX_COMMENT_LENGTH:
        raise ValidationError(f"Ein Kommentar darf höchstens {MAX_COMMENT_LENGTH} Zeichen lang sein.")
    return body


def _clean_position(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValidationError("Die Position des Kommentars ist ungültig.")
    return round(max(-MAX_COORDINATE, min(MAX_COORDINATE, float(value))), 1)


def _thread(board, thread_id):
    thread = board.comments.filter(pk=thread_id, parent=None).first() if type(thread_id) is int else None
    if thread is None:
        raise ValidationError("Diesen Kommentar gibt es nicht mehr.")
    return thread


@transaction.atomic
def add_comment(board, user, *, body, x=None, y=None, thread_id=None):
    """A new thread at (x, y), or a reply when `thread_id` names one; notifies the others."""
    body = _clean_body(body)
    if thread_id is None:
        if board.comments.filter(parent=None).count() >= MAX_THREADS_PER_BOARD:
            raise ValidationError(f"Ein Whiteboard hat höchstens {MAX_THREADS_PER_BOARD} Kommentare.")
        comment = WhiteboardComment.objects.create(
            board=board, author=user, body=body, x=_clean_position(x), y=_clean_position(y)
        )
    else:
        thread = _thread(board, thread_id)
        if thread.replies.count() >= MAX_COMMENTS_PER_THREAD:
            raise ValidationError("Dieser Kommentar hat schon zu viele Antworten.")
        comment = WhiteboardComment.objects.create(board=board, parent=thread, author=user, body=body)
        if thread.resolved_at:
            # Answering a done thread opens it again.
            thread.resolved_at = None
            thread.save(update_fields=["resolved_at"])
    materialize_whiteboard_comment_notification(comment, board_members(board) - {user.pk})
    return comment


def set_resolved(board, thread_id, resolved):
    thread = _thread(board, thread_id)
    thread.resolved_at = timezone.now() if resolved else None
    thread.save(update_fields=["resolved_at"])


@transaction.atomic
def delete_comment(board, user, comment_id):
    comment = board.comments.filter(pk=comment_id).first() if type(comment_id) is int else None
    if comment is None:
        raise ValidationError("Diesen Kommentar gibt es nicht mehr.")
    if comment.author_id != user.pk:
        raise PermissionDenied("Du kannst nur eigene Kommentare löschen.")
    ids = [comment.pk, *comment.replies.values_list("pk", flat=True)]
    UserNotification.objects.filter(source_key__in=[f"whiteboard-comment:{pk}" for pk in ids]).delete()
    comment.delete()
