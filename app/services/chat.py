"""Chat domain logic: membership, attachments, forwarding, @-mentions, polls and scheduled messages.

The message views stay request-flow only and call into here; ``run_scheduled_tasks`` delivers due
scheduled messages, and the messages page delivers the open conversation's due ones on every poll,
so they also go out on setups without the automations loop.
"""

from datetime import timedelta

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.text import normalize_newlines

from app.models import (
    ChatMessage,
    ChatMessageAttachment,
    ChatMessageReaction,
    ChatPoll,
    ChatPollOption,
    ChatPollVote,
    Conversation,
    ConversationMember,
    ScheduledChatMessage,
    UserNotification,
)
from app.services.chat_files import audio_content_type, infer_attachment_kind
from app.services.user_preferences import format_user_datetime, localtime_for_user

MENTION_ALL = "alle"
MAX_SCHEDULED_MESSAGES = 50
SCHEDULE_MIN_LEAD = timedelta(minutes=1)
SCHEDULE_MAX_AHEAD = timedelta(days=365)
SCHEDULED_DELIVERY_BATCH = 200
# Own messages stay editable this long after sending.
CHAT_EDIT_WINDOW = timedelta(hours=24)
CHAT_MESSAGE_MAX_LENGTH = ChatMessage._meta.get_field("body").max_length
MESSAGE_REACTION_EMOJIS = [emoji for emoji, _label in ChatMessageReaction.EMOJI_CHOICES]


# --- Sending -----------------------------------------------------------------------------------


def can_send(conversation, user, members=None):
    """Whether ``user`` may write in ``conversation``: a member who hasn't blocked it, in a direct
    chat whose other side hasn't blocked it either."""
    members = list(conversation.member_rows.all()) if members is None else members
    own = next((member for member in members if member.user_id == user.pk), None)
    if own is None or own.is_blocked:
        return False
    if conversation.is_group:
        return True
    return not any(member.is_blocked for member in members if member.user_id != user.pk)


def touch_conversation(message):
    Conversation.objects.filter(pk=message.conversation_id).update(updated_at=message.created_at)


# --- Members and message actions --------------------------------------------------------------


def conversation_member(conversation, user):
    if not conversation:
        return None
    return next(
        (member for member in conversation.member_rows.all() if member.user_id == user.id),
        None,
    )


def other_members(conversation, user):
    return [member for member in conversation.member_rows.all() if member.user_id != user.id]


def blocked_by_other_side(conversation, user):
    """Whether the other side of a direct conversation has blocked it, so `user` cannot send."""
    if not conversation or conversation.is_group:
        return False
    return any(member.is_blocked for member in other_members(conversation, user))


def apply_member_action(conversation, user, action):
    member = conversation_member(conversation, user)
    if not member:
        return

    now = timezone.now()
    update_fields = []
    if action == "mute_8h":
        member.muted_until = now + timedelta(hours=8)
        update_fields.append("muted_until")
    elif action == "mute_1w":
        member.muted_until = now + timedelta(weeks=1)
        update_fields.append("muted_until")
    elif action == "mute_1y":
        member.muted_until = now + timedelta(days=365)
        update_fields.append("muted_until")
    elif action == "unmute":
        member.muted_until = None
        update_fields.append("muted_until")
    elif action == "block":
        member.is_blocked = True
        update_fields.append("is_blocked")
    elif action == "unblock":
        member.is_blocked = False
        update_fields.append("is_blocked")

    if update_fields:
        member.save(update_fields=update_fields)


def delete_message(message, user):
    if message.sender_id != user.id or message.is_deleted:
        return
    attachment = getattr(message, "attachment", None)
    if attachment:
        attachment.file.delete(save=False)
        attachment.delete()
    message.body = ""
    message.is_deleted = True
    message.deleted_at = timezone.now()
    message.is_pinned = False
    message.pinned_at = None
    message.pinned_by = None
    message.reactions.all().delete()
    delete_poll(message)
    forget_mentions(message)
    message.mentioned_users.clear()
    message.save(update_fields=["body", "is_deleted", "deleted_at", "is_pinned", "pinned_at", "pinned_by"])


def attach_file(message, user, upload, duration_seconds=None):
    kind = infer_attachment_kind(upload.name)
    is_audio = kind == ChatMessageAttachment.KIND_AUDIO
    content_type = (
        audio_content_type(upload.name)
        if is_audio
        else (getattr(upload, "content_type", "") or "application/octet-stream")
    )
    ChatMessageAttachment.objects.create(
        message=message,
        uploaded_by=user,
        kind=kind,
        original_name=upload.name[:255],
        content_type=content_type[:160],
        size=upload.size,
        duration_seconds=duration_seconds if is_audio else None,
        file=upload,
    )


def forward_message(message, target, user):
    """Copy a message (body and attachment, no reply quote) into ``target`` as ``user``'s own."""
    forwarded = ChatMessage.objects.create(
        conversation=target, sender=user, body=message.body, is_forwarded=True
    )
    attachment = getattr(message, "attachment", None)
    if attachment:
        # A real copy, so deleting either message never removes the other one's file.
        with attachment.file.open("rb") as handle:
            content = ContentFile(handle.read(), name=attachment.original_name)
        ChatMessageAttachment.objects.create(
            message=forwarded,
            uploaded_by=user,
            kind=attachment.kind,
            original_name=attachment.original_name,
            content_type=attachment.content_type,
            size=attachment.size,
            duration_seconds=attachment.duration_seconds,
            file=content,
        )
    return forwarded


def forward_targets(user, current_conversation):
    """Conversations a message can be forwarded to: visible ones the user can write in."""
    targets = []
    for conversation in Conversation.visible_for(user).order_by("-updated_at"):
        if conversation.pk == current_conversation.pk:
            continue
        member = next((row for row in conversation.member_rows.all() if row.user_id == user.id), None)
        if (member and member.is_blocked) or blocked_by_other_side(conversation, user):
            continue
        targets.append({"id": conversation.pk, "title": conversation.display_title_for(user)})
    return targets


def toggle_pin(message, user):
    if message.is_deleted:
        return
    if message.is_pinned:
        message.is_pinned = False
        message.pinned_at = None
        message.pinned_by = None
    else:
        message.is_pinned = True
        message.pinned_at = timezone.now()
        message.pinned_by = user
    message.save(update_fields=["is_pinned", "pinned_at", "pinned_by"])


def toggle_reaction(message, user, emoji):
    if message.is_deleted or emoji not in MESSAGE_REACTION_EMOJIS:
        return
    reaction = ChatMessageReaction.objects.filter(message=message, user=user).first()
    if reaction and reaction.emoji == emoji:
        reaction.delete()
        return
    if reaction:
        reaction.emoji = emoji
        reaction.save(update_fields=["emoji"])
        return
    ChatMessageReaction.objects.create(message=message, user=user, emoji=emoji)


# --- Mentions ----------------------------------------------------------------------------------


def mention_source_key(message):
    return f"chat-mention:{message.conversation_id}:{message.pk}"


def mention_candidates(conversation, user):
    """Other members of a group chat as ``{id, name}``, for the compose field's @ suggestions."""
    if not conversation or not conversation.is_group:
        return []
    candidates = [
        {"id": member.user_id, "name": Conversation.display_name_for_user(member.user)}
        for member in conversation.member_rows.all()
        if member.user_id != user.pk
    ]
    candidates.sort(key=lambda item: item["name"].casefold())
    if candidates:
        candidates.append({"id": 0, "name": MENTION_ALL})
    return candidates


def _mention_spans(body, names):
    """``(start, end, user_ids)`` for every ``@name`` in ``body``.

    ``names`` maps a casefolded name to user ids. Longer names win, so "@Anna Berg" is not cut to
    "@Anna"; a match needs the "@" at a word start and no word character right after the name.
    """
    if "@" not in body or not names:
        return []
    ordered = sorted(names.items(), key=lambda item: len(item[0]), reverse=True)
    spans = []
    position = 0
    while True:
        start = body.find("@", position)
        if start < 0:
            return spans
        position = start + 1
        if start and (body[start - 1].isalnum() or body[start - 1] in "_@"):
            continue
        for name, user_ids in ordered:
            end = start + 1 + len(name)
            if body[start + 1 : end].casefold() != name:
                continue
            if end < len(body) and (body[end].isalnum() or body[end] == "_"):
                continue
            spans.append((start, end, user_ids))
            position = end
            break


def _member_mention_names(members, sender_id):
    names = {}
    for member in members:
        if member.user_id == sender_id:
            continue
        name = Conversation.display_name_for_user(member.user).strip().casefold()
        if name:
            names.setdefault(name, set()).add(member.user_id)
    others = {member.user_id for member in members if member.user_id != sender_id}
    if others:
        names.setdefault(MENTION_ALL, set()).update(others)
    return names


def resolve_mentions(conversation, sender, body):
    """User ids of the group members ``body`` names with ``@Name`` (or ``@alle``)."""
    if not conversation.is_group or "@" not in (body or ""):
        return set()
    members = list(
        ConversationMember.objects.filter(conversation=conversation).select_related("user", "user__profile")
    )
    names = _member_mention_names(members, sender.pk)
    return {user_id for _start, _end, user_ids in _mention_spans(body, names) for user_id in user_ids}


def apply_mentions(message):
    """Store the users ``message`` mentions and notify them (also while they muted the chat)."""
    from app.services.notifications import materialize_chat_mention_notifications

    user_ids = resolve_mentions(message.conversation, message.sender, message.body)
    if not user_ids:
        return 0
    message.mentioned_users.set(user_ids)
    return materialize_chat_mention_notifications(message)


def update_mentions(message):
    """Re-resolve an edited message's mentions: new names are notified, removed ones lose their
    notification, and the others get the new text."""
    from app.services.notifications import materialize_chat_mention_notifications

    previous = set(message.mentioned_users.values_list("pk", flat=True))
    current = resolve_mentions(message.conversation, message.sender, message.body)
    message.mentioned_users.set(current)
    notifications = UserNotification.objects.filter(
        kind=UserNotification.KIND_CHAT_MENTION, source_key=mention_source_key(message)
    )
    notifications.filter(recipient_id__in=previous - current).delete()
    notifications.filter(recipient_id__in=previous & current).update(body=message.body[:300])
    if current - previous:
        return materialize_chat_mention_notifications(message)
    return 0


def can_edit_message(message, user, now=None):
    """Own text messages, not deleted, forwarded or polls, within ``CHAT_EDIT_WINDOW``."""
    return (
        message.sender_id == user.pk
        and not message.is_deleted
        and not message.is_forwarded
        and getattr(message, "poll", None) is None
        and message.created_at >= (now or timezone.now()) - CHAT_EDIT_WINDOW
    )


def edit_message(message, user, body):
    """Replace the text of an own message; raises ValueError with a German message otherwise."""
    body = (body or "").strip()
    if not can_edit_message(message, user):
        raise ValueError("Diese Nachricht kann nicht mehr bearbeitet werden.")
    if not body and getattr(message, "attachment", None) is None:
        raise ValueError("Die Nachricht darf nicht leer sein.")
    if len(body) > CHAT_MESSAGE_MAX_LENGTH:
        raise ValueError(f"Eine Nachricht darf höchstens {CHAT_MESSAGE_MAX_LENGTH} Zeichen lang sein.")
    if body == message.body:
        return False
    message.body = body
    message.edited_at = timezone.now()
    message.save(update_fields=["body", "edited_at"])
    update_mentions(message)
    return True


def forget_mentions(message):
    """Drop the inbox rows of a deleted message's mentions for every recipient."""
    UserNotification.objects.filter(
        kind=UserNotification.KIND_CHAT_MENTION, source_key=mention_source_key(message)
    ).delete()


def mark_conversation_mentions_read(conversation, user):
    """Opening a conversation settles its mention notifications, so no push follows for them."""
    return UserNotification.objects.filter(
        recipient=user,
        kind=UserNotification.KIND_CHAT_MENTION,
        read_at__isnull=True,
        source_key__startswith=f"chat-mention:{conversation.pk}:",
    ).update(read_at=timezone.now())


def render_message_body(message, user):
    """Escaped message text with line breaks and highlighted mentions, ready for the template."""
    body = normalize_newlines(message.display_body)
    mentioned = list(message.mentioned_users.all()) if not message.is_deleted else []
    names = {}
    for mentioned_user in mentioned:
        name = Conversation.display_name_for_user(mentioned_user).strip().casefold()
        if name:
            names.setdefault(name, set()).add(mentioned_user.pk)
    if mentioned:
        names.setdefault(MENTION_ALL, set()).update(item.pk for item in mentioned)

    parts = []
    position = 0
    for start, end, user_ids in _mention_spans(body, names):
        parts.append(escape(body[position:start]))
        css_class = "chat-mention is-self" if user.pk in user_ids else "chat-mention"
        parts.append(f'<span class="{css_class}">{escape(body[start:end])}</span>')
        position = end
    parts.append(escape(body[position:]))
    # Every part above went through escape(), so the joined string is safe to mark.
    return mark_safe("".join(parts).replace("\n", "<br>"))


# --- Polls -------------------------------------------------------------------------------------


def create_poll(conversation, sender, question, options, *, allows_multiple=False):
    """Post a poll as a new message of ``conversation``; only group chats have polls."""
    if not conversation.is_group:
        raise ValueError("Umfragen gibt es nur in Gruppenchats.")
    with transaction.atomic():
        message = ChatMessage.objects.create(conversation=conversation, sender=sender, body=question)
        poll = ChatPoll.objects.create(message=message, question=question, allows_multiple=allows_multiple)
        ChatPollOption.objects.bulk_create(
            ChatPollOption(poll=poll, text=text, position=index) for index, text in enumerate(options)
        )
    touch_conversation(message)
    return poll


def toggle_poll_vote(poll, user, option_id):
    """Tick or untick one option; a single-choice poll moves the user's vote to it."""
    if poll.is_closed or poll.message.is_deleted:
        return False
    option = poll.options.filter(pk=option_id).first()
    if option is None:
        return False
    with transaction.atomic():
        # Locking the voter's membership row serialises their clicks, so a fast double click on two
        # options of a single-choice poll can't leave two votes behind.
        ConversationMember.objects.select_for_update().filter(
            conversation_id=poll.message.conversation_id, user=user
        ).first()
        own_vote = ChatPollVote.objects.filter(option=option, user=user)
        if own_vote.exists():
            own_vote.delete()
            return True
        if not poll.allows_multiple:
            ChatPollVote.objects.filter(poll=poll, user=user).delete()
        ChatPollVote.objects.get_or_create(poll=poll, option=option, user=user)
    return True


def close_poll(poll, user):
    """End voting; only whoever started the poll may."""
    if poll.message.sender_id != user.pk or poll.is_closed:
        return False
    poll.closed_at = timezone.now()
    poll.save(update_fields=["closed_at"])
    return True


def delete_poll(message):
    ChatPoll.objects.filter(message=message).delete()


def poll_prefetch():
    """Prefetch lookups for rendering the polls of a message queryset with ``poll_item``."""
    return [
        Prefetch(
            "poll__options",
            queryset=ChatPollOption.objects.prefetch_related(
                Prefetch("votes", queryset=ChatPollVote.objects.select_related("user", "user__profile"))
            ),
        )
    ]


def poll_item(message, user):
    """Template data for a message's poll, or None; expects ``poll_prefetch()`` to be applied."""
    poll = getattr(message, "poll", None)
    if poll is None or message.is_deleted:
        return None
    options = list(poll.options.all())
    voter_ids = {vote.user_id for option in options for vote in option.votes.all()}
    voter_count = len(voter_ids)
    option_items = []
    for option in options:
        votes = list(option.votes.all())
        count = len(votes)
        option_items.append(
            {
                "id": option.pk,
                "text": option.text,
                "count": count,
                "percent": round(100 * count / voter_count) if voter_count else 0,
                "is_own": any(vote.user_id == user.pk for vote in votes),
                "voter_names": ", ".join(Conversation.display_name_for_user(vote.user) for vote in votes),
            }
        )
    return {
        "id": poll.pk,
        "question": poll.question,
        "allows_multiple": poll.allows_multiple,
        "is_closed": poll.is_closed,
        "can_close": poll.message.sender_id == user.pk and not poll.is_closed,
        "voter_count": voter_count,
        "options": option_items,
    }


# --- Scheduled messages ------------------------------------------------------------------------


def schedule_message(conversation, sender, body, send_at, *, reply_to=None):
    if ScheduledChatMessage.objects.filter(sender=sender).count() >= MAX_SCHEDULED_MESSAGES:
        raise ValueError(f"Du kannst höchstens {MAX_SCHEDULED_MESSAGES} Nachrichten gleichzeitig planen.")
    return ScheduledChatMessage.objects.create(
        conversation=conversation, sender=sender, body=body, send_at=send_at, reply_to=reply_to
    )


def schedule_bounds(user, now=None):
    """``min``/``max``/``default`` for the datetime-local field, in the user's own timezone.

    Computed server-side because the server reads the field in the profile timezone, which need
    not be the browser's.
    """
    local_now = localtime_for_user(now or timezone.now(), user)
    value_format = "%Y-%m-%dT%H:%M"
    earliest = (local_now + SCHEDULE_MIN_LEAD + timedelta(minutes=1)).replace(second=0, microsecond=0)
    suggestion = (local_now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    if suggestion < earliest:
        suggestion = earliest
    latest = (local_now + SCHEDULE_MAX_AHEAD).replace(second=0, microsecond=0)
    return {
        "min": earliest.strftime(value_format),
        "max": latest.strftime(value_format),
        "default": suggestion.strftime(value_format),
    }


def scheduled_message_items(conversation, user):
    """The user's own pending messages in ``conversation`` for the list above the compose field."""
    if not conversation:
        return []
    return [
        {
            "id": scheduled.pk,
            "body": scheduled.body,
            "send_at_label": format_user_datetime(scheduled.send_at, user),
        }
        for scheduled in ScheduledChatMessage.objects.filter(conversation=conversation, sender=user)
    ]


def deliver_scheduled_message(scheduled_id):
    """Turn one scheduled message into a real one; None when it was already taken or is dropped.

    The row is claimed by deleting it inside the transaction, so concurrent deliverers (the
    automations loop and open chat tabs) can never send it twice. A sender who left the chat, was
    deactivated or can no longer write there loses the message silently.
    """
    with transaction.atomic():
        scheduled = (
            ScheduledChatMessage.objects.select_related("sender", "reply_to").filter(pk=scheduled_id).first()
        )
        if scheduled is None:
            return None
        deleted, _details = ScheduledChatMessage.objects.filter(pk=scheduled_id).delete()
        if not deleted:
            return None
        conversation = (
            Conversation.objects.prefetch_related("member_rows").filter(pk=scheduled.conversation_id).first()
        )
        if (
            conversation is None
            or not scheduled.sender.is_active
            or not can_send(conversation, scheduled.sender)
        ):
            return None
        reply_to = scheduled.reply_to
        if reply_to is not None and (reply_to.is_deleted or reply_to.conversation_id != conversation.pk):
            reply_to = None
        message = ChatMessage.objects.create(
            conversation=conversation, sender=scheduled.sender, body=scheduled.body, reply_to=reply_to
        )
        touch_conversation(message)
        apply_mentions(message)
    return message


def send_scheduled_message_now(conversation, user, scheduled_id):
    if not ScheduledChatMessage.objects.filter(
        pk=scheduled_id, conversation=conversation, sender=user
    ).exists():
        return None
    return deliver_scheduled_message(scheduled_id)


def cancel_scheduled_message(conversation, user, scheduled_id):
    deleted, _details = ScheduledChatMessage.objects.filter(
        pk=scheduled_id, conversation=conversation, sender=user
    ).delete()
    return bool(deleted)


def send_due_scheduled_messages(*, now=None, conversation=None):
    current_time = now or timezone.now()
    due = ScheduledChatMessage.objects.filter(send_at__lte=current_time)
    if conversation is not None:
        due = due.filter(conversation=conversation)
    due_ids = list(due.order_by("send_at", "id").values_list("pk", flat=True)[:SCHEDULED_DELIVERY_BATCH])
    sent = sum(1 for scheduled_id in due_ids if deliver_scheduled_message(scheduled_id))
    return {"sent": sent, "skipped": len(due_ids) - sent}
