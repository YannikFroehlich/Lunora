"""Whiteboard access, sharing and conflict-checked saves.

The owner and everyone the board is shared with have the same drawing rights; only the owner
shares, unshares, and deletes. Saves are checked against `Whiteboard.revision`, so two people
drawing at once never silently overwrite each other.
"""

from django.db import transaction
from django.db.models import Q

from app.models import UserNotification, Whiteboard, WhiteboardShare
from app.services.notifications import materialize_whiteboard_share_notification
from app.services.vocabulary import resolve_share_recipient

MAX_WHITEBOARD_SHARES = 10


class WhiteboardConflictError(Exception):
    def __init__(self, board):
        super().__init__("revision_conflict")
        self.board = board


def accessible_whiteboards(user):
    """Own boards plus those shared with `user`; a subquery, so no duplicates."""
    shared_ids = WhiteboardShare.objects.filter(user=user).values("board_id")
    return Whiteboard.objects.filter(Q(owner=user) | Q(pk__in=shared_ids, owner__is_active=True))


def save_whiteboard(board_id, user, *, title, document, base_revision, force=False):
    """Store a validated title/document; raises WhiteboardConflictError on a stale base revision."""
    with transaction.atomic():
        board = accessible_whiteboards(user).select_for_update().filter(pk=board_id).first()
        if board is None:
            return None
        if not force and base_revision != board.revision:
            raise WhiteboardConflictError(board)
        board.title = title
        board.document = document
        board.revision += 1
        board.save(update_fields=["title", "document", "revision", "updated_at"])
        return board


def share_whiteboard(board, identifier):
    """Shares the owner's board with the user named by e-mail or username; returns an error or None."""
    user, error = resolve_share_recipient(board.owner, identifier)
    if error:
        return error
    if board.shares.count() >= MAX_WHITEBOARD_SHARES:
        return f"Ein Whiteboard lässt sich mit höchstens {MAX_WHITEBOARD_SHARES} Personen teilen."
    share, created = WhiteboardShare.objects.get_or_create(board=board, user=user)
    if not created:
        return "Das Whiteboard ist mit diesem Konto bereits geteilt."
    materialize_whiteboard_share_notification(share)
    return None


def end_whiteboard_share(board_id, user_id):
    """Ends a share (owner removes a member, or a member leaves) and drops its inbox row."""
    for share in WhiteboardShare.objects.filter(board_id=board_id, user_id=user_id):
        UserNotification.objects.filter(source_key=f"whiteboard-share:{share.pk}").delete()
        share.delete()
