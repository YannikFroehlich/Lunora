"""Per-user limit for uploaded files, set on the administration page.

A file counts for whoever uploaded it, so a note's attachments can belong to several people.
Profile images stay out (one small file per user), and so does text stored in the database.
"""

from collections import Counter

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.template.defaultfilters import filesizeformat

from app.models import ChatMessageAttachment, NoteAttachment
from app.services.system_settings import get_system_settings

BYTES_PER_MB = 1024 * 1024


def _total_size(queryset):
    return queryset.aggregate(total=Sum("size"))["total"] or 0


def user_storage_used(user):
    return _total_size(NoteAttachment.objects.filter(uploaded_by=user)) + _total_size(
        ChatMessageAttachment.objects.filter(uploaded_by=user)
    )


def total_storage_used():
    return _total_size(NoteAttachment.objects.all()) + _total_size(ChatMessageAttachment.objects.all())


def top_storage_users(count=10):
    """The accounts using the most storage: [{"user", "used"}], largest first."""
    totals = Counter()
    for model in (NoteAttachment, ChatMessageAttachment):
        rows = model.objects.exclude(uploaded_by=None).values_list("uploaded_by").annotate(total=Sum("size"))
        totals.update(dict(rows))
    top = totals.most_common(count)
    users = get_user_model().objects.in_bulk([user_id for user_id, _used in top])
    return [{"user": users[user_id], "used": used} for user_id, used in top]


def user_storage_limit():
    """The limit in bytes, or None when it is switched off."""
    return get_system_settings().user_storage_limit_mb * BYTES_PER_MB or None


def ensure_storage_available(user, incoming_bytes):
    """Raise ValidationError unless ``user`` may store ``incoming_bytes`` more.

    Call it for every path that writes a new file for a user, copies included.
    """
    # ponytail: check-then-write without a lock, so parallel uploads can overshoot by a few files
    # (20 MB each at most). Lock the user's row around the write if that ever matters.
    limit = user_storage_limit()
    if not incoming_bytes or limit is None:
        return
    used = user_storage_used(user)
    if used + incoming_bytes > limit:
        raise ValidationError(
            f"Dein Speicher reicht dafür nicht: {filesizeformat(used)} von {filesizeformat(limit)} "
            "belegt. Lösche nicht mehr benötigte Notizen oder Nachrichten mit Anhängen."
        )


def storage_summary(user):
    used = user_storage_used(user)
    limit = user_storage_limit()
    return {
        "used": used,
        "limit": limit,
        "percent": min(round(used * 100 / limit), 100) if limit else 0,
    }
