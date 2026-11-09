import json
from datetime import date

from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from app.forms import HabitForm, HabitReminderForm, ShoppingItemForm
from app.models import Habit, ShoppingItem, UmlDiagram
from app.services.habits import habit_items, toggle_habit_check
from app.services.notes import display_name
from app.services.shopping import SUGGESTIONS, classify_item, group_items
from app.services.shopping_lists import (
    end_shopping_list_share,
    list_owner_for,
    list_version,
    share_shopping_list,
    shopping_list_members,
    shopping_list_owners,
)
from app.services.shopping_products import MAX_SHOPPING_ITEMS, attach_products, scan_barcode
from app.services.system_settings import disabled_feature_response, feature_enabled
from app.services.uml_content import validate_uml_document
from app.services.user_preferences import format_user_datetime, localtime_for_user
from app.view_models import get_tools_context
from app.views.navigation import local_return_to

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
def habits(request):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools")

    today = localtime_for_user(profile_or_user=request.user).date()
    form_name = request.POST.get("form_name") if request.method == "POST" else None
    habit_form = HabitForm()
    if form_name == "habit_add":
        habit_form = HabitForm(request.POST)
        if habit_form.is_valid():
            habit = habit_form.save(commit=False)
            habit.owner = request.user
            habit.save()
            django_messages.success(request, "Gewohnheit angelegt.")
            return redirect("habits")
    elif form_name == "habit_toggle":
        try:
            day = date.fromisoformat(request.POST.get("date", ""))
        except ValueError:
            day = None
        if day:
            toggle_habit_check(request.user, request.POST.get("habit_id") or 0, day, today)
        return redirect(local_return_to(request) or "habits")
    elif form_name == "habit_reminder":
        habit = Habit.objects.filter(owner=request.user, pk=request.POST.get("habit_id") or 0).first()
        reminder_form = HabitReminderForm(request.POST, instance=habit)
        if habit and reminder_form.is_valid():
            reminder_form.save()
            django_messages.success(
                request,
                "Erinnerung gespeichert." if habit.reminder_time else "Erinnerung ausgeschaltet.",
            )
        return redirect("habits")
    elif form_name == "habit_delete":
        if Habit.objects.filter(owner=request.user, pk=request.POST.get("habit_id") or 0).delete()[0]:
            django_messages.success(request, "Gewohnheit gelöscht.")
        return redirect("habits")

    return render(
        request,
        "app/habits.html",
        {"active_page": "tools", "habits": habit_items(request.user, today), "habit_form": habit_form},
    )


def _posted_shopping_item(request, list_owner):
    """The posted item of the open list; a tampered id counts as a missing item."""
    try:
        return ShoppingItem.objects.filter(owner=list_owner, pk=request.POST.get("item_id")).first()
    except ValueError:
        return None


def _shopping_list_url(request, list_owner, query=""):
    """The page of this list, keeping `?list=` on someone else's."""
    params = [] if list_owner == request.user else [f"list={list_owner.pk}"]
    params += [query] if query else []
    return reverse("shopping_list") + ("?" + "&".join(params) if params else "")


@login_required
def shopping_list(request):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools", json_response="version" in request.GET)

    list_owner = list_owner_for(request.user, request.GET.get("list"))
    if list_owner is None:
        # A list that was never or is no longer shared with this user.
        django_messages.info(request, "Dieser Einkaufszettel ist nicht (mehr) mit dir geteilt.")
        return redirect("shopping_list")
    is_own = list_owner == request.user
    page_url = _shopping_list_url(request, list_owner)
    form_name = request.POST.get("form_name") if request.method == "POST" else None
    form = ShoppingItemForm()
    share_error = None
    if form_name == "shopping_add":
        form = ShoppingItemForm(request.POST)
        if form.is_valid():
            # Adding something already on the list (e.g. picked again from the suggestions)
            # brings that row back instead of writing it twice.
            existing = ShoppingItem.objects.filter(
                owner=list_owner, name__iexact=form.cleaned_data["name"]
            ).first()
            if existing is not None:
                existing.is_done = False
                existing.quantity = form.cleaned_data["quantity"] or existing.quantity
                existing.save(update_fields=["is_done", "quantity"])
                return redirect(_shopping_list_url(request, list_owner, "add=1"))
            if ShoppingItem.objects.filter(owner=list_owner).count() >= MAX_SHOPPING_ITEMS:
                form.add_error(None, f"Es passen höchstens {MAX_SHOPPING_ITEMS} Artikel auf den Zettel.")
            else:
                item = form.save(commit=False)
                item.owner = list_owner
                item.save()
                return redirect(_shopping_list_url(request, list_owner, "add=1"))
    elif form_name == "shopping_toggle":
        item = _posted_shopping_item(request, list_owner)
        if item is None:
            return redirect(page_url)
        # The button sends the state it wants, so two people ticking the same item at once both
        # end up with it ticked instead of one toggling it back.
        wanted = request.POST.get("done")
        item.is_done = wanted == "1" if wanted in ("0", "1") else not item.is_done
        item.save(update_fields=["is_done"])
        # A ticked item moves to "Im Wagen"; return to the aisle it came from, so a long list
        # doesn't jump around while shopping. An unticked one goes back to its own row.
        anchor = f"group-{classify_item(item.name)[0]}" if item.is_done else f"item-{item.pk}"
        return redirect(f"{page_url}#{anchor}")
    elif form_name == "shopping_delete":
        item = _posted_shopping_item(request, list_owner)
        if item is not None:
            item.delete()
        return redirect(page_url)
    elif form_name == "shopping_clear_done":
        ShoppingItem.objects.filter(owner=list_owner, is_done=True).delete()
        return redirect(page_url)
    elif form_name == "shopping_share" and is_own:
        share_error = share_shopping_list(request.user, request.POST.get("identifier", "")[:254])
        if share_error is None:
            django_messages.success(request, "Der Zettel ist jetzt geteilt.")
            return redirect(page_url)
    elif form_name == "shopping_unshare" and is_own:
        try:
            end_shopping_list_share(request.user.pk, int(request.POST.get("user_id", "")))
        except ValueError:
            pass
        return redirect(page_url)
    elif form_name == "shopping_leave" and not is_own:
        end_shopping_list_share(list_owner.pk, request.user.pk)
        return redirect("shopping_list")

    items = list(ShoppingItem.objects.filter(owner=list_owner))
    version = list_version(items)
    if "version" in request.GET:
        # The page polls this on a shared list to pick up the others' changes.
        return JsonResponse({"version": version}, headers={"Cache-Control": "no-store"})

    # Only after adding: on a phone, focus on opening would cover the list with the keyboard.
    if request.GET.get("add"):
        form.fields["name"].widget.attrs["autofocus"] = True

    members = shopping_list_members(list_owner)
    # Product details come from whoever on the list scanned it, the viewer's own first.
    product_users = list(dict.fromkeys([request.user, list_owner, *members]))
    attach_products(product_users, items)
    groups, done_items = group_items(items)
    return render(
        request,
        "app/shopping_list.html",
        {
            "active_page": "tools",
            "shopping_form": form,
            "item_count": len(items),
            "groups": groups,
            "done_items": done_items,
            "suggestions": SUGGESTIONS,
            "list_owner_name": display_name(list_owner),
            "is_own_list": is_own,
            "list_query": "" if is_own else f"?list={list_owner.pk}",
            "poll_url": f"{page_url}{'&' if '?' in page_url else '?'}version=1",
            "list_tabs": [
                {
                    "label": "Mein Zettel" if owner == request.user else f"Von {display_name(owner)}",
                    "url": _shopping_list_url(request, owner),
                    "active": owner == list_owner,
                }
                for owner in shopping_list_owners(request.user)
            ],
            "members": [{"id": member.pk, "name": display_name(member)} for member in members],
            "is_shared": bool(members),
            "list_version": version,
            "share_error": share_error,
        },
    )


@login_required
@require_POST
def shopping_scan(request):
    """One barcode from the scanner dialog: ticks off or adds the product, as JSON."""
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools", json_response=True)
    list_owner = list_owner_for(request.user, request.GET.get("list"))
    if list_owner is None:
        message = "Dieser Einkaufszettel ist nicht (mehr) mit dir geteilt."
        return JsonResponse({"status": "error", "message": message}, status=404)
    status, result = scan_barcode(
        request.user,
        request.POST.get("barcode", "")[:40],
        request.POST.get("mode", ""),
        request.POST.get("name", "")[:500],
        list_owner=list_owner,
    )
    return JsonResponse(result, status=status)


@login_required
def focus_timer(request):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools")
    return render(request, "app/focus_timer.html", {"active_page": "tools"})


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
    except (ValueError, RecursionError):  # ValueError covers bad encoding, bad JSON and giant integers.
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
