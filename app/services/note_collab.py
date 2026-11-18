"""Live editing of notes: a central step log in the style of prosemirror-collab.

Editors send the ProseMirror steps they made on top of the version they know; the server
only numbers and stores them (``NoteStep``) when that version is the newest, otherwise it
answers with the steps the editor is missing so it can rebase and send again. Every editor
applies the same steps in the same order and so ends up with the same document; one of them
stores it from time to time through ``save_note(..., collab_version=...)``. Readers poll the
same endpoint for a live view. Cursors are cosmetic and live in the cache only.
"""

import re
import time

from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from app.models import NoteActivityNotification, NoteStep
from app.services.note_content import extract_mention_user_ids, validate_note_steps
from app.services.notes import (
    _create_note_activity_notifications,
    _validate_attachment_references,
    _validate_comment_thread_references,
    _validate_mention_references,
    _validate_note_link_references,
    can_edit_note,
    collab_head,
    display_name,
    get_accessible_note,
    note_accessible_user_ids,
)

TAB_RE = re.compile(r"^[A-Za-z0-9_-]{8,40}$")
# Steps a note may collect beyond its stored document; editors store one every few seconds.
MAX_UNSAVED_STEPS = 5000
CURSOR_TTL_SECONDS = 20


def _cursor_key(note):
    return f"note-collab-cursors:{note.id}"


def _position(value):
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 10_000_000:
        raise ValidationError("Die Cursorposition ist ungültig.")
    return value


@transaction.atomic
def sync_note(user, note_id, *, version, tab, steps=None, selection=None):
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        raise ValidationError("Der Bearbeitungsstand ist ungültig.")
    if not isinstance(tab, str) or not TAB_RE.fullmatch(tab):
        raise ValidationError("Die Sitzungskennung ist ungültig.")
    # The row lock orders concurrent pushes; plain polls don't need it.
    note = get_accessible_note(user, note_id, allow_deleted=not steps, for_update=bool(steps))
    can_edit = can_edit_note(note, user) and not note.deleted_at
    if steps and not can_edit:
        raise PermissionDenied("Du darfst diese Notiz nicht bearbeiten.")
    client_id = f"{user.id}-{tab}"
    head = collab_head(note)
    result = {
        "client_id": client_id,
        "version": head,
        "title": note.title,
        "revision": note.revision,
        "accepted": False,
        "steps": [],
        "resync": False,
    }

    missing = []
    if version < head:
        missing = list(
            note.collab_steps.filter(version__gt=version).order_by("version").values("data", "client_id")
        )
    # Steps before a reset (or long gone) can't be replayed: the editor loads the note again.
    if version > head or len(missing) != head - version:
        result["resync"] = True
    elif steps and version == head:
        if head - note.collab_version + len(steps) > MAX_UNSAVED_STEPS:
            raise ValidationError("Zu viele ungespeicherte Änderungen – bitte kurz warten.")
        references = validate_note_steps(steps)
        _validate_attachment_references(note, references["attachments"])
        _validate_mention_references(note, references["mentions"])
        _validate_comment_thread_references(note, references["comment_threads"])
        _validate_note_link_references(user, references["note_links"])
        NoteStep.objects.bulk_create(
            [
                NoteStep(note=note, version=head + index, client_id=client_id, data=step)
                for index, step in enumerate(steps, start=1)
            ]
        )
        result["version"] = head + len(steps)
        result["accepted"] = True
        # ponytail: compares with the stored document only, so a mention cut and pasted again
        # before the next save notifies twice.
        mentioned = references["mentions"] - extract_mention_user_ids(note.document) - {user.id}
        if mentioned:
            _create_note_activity_notifications(note, user, mentioned, NoteActivityNotification.KIND_MENTION)
    else:
        result["steps"] = [{"step": item["data"], "client_id": item["client_id"]} for item in missing]

    result["cursors"] = _exchange_cursors(note, user, client_id, selection if can_edit else None)
    return result


def _exchange_cursors(note, user, client_id, selection):
    # ponytail: read-modify-write without a lock; two editors moving at once can drop one
    # cursor until its next poll, which is fine for something purely cosmetic.
    now = time.time()
    cursors = {
        key: value for key, value in (cache.get(_cursor_key(note)) or {}).items() if value["until"] > now
    }
    if isinstance(selection, dict):
        previous = cursors.get(client_id) or {}
        cursors[client_id] = {
            "user_id": user.id,
            "name": previous.get("name") or display_name(user),
            "anchor": _position(selection.get("anchor")),
            "head": _position(selection.get("head")),
            "until": now + CURSOR_TTL_SECONDS,
        }
        cache.set(_cursor_key(note), cursors, CURSOR_TTL_SECONDS)
    elif cursors.pop(client_id, None):
        cache.set(_cursor_key(note), cursors, CURSOR_TTL_SECONDS)
    allowed = note_accessible_user_ids(note)
    return [
        {
            "client_id": key,
            "user_id": value["user_id"],
            "name": value["name"],
            "anchor": value["anchor"],
            "head": value["head"],
        }
        for key, value in cursors.items()
        if value["user_id"] != user.id and value["user_id"] in allowed
    ]
