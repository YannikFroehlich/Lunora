"""Whiteboard access, sharing, live editing and conflict-checked saves.

The owner and everyone the board is shared with have the same drawing rights; only the owner
shares, unshares, and deletes. The editor sends its changes as operations (`sync_whiteboard`),
which the server applies in arrival order and hands to the other editors, so everyone sees
exactly the same board. Whole-board saves (`save_whiteboard`, the plain API) are checked
against `Whiteboard.revision` and make open editors reload.
"""

import math
import re
import time
from itertools import pairwise

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q

from app.models import Conversation, UserNotification, Whiteboard, WhiteboardOp, WhiteboardShare
from app.services.notes import _profile_image_url, display_name
from app.services.notifications import materialize_whiteboard_share_notification
from app.services.uml_content import MAX_COORDINATE
from app.services.vocabulary import resolve_share_recipient
from app.services.whiteboard_content import (
    MAX_OPS_PER_PUSH,
    WHITEBOARD_DOCUMENT_VERSION,
    apply_whiteboard_ops,
    clean_whiteboard_op,
    validate_whiteboard_document,
    whiteboard_hash,
)

MAX_WHITEBOARD_SHARES = 10
# Operations kept for editors that lag behind; older ones reload the whole board.
OP_HISTORY = 500
TAB_RE = re.compile(r"^[A-Za-z0-9_-]{8,40}$")
# Who else has the board open, with their pointer; kept in the cache, refreshed by every poll.
PEER_TTL_SECONDS = 10
TITLE_MAX_LENGTH = Whiteboard._meta.get_field("title").max_length


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


def _board_hash(board):
    # updated_at as well: a reused primary key (SQLite) must not find an old board's hash.
    key = f"whiteboard-hash:{board.pk}:{board.revision}:{board.updated_at.timestamp()}"
    value = cache.get(key)
    if value is None:
        value = whiteboard_hash(board.document["elements"])
        cache.set(key, value, 300)
    return value


def _clean_title(value):
    title = value.strip()
    if not title:
        raise ValidationError("Der Titel darf nicht leer sein.")
    if len(title) > TITLE_MAX_LENGTH:
        raise ValidationError(f"Der Titel darf höchstens {TITLE_MAX_LENGTH} Zeichen lang sein.")
    return title


def _apply_push(board, client_id, ops):
    if not isinstance(ops, list) or len(ops) > MAX_OPS_PER_PUSH:
        raise ValidationError("Die Änderungen sind ungültig.")
    cleaned = [clean_whiteboard_op(op) for op in ops]
    if any(later["seq"] <= earlier["seq"] for earlier, later in pairwise(cleaned)):
        raise ValidationError("Die Änderungen sind ungültig.")
    last_seq = board.ops.filter(client_id=client_id).aggregate(last=Max("seq"))["last"] or 0
    # A push whose answer got lost comes again; what the log already has is skipped.
    fresh = [op for op in cleaned if op["seq"] > last_seq]
    if not fresh:
        return
    for op in fresh:
        if op["op"] == "title":
            op["title"] = _clean_title(op["title"])
            board.title = op["title"]
    elements = apply_whiteboard_ops(board.document["elements"], fresh)
    board.document = validate_whiteboard_document(
        {"version": WHITEBOARD_DOCUMENT_VERSION, "elements": elements}
    )
    start = board.revision
    board.revision += len(fresh)
    board.save(update_fields=["title", "document", "revision", "updated_at"])
    WhiteboardOp.objects.bulk_create(
        [
            WhiteboardOp(
                board=board,
                version=start + index,
                client_id=client_id,
                seq=op["seq"],
                data={key: value for key, value in op.items() if key != "seq"},
            )
            for index, op in enumerate(fresh, start=1)
        ]
    )
    board.ops.filter(version__lte=board.revision - OP_HISTORY).delete()


def sync_whiteboard(board_id, user, *, version, tab, ops=None, pointer=None, drawing=False):
    """Store an editor's operations and return everything after `version`; None without access.

    The answer holds the operations in the order the server applied them (the editor's own
    included), the newest version, the hash of the board at that version, and `acked`, the
    editor's last stored change. `resync` asks the editor to load the whole board again.
    `peers` lists the others who have the board open, with their pointer on it.
    """
    if type(version) is not int or version < 0:
        raise ValidationError("Der Bearbeitungsstand ist ungültig.")
    if not isinstance(tab, str) or not TAB_RE.fullmatch(tab):
        raise ValidationError("Die Sitzungskennung ist ungültig.")
    pointer = _clean_pointer(pointer)
    client_id = f"{user.pk}-{tab}"
    with transaction.atomic():
        boards = accessible_whiteboards(user).filter(pk=board_id)
        # The row lock puts concurrent pushes in one order; plain polls don't need it.
        board = (boards.select_for_update() if ops else boards).first()
        if board is None:
            return None
        if ops:
            _apply_push(board, client_id, ops)
        head = board.revision
        missing = []
        if version < head:
            missing = list(
                board.ops.filter(version__gt=version).order_by("version").values_list("data", flat=True)
            )
        acked = board.ops.filter(client_id=client_id).aggregate(last=Max("seq"))["last"] or 0
    # Operations from before a whole-board save (or long pruned) can't be replayed.
    resync = version > head or len(missing) != head - version
    return {
        "version": head,
        "ops": [] if resync else missing,
        "acked": acked,
        "hash": _board_hash(board),
        "title": board.title,
        "resync": resync,
        "peers": _exchange_peers(board, user, client_id, pointer, drawing is True),
    }


def _clean_pointer(pointer):
    if pointer is None:
        return None
    if not isinstance(pointer, dict) or set(pointer) != {"x", "y"}:
        raise ValidationError("Die Zeigerposition ist ungültig.")
    cleaned = {}
    for key in ("x", "y"):
        value = pointer[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValidationError("Die Zeigerposition ist ungültig.")
        cleaned[key] = round(max(-MAX_COORDINATE, min(MAX_COORDINATE, float(value))), 1)
    return cleaned


def _exchange_peers(board, user, client_id, pointer, drawing):
    # ponytail: read-modify-write without a lock; two polls at once can drop one peer until its
    # next poll a second later, which is fine for something shown only for orientation.
    key = f"whiteboard-peers:{board.pk}"
    now = time.time()
    peers = {name: peer for name, peer in (cache.get(key) or {}).items() if peer["until"] > now}
    previous = peers.get(client_id)
    peers[client_id] = {
        "user_id": user.pk,
        "name": previous["name"] if previous else display_name(user),
        "initials": previous["initials"] if previous else Conversation.initials_for_user(user),
        "avatar_url": previous["avatar_url"] if previous else _profile_image_url(user),
        "pointer": pointer,
        "drawing": drawing,
        "until": now + PEER_TTL_SECONDS,
    }
    cache.set(key, peers, PEER_TTL_SECONDS)
    members = {board.owner_id, *board.shares.values_list("user_id", flat=True)}
    return [
        {"client_id": name, **{field: peer[field] for field in peer if field != "until"}}
        for name, peer in peers.items()
        if peer["user_id"] != user.pk and peer["user_id"] in members
    ]


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
