import json
import re

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_http_methods

from app.models import Note, NoteAttachment, NoteShare, NoteVersion, Profile
from app.services.note_files import NOTE_FILE_ACCEPT, NOTE_IMAGE_ACCEPT
from app.services.note_pdf import note_pdf_filename, render_note_pdf
from app.services.notes import (
    NoteConflictError,
    accessible_notes,
    create_attachment,
    create_note,
    display_name,
    duplicate_note,
    get_accessible_note,
    mark_shared_note_opened,
    remove_note_share,
    restore_note_version,
    save_note,
    serialize_note,
    serialize_version,
    set_personal_state,
    set_trash_state,
    share_note,
)
from app.services.system_settings import disabled_feature_response, feature_enabled


SHORTCUT_ACTIONS = {
    "save", "undo", "redo", "bold", "italic", "underline", "strike", "link",
    "paragraph", "heading1", "heading2", "heading3", "alignLeft", "alignCenter",
    "alignRight", "alignJustify", "bulletList", "orderedList", "taskList", "indent",
    "outdent", "fontFamily", "fontSize", "textColor", "highlight", "lineHeight",
    "image", "attachment", "horizontalRule", "insertTable", "addRowBefore", "addRowAfter",
    "addColumnBefore", "addColumnAfter", "deleteRow", "deleteColumn", "deleteTable",
    "mergeCells", "splitCell", "clearFormat", "newNote", "focusSearch", "pin", "archive",
    "duplicate", "trash", "share", "versions", "exportPdf", "shortcutSettings",
}
RESERVED_SHORTCUTS = {"Mod+W", "Mod+T", "Mod+N", "Mod+L", "Mod+R", "Mod+Shift+N"}
SHORTCUT_RE = re.compile(r"^(?=.{3,40}$)(?=.*(?:Mod|Ctrl|Alt|Shift)\+)[A-Za-z0-9+\[\]\\\-]+$")


def _json_body(request):
    try:
        return json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValidationError("Die Anfrage enthält ungültiges JSON.") from error


def _error_response(error, *, status=400):
    if isinstance(error, ValidationError):
        message = "; ".join(error.messages)
    else:
        message = str(error)
    return JsonResponse({"ok": False, "error": message}, status=status)


def _feature_guard(request, *, json_response=False):
    if feature_enabled("notes"):
        return None
    return disabled_feature_response(request, "notes", json_response=json_response)


@login_required
def notes(request, note_id=None):
    disabled = _feature_guard(request)
    if disabled:
        return disabled

    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "all")
    scope = request.GET.get("scope", "all")
    tag_filter = request.GET.get("tag", "").strip().casefold()
    sort = request.GET.get("sort", "updated")

    queryset = accessible_notes(request.user, include_deleted=status == "trash")
    if status == "trash":
        queryset = queryset.filter(owner=request.user, deleted_at__isnull=False)
    else:
        queryset = queryset.filter(deleted_at__isnull=True)
        if status == "pinned":
            queryset = queryset.filter(state_is_pinned=True, state_is_archived=False)
        elif status == "archived":
            queryset = queryset.filter(state_is_archived=True)
        else:
            queryset = queryset.filter(state_is_archived=False)
    if scope == "shared":
        queryset = queryset.exclude(owner=request.user)
    elif scope == "owned":
        queryset = queryset.filter(owner=request.user)
    if query:
        queryset = queryset.filter(
            Q(title__icontains=query) | Q(plain_text__icontains=query) | Q(tags__display_name__icontains=query)
        ).distinct()
    if tag_filter:
        queryset = queryset.filter(tags__normalized_name=tag_filter)
    order_map = {
        "created": ("-created_at", "-id"),
        "title": ("title", "id"),
        "updated": ("-state_is_pinned", "-updated_at", "-id"),
    }
    note_list = list(queryset.order_by(*order_map.get(sort, order_map["updated"]))[:200])

    selected_note = None
    if note_id:
        try:
            selected_note = get_accessible_note(
                request.user,
                note_id,
                allow_deleted=status == "trash",
            )
        except Note.DoesNotExist:
            return redirect("notes")
    if selected_note:
        mark_shared_note_opened(request.user, selected_note)

    all_visible = accessible_notes(request.user).filter(deleted_at__isnull=True)
    available_tags = sorted(
        {tag.display_name for note in all_visible.prefetch_related("tags") for tag in note.tags.all()},
        key=str.casefold,
    )
    context = {
        "active_page": "notes",
        "note_items": [serialize_note(note, request.user, include_document=False) for note in note_list],
        "selected_note_data": serialize_note(selected_note, request.user) if selected_note else None,
        "query": query,
        "current_status": status,
        "current_scope": scope,
        "current_tag": tag_filter,
        "current_sort": sort,
        "available_tags": available_tags,
        "note_image_accept": NOTE_IMAGE_ACCEPT,
        "note_file_accept": NOTE_FILE_ACCEPT,
        "shortcut_overrides": getattr(getattr(request.user, "profile", None), "note_shortcuts", {}),
    }
    return render(request, "app/notes.html", context)


@login_required
@require_http_methods(["GET"])
def note_pdf_export(request, note_id):
    disabled = _feature_guard(request)
    if disabled:
        return disabled
    try:
        note = get_accessible_note(request.user, note_id, allow_deleted=True)
    except Note.DoesNotExist:
        raise Http404("Notiz nicht gefunden.")
    response = FileResponse(
        render_note_pdf(note),
        as_attachment=True,
        filename=note_pdf_filename(note),
        content_type="application/pdf",
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_http_methods(["POST"])
def note_create_api(request):
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
    try:
        payload = _json_body(request)
        note = create_note(request.user, title=payload.get("title", "Unbenannte Notiz"))
        return JsonResponse({"ok": True, "note": serialize_note(note, request.user)}, status=201)
    except ValidationError as error:
        return _error_response(error)


@login_required
@require_http_methods(["GET", "PATCH"])
def note_detail_api(request, note_id):
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
    try:
        if request.method == "GET":
            note = get_accessible_note(request.user, note_id, allow_deleted=True)
            return JsonResponse({"ok": True, "note": serialize_note(note, request.user)})
        payload = _json_body(request)
        note = save_note(
            request.user,
            note_id,
            title=payload.get("title"),
            document=payload.get("document"),
            tags=payload.get("tags", []),
            base_revision=int(payload.get("base_revision", 0)),
            conflict_resolution=bool(payload.get("conflict_resolution")),
        )
        return JsonResponse({"ok": True, "note": serialize_note(note, request.user)})
    except NoteConflictError as error:
        return JsonResponse(
            {"ok": False, "error": "revision_conflict", "note": serialize_note(error.note, request.user)},
            status=409,
        )
    except Note.DoesNotExist:
        return _error_response("Notiz nicht gefunden.", status=404)
    except PermissionDenied as error:
        return _error_response(error, status=403)
    except (ValidationError, TypeError, ValueError) as error:
        return _error_response(error)


@login_required
@require_http_methods(["POST"])
def note_action_api(request, note_id):
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
    try:
        payload = _json_body(request)
        action = payload.get("action")
        if action in {"pin", "unpin", "archive", "unarchive"}:
            note = set_personal_state(request.user, note_id, action=action)
            return JsonResponse({"ok": True, "note": serialize_note(note, request.user)})
        if action in {"trash", "restore", "purge"}:
            note = set_trash_state(request.user, note_id, action=action)
            return JsonResponse({"ok": True, "deleted": note is None})
        if action == "duplicate":
            note = duplicate_note(
                request.user,
                note_id,
                title=payload.get("title"),
                document=payload.get("document"),
                tags=payload.get("tags"),
            )
            return JsonResponse({"ok": True, "note": serialize_note(note, request.user)}, status=201)
        raise ValidationError("Unbekannte Notizaktion.")
    except Note.DoesNotExist:
        return _error_response("Notiz nicht gefunden.", status=404)
    except PermissionDenied as error:
        return _error_response(error, status=403)
    except ValidationError as error:
        return _error_response(error)


@login_required
@require_http_methods(["GET", "POST"])
def note_shares_api(request, note_id):
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
    try:
        note = get_accessible_note(request.user, note_id)
        if note.owner_id != request.user.id:
            raise PermissionDenied("Nur der Eigentümer darf Freigaben verwalten.")
        if request.method == "POST":
            payload = _json_body(request)
            share_note(request.user, note_id, payload.get("user_id"), payload.get("role"))
            note = get_accessible_note(request.user, note_id)
        return JsonResponse({"ok": True, "shares": serialize_note(note, request.user)["shares"]})
    except Note.DoesNotExist:
        return _error_response("Notiz nicht gefunden.", status=404)
    except PermissionDenied as error:
        return _error_response(error, status=403)
    except ValidationError as error:
        return _error_response(error)


@login_required
@require_http_methods(["DELETE"])
def note_share_delete_api(request, note_id, user_id):
    try:
        remove_note_share(request.user, note_id, user_id)
        return JsonResponse({"ok": True})
    except Note.DoesNotExist:
        return _error_response("Notiz nicht gefunden.", status=404)
    except PermissionDenied as error:
        return _error_response(error, status=403)


@login_required
@require_http_methods(["GET"])
def note_share_candidates_api(request):
    query = request.GET.get("q", "").strip()
    if len(query) < 2:
        return JsonResponse({"ok": True, "users": []})
    users = (
        get_user_model().objects.filter(is_active=True)
        .exclude(pk=request.user.id)
        .filter(Q(first_name__icontains=query) | Q(username__icontains=query) | Q(email__icontains=query))
        .select_related("profile")[:10]
    )
    return JsonResponse(
        {
            "ok": True,
            "users": [{"id": user.id, "name": display_name(user), "email": user.email} for user in users],
        }
    )


@login_required
@require_http_methods(["POST"])
def note_attachment_upload_api(request, note_id):
    try:
        upload = request.FILES.get("file")
        kind = request.POST.get("kind", "file")
        if kind not in {NoteAttachment.KIND_IMAGE, NoteAttachment.KIND_FILE}:
            raise ValidationError("Der Upload-Typ ist ungültig.")
        attachment = create_attachment(request.user, note_id, upload, kind=kind)
        return JsonResponse(
            {
                "ok": True,
                "attachment": {
                    "id": str(attachment.file_id),
                    "name": attachment.original_name,
                    "size": attachment.size,
                    "kind": attachment.kind,
                    "url": f"/notes/attachments/{attachment.file_id}/{'inline' if kind == 'image' else 'download'}/",
                },
            },
            status=201,
        )
    except Note.DoesNotExist:
        return _error_response("Notiz nicht gefunden.", status=404)
    except PermissionDenied as error:
        return _error_response(error, status=403)
    except ValidationError as error:
        return _error_response(error)


@login_required
@require_http_methods(["GET"])
def note_attachment_download(request, file_id, disposition):
    attachment = NoteAttachment.objects.select_related("note").filter(file_id=file_id).first()
    if not attachment or disposition not in {"inline", "download"}:
        return _error_response("Datei nicht gefunden.", status=404)
    try:
        get_accessible_note(request.user, attachment.note_id)
    except Note.DoesNotExist:
        return _error_response("Datei nicht gefunden.", status=404)
    response = FileResponse(
        attachment.file.open("rb"),
        content_type=attachment.content_type or "application/octet-stream",
    )
    response["Content-Disposition"] = content_disposition_header(
        disposition == "download",
        attachment.original_name,
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_http_methods(["GET"])
def note_versions_api(request, note_id):
    try:
        note = get_accessible_note(request.user, note_id)
        return JsonResponse({"ok": True, "versions": [serialize_version(item) for item in note.versions.all()[:100]]})
    except Note.DoesNotExist:
        return _error_response("Notiz nicht gefunden.", status=404)


@login_required
@require_http_methods(["POST"])
def note_version_restore_api(request, note_id, version_id):
    try:
        payload = _json_body(request)
        note = restore_note_version(
            request.user,
            note_id,
            version_id,
            base_revision=int(payload.get("base_revision", 0)),
        )
        return JsonResponse({"ok": True, "note": serialize_note(note, request.user)})
    except NoteConflictError as error:
        return JsonResponse(
            {"ok": False, "error": "revision_conflict", "note": serialize_note(error.note, request.user)}, status=409
        )
    except (Note.DoesNotExist, NoteVersion.DoesNotExist):
        return _error_response("Notiz oder Version nicht gefunden.", status=404)
    except PermissionDenied as error:
        return _error_response(error, status=403)
    except (ValidationError, TypeError, ValueError) as error:
        return _error_response(error)


@login_required
@require_http_methods(["GET", "PATCH"])
def note_shortcuts_api(request):
    profile, _created = Profile.objects.get_or_create(
        user=request.user,
        defaults={"display_name": request.user.first_name or request.user.get_username()},
    )
    if request.method == "GET":
        return JsonResponse({"ok": True, "shortcuts": profile.note_shortcuts})
    try:
        payload = _json_body(request)
        shortcuts = payload.get("shortcuts", {})
        if not isinstance(shortcuts, dict) or set(shortcuts) - SHORTCUT_ACTIONS:
            raise ValidationError("Die Shortcut-Konfiguration enthält unbekannte Aktionen.")
        clean = {}
        used = set()
        for action, value in shortcuts.items():
            shortcut = str(value).strip()
            if not shortcut:
                clean[action] = ""
                continue
            if not SHORTCUT_RE.fullmatch(shortcut) or shortcut in RESERVED_SHORTCUTS:
                raise ValidationError(f"Der Shortcut für „{action}“ ist nicht erlaubt.")
            folded = shortcut.casefold()
            if folded in used:
                raise ValidationError("Ein Shortcut darf nicht doppelt vergeben werden.")
            used.add(folded)
            clean[action] = shortcut
        profile.note_shortcuts = clean
        profile.save(update_fields=["note_shortcuts", "updated_at"])
        return JsonResponse({"ok": True, "shortcuts": clean})
    except ValidationError as error:
        return _error_response(error)
