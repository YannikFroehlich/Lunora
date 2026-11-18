"""Shared note folders.

Notes are shared one by one (NoteShare), and every permission check works on those rows. A shared
folder therefore doesn't change how access is checked: it keeps NoteShare rows in step with where
notes lie. Each note's rows that came from a folder carry ``via_folder``, and `sync_note_shares`
recomputes them from scratch whenever something moves:

- The folder owner's notes in a shared folder (or below it) are shared with every member, in the
  member's role, and placed into the member's ``mirror`` folder.
- An editor's own notes in their mirror (or below it) are shared with the folder owner (as editor)
  and the other members (in their roles).

Only the owner's own placement of a note counts (folders are per viewer), so a member moving a
shared note around in their own tree changes nothing for the others. Shares made by hand
(``via_folder`` null) are never touched.
"""

from collections import Counter

from django.db import transaction

from app.models import Note, NoteFolder, NoteFolderShare, NoteShare, NoteUserState, UserNotification
from app.services.notes import _next_tree_position, display_name, get_note_folder
from app.services.notifications import materialize_note_folder_share_notification
from app.services.vocabulary import resolve_share_recipient

MAX_FOLDER_SHARES = 20
ROLES = {NoteShare.ROLE_READER, NoteShare.ROLE_EDITOR}


def _parents(owner_id):
    return dict(NoteFolder.objects.filter(owner_id=owner_id).values_list("id", "parent_id"))


def _chain(parents, folder_id):
    """The folder and all its ancestors, innermost first."""
    chain = []
    while folder_id is not None and folder_id not in chain:
        chain.append(folder_id)
        folder_id = parents.get(folder_id)
    return chain


def _subtree(parents, folder_id):
    children = {}
    for child, parent in parents.items():
        children.setdefault(parent, []).append(child)
    found, stack = set(), [folder_id]
    while stack:
        current = stack.pop()
        if current not in found:
            found.add(current)
            stack.extend(children.get(current, []))
    return found


def _desired_shares(note):
    """{user_id: (role, via_folder_id, folder the note should appear in)} from the folders `note` lies in."""
    folder_id = (
        NoteUserState.objects.filter(note=note, user_id=note.owner_id)
        .values_list("folder_id", flat=True)
        .first()
    )
    chain = _chain(_parents(note.owner_id), folder_id)
    if not chain:
        return {}
    desired = {}

    def offer(user_id, role, via_id, place_id):
        current = desired.get(user_id)
        if current is None or (role == NoteShare.ROLE_EDITOR and current[0] == NoteShare.ROLE_READER):
            desired[user_id] = (role, via_id, place_id)

    for share in NoteFolderShare.objects.filter(folder_id__in=chain):
        offer(share.user_id, share.role, share.folder_id, share.mirror_id)
    own_mirrors = NoteFolderShare.objects.filter(
        mirror_id__in=chain, user_id=note.owner_id, role=NoteShare.ROLE_EDITOR
    ).select_related("folder")
    for mirror_share in own_mirrors:
        root = mirror_share.folder
        offer(root.owner_id, NoteShare.ROLE_EDITOR, root.pk, root.pk)
        for share in NoteFolderShare.objects.filter(folder=root).exclude(user_id=note.owner_id):
            offer(share.user_id, share.role, root.pk, share.mirror_id)
    desired.pop(note.owner_id, None)
    return desired


def sync_note_shares(note):
    """Brings the note's folder-made NoteShare rows in line with the shared folders it lies in."""
    desired = _desired_shares(note)
    existing = {share.user_id: share for share in NoteShare.objects.filter(note=note)}
    for user_id, share in existing.items():
        if share.via_folder_id and user_id not in desired:
            share.delete()
            NoteUserState.objects.filter(note=note, user_id=user_id).delete()
    for user_id, (role, via_id, place_id) in desired.items():
        share = existing.get(user_id)
        if share is None:
            NoteShare.objects.create(note=note, user_id=user_id, role=role, via_folder_id=via_id)
            NoteUserState.objects.update_or_create(
                note=note,
                user_id=user_id,
                defaults={"folder_id": place_id, "position": _next_tree_position(user_id, place_id)},
            )
        elif share.via_folder_id and (share.role, share.via_folder_id) != (role, via_id):
            share.role = role
            share.via_folder_id = via_id
            share.save(update_fields=["role", "via_folder"])


def sync_notes(note_ids):
    for note in Note.objects.filter(pk__in=set(note_ids)):
        sync_note_shares(note)


def _owned_notes_in(owner_id, folder_ids):
    return set(
        NoteUserState.objects.filter(
            user_id=owner_id, folder_id__in=folder_ids, note__owner_id=owner_id
        ).values_list("note_id", flat=True)
    )


def sync_folder_tree(owner_id, folder_id):
    """Re-syncs the owner's notes in the folder and below it, e.g. after the folder moved."""
    sync_notes(_owned_notes_in(owner_id, _subtree(_parents(owner_id), folder_id)))


def _group_notes(root):
    """Every note whose folder-made shares can depend on the shared folder `root`."""
    note_ids = _owned_notes_in(root.owner_id, _subtree(_parents(root.owner_id), root.pk))
    for share in root.shares.filter(role=NoteShare.ROLE_EDITOR, mirror__isnull=False):
        note_ids |= _owned_notes_in(share.user_id, _subtree(_parents(share.user_id), share.mirror_id))
    note_ids |= set(NoteShare.objects.filter(via_folder=root).values_list("note_id", flat=True))
    return note_ids


def _mirror_name(user, folder):
    taken = {
        name.casefold()
        for name in NoteFolder.objects.filter(owner=user, parent__isnull=True).values_list("name", flat=True)
    }
    candidates = [folder.name, f"{folder.name} ({display_name(folder.owner)})"]
    candidates += [f"{folder.name} ({number})" for number in range(2, 100)]
    return next((name[:100] for name in candidates if name[:100].casefold() not in taken), folder.name[:100])


def share_folder(owner, folder_id, identifier, role):
    """Shares the owner's folder with the user named by e-mail or username; returns an error or None."""
    folder = get_note_folder(owner, folder_id)
    if role not in ROLES:
        return "Die Freigaberolle ist ungültig."
    if NoteFolderShare.objects.filter(mirror=folder).exists():
        return "Ein Ordner, der mit dir geteilt wurde, lässt sich nicht weiterteilen."
    user, error = resolve_share_recipient(owner, identifier)
    if error:
        return error
    if folder.shares.count() >= MAX_FOLDER_SHARES:
        return f"Ein Ordner lässt sich mit höchstens {MAX_FOLDER_SHARES} Personen teilen."
    with transaction.atomic():
        if folder.shares.filter(user=user).exists():
            return "Der Ordner ist mit diesem Konto bereits geteilt."
        mirror = NoteFolder.objects.create(
            owner=user, name=_mirror_name(user, folder), position=_next_tree_position(user, None)
        )
        share = NoteFolderShare.objects.create(folder=folder, user=user, role=role, mirror=mirror)
        sync_notes(_group_notes(folder))
    materialize_note_folder_share_notification(share)
    return None


@transaction.atomic
def set_folder_share_role(owner, share_id, role):
    share = NoteFolderShare.objects.filter(pk=share_id, folder__owner=owner).select_related("folder").first()
    if share is None or role not in ROLES:
        return
    share.role = role
    share.save(update_fields=["role"])
    sync_notes(_group_notes(share.folder))


@transaction.atomic
def end_folder_share(share, *, keep_mirror=False):
    """Ends a folder share (owner removes a member, the member leaves, or either deletes the folder).

    The member loses the folder's notes; their own notes in the mirror stay with them, unshared, and
    an empty mirror goes away unless the caller deletes it anyway."""
    root = share.folder
    note_ids = _group_notes(root)
    mirror = share.mirror
    UserNotification.objects.filter(source_key=f"note-folder-share:{share.pk}").delete()
    share.delete()
    sync_notes(note_ids)
    if mirror and not keep_mirror and not (mirror.children.exists() or mirror.note_states.exists()):
        mirror.delete()


def end_shares_of_deleted_folder(folder):
    """Called before a folder is deleted: it can be a shared folder, a mirror, or neither."""
    for share in NoteFolderShare.objects.filter(folder=folder).select_related("folder", "mirror"):
        end_folder_share(share)
    for share in NoteFolderShare.objects.filter(mirror=folder).select_related("folder", "mirror"):
        end_folder_share(share, keep_mirror=True)


def folder_share_page(user, folder_id):
    """What the folder's sharing page shows: its members (owner) or where it comes from (member)."""
    folder = get_note_folder(user, folder_id)
    incoming = (
        NoteFolderShare.objects.filter(mirror=folder, user=user)
        .select_related("folder__owner__profile")
        .first()
    )
    return {
        "folder": folder,
        "incoming": incoming,
        "incoming_owner": display_name(incoming.folder.owner) if incoming else "",
        "members": [
            {"share": share, "name": display_name(share.user)}
            for share in folder.shares.select_related("user__profile")
        ],
        "role_choices": NoteShare.ROLE_CHOICES,
    }


def folder_share_labels(user):
    """{folder_id: label} for the user's folders that are shared, or shared with them, for the tree."""
    counts = Counter(NoteFolderShare.objects.filter(folder__owner=user).values_list("folder_id", flat=True))
    labels = {folder_id: f"Geteilt mit {count} Person(en)" for folder_id, count in counts.items()}
    incoming = NoteFolderShare.objects.filter(user=user, mirror__isnull=False).select_related(
        "folder__owner__profile"
    )
    for share in incoming:
        labels[share.mirror_id] = f"Geteilt von {display_name(share.folder.owner)}"
    return labels
