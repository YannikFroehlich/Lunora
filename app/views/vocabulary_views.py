import json

from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from app.forms import VocabularyCardForm, VocabularyListForm
from app.models import VocabularyCard, VocabularyList
from app.services.system_settings import disabled_feature_response, feature_enabled
from app.services.user_preferences import format_user_datetime
from app.services.vocabulary import (
    LEITNER_INTERVAL_DAYS,
    MAX_BOX,
    MAX_CARDS_PER_LIST,
    box_distribution,
    export_csv,
    is_due,
    parse_bulk_cards,
    reset_progress,
    review_card,
    with_card_counts,
)


def _posted_id(request, key):
    value = request.POST.get(key, "")
    return int(value) if value.isdigit() else None


def _form_error_message(form):
    return " ".join(error for errors in form.errors.values() for error in errors)


@login_required
def vocabulary_lists(request):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools")

    form = VocabularyListForm()
    form_name = request.POST.get("form_name") if request.method == "POST" else None
    if form_name == "vocab_list_create":
        form = VocabularyListForm(request.POST)
        if form.is_valid():
            vocabulary_list = form.save(commit=False)
            vocabulary_list.owner = request.user
            vocabulary_list.save()
            return redirect("vocabulary_list_detail", list_id=vocabulary_list.pk)
    elif form_name == "vocab_list_delete":
        deleted, _ = VocabularyList.objects.filter(
            owner=request.user, pk=_posted_id(request, "list_id")
        ).delete()
        if deleted:
            django_messages.success(request, "Vokabelliste gelöscht.")
        return redirect("vocabulary_lists")

    lists = with_card_counts(VocabularyList.objects.filter(owner=request.user))
    return render(
        request,
        "app/vocabulary_lists.html",
        {
            "active_page": "tools",
            "form": form,
            "lists": [
                {
                    "id": item.pk,
                    "title": item.title,
                    "languages": f"{item.get_source_language_display()} → {item.get_target_language_display()}",
                    "card_count": item.card_count,
                    "due_count": item.due_count,
                    "learned_count": item.learned_count,
                    "updated_label": format_user_datetime(item.updated_at, request.user),
                }
                for item in lists
            ],
        },
    )


@login_required
def vocabulary_list_detail(request, list_id):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools")
    vocabulary_list = get_object_or_404(VocabularyList, owner=request.user, pk=list_id)

    form_name = request.POST.get("form_name") if request.method == "POST" else None
    list_form = VocabularyListForm(instance=vocabulary_list)
    card_form = VocabularyCardForm()
    bulk_text = ""

    if form_name:
        card_total = vocabulary_list.cards.count()
        if form_name == "vocab_list_update":
            list_form = VocabularyListForm(request.POST, instance=vocabulary_list)
            if list_form.is_valid():
                list_form.save()
                django_messages.success(request, "Liste gespeichert.")
                return redirect("vocabulary_list_detail", list_id=list_id)
        elif form_name == "vocab_card_add":
            card_form = VocabularyCardForm(request.POST)
            if card_total >= MAX_CARDS_PER_LIST:
                django_messages.error(
                    request, f"Eine Liste kann höchstens {MAX_CARDS_PER_LIST} Vokabeln enthalten."
                )
            elif card_form.is_valid():
                card = card_form.save(commit=False)
                card.vocabulary_list = vocabulary_list
                card.save()
                vocabulary_list.save(update_fields=["updated_at"])
                return redirect(f"{request.path}#vokabel-neu")
        elif form_name == "vocab_card_bulk":
            bulk_text = request.POST.get("lines", "")
            cards, errors = parse_bulk_cards(bulk_text)
            if errors:
                django_messages.error(request, " ".join(errors[:5]) + (" …" if len(errors) > 5 else ""))
            elif not cards:
                django_messages.error(request, "Keine Vokabeln zum Importieren gefunden.")
            elif card_total + len(cards) > MAX_CARDS_PER_LIST:
                django_messages.error(
                    request, f"Eine Liste kann höchstens {MAX_CARDS_PER_LIST} Vokabeln enthalten."
                )
            else:
                VocabularyCard.objects.bulk_create(
                    VocabularyCard(
                        vocabulary_list=vocabulary_list, term=term, translation=translation, note=note
                    )
                    for term, translation, note in cards
                )
                vocabulary_list.save(update_fields=["updated_at"])
                django_messages.success(request, f"{len(cards)} Vokabel(n) importiert.")
                return redirect("vocabulary_list_detail", list_id=list_id)
        elif form_name == "vocab_card_edit":
            card = get_object_or_404(vocabulary_list.cards, pk=_posted_id(request, "card_id"))
            edit_form = VocabularyCardForm(request.POST, instance=card)
            if edit_form.is_valid():
                edit_form.save()
                django_messages.success(request, "Vokabel gespeichert.")
            else:
                django_messages.error(request, _form_error_message(edit_form))
            return redirect("vocabulary_list_detail", list_id=list_id)
        elif form_name == "vocab_card_delete":
            vocabulary_list.cards.filter(pk=_posted_id(request, "card_id")).delete()
            return redirect("vocabulary_list_detail", list_id=list_id)
        elif form_name == "vocab_reset_progress":
            reset_progress(vocabulary_list)
            django_messages.success(request, "Lernfortschritt zurückgesetzt.")
            return redirect("vocabulary_list_detail", list_id=list_id)

    now = timezone.now()
    cards = list(vocabulary_list.cards.all())
    return render(
        request,
        "app/vocabulary_detail.html",
        {
            "active_page": "tools",
            "vocabulary_list": vocabulary_list,
            "list_form": list_form,
            "card_form": card_form,
            "bulk_text": bulk_text,
            "cards": [{"card": card, "is_due": is_due(card, now)} for card in cards],
            "card_count": len(cards),
            "due_count": sum(1 for card in cards if is_due(card, now)),
            "boxes": box_distribution(vocabulary_list),
            "max_box": MAX_BOX,
        },
    )


@login_required
def vocabulary_practice(request, list_id):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools")
    vocabulary_list = get_object_or_404(VocabularyList, owner=request.user, pk=list_id)
    now = timezone.now()
    return render(
        request,
        "app/vocabulary_practice.html",
        {
            "active_page": "tools",
            "vocabulary_list": vocabulary_list,
            "practice_payload": {
                "list": {
                    "title": vocabulary_list.title,
                    "source_language": vocabulary_list.source_language,
                    "target_language": vocabulary_list.target_language,
                    "source_label": vocabulary_list.get_source_language_display(),
                    "target_label": vocabulary_list.get_target_language_display(),
                },
                "max_box": MAX_BOX,
                "cards": [
                    {
                        "id": card.pk,
                        "term": card.term,
                        "translation": card.translation,
                        "note": card.note,
                        "box": card.box,
                        "due": is_due(card, now),
                    }
                    for card in vocabulary_list.cards.all()
                ],
            },
        },
    )


@login_required
@require_POST
def vocabulary_review_api(request, card_id):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools", json_response=True)
    card = VocabularyCard.objects.filter(vocabulary_list__owner=request.user, pk=card_id).first()
    if card is None:
        return JsonResponse({"ok": False, "error": "Vokabel nicht gefunden."}, status=404)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "Die Anfrage muss gültiges JSON sein."}, status=400)
    correct = payload.get("correct") if isinstance(payload, dict) else None
    if not isinstance(correct, bool):
        return JsonResponse({"ok": False, "error": "„correct“ muss true oder false sein."}, status=400)

    review_card(card, correct=correct)
    return JsonResponse({"ok": True, "box": card.box, "interval_days": LEITNER_INTERVAL_DAYS[card.box]})


@login_required
def vocabulary_export(request, list_id):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools")
    vocabulary_list = get_object_or_404(VocabularyList, owner=request.user, pk=list_id)
    filename = f"{slugify(vocabulary_list.title) or 'vokabeln'}.csv"
    response = HttpResponse(export_csv(vocabulary_list), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
