from django.db.models import Count, OuterRef, Q, Subquery

from app.models import ChatMessage, Conversation, ConversationMember


def current_members_by_conversation(conversations, user):
    return {
        member.conversation_id: member
        for conversation in conversations
        for member in conversation.member_rows.all()
        if member.user_id == user.id
    }


def last_messages_by_conversation(conversations):
    conversation_ids = [conversation.id for conversation in conversations]
    if not conversation_ids:
        return {}

    latest_message_id = Subquery(
        ChatMessage.objects.filter(conversation=OuterRef("pk"))
        .order_by("-created_at", "-id")
        .values("pk")[:1]
    )
    rows = (
        Conversation.objects.filter(pk__in=conversation_ids)
        .annotate(latest_message_id=latest_message_id)
        .values_list("id", "latest_message_id")
    )
    message_ids_by_conversation = {
        conversation_id: message_id for conversation_id, message_id in rows if message_id
    }
    messages_by_id = {
        message.id: message
        for message in ChatMessage.objects.filter(pk__in=message_ids_by_conversation.values()).select_related(
            "sender",
            "sender__profile",
            "attachment",
        )
    }
    return {
        conversation_id: messages_by_id[message_id]
        for conversation_id, message_id in message_ids_by_conversation.items()
        if message_id in messages_by_id
    }


def unread_counts_by_conversation(conversations, user, members_by_conversation=None):
    if members_by_conversation is None:
        members_by_conversation = current_members_by_conversation(conversations, user)
    unread_filter = None

    for conversation in conversations:
        member = members_by_conversation.get(conversation.id)
        if not member:
            continue

        conversation_filter = Q(conversation_id=conversation.id) & ~Q(sender_id=user.id)
        if member.last_read_at:
            conversation_filter &= Q(created_at__gt=member.last_read_at)
        unread_filter = conversation_filter if unread_filter is None else unread_filter | conversation_filter

    if unread_filter is None:
        return {}

    return {
        row["conversation_id"]: row["count"]
        for row in ChatMessage.objects.filter(unread_filter)
        .values("conversation_id")
        .annotate(count=Count("id"))
    }


def unread_total_for_user(user):
    if not user or not getattr(user, "is_authenticated", False):
        return 0

    memberships = list(
        ConversationMember.objects.filter(user=user, is_archived=False).select_related("conversation")
    )
    conversations = [membership.conversation for membership in memberships]
    members_by_conversation = {membership.conversation_id: membership for membership in memberships}
    return sum(unread_counts_by_conversation(conversations, user, members_by_conversation).values())
