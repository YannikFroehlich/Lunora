from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.cache import patch_cache_control
from django.views.decorators.http import require_GET

from app.forms import WishListForm, WishListItemForm
from app.models import WishList, WishListItem, WishListReservation
from app.services.notes import display_name
from app.services.safe_fetch import FetchError
from app.services.system_settings import disabled_feature_response, feature_enabled
from app.services.user_preferences import format_user_datetime
from app.services.wishlist import (
    MAX_LISTS,
    accessible_wish_lists,
    add_item,
    end_wish_list_share,
    image_version,
    list_totals,
    money,
    price_change,
    record_price_point,
    refresh_item,
    release_item,
    reserve_item,
    share_wish_list,
    sorted_items,
    sparkline_points,
    thumbnail,
)
from app.views.navigation import posted_id


@login_required
@require_GET
def wish_item_image(request, item_id):
    """The item's product picture, fetched and re-encoded by the server so the browser never
    contacts the shop."""
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools")
    item = get_object_or_404(WishListItem, pk=item_id, wish_list__in=accessible_wish_lists(request.user))
    data = thumbnail(request.user, item.image_url) if item.image_url else None
    if data is None:
        raise Http404
    response = HttpResponse(data, content_type="image/jpeg")
    patch_cache_control(response, private=True, max_age=7 * 24 * 3600)
    return response


def _list_card(wish_list):
    items = wish_list.items.all()
    return {
        "id": wish_list.pk,
        "title": wish_list.title,
        "count": len(items),
        "totals": list_totals(items),
        "owner_name": display_name(wish_list.owner),
        "share_count": len(wish_list.shares.all()),
    }


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

    lists = (
        accessible_wish_lists(request.user)
        .select_related("owner__profile")
        .prefetch_related("items", "shares")
    )
    return render(
        request,
        "app/wish_lists.html",
        {
            "active_page": "tools",
            "form": form,
            "lists": [_list_card(wish_list) for wish_list in lists if wish_list.owner_id == request.user.pk],
            "shared_lists": [
                _list_card(wish_list) for wish_list in lists if wish_list.owner_id != request.user.pk
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
            django_messages.error(request, "Bitte prüf Titel, Preis, Anzahl und Preisalarm.")
    elif form_name == "wish_item_refresh":
        if problem := refresh_item(request.user, item):
            django_messages.error(request, problem)
        else:
            item.save()
            record_price_point(item)
            django_messages.success(request, "Preis und Angaben aktualisiert.")
    elif form_name == "wish_item_quantity":
        delta = 1 if request.POST.get("delta") == "up" else -1
        item.quantity = max(0, min(99, item.quantity + delta))
        item.save(update_fields=["quantity"])
    elif form_name == "wish_item_delete":
        item.delete()
        django_messages.success(request, "Artikel entfernt.")


def _handle_owner_post(request, wish_list, form_name):
    """The owner's POST branches; returns a response, or None to render the page (add form errors)."""
    if form_name == "wish_item_add":
        add_url = request.POST.get("url", "").strip()[:500]
        try:
            item, problem = add_item(request.user, wish_list, add_url)
        except FetchError as error:
            request.wish_add_state = (add_url, str(error))
            return None
        if problem:
            django_messages.warning(
                request, f"{problem} Der Artikel ist gespeichert – trag Titel und Preis selbst ein."
            )
        else:
            django_messages.success(request, f"„{item.title}“ hinzugefügt.")
    elif form_name in {"wish_item_update", "wish_item_refresh", "wish_item_quantity", "wish_item_delete"}:
        _handle_item_post(request, wish_list, form_name)
    elif form_name == "wish_list_refresh_all":
        updated = failed = 0
        for item in wish_list.items.all():
            if refresh_item(request.user, item):
                failed += 1
            else:
                item.save()
                record_price_point(item)
                updated += 1
        if failed:
            django_messages.warning(request, f"{updated} von {updated + failed} Preisen aktualisiert.")
        elif updated:
            django_messages.success(request, f"{updated} Preis(e) aktualisiert.")
    elif form_name == "wish_list_rename":
        form = WishListForm(request.POST, instance=wish_list)
        if form.is_valid():
            form.save()
        else:
            django_messages.error(request, "Der Name darf nicht leer sein.")
    elif form_name == "wish_list_share":
        if error := share_wish_list(wish_list, request.POST.get("identifier", "")[:254]):
            django_messages.error(request, error)
        else:
            django_messages.success(request, "Liste geteilt.")
    elif form_name == "wish_list_unshare" and (user_id := posted_id(request, "user_id")) is not None:
        end_wish_list_share(wish_list.pk, user_id)
    elif form_name == "wish_list_delete":
        wish_list.delete()
        django_messages.success(request, "Wunschliste gelöscht.")
        return redirect("wish_lists")
    return redirect("wish_list_detail", list_id=wish_list.pk)


def _handle_member_post(request, wish_list, form_name):
    """A member's POST branches: reserve, release, leave. Members never edit the list."""
    if form_name == "wish_list_leave":
        end_wish_list_share(wish_list.pk, request.user.pk)
        django_messages.success(request, "Du siehst die Liste nicht mehr.")
        return redirect("wish_lists")
    item = wish_list.items.filter(pk=posted_id(request, "item_id"), quantity__gt=0).first()
    if item is not None and form_name == "wish_item_reserve":
        if error := reserve_item(request.user, item):
            django_messages.error(request, error)
        else:
            django_messages.success(request, f"Du schenkst „{item.title}“.")
    elif item is not None and form_name == "wish_item_release":
        release_item(request.user, item)
    return redirect("wish_list_detail", list_id=wish_list.pk)


def _decorate_item(item, user):
    points = list(item.price_points.all())
    item.price_label = money(item.price, item.currency) if item.price is not None else ""
    item.line_label = (
        money(item.price * item.quantity, item.currency)
        if item.price is not None and item.quantity != 1
        else ""
    )
    item.fetched_label = format_user_datetime(item.fetched_at, user)
    item.image_version = image_version(item) if item.image_url else ""
    item.reviews_label = f"{item.review_count:,}".replace(",", ".") if item.review_count else ""
    item.price_change_label, item.price_change_up = price_change(item, points)
    item.sparkline = sparkline_points(points)
    if item.sparkline:
        prices = [point.price for point in points]
        item.sparkline_label = (
            f"Preisverlauf: {len(prices)} Preise, zwischen {money(min(prices), item.currency)} "
            f"und {money(max(prices), item.currency)}"
        )
    item.target_label = money(item.target_price, item.currency) if item.target_price is not None else ""
    item.target_reached = (
        item.target_price is not None and item.price is not None and item.price <= item.target_price
    )


@login_required
def wish_list_detail(request, list_id):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools")
    wish_list = get_object_or_404(
        accessible_wish_lists(request.user).select_related("owner__profile"), pk=list_id
    )
    is_owner = wish_list.owner_id == request.user.pk

    if request.method == "POST":
        form_name = request.POST.get("form_name")
        response = (_handle_owner_post if is_owner else _handle_member_post)(request, wish_list, form_name)
        if response is not None:
            return response

    items = wish_list.items.prefetch_related("price_points")
    if not is_owner:
        # Quantity 0 means "not wanted right now", so givers don't see it.
        items = items.filter(quantity__gt=0)
    items = sorted_items(items)
    for item in items:
        _decorate_item(item, request.user)
    context = {
        "active_page": "tools",
        "wish_list": wish_list,
        "is_owner": is_owner,
        "items": items,
        "totals": list_totals(items),
        "must_have_totals": list_totals(
            item for item in items if item.priority == WishListItem.PRIORITY_HIGH
        ),
        "unpriced": sum(item.price is None for item in items),
        "priority_choices": WishListItem.PRIORITY_CHOICES,
    }
    if is_owner:
        add_url, add_error = getattr(request, "wish_add_state", ("", ""))
        context.update(
            add_url=add_url,
            add_error=add_error,
            members=[
                {"id": share.user_id, "name": display_name(share.user)}
                for share in wish_list.shares.select_related("user__profile")
            ],
        )
    else:
        # Reservations exist for the givers only: this branch is the one place that reads them.
        reservations = {
            reservation.item_id: reservation
            for reservation in WishListReservation.objects.filter(item__in=items).select_related(
                "user__profile"
            )
        }
        for item in items:
            reservation = reservations.get(item.pk)
            item.reserved_by_me = reservation is not None and reservation.user_id == request.user.pk
            item.reserved_by = (
                display_name(reservation.user) if reservation and not item.reserved_by_me else ""
            )
        context.update(
            owner_name=display_name(wish_list.owner),
            open_totals=list_totals(item for item in items if item.pk not in reservations),
            mine_count=sum(item.reserved_by_me for item in items),
        )
    return render(request, "app/wish_list_detail.html", context)
