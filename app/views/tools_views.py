import json

from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from app.models import UmlDiagram
from app.services.system_settings import disabled_feature_response, feature_enabled
from app.services.uml_content import validate_uml_document
from app.services.user_preferences import format_user_datetime
from app.view_models import get_tools_context

UML_TITLE_MAX_LENGTH = UmlDiagram._meta.get_field("title").max_length


def _clean_title(value):
    title = (value or "").strip()
    if not title:
        raise ValidationError("Der Titel darf nicht leer sein.")
    if len(title) > UML_TITLE_MAX_LENGTH:
        raise ValidationError(f"Der Titel darf höchstens {UML_TITLE_MAX_LENGTH} Zeichen lang sein.")
    return title


def _posted_diagram_id(request):
    value = request.POST.get("diagram_id", "")
    return int(value) if value.isdigit() else None


@login_required
def tools(request):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools")
    return render(request, "app/tools.html", get_tools_context(request.user))


@login_required
def uml_diagrams(request):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools")

    form_name = request.POST.get("form_name") if request.method == "POST" else None
    if form_name == "uml_create":
        try:
            title = _clean_title(request.POST.get("title"))
        except ValidationError as error:
            django_messages.error(request, " ".join(error.messages))
            return redirect("uml_diagrams")
        diagram = UmlDiagram.objects.create(owner=request.user, title=title)
        return redirect("uml_diagram_editor", diagram_id=diagram.pk)
    if form_name == "uml_duplicate":
        source = get_object_or_404(UmlDiagram, owner=request.user, pk=_posted_diagram_id(request))
        copy_title = f"{source.title} (Kopie)"[:UML_TITLE_MAX_LENGTH]
        diagram = UmlDiagram.objects.create(owner=request.user, title=copy_title, document=source.document)
        django_messages.success(request, "Diagramm dupliziert.")
        return redirect("uml_diagram_editor", diagram_id=diagram.pk)
    if form_name == "uml_delete":
        deleted, _ = UmlDiagram.objects.filter(owner=request.user, pk=_posted_diagram_id(request)).delete()
        if deleted:
            django_messages.success(request, "Diagramm gelöscht.")
        return redirect("uml_diagrams")

    diagrams = [
        {
            "id": diagram.pk,
            "title": diagram.title,
            "class_count": len(diagram.document.get("classes", [])),
            "relation_count": len(diagram.document.get("relations", [])),
            "updated_label": format_user_datetime(diagram.updated_at, request.user),
        }
        for diagram in UmlDiagram.objects.filter(owner=request.user)
    ]
    return render(
        request,
        "app/uml_diagrams.html",
        {"active_page": "tools", "diagrams": diagrams, "title_max_length": UML_TITLE_MAX_LENGTH},
    )


@login_required
def uml_diagram_editor(request, diagram_id):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools")
    diagram = get_object_or_404(UmlDiagram, owner=request.user, pk=diagram_id)
    return render(
        request,
        "app/uml_editor.html",
        {
            "active_page": "tools",
            "diagram": diagram,
            "diagram_payload": {"id": diagram.pk, "title": diagram.title, "document": diagram.document},
            "title_max_length": UML_TITLE_MAX_LENGTH,
        },
    )


@login_required
@require_http_methods(["PUT"])
def uml_diagram_api(request, diagram_id):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools", json_response=True)
    diagram = UmlDiagram.objects.filter(owner=request.user, pk=diagram_id).first()
    if diagram is None:
        return JsonResponse({"ok": False, "error": "Diagramm nicht gefunden."}, status=404)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "Die Anfrage muss gültiges JSON sein."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"ok": False, "error": "Die Anfrage ist ungültig."}, status=400)

    try:
        title = _clean_title(payload.get("title"))
        document = validate_uml_document(payload.get("document"))
    except ValidationError as error:
        return JsonResponse({"ok": False, "error": " ".join(error.messages)}, status=400)

    diagram.title = title
    diagram.document = document
    diagram.save(update_fields=["title", "document", "updated_at"])
    return JsonResponse({"ok": True, "updated_label": format_user_datetime(diagram.updated_at, request.user)})
