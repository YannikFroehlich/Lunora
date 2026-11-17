from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from app.forms import WishListForm, WishListItemForm
from app.models import WishList
from app.services.safe_fetch import FetchError
from app.services.system_settings import disabled_feature_response, feature_enabled
from app.services.user_preferences import format_user_datetime
from app.services.wishlist import MAX_LISTS, add_item, list_totals, money, refresh_item
from app.views.navigation import posted_id


@login_required
def wish_lists(request):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools")

    form = WishListForm()
    if request.method == "POST" and request.POST.get("form_name") == "wish_list_create":
        form = WishListForm(request.POST)
        if WishList.objects.filter(owner=request.user).count() >= MAX_LISTS:
            form.add_error(None, f"Du kannst höchstens {MAX_LISTS} Wunschlisten anlegen.")
        elif form.is_valid():
            wish_list = form.save(commit=False)
            wish_list.owner = request.user
            wish_list.save()
            return redirect("wish_list_detail", list_id=wish_list.pk)

    lists = WishList.objects.filter(owner=request.user).prefetch_related("items")
    return render(
        request,
        "app/wish_lists.html",
        {
            "active_page": "tools",
            "form": form,
            "lists": [
                {
                    "id": wish_list.pk,
                    "title": wish_list.title,
                    "count": len(wish_list.items.all()),
                    "totals": list_totals(wish_list.items.all()),
                }
                for wish_list in lists
            ],
        },
    )


def _handle_item_post(request, wish_list, form_name):
    item = wish_list.items.filter(pk=posted_id(request, "item_id")).first()
    if item is None:
        return
    if form_name == "wish_item_update":
        form = WishListItemForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            django_messages.success(request, "Artikel gespeichert.")
        else:
            django_messages.error(request, "Bitte prüf Titel, Preis und Anzahl.")
    elif form_name == "wish_item_refresh":
        if problem := refresh_item(request.user, item):
            django_messages.error(request, problem)
        else:
            item.save()
            django_messages.success(request, "Preis und Angaben aktualisiert.")
    elif form_name == "wish_item_delete":
        item.delete()
        django_messages.success(request, "Artikel entfernt.")


@login_required
def wish_list_detail(request, list_id):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools")
    wish_list = get_object_or_404(WishList, owner=request.user, pk=list_id)

    add_url = ""
    add_error = ""
    form_name = request.POST.get("form_name") if request.method == "POST" else None
    if form_name == "wish_item_add":
        add_url = request.POST.get("url", "").strip()[:500]
        try:
            item, problem = add_item(request.user, wish_list, add_url)
        except FetchError as error:
            add_error = str(error)
        else:
            if problem:
                django_messages.warning(
                    request, f"{problem} Der Artikel ist gespeichert – trag Titel und Preis selbst ein."
                )
            else:
                django_messages.success(request, f"„{item.title}“ hinzugefügt.")
            return redirect("wish_list_detail", list_id=wish_list.pk)
    elif form_name in {"wish_item_update", "wish_item_refresh", "wish_item_delete"}:
        _handle_item_post(request, wish_list, form_name)
        return redirect("wish_list_detail", list_id=wish_list.pk)
    elif form_name == "wish_list_rename":
        form = WishListForm(request.POST, instance=wish_list)
        if form.is_valid():
            form.save()
        else:
            django_messages.error(request, "Der Name darf nicht leer sein.")
        return redirect("wish_list_detail", list_id=wish_list.pk)
    elif form_name == "wish_list_delete":
        wish_list.delete()
        django_messages.success(request, "Wunschliste gelöscht.")
        return redirect("wish_lists")

    items = list(wish_list.items.all())
    for item in items:
        item.price_label = money(item.price, item.currency) if item.price is not None else ""
        item.line_label = (
            money(item.price * item.quantity, item.currency)
            if item.price is not None and item.quantity > 1
            else ""
        )
        item.fetched_label = format_user_datetime(item.fetched_at, request.user)
        item.reviews_label = f"{item.review_count:,}".replace(",", ".") if item.review_count else ""
    return render(
        request,
        "app/wish_list_detail.html",
        {
            "active_page": "tools",
            "wish_list": wish_list,
            "items": items,
            "totals": list_totals(items),
            "unpriced": sum(item.price is None for item in items),
            "add_url": add_url,
            "add_error": add_error,
        },
    )
