"""Shared shopping lists: whose list a user may open, and sharing one's own.

Every user has exactly one list (their `ShoppingItem` rows). Sharing it gives each member the
same rights as the owner on its items; only the owner manages who is in.
"""

from hashlib import sha256

from django.contrib.auth import get_user_model

from app.models import ShoppingListShare, UserNotification
from app.services.notifications import materialize_shopping_share_notification
from app.services.vocabulary import resolve_share_recipient

MAX_SHOPPING_LIST_SHARES = 10


def shopping_list_owners(user):
    """The lists `user` may open: their own first, then those shared with them."""
    shares = ShoppingListShare.objects.filter(user=user, owner__is_active=True).select_related("owner")
    return [user, *(share.owner for share in shares)]


def list_owner_for(user, raw_owner_id):
    """The owner of the list asked for with `?list=`; the user's own without one, None if not allowed."""
    if not raw_owner_id:
        return user
    try:
        owner_id = int(raw_owner_id)
    except ValueError:
        return None
    if owner_id == user.pk:
        return user
    share = (
        ShoppingListShare.objects.filter(user=user, owner_id=owner_id, owner__is_active=True)
        .select_related("owner")
        .first()
    )
    return share.owner if share else None


def share_shopping_list(owner, identifier):
    """Shares the owner's list with the user named by e-mail or username; returns an error or None."""
    user, error = resolve_share_recipient(owner, identifier)
    if error:
        return error
    if ShoppingListShare.objects.filter(owner=owner).count() >= MAX_SHOPPING_LIST_SHARES:
        return f"Der Zettel lässt sich mit höchstens {MAX_SHOPPING_LIST_SHARES} Personen teilen."
    share, created = ShoppingListShare.objects.get_or_create(owner=owner, user=user)
    if not created:
        return "Der Zettel ist mit diesem Konto bereits geteilt."
    materialize_shopping_share_notification(share)
    return None


def end_shopping_list_share(owner_id, user_id):
    """Ends a share (owner removes a member, or a member leaves) and drops its inbox row."""
    for share in ShoppingListShare.objects.filter(owner_id=owner_id, user_id=user_id):
        UserNotification.objects.filter(source_key=f"shopping-share:{share.pk}").delete()
        share.delete()


def shopping_list_members(owner):
    return list(
        get_user_model()
        .objects.filter(shared_shopping_lists__owner=owner)
        .order_by("shared_shopping_lists__created_at")
    )


def list_version(items):
    """Changes whenever anything visible on the list changes; members poll it for live updates."""
    state = sorted((item.pk, item.name, item.quantity, item.is_done, item.barcode) for item in items)
    return sha256(repr(state).encode("utf-8")).hexdigest()[:16]
