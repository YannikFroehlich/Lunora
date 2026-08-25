import copy
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import BooleanField, Case, Exists, IntegerField, Max, OuterRef, Q, Subquery, Sum, Value, When
from django.utils import timezone

from app.models import (
    Note,
    NoteActivityNotification,
    NoteAttachment,
    NoteComment,
    NoteCommentThread,
    NoteFolder,
    NoteLink,
    NoteShare,
    NoteTemplate,
    NoteUserState,
    NoteVersion,
)
from app.services.note_content import (
    NOTE_TEMPLATES,
    document_plain_text,
    empty_note_document,
    extract_mention_user_ids,
    extract_note_link_ids,
    validate_note_document,
)
from app.services.note_files import NOTE_TOTAL_ATTACHMENT_BYTES


VERSION_INTERVAL = timedelta(minutes=5)
VERSION_RETENTION = timedelta(days=90)
VERSION_LIMIT = 100
TRASH_RETENTION = timedelta(days=30)
FOLDER_MAX_DEPTH = 12
TREE_POSITION_STEP = 1000
MAX_NOTE_TEMPLATES = 30


class NoteConflictError(Exception):
    def __init__(self, note):
        self.note = note
        super().__init__("Die Notiz wurde zwischenzeitlich geändert.")


def display_name(user):
    profile_name = getattr(getattr(user, "profile", None), "display_name", "")
    return profile_name or user.get_full_name() or user.email or user.get_username()


def note_permission(note, user):
    if note.owner_id == user.id:
        return "owner"
    share = next((item for item in note.shares.all() if item.user_id == user.id), None)
    return share.role if share else None


def can_edit_note(note, user):
    return note_permission(note, user) in {"owner", NoteShare.ROLE_EDITOR}


def accessible_notes(user, *, include_deleted=False):
    queryset = (
        Note.objects.filter(Q(owner=user) | Q(shares__user=user))
        .select_related("owner", "owner__profile", "last_edited_by", "last_edited_by__profile")
        .prefetch_related("shares__user", "shares__user__profile")
        .distinct()
    )
    if include_deleted:
        queryset = queryset.filter(Q(deleted_at__isnull=True) | Q(owner=user))
    else:
        queryset = queryset.filter(deleted_at__isnull=True)
    state_query = NoteUserState.objects.filter(note_id=OuterRef("pk"), user=user)
    return queryset.annotate(
        state_is_pinned=Exists(state_query.filter(is_pinned=True)),
        state_is_archived=Exists(state_query.filter(is_archived=True)),
        state_folder_id=Subquery(state_query.values("folder_id")[:1], output_field=IntegerField()),
        state_position=Subquery(state_query.values("position")[:1], output_field=IntegerField()),
        is_shared_with_user=Case(
            When(owner=user, then=Value(False)),
            default=Value(True),
            output_field=BooleanField(),
        ),
        has_unseen_share=Exists(NoteShare.objects.filter(note_id=OuterRef("pk"), user=user, first_opened_at__isnull=True)),
    )


def get_accessible_note(user, note_id, *, allow_deleted=False, for_update=False):
    queryset = Note.objects.select_related(
        "owner", "owner__profile", "last_edited_by", "last_edited_by__profile"
    ).prefetch_related("shares__user", "shares__user__profile")
    if for_update:
        queryset = queryset.select_for_update()
    note = queryset.filter(pk=note_id).filter(Q(owner=user) | Q(shares__user=user)).distinct().first()
    if not note or (note.deleted_at and (not allow_deleted or note.owner_id != user.id)):
        raise Note.DoesNotExist
    return note


def get_note_state(note, user):
    state, _created = NoteUserState.objects.get_or_create(
        note=note,
        user=user,
        defaults={"position": _next_tree_position(user, None)},
    )
    return state


def _next_tree_position(user, folder_id):
    folder_max = NoteFolder.objects.filter(owner=user, parent_id=folder_id).aggregate(value=Max("position"))["value"] or 0
    note_max = NoteUserState.objects.filter(user=user, folder_id=folder_id).aggregate(value=Max("position"))["value"] or 0
    return max(folder_max, note_max) + TREE_POSITION_STEP


def _clean_folder_name(name):
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValidationError("Der Ordnername darf nicht leer sein.")
    if len(clean_name) > 100:
        raise ValidationError("Der Ordnername darf maximal 100 Zeichen lang sein.")
    return clean_name


def get_note_folder(user, folder_id, *, for_update=False):
    queryset = NoteFolder.objects.filter(owner=user)
    if for_update:
        queryset = queryset.select_for_update()
    folder = queryset.filter(pk=folder_id).first()
    if not folder:
        raise NoteFolder.DoesNotExist
    return folder


def _folder_depth(folder):
    depth = 0
    seen = set()
    current = folder
    while current:
        if current.id in seen or current.owner_id != folder.owner_id:
            raise ValidationError("Die Ordnerstruktur ist ungültig.")
        seen.add(current.id)
        depth += 1
        current = current.parent
    return depth


def _validate_unique_folder_name(user, parent_id, name, *, exclude_id=None):
    queryset = NoteFolder.objects.filter(owner=user, parent_id=parent_id, name__iexact=name)
    if exclude_id is not None:
        queryset = queryset.exclude(pk=exclude_id)
    if queryset.exists():
        raise ValidationError("In diesem Ordner gibt es bereits einen Ordner mit diesem Namen.")


@transaction.atomic
def create_note_folder(user, *, name, parent_id=None):
    clean_name = _clean_folder_name(name)
    parent = get_note_folder(user, parent_id, for_update=True) if parent_id is not None else None
    if parent and _folder_depth(parent) >= FOLDER_MAX_DEPTH:
        raise ValidationError(f"Ordner dürfen höchstens {FOLDER_MAX_DEPTH} Ebenen tief verschachtelt werden.")
    _validate_unique_folder_name(user, parent.id if parent else None, clean_name)
    return NoteFolder.objects.create(
        owner=user,
        parent=parent,
        name=clean_name,
        position=_next_tree_position(user, parent.id if parent else None),
    )


@transaction.atomic
def rename_note_folder(user, folder_id, *, name):
    folder = get_note_folder(user, folder_id, for_update=True)
    clean_name = _clean_folder_name(name)
    _validate_unique_folder_name(user, folder.parent_id, clean_name, exclude_id=folder.id)
    if folder.name != clean_name:
        folder.name = clean_name
        folder.save(update_fields=["name", "updated_at"])
    return folder


@transaction.atomic
def delete_note_folder(user, folder_id):
    folder = get_note_folder(user, folder_id, for_update=True)
    parent_id = folder.parent_id
    siblings = _tree_container_items(user, parent_id)
    contents = _tree_container_items(user, folder.id)
    folder_index = next(
        (index for index, item in enumerate(siblings) if item["type"] == "folder" and item["id"] == folder.id),
        len(siblings),
    )
    siblings = [item for item in siblings if not (item["type"] == "folder" and item["id"] == folder.id)]
    siblings[folder_index:folder_index] = contents
    _reindex_tree_container(siblings, parent_id)
    folder.delete()


def serialize_note_folder(folder):
    return {"id": folder.id, "name": folder.name, "parent_id": folder.parent_id, "position": folder.position}


@transaction.atomic
def set_note_folder(user, note_id, folder_id):
    note = get_accessible_note(user, note_id, allow_deleted=True, for_update=True)
    folder = get_note_folder(user, folder_id, for_update=True) if folder_id is not None else None
    state = get_note_state(note, user)
    if state.folder_id != (folder.id if folder else None):
        state.folder = folder
        state.position = _next_tree_position(user, folder.id if folder else None)
        state.save(update_fields=["folder", "position"])
    return note


def bulk_move_notes_to_folder(user, note_ids, folder_id):
    updated_ids = []
    skipped_ids = []
    for note_id in note_ids:
        try:
            set_note_folder(user, note_id, folder_id)
        except Note.DoesNotExist:
            skipped_ids.append(note_id)
            continue
        updated_ids.append(note_id)
    return updated_ids, skipped_ids


def _tree_container_items(user, folder_id, *, exclude=None):
    folder_queryset = NoteFolder.objects.select_for_update().filter(owner=user, parent_id=folder_id)
    state_queryset = NoteUserState.objects.select_for_update().filter(user=user, folder_id=folder_id)
    items = [
        {"type": "folder", "id": folder.id, "position": folder.position, "object": folder}
        for folder in folder_queryset
        if exclude != ("folder", folder.id)
    ]
    items.extend(
        {"type": "note", "id": state.note_id, "position": state.position, "object": state}
        for state in state_queryset
        if exclude != ("note", state.note_id)
    )
    return sorted(items, key=lambda item: (item["position"], 0 if item["type"] == "note" else 1, item["id"]))


def _reindex_tree_container(items, folder_id):
    folders = []
    states = []
    for index, item in enumerate(items, start=1):
        position = index * TREE_POSITION_STEP
        item["position"] = position
        item["object"].position = position
        if item["type"] == "folder":
            item["object"].parent_id = folder_id
            folders.append(item["object"])
        else:
            item["object"].folder_id = folder_id
            states.append(item["object"])
    if folders:
        NoteFolder.objects.bulk_update(folders, ["parent", "position"])
    if states:
        NoteUserState.objects.bulk_update(states, ["folder", "position"])


def _validate_folder_move_destination(folder, destination):
    current = destination
    visited = set()
    while current:
        if current.id == folder.id:
            raise ValidationError("Ein Ordner kann nicht in sich selbst oder einen Unterordner verschoben werden.")
        if current.id in visited:
            raise ValidationError("Die Ordnerstruktur ist ungültig.")
        visited.add(current.id)
        current = current.parent


@transaction.atomic
def move_note_tree_item(user, *, item_type, item_id, placement, target_type=None, target_id=None):
    if item_type not in {"note", "folder"}:
        raise ValidationError("Dieses Element kann nicht verschoben werden.")
    if placement not in {"before", "after", "inside", "root"}:
        raise ValidationError("Das Verschiebeziel ist ungültig.")

    if item_type == "folder":
        moved_folder = get_note_folder(user, item_id, for_update=True)
        moved_note = None
        moved_object = moved_folder
    else:
        moved_note = get_accessible_note(user, item_id, for_update=True)
        moved_folder = None
        moved_object = get_note_state(moved_note, user)
        moved_object = NoteUserState.objects.select_for_update().get(pk=moved_object.pk)

    if placement == "root":
        destination = None
        destination_id = None
        target_key = None
    elif placement == "inside":
        if target_type != "folder" or target_id is None:
            raise ValidationError("Elemente können nur in einen Ordner verschoben werden.")
        destination = get_note_folder(user, target_id, for_update=True)
        destination_id = destination.id
        target_key = None
    else:
        if target_type not in {"note", "folder"} or target_id is None:
            raise ValidationError("Das Verschiebeziel ist ungültig.")
        if target_type == item_type and target_id == item_id:
            return moved_note or moved_folder
        if target_type == "folder":
            target = get_note_folder(user, target_id, for_update=True)
            destination_id = target.parent_id
        else:
            target_note = get_accessible_note(user, target_id, for_update=True)
            target_state = get_note_state(target_note, user)
            target_state = NoteUserState.objects.select_for_update().get(pk=target_state.pk)
            destination_id = target_state.folder_id
        destination = get_note_folder(user, destination_id, for_update=True) if destination_id else None
        target_key = (target_type, target_id)

    if moved_folder:
        _validate_folder_move_destination(moved_folder, destination)

    items = _tree_container_items(user, destination_id, exclude=(item_type, item_id))
    insertion_index = len(items)
    if target_key:
        try:
            target_index = next(index for index, item in enumerate(items) if (item["type"], item["id"]) == target_key)
        except StopIteration as error:
            raise ValidationError("Das Verschiebeziel ist nicht mehr vorhanden.") from error
        insertion_index = target_index if placement == "before" else target_index + 1

    items.insert(
        insertion_index,
        {"type": item_type, "id": item_id, "position": 0, "object": moved_object},
    )
    _reindex_tree_container(items, destination_id)
    return moved_note or moved_folder


def create_note(user, *, title="Unbenannte Notiz", template="blank", custom_template_id=None, folder_id=None):
    folder = get_note_folder(user, folder_id) if folder_id is not None else None
    if custom_template_id is not None:
        custom_template = NoteTemplate.objects.get(owner=user, pk=custom_template_id)
        document = copy.deepcopy(custom_template.document)
        validate_note_document(document)
    else:
        document = NOTE_TEMPLATES.get(template, empty_note_document)()
    note = Note.objects.create(
        owner=user,
        title=(title or "Unbenannte Notiz")[:200],
        document=document,
        plain_text=document_plain_text(document),
        last_edited_by=user,
    )
    NoteUserState.objects.create(
        note=note,
        user=user,
        folder=folder,
        position=_next_tree_position(user, folder.id if folder else None),
    )
    return note


def serialize_note(note, user, *, include_document=True):
    permission = note_permission(note, user)
    if hasattr(note, "state_is_pinned"):
        is_pinned = note.state_is_pinned
        is_archived = note.state_is_archived
        folder_id = note.state_folder_id
        position = note.state_position
    else:
        state = get_note_state(note, user)
        is_pinned = state.is_pinned
        is_archived = state.is_archived
        folder_id = state.folder_id
        position = state.position
    payload = {
        "id": note.id,
        "title": note.title,
        "plain_text": note.plain_text,
        "preview": note.plain_text[:180],
        "revision": note.revision,
        "permission": permission,
        "can_edit": permission in {"owner", NoteShare.ROLE_EDITOR},
        "can_manage": permission == "owner",
        "is_pinned": is_pinned,
        "is_archived": is_archived,
        "folder_id": folder_id,
        "position": position,
        "is_deleted": bool(note.deleted_at),
        "has_unseen_share": bool(getattr(note, "has_unseen_share", False)),
        "deleted_at": note.deleted_at.isoformat() if note.deleted_at else None,
        "created_at": note.created_at.isoformat(),
        "updated_at": note.updated_at.isoformat(),
        "updated_by": display_name(note.last_edited_by) if note.last_edited_by else display_name(note.owner),
        "owner": {"id": note.owner_id, "name": display_name(note.owner)},
    }
    if include_document:
        payload["document"] = note.document
        payload["shares"] = [
            {
                "user_id": share.user_id,
                "name": display_name(share.user),
                "email": share.user.email,
                "role": share.role,
            }
            for share in note.shares.all()
        ]
        payload["backlinks"] = get_note_backlinks(note, user)
    return payload


@transaction.atomic
def save_note(user, note_id, *, title, document, base_revision, conflict_resolution=False):
    note = get_accessible_note(user, note_id, for_update=True)
    if not can_edit_note(note, user):
        raise PermissionDenied("Du darfst diese Notiz nicht bearbeiten.")
    if note.revision != base_revision:
        raise NoteConflictError(note)

    clean_title = str(title or "").strip()
    if not clean_title:
        clean_title = "Unbenannte Notiz"
    if len(clean_title) > 200:
        raise ValidationError("Der Titel darf maximal 200 Zeichen lang sein.")
    validated = validate_note_document(document)
    attachment_ids = validated["attachments"]
    mention_ids = validated["mentions"]
    comment_thread_ids = validated["comment_threads"]
    note_link_ids = validated["note_links"]
    _validate_attachment_references(note, attachment_ids)
    _validate_mention_references(note, mention_ids)
    _validate_comment_thread_references(note, comment_thread_ids)
    _validate_note_link_references(user, note_link_ids)
    new_mention_ids = mention_ids - extract_mention_user_ids(note.document) - {user.id}

    if conflict_resolution:
        _create_version(note, user, NoteVersion.REASON_CONFLICT)
    else:
        _create_interval_version(note, user)

    note.title = clean_title
    note.document = document
    note.plain_text = document_plain_text(document)
    note.revision += 1
    note.last_edited_by = user
    note.updated_at = timezone.now()
    note.save(
        update_fields=["title", "document", "plain_text", "revision", "last_edited_by", "updated_at"]
    )
    _set_note_links(note, note_link_ids)
    prune_note_versions(note)
    if new_mention_ids:
        _create_note_activity_notifications(note, user, new_mention_ids, NoteActivityNotification.KIND_MENTION)
    return note


def _validate_attachment_references(note, attachment_ids):
    if not attachment_ids:
        return
    found = {
        str(value).lower()
        for value in note.attachments.filter(file_id__in=attachment_ids).values_list("file_id", flat=True)
    }
    if found != set(attachment_ids):
        raise ValidationError("Die Notiz verweist auf eine fremde oder nicht vorhandene Datei.")


def _validate_comment_thread_references(note, thread_ids):
    if not thread_ids:
        return
    found = {
        str(value).lower()
        for value in note.comment_threads.filter(thread_id__in=thread_ids).values_list("thread_id", flat=True)
    }
    if found != set(thread_ids):
        raise ValidationError("Die Notiz verweist auf einen unbekannten Kommentarbezug.")


def note_accessible_user_ids(note):
    return {note.owner_id} | {share.user_id for share in note.shares.all()}


def _validate_mention_references(note, mentioned_user_ids):
    if not mentioned_user_ids:
        return
    if not set(mentioned_user_ids) <= note_accessible_user_ids(note):
        raise ValidationError("Du kannst nur Personen erwähnen, die diese Notiz bereits sehen dürfen.")


def _validate_note_link_references(user, linked_note_ids):
    if not linked_note_ids:
        return
    accessible_ids = set(accessible_notes(user).filter(pk__in=linked_note_ids).values_list("id", flat=True))
    if not set(linked_note_ids) <= accessible_ids:
        raise ValidationError("Du kannst nur auf Notizen verlinken, auf die du selbst Zugriff hast.")


def _set_note_links(note, target_ids):
    NoteLink.objects.filter(source_note=note).exclude(target_note_id__in=target_ids).delete()
    existing_ids = set(note.outgoing_links.values_list("target_note_id", flat=True))
    NoteLink.objects.bulk_create(
        [NoteLink(source_note=note, target_note_id=target_id) for target_id in target_ids if target_id not in existing_ids]
    )


def get_note_backlinks(note, user):
    source_ids = NoteLink.objects.filter(target_note=note).values_list("source_note_id", flat=True)
    notes = accessible_notes(user).filter(pk__in=source_ids).order_by("-updated_at", "-id")
    return [{"id": item.id, "title": item.title} for item in notes]


def _create_note_activity_notifications(note, actor, recipient_ids, kind):
    excerpt = note.plain_text[:200]
    NoteActivityNotification.objects.bulk_create(
        [
            NoteActivityNotification(note=note, recipient_id=recipient_id, actor=actor, kind=kind, excerpt=excerpt)
            for recipient_id in recipient_ids
            if recipient_id != actor.id
        ]
    )


def _create_interval_version(note, user):
    latest = note.versions.order_by("-created_at").first()
    if latest and latest.created_at > timezone.now() - VERSION_INTERVAL:
        return None
    return _create_version(note, user, NoteVersion.REASON_AUTOSAVE)


def _create_version(note, user, reason):
    return NoteVersion.objects.create(
        note=note,
        created_by=user,
        source_revision=note.revision,
        title=note.title,
        document=copy.deepcopy(note.document),
        reason=reason,
    )


def prune_note_versions(note):
    cutoff = timezone.now() - VERSION_RETENTION
    note.versions.filter(created_at__lt=cutoff).delete()
    stale_ids = list(note.versions.order_by("-created_at").values_list("id", flat=True)[VERSION_LIMIT:])
    if stale_ids:
        NoteVersion.objects.filter(id__in=stale_ids).delete()
    referenced_ids = _document_attachment_ids(note.document)
    for version_document in note.versions.values_list("document", flat=True):
        referenced_ids.update(_document_attachment_ids(version_document))
    orphan_cutoff = timezone.now() - timedelta(hours=1)
    for attachment in note.attachments.filter(created_at__lt=orphan_cutoff).exclude(file_id__in=referenced_ids):
        attachment.file.delete(save=False)
        attachment.delete()


@transaction.atomic
def restore_note_version(user, note_id, version_id, *, base_revision):
    note = get_accessible_note(user, note_id, for_update=True)
    if not can_edit_note(note, user):
        raise PermissionDenied("Du darfst diese Version nicht wiederherstellen.")
    if note.revision != base_revision:
        raise NoteConflictError(note)
    version = note.versions.filter(pk=version_id).first()
    if not version:
        raise NoteVersion.DoesNotExist
    validated = validate_note_document(version.document)
    _create_version(note, user, NoteVersion.REASON_RESTORE)
    note.title = version.title
    note.document = copy.deepcopy(version.document)
    note.plain_text = document_plain_text(note.document)
    note.revision += 1
    note.last_edited_by = user
    note.updated_at = timezone.now()
    note.save(update_fields=["title", "document", "plain_text", "revision", "last_edited_by", "updated_at"])
    _set_note_links(note, validated["note_links"])
    prune_note_versions(note)
    return note


def serialize_version(version):
    return {
        "id": version.id,
        "revision": version.source_revision,
        "title": version.title,
        "preview": document_plain_text(version.document)[:180],
        "created_at": version.created_at.isoformat(),
        "created_by": display_name(version.created_by) if version.created_by else "Unbekannt",
        "reason": version.reason,
    }


@transaction.atomic
def set_personal_state(user, note_id, *, action):
    note = get_accessible_note(user, note_id, allow_deleted=True, for_update=True)
    state = get_note_state(note, user)
    now = timezone.now()
    if action == "pin":
        state.is_pinned = True
        state.pinned_at = now
    elif action == "unpin":
        state.is_pinned = False
        state.pinned_at = None
    elif action == "archive":
        state.is_archived = True
        state.archived_at = now
    elif action == "unarchive":
        state.is_archived = False
        state.archived_at = None
    else:
        raise ValidationError("Unbekannte Notizaktion.")
    state.save(update_fields=["is_pinned", "pinned_at", "is_archived", "archived_at"])
    return note


@transaction.atomic
def set_trash_state(user, note_id, *, action):
    note = get_accessible_note(user, note_id, allow_deleted=True, for_update=True)
    if note.owner_id != user.id:
        raise PermissionDenied("Nur der Eigentümer darf diese Notiz löschen.")
    if action == "trash":
        note.deleted_at = timezone.now()
    elif action == "restore":
        note.deleted_at = None
    elif action == "purge":
        _delete_note_files(note)
        note.delete()
        return None
    else:
        raise ValidationError("Unbekannte Papierkorbaktion.")
    note.save(update_fields=["deleted_at"])
    return note


def bulk_set_notes_state(user, note_ids, *, action):
    updated_ids = []
    skipped_ids = []
    for note_id in note_ids:
        try:
            if action in {"pin", "unpin", "archive", "unarchive"}:
                set_personal_state(user, note_id, action=action)
            else:
                set_trash_state(user, note_id, action=action)
        except (Note.DoesNotExist, PermissionDenied):
            skipped_ids.append(note_id)
            continue
        updated_ids.append(note_id)
    return updated_ids, skipped_ids


@transaction.atomic
def duplicate_note(user, note_id, *, title=None, document=None):
    source = get_accessible_note(user, note_id)
    source_state = get_note_state(source, user)
    source_document = document if document is not None else source.document
    validated = validate_note_document(source_document)
    attachment_ids = validated["attachments"]
    _validate_attachment_references(source, attachment_ids)
    target = create_note(
        user,
        title=(title or f"{source.title} (Kopie)")[:200],
        folder_id=source_state.folder_id,
    )
    id_map = {}
    for attachment in source.attachments.filter(file_id__in=attachment_ids):
        with attachment.file.open("rb") as file_handle:
            target_attachment = NoteAttachment(
                note=target,
                uploaded_by=user,
                kind=attachment.kind,
                original_name=attachment.original_name,
                content_type=attachment.content_type,
                size=attachment.size,
            )
            target_attachment.file.save(
                attachment.original_name,
                ContentFile(file_handle.read()),
                save=False,
            )
            target_attachment.save()
            id_map[str(attachment.file_id).lower()] = str(target_attachment.file_id)
    target.document = _strip_comment_marks(_rewrite_attachment_ids(copy.deepcopy(source_document), id_map))
    target.plain_text = document_plain_text(target.document)
    target.last_edited_by = user
    target.save(update_fields=["document", "plain_text", "last_edited_by", "updated_at"])
    _set_note_links(target, validated["note_links"])
    return target


def _rewrite_attachment_ids(node, id_map):
    attrs = node.get("attrs") or {}
    if "attachmentId" in attrs:
        attrs["attachmentId"] = id_map.get(str(attrs["attachmentId"]).lower(), attrs["attachmentId"])
    for child in node.get("content") or []:
        _rewrite_attachment_ids(child, id_map)
    return node


def _strip_comment_marks(node):
    """A duplicated note starts without the source note's comment threads, so any
    commentThread marks would otherwise dangle and fail validation on the next save."""
    marks = node.get("marks")
    if marks:
        node["marks"] = [mark for mark in marks if mark.get("type") != "commentThread"]
    for child in node.get("content") or []:
        _strip_comment_marks(child)
    return node


def _strip_template_hazards(node):
    """A saved template must stand alone: comment threads, mentions of a specific
    person, links to a specific note, and attachments all belong to the source note
    and would either dangle or repeat unexpectedly every time the template is reused,
    so they are dropped (mentions/links keep their visible label as plain text)."""
    node_type = node.get("type")
    if node_type == "mention":
        label = (node.get("attrs") or {}).get("label", "")
        return {"type": "text", "text": f"@{label}"} if label else None
    if node_type == "noteLink":
        label = (node.get("attrs") or {}).get("label", "")
        return {"type": "text", "text": label} if label else None
    if node_type in {"noteImage", "noteAttachment"}:
        return None
    marks = node.get("marks")
    if marks:
        node["marks"] = [mark for mark in marks if mark.get("type") != "commentThread"]
    content = node.get("content")
    if content:
        node["content"] = [
            stripped for child in content if (stripped := _strip_template_hazards(child)) is not None
        ]
    return node


def serialize_note_template(template):
    return {"id": template.id, "name": template.name}


def list_note_templates(user):
    return NoteTemplate.objects.filter(owner=user)


def create_note_template(user, note_id, *, name):
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValidationError("Der Vorlagenname darf nicht leer sein.")
    if len(clean_name) > 100:
        raise ValidationError("Der Vorlagenname darf maximal 100 Zeichen lang sein.")
    if NoteTemplate.objects.filter(owner=user).count() >= MAX_NOTE_TEMPLATES:
        raise ValidationError(f"Es sind maximal {MAX_NOTE_TEMPLATES} eigene Vorlagen erlaubt.")
    note = get_accessible_note(user, note_id)
    document = _strip_template_hazards(copy.deepcopy(note.document))
    try:
        return NoteTemplate.objects.create(owner=user, name=clean_name, document=document)
    except IntegrityError as error:
        raise ValidationError("Es gibt bereits eine Vorlage mit diesem Namen.") from error


def delete_note_template(user, template_id):
    deleted, _ = NoteTemplate.objects.filter(owner=user, pk=template_id).delete()
    if not deleted:
        raise NoteTemplate.DoesNotExist


def _document_attachment_ids(document):
    found = set()

    def visit(node):
        attachment_id = (node.get("attrs") or {}).get("attachmentId")
        if attachment_id:
            found.add(str(attachment_id).lower())
        for child in node.get("content") or []:
            visit(child)

    if isinstance(document, dict):
        visit(document)
    return found


def create_attachment(user, note_id, upload, *, kind):
    note = get_accessible_note(user, note_id)
    if not can_edit_note(note, user):
        raise PermissionDenied("Du darfst keine Dateien zu dieser Notiz hinzufügen.")
    current_size = note.attachments.aggregate(total=Sum("size"))["total"] or 0
    if current_size + (getattr(upload, "size", 0) or 0) > NOTE_TOTAL_ATTACHMENT_BYTES:
        raise ValidationError("Eine Notiz darf insgesamt höchstens 100 MB Dateien enthalten.")
    from app.services.note_files import validate_note_upload

    validate_note_upload(upload, kind=kind)
    attachment = NoteAttachment(
        note=note,
        uploaded_by=user,
        kind=kind,
        original_name=str(upload.name)[:255],
        content_type=(getattr(upload, "content_type", "") or "application/octet-stream")[:160],
        size=upload.size,
    )
    attachment.file.save(attachment.original_name, upload, save=False)
    attachment.save()
    return attachment


def mark_shared_note_opened(user, note):
    if note.owner_id != user.id:
        NoteShare.objects.filter(note=note, user=user, first_opened_at__isnull=True).update(first_opened_at=timezone.now())


def share_note(owner, note_id, target_user_id, role):
    note = get_accessible_note(owner, note_id)
    if note.owner_id != owner.id:
        raise PermissionDenied("Nur der Eigentümer darf Freigaben verwalten.")
    if role not in {NoteShare.ROLE_READER, NoteShare.ROLE_EDITOR}:
        raise ValidationError("Die Freigaberolle ist ungültig.")
    target = get_user_model().objects.filter(pk=target_user_id, is_active=True).first()
    if not target or target.id == owner.id:
        raise ValidationError("Der ausgewählte Benutzer ist ungültig.")
    share, created = NoteShare.objects.get_or_create(note=note, user=target, defaults={"role": role})
    if not created and share.role != role:
        share.role = role
        share.save(update_fields=["role"])
    NoteUserState.objects.get_or_create(note=note, user=target)
    return share


def remove_note_share(owner, note_id, target_user_id):
    note = get_accessible_note(owner, note_id)
    if note.owner_id != owner.id:
        raise PermissionDenied("Nur der Eigentümer darf Freigaben verwalten.")
    NoteShare.objects.filter(note=note, user_id=target_user_id).delete()
    NoteUserState.objects.filter(note=note, user_id=target_user_id).delete()


def purge_expired_notes(*, now=None):
    current_time = now or timezone.now()
    for active_note in Note.objects.filter(deleted_at__isnull=True).iterator():
        prune_note_versions(active_note)
    cutoff = current_time - TRASH_RETENTION
    notes = list(Note.objects.filter(deleted_at__lte=cutoff))
    for note in notes:
        _delete_note_files(note)
        note.delete()
    return len(notes)


def serialize_comment_thread(thread):
    return {
        "thread_id": str(thread.thread_id),
        "anchor_text": thread.anchor_text,
        "is_resolved": thread.is_resolved,
        "resolved_by": display_name(thread.resolved_by) if thread.resolved_by else None,
        "created_at": thread.created_at.isoformat(),
        "comments": [
            {
                "id": comment.id,
                "author": display_name(comment.author) if comment.author else "Unbekannt",
                "author_id": comment.author_id,
                "body": comment.body,
                "created_at": comment.created_at.isoformat(),
            }
            for comment in thread.comments.all()
        ],
    }


def list_comment_threads(note):
    threads = note.comment_threads.select_related(
        "created_by", "created_by__profile", "resolved_by", "resolved_by__profile"
    ).prefetch_related("comments__author", "comments__author__profile")
    return [serialize_comment_thread(thread) for thread in threads]


def _get_note_comment_thread(note, thread_id):
    thread = note.comment_threads.filter(thread_id=thread_id).first()
    if not thread:
        raise NoteCommentThread.DoesNotExist
    return thread


def create_comment_thread(user, note_id, *, thread_id, anchor_text, body):
    note = get_accessible_note(user, note_id)
    clean_body = str(body or "").strip()
    if not clean_body:
        raise ValidationError("Bitte gib einen Kommentar ein.")
    if len(clean_body) > 2000:
        raise ValidationError("Der Kommentar ist zu lang.")
    try:
        parsed_thread_id = uuid.UUID(str(thread_id))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValidationError("Der Kommentarbezug ist ungültig.") from error

    thread = NoteCommentThread.objects.create(
        note=note,
        thread_id=parsed_thread_id,
        anchor_text=str(anchor_text or "").strip()[:200],
        created_by=user,
    )
    NoteComment.objects.create(thread=thread, author=user, body=clean_body)
    recipients = note_accessible_user_ids(note) - {user.id}
    if recipients:
        _create_note_activity_notifications(note, user, recipients, NoteActivityNotification.KIND_COMMENT)
    return thread


def add_comment_reply(user, note_id, thread_id, body):
    note = get_accessible_note(user, note_id)
    thread = _get_note_comment_thread(note, thread_id)
    clean_body = str(body or "").strip()
    if not clean_body:
        raise ValidationError("Bitte gib einen Kommentar ein.")
    if len(clean_body) > 2000:
        raise ValidationError("Der Kommentar ist zu lang.")
    NoteComment.objects.create(thread=thread, author=user, body=clean_body)
    recipients = note_accessible_user_ids(note) - {user.id}
    if recipients:
        _create_note_activity_notifications(note, user, recipients, NoteActivityNotification.KIND_COMMENT)
    return thread


def set_comment_thread_resolved(user, note_id, thread_id, resolved):
    note = get_accessible_note(user, note_id)
    thread = _get_note_comment_thread(note, thread_id)
    thread.is_resolved = bool(resolved)
    thread.resolved_by = user if resolved else None
    thread.resolved_at = timezone.now() if resolved else None
    thread.save(update_fields=["is_resolved", "resolved_by", "resolved_at"])
    return thread


def delete_comment_thread(user, note_id, thread_id):
    note = get_accessible_note(user, note_id)
    thread = _get_note_comment_thread(note, thread_id)
    if thread.created_by_id != user.id and note.owner_id != user.id:
        raise PermissionDenied("Nur die Ersteller*in oder die Notiz-Eigentümer*in darf diesen Kommentar löschen.")
    thread.delete()


def _delete_note_files(note):
    for attachment in note.attachments.all():
        if attachment.file:
            attachment.file.delete(save=False)
