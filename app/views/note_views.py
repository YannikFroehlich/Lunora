import json
import re
from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_http_methods

from app.models import (
    Note,
    NoteAttachment,
    NoteCommentThread,
    NoteFolder,
    NoteShare,
    NoteTemplate,
    NoteUserState,
    NoteVersion,
    Profile,
)
from app.services.note_content import NOTE_TEMPLATE_LABELS, NOTE_TEMPLATES
from app.services.note_files import NOTE_FILE_ACCEPT, NOTE_IMAGE_ACCEPT
from app.services.note_markdown import note_markdown_filename, render_note_markdown
from app.services.note_pdf import note_pdf_filename, render_note_pdf
from app.services.note_search import search_notes
from app.services.notes import (
    NoteConflictError,
    accessible_notes,
    add_comment_reply,
    bulk_move_notes_to_folder,
    bulk_set_notes_state,
    create_attachment,
    create_comment_thread,
    create_note,
    create_note_folder,
    create_note_template,
    delete_comment_thread,
    delete_note_folder,
    delete_note_template,
    display_name,
    duplicate_note,
    get_accessible_note,
    list_comment_threads,
    list_note_templates,
    mark_shared_note_opened,
    move_note_tree_item,
    note_accessible_user_ids,
    remove_note_share,
    rename_note_folder,
    restore_note_version,
    save_note,
    serialize_note,
    serialize_note_folder,
    serialize_note_template,
    serialize_version,
    set_comment_thread_resolved,
    set_note_folder,
    set_note_style,
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
    "image", "attachment", "horizontalRule", "insertMath", "insertTable", "addRowBefore", "addRowAfter",
    "addColumnBefore", "addColumnAfter", "deleteRow", "deleteColumn", "deleteTable",
    "mergeCells", "splitCell", "clearFormat", "newNote", "focusSearch", "pin", "archive", "style",
    "duplicate", "trash", "share", "versions", "exportPdf", "exportMarkdown", "saveAsTemplate", "shortcutSettings",
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


def _optional_positive_int(value, label):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValidationError(f"{label} ist ungültig.")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValidationError(f"{label} ist ungültig.") from error
    if result <= 0:
        raise ValidationError(f"{label} ist ungültig.")
    return result


def _build_note_navigation(user, note_items, selected_folder_id=None, *, custom_order=True):
    folders = list(NoteFolder.objects.filter(owner=user).values("id", "name", "parent_id", "position"))
    nodes = {
        item["id"]: {
            "kind": "folder",
            "id": item["id"],
            "name": item["name"],
            "parent_id": item["parent_id"],
            "position": item["position"],
            "items": [],
            "child_folders": [],
            "contains_selected": False,
        }
        for item in folders
    }
    tree_items = []
    for node in nodes.values():
        parent = nodes.get(node["parent_id"])
        if parent and parent is not node:
            parent["items"].append(node)
            parent["child_folders"].append(node)
        else:
            tree_items.append(node)

    for rank, note in enumerate(note_items):
        note["kind"] = "note"
        note["view_rank"] = rank
        folder = nodes.get(note.get("folder_id"))
        if folder:
            folder["items"].append(note)
        else:
            tree_items.append(note)

    def tree_sort_key(item):
        if custom_order:
            position = item.get("position")
            return (position if position is not None else 2**31, 0 if item["kind"] == "note" else 1, item["id"])
        if item["kind"] == "note":
            return (0, item["view_rank"], item["id"])
        return (1, item["name"].casefold(), item["id"])

    tree_items.sort(key=tree_sort_key)
    for node in nodes.values():
        node["items"].sort(key=tree_sort_key)
        node["child_folders"].sort(key=lambda item: (item["name"].casefold(), item["id"]))

    current_id = selected_folder_id
    visited = set()
    while current_id in nodes and current_id not in visited:
        visited.add(current_id)
        nodes[current_id]["contains_selected"] = True
        current_id = nodes[current_id]["parent_id"]

    options = []
    traversed = set()

    def visit(node, depth):
        if node["id"] in traversed:
            return
        traversed.add(node["id"])
        options.append({"id": node["id"], "label": f"{'— ' * depth}{node['name']}"})
        for child in node["child_folders"]:
            visit(child, depth + 1)

    root_folders = [item for item in tree_items if item["kind"] == "folder"]
    root_folders.sort(key=lambda item: (item["name"].casefold(), item["id"]))
    for root in root_folders:
        visit(root, 0)
    for node in nodes.values():
        if node["id"] not in traversed:
            if node not in tree_items:
                tree_items.append(node)
            visit(node, 0)
    return tree_items, options


@login_required
def notes(request, note_id=None):
    disabled = _feature_guard(request)
    if disabled:
        return disabled

    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "all")
    scope = request.GET.get("scope", "all")
    sort = request.GET.get("sort", "custom")
    # Manual tree ordering says nothing about a filtered result set, so a search
    # ranks by relevance instead. An explicit sort choice still wins.
    if query and sort == "custom":
        sort = "relevance"
    elif sort == "relevance" and not query:
        sort = "custom"

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
        queryset = search_notes(queryset, query)
    order_map = {
        "custom": ("state_position", "id"),
        "created": ("-created_at", "-id"),
        "relevance": ("-search_rank", "-updated_at", "-id"),
        "title": ("title", "id"),
        "updated": ("-state_is_pinned", "-updated_at", "-id"),
    }
    note_list = list(queryset.order_by(*order_map.get(sort, order_map["custom"]))[:200])

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

    note_items = [serialize_note(note, request.user, include_document=False) for note in note_list]
    selected_note_data = serialize_note(selected_note, request.user) if selected_note else None
    tree_items, folder_options = _build_note_navigation(
        request.user,
        note_items,
        selected_note_data.get("folder_id") if selected_note_data else None,
        custom_order=sort == "custom",
    )
    context = {
        "active_page": "notes",
        "note_items": note_items,
        "tree_items": tree_items,
        "folder_options": folder_options,
        "selected_note_data": selected_note_data,
        "query": query,
        "current_status": status,
        "current_scope": scope,
        "current_sort": sort,
        "note_image_accept": NOTE_IMAGE_ACCEPT,
        "note_file_accept": NOTE_FILE_ACCEPT,
        "note_templates": [{"key": key, "label": NOTE_TEMPLATE_LABELS[key]} for key in NOTE_TEMPLATES],
        "custom_note_templates": [serialize_note_template(t) for t in list_note_templates(request.user)],
        "shortcut_overrides": getattr(getattr(request.user, "profile", None), "note_shortcuts", {}),
        "note_color_choices": NoteUserState.COLOR_CHOICES,
        "note_icon_choices": NoteUserState.ICON_CHOICES,
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
@require_http_methods(["GET"])
def note_markdown_export(request, note_id):
    disabled = _feature_guard(request)
    if disabled:
        return disabled
    try:
        note = get_accessible_note(request.user, note_id, allow_deleted=True)
    except Note.DoesNotExist:
        raise Http404("Notiz nicht gefunden.")
    response = FileResponse(
        BytesIO(render_note_markdown(note).encode("utf-8")),
        as_attachment=True,
        filename=note_markdown_filename(note),
        content_type="text/markdown; charset=utf-8",
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
        folder_id = _optional_positive_int(payload.get("folder_id"), "Der Ordner")
        custom_template_id = _optional_positive_int(payload.get("custom_template_id"), "Die Vorlage")
        if custom_template_id is not None:
            note = create_note(
                request.user,
                title=payload.get("title", "Unbenannte Notiz"),
                custom_template_id=custom_template_id,
                folder_id=folder_id,
            )
        else:
            template = payload.get("template", "blank")
            if template not in NOTE_TEMPLATES:
                raise ValidationError("Unbekannte Vorlage.")
            note = create_note(
                request.user,
                title=payload.get("title", "Unbenannte Notiz"),
                template=template,
                folder_id=folder_id,
            )
        return JsonResponse({"ok": True, "note": serialize_note(note, request.user)}, status=201)
    except NoteFolder.DoesNotExist:
        return _error_response("Ordner nicht gefunden.", status=404)
    except NoteTemplate.DoesNotExist:
        return _error_response("Vorlage nicht gefunden.", status=404)
    except ValidationError as error:
        return _error_response(error)


@login_required
@require_http_methods(["POST"])
def note_template_create_api(request):
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
    try:
        payload = _json_body(request)
        note_id = _optional_positive_int(payload.get("note_id"), "Die Notiz")
        if note_id is None:
            raise ValidationError("Es wurde keine Notiz angegeben.")
        template = create_note_template(request.user, note_id, name=payload.get("name"))
        return JsonResponse({"ok": True, "template": serialize_note_template(template)}, status=201)
    except Note.DoesNotExist:
        return _error_response("Notiz nicht gefunden.", status=404)
    except ValidationError as error:
        return _error_response(error)


@login_required
@require_http_methods(["DELETE"])
def note_template_detail_api(request, template_id):
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
    try:
        delete_note_template(request.user, template_id)
        return JsonResponse({"ok": True})
    except NoteTemplate.DoesNotExist:
        return _error_response("Vorlage nicht gefunden.", status=404)


@login_required
@require_http_methods(["POST"])
def note_folders_api(request):
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
    try:
        payload = _json_body(request)
        parent_id = _optional_positive_int(payload.get("parent_id"), "Der übergeordnete Ordner")
        folder = create_note_folder(request.user, name=payload.get("name"), parent_id=parent_id)
        return JsonResponse({"ok": True, "folder": serialize_note_folder(folder)}, status=201)
    except NoteFolder.DoesNotExist:
        return _error_response("Ordner nicht gefunden.", status=404)
    except ValidationError as error:
        return _error_response(error)


@login_required
@require_http_methods(["PATCH", "DELETE"])
def note_folder_detail_api(request, folder_id):
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
    try:
        if request.method == "DELETE":
            delete_note_folder(request.user, folder_id)
            return JsonResponse({"ok": True})
        payload = _json_body(request)
        folder = rename_note_folder(request.user, folder_id, name=payload.get("name"))
        return JsonResponse({"ok": True, "folder": serialize_note_folder(folder)})
    except NoteFolder.DoesNotExist:
        return _error_response("Ordner nicht gefunden.", status=404)
    except ValidationError as error:
        return _error_response(error)


@login_required
@require_http_methods(["PATCH"])
def note_folder_assignment_api(request, note_id):
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
    try:
        payload = _json_body(request)
        folder_id = _optional_positive_int(payload.get("folder_id"), "Der Ordner")
        note = set_note_folder(request.user, note_id, folder_id)
        return JsonResponse({"ok": True, "note": serialize_note(note, request.user)})
    except Note.DoesNotExist:
        return _error_response("Notiz nicht gefunden.", status=404)
    except NoteFolder.DoesNotExist:
        return _error_response("Ordner nicht gefunden.", status=404)
    except ValidationError as error:
        return _error_response(error)


@login_required
@require_http_methods(["PATCH"])
def note_tree_move_api(request):
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
    try:
        payload = _json_body(request)
        item_type = payload.get("item_type")
        target_type = payload.get("target_type")
        item_id = _optional_positive_int(payload.get("item_id"), "Das Element")
        target_id = _optional_positive_int(payload.get("target_id"), "Das Verschiebeziel")
        if item_id is None:
            raise ValidationError("Das Element ist ungültig.")
        item = move_note_tree_item(
            request.user,
            item_type=item_type,
            item_id=item_id,
            placement=payload.get("placement"),
            target_type=target_type,
            target_id=target_id,
        )
        serialized = (
            serialize_note(item, request.user)
            if item_type == "note"
            else serialize_note_folder(item)
        )
        return JsonResponse({"ok": True, "item_type": item_type, "item": serialized})
    except Note.DoesNotExist:
        return _error_response("Notiz nicht gefunden.", status=404)
    except NoteFolder.DoesNotExist:
        return _error_response("Ordner nicht gefunden.", status=404)
    except (PermissionDenied, ValidationError) as error:
        return _error_response(error, status=403 if isinstance(error, PermissionDenied) else 400)


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
        if action == "style":
            note = set_note_style(request.user, note_id, color=payload.get("color"), icon=payload.get("icon"))
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
@require_http_methods(["POST"])
def note_bulk_action_api(request):
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
    try:
        payload = _json_body(request)
        raw_ids = payload.get("note_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            raise ValidationError("Es wurden keine Notizen ausgewählt.")
        if len(raw_ids) > 200:
            raise ValidationError("Es können höchstens 200 Notizen auf einmal bearbeitet werden.")
        note_ids = [_optional_positive_int(value, "Die Notiz") for value in raw_ids]
        if any(value is None for value in note_ids):
            raise ValidationError("Ungültige Notiz-ID.")

        action = payload.get("action")
        if action == "move_folder":
            folder_id = _optional_positive_int(payload.get("folder_id"), "Der Ordner")
            updated_ids, skipped_ids = bulk_move_notes_to_folder(request.user, note_ids, folder_id)
        elif action in {"pin", "unpin", "archive", "unarchive", "trash", "restore", "purge"}:
            updated_ids, skipped_ids = bulk_set_notes_state(request.user, note_ids, action=action)
        else:
            raise ValidationError("Unbekannte Notizaktion.")
        return JsonResponse({"ok": True, "updated_ids": updated_ids, "skipped_ids": skipped_ids})
    except NoteFolder.DoesNotExist:
        return _error_response("Ordner nicht gefunden.", status=404)
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
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
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
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
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
@require_http_methods(["GET"])
def note_mention_candidates_api(request, note_id):
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
    try:
        note = get_accessible_note(request.user, note_id)
    except Note.DoesNotExist:
        return _error_response("Notiz nicht gefunden.", status=404)

    query = request.GET.get("q", "").strip()
    users = (
        get_user_model().objects.filter(is_active=True, pk__in=note_accessible_user_ids(note))
        .exclude(pk=request.user.id)
        .select_related("profile")
    )
    if query:
        users = users.filter(Q(first_name__icontains=query) | Q(username__icontains=query) | Q(email__icontains=query))
    return JsonResponse(
        {
            "ok": True,
            "users": [{"id": user.id, "name": display_name(user)} for user in users[:10]],
        }
    )


@login_required
@require_http_methods(["GET"])
def note_link_candidates_api(request, note_id):
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
    try:
        get_accessible_note(request.user, note_id)
    except Note.DoesNotExist:
        return _error_response("Notiz nicht gefunden.", status=404)

    query = request.GET.get("q", "").strip()
    notes_queryset = accessible_notes(request.user).filter(deleted_at__isnull=True).exclude(pk=note_id)
    if query:
        notes_queryset = notes_queryset.filter(title__icontains=query)
    return JsonResponse(
        {
            "ok": True,
            "notes": [{"id": note.id, "title": note.title} for note in notes_queryset.order_by("-updated_at")[:10]],
        }
    )


@login_required
@require_http_methods(["GET", "POST"])
def note_comments_api(request, note_id):
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
    try:
        note = get_accessible_note(request.user, note_id)
        if request.method == "POST":
            payload = _json_body(request)
            create_comment_thread(
                request.user,
                note_id,
                thread_id=payload.get("thread_id"),
                anchor_text=payload.get("anchor_text", ""),
                body=payload.get("body"),
            )
        return JsonResponse({"ok": True, "threads": list_comment_threads(note)})
    except Note.DoesNotExist:
        return _error_response("Notiz nicht gefunden.", status=404)
    except PermissionDenied as error:
        return _error_response(error, status=403)
    except ValidationError as error:
        return _error_response(error)


@login_required
@require_http_methods(["POST", "DELETE"])
def note_comment_thread_api(request, note_id, thread_id):
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
    try:
        if request.method == "DELETE":
            delete_comment_thread(request.user, note_id, thread_id)
            return JsonResponse({"ok": True})

        payload = _json_body(request)
        action = payload.get("action")
        if action == "reply":
            add_comment_reply(request.user, note_id, thread_id, payload.get("body"))
        elif action == "resolve":
            set_comment_thread_resolved(request.user, note_id, thread_id, True)
        elif action == "reopen":
            set_comment_thread_resolved(request.user, note_id, thread_id, False)
        else:
            raise ValidationError("Unbekannte Kommentaraktion.")
        note = get_accessible_note(request.user, note_id)
        return JsonResponse({"ok": True, "threads": list_comment_threads(note)})
    except (Note.DoesNotExist, NoteCommentThread.DoesNotExist):
        return _error_response("Kommentar nicht gefunden.", status=404)
    except PermissionDenied as error:
        return _error_response(error, status=403)
    except ValidationError as error:
        return _error_response(error)


@login_required
@require_http_methods(["POST"])
def note_attachment_upload_api(request, note_id):
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
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
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
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
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
    try:
        note = get_accessible_note(request.user, note_id)
        return JsonResponse({"ok": True, "versions": [serialize_version(item) for item in note.versions.all()[:100]]})
    except Note.DoesNotExist:
        return _error_response("Notiz nicht gefunden.", status=404)


@login_required
@require_http_methods(["POST"])
def note_version_restore_api(request, note_id, version_id):
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
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
    disabled = _feature_guard(request, json_response=True)
    if disabled:
        return disabled
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
