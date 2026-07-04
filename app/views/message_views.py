from datetime import timedelta

from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils import timezone

from app.forms import ConversationStartForm, MessageForm
from app.models import ChatMessage, ChatMessageReaction, Conversation, ConversationMember
from app.services.message_queries import (
    current_members_by_conversation,
    last_messages_by_conversation,
    unread_counts_by_conversation,
)
from app.services.user_preferences import (
    format_user_date,
    format_user_datetime,
    format_user_time,
    localtime_for_user,
)


MESSAGE_REACTION_EMOJIS = [emoji for emoji, _label in ChatMessageReaction.EMOJI_CHOICES]
MESSAGE_STREAM_PAGE_SIZE = 50


@login_required
def messages(request, conversation_id=None):
    selected_conversation = _get_user_conversation(request.user, conversation_id) if conversation_id else None
    message_form = MessageForm()
    start_form = ConversationStartForm(user=request.user)

    if request.method == "POST":
        form_name = request.POST.get("form_name")

        if form_name == "message":
            selected_conversation = _get_user_conversation(
                request.user,
                request.POST.get("conversation_id") or conversation_id,
            )
            message_form = MessageForm(request.POST)
            if not selected_conversation:
                django_messages.error(request, "Diese Unterhaltung wurde nicht gefunden.")
                return redirect("messages")
            current_member = _get_conversation_member(selected_conversation, request.user)
            if current_member and current_member.is_blocked:
                django_messages.error(request, "Du hast diesen Chat blockiert. Hebe die Blockierung auf, um wieder zu schreiben.")
                return redirect("messages_detail", conversation_id=selected_conversation.id)
            if message_form.is_valid():
                message = message_form.save(commit=False)
                message.conversation = selected_conversation
                message.sender = request.user
                message.save()
                selected_conversation.updated_at = message.created_at
                selected_conversation.save(update_fields=["updated_at"])
                selected_conversation.mark_read_for(request.user)
                return redirect("messages_detail", conversation_id=selected_conversation.id)

        elif form_name == "start_conversation":
            start_form = ConversationStartForm(request.POST, user=request.user)
            if start_form.is_valid():
                recipient = start_form.cleaned_data["recipient"]
                selected_conversation = Conversation.find_direct_between(request.user, recipient)
                if not selected_conversation:
                    selected_conversation = Conversation.objects.create(created_by=request.user)
                    ConversationMember.objects.bulk_create(
                        [
                            ConversationMember(conversation=selected_conversation, user=request.user),
                            ConversationMember(conversation=selected_conversation, user=recipient),
                        ]
                    )
                else:
                    ConversationMember.objects.filter(
                        conversation=selected_conversation,
                        user__in=[request.user, recipient],
                    ).update(is_archived=False)

                body = start_form.cleaned_data.get("body")
                if body:
                    message = ChatMessage.objects.create(
                        conversation=selected_conversation,
                        sender=request.user,
                        body=body,
                    )
                    selected_conversation.updated_at = message.created_at
                    selected_conversation.save(update_fields=["updated_at"])
                selected_conversation.mark_read_for(request.user)
                return redirect("messages_detail", conversation_id=selected_conversation.id)

        elif form_name == "message_action":
            selected_conversation = _get_user_conversation(
                request.user,
                request.POST.get("conversation_id") or conversation_id,
            )
            if not selected_conversation:
                django_messages.error(request, "Diese Unterhaltung wurde nicht gefunden.")
                return redirect("messages")

            message = selected_conversation.messages.filter(pk=request.POST.get("message_id")).first()
            if not message:
                django_messages.error(request, "Diese Nachricht wurde nicht gefunden.")
                return redirect("messages_detail", conversation_id=selected_conversation.id)

            action = request.POST.get("action")
            if action == "delete":
                _delete_chat_message_for_user(message, request.user)
            elif action == "pin":
                _toggle_chat_message_pin(message, request.user)
            elif action == "reaction":
                _toggle_chat_message_reaction(message, request.user, request.POST.get("emoji"))

            return redirect("messages_detail", conversation_id=selected_conversation.id)

        elif form_name == "member_action":
            selected_conversation = _get_user_conversation(
                request.user,
                request.POST.get("conversation_id") or conversation_id,
            )
            if selected_conversation:
                _apply_conversation_member_action(selected_conversation, request.user, request.POST.get("action"))
                return redirect("messages_detail", conversation_id=selected_conversation.id)
            return redirect("messages")

        elif form_name == "archive":
            selected_conversation = _get_user_conversation(
                request.user,
                request.POST.get("conversation_id") or conversation_id,
            )
            if selected_conversation:
                ConversationMember.objects.filter(conversation=selected_conversation, user=request.user).update(is_archived=True)
            return redirect("messages")

        elif form_name == "mark_read":
            selected_conversation = _get_user_conversation(
                request.user,
                request.POST.get("conversation_id") or conversation_id,
            )
            if selected_conversation:
                selected_conversation.mark_read_for(request.user)
                return redirect("messages_detail", conversation_id=selected_conversation.id)
            return redirect("messages")

    all_conversations = list(Conversation.visible_for(request.user))

    if selected_conversation and request.method == "GET" and conversation_id:
        selected_conversation.mark_read_for(request.user)
        selected_conversation = _get_user_conversation(request.user, selected_conversation.id)
        all_conversations = list(Conversation.visible_for(request.user))

    query = request.GET.get("q", "").strip()
    current_filter = request.GET.get("filter", "all")
    message_before_id = request.GET.get("before", "").strip()
    all_inbox_items = _build_inbox_items(all_conversations, request.user)
    inbox_items = _filter_inbox_items(all_inbox_items, query, current_filter)
    overview_items = _build_messages_overview_items(all_inbox_items)

    message_window = _build_message_window(selected_conversation, request.user, before_id=message_before_id)
    message_items = message_window["message_items"]
    pinned_message_items = _build_pinned_message_items(selected_conversation, request.user) if selected_conversation else []
    selected_members = _build_member_items(selected_conversation, request.user) if selected_conversation else []
    current_member = _get_conversation_member(selected_conversation, request.user) if selected_conversation else None
    current_member_state = _build_current_member_state(current_member, request.user)

    context = {
        "active_page": "messages",
        "conversations": inbox_items,
        "current_filter": current_filter,
        "query": query,
        "selected_conversation": selected_conversation,
        "selected_title": selected_conversation.display_title_for(request.user) if selected_conversation else "",
        "selected_avatar": selected_conversation.avatar_for(request.user) if selected_conversation else "",
        "selected_avatar_url": _conversation_avatar_url_for(selected_conversation, request.user) if selected_conversation else "",
        "selected_members": selected_members,
        "message_items": message_items,
        "has_older_messages": message_window["has_older_messages"],
        "oldest_message_id": message_window["oldest_message_id"],
        "pinned_message_items": pinned_message_items,
        "reaction_emojis": MESSAGE_REACTION_EMOJIS,
        "message_form": message_form,
        "start_form": start_form,
        "unread_total": sum(item["unread"] for item in all_inbox_items),
        "conversation_total": len(all_inbox_items),
        "overview_items": overview_items,
        "current_member_state": current_member_state,
    }
    return render(request, "app/messages.html", context)


@login_required
def messages_live_updates(request, conversation_id=None):
    if request.method != "GET":
        return JsonResponse({"ok": False, "error": "Nur GET ist erlaubt."}, status=405)

    selected_conversation = _get_user_conversation(request.user, conversation_id) if conversation_id else None
    if conversation_id and not selected_conversation:
        return JsonResponse({"ok": False, "error": "Diese Unterhaltung wurde nicht gefunden."}, status=404)

    all_conversations = list(Conversation.visible_for(request.user))

    if selected_conversation:
        selected_conversation.mark_read_for(request.user)
        selected_conversation = _get_user_conversation(request.user, selected_conversation.id)
        all_conversations = list(Conversation.visible_for(request.user))

    query = request.GET.get("q", "").strip()
    current_filter = request.GET.get("filter", "all")
    message_before_id = request.GET.get("before", "").strip()
    all_inbox_items = _build_inbox_items(all_conversations, request.user)
    inbox_items = _filter_inbox_items(all_inbox_items, query, current_filter)
    unread_total = sum(item["unread"] for item in all_inbox_items)

    context = {
        "active_page": "messages",
        "conversations": inbox_items,
        "current_filter": current_filter,
        "query": query,
        "selected_conversation": selected_conversation,
        "unread_total": unread_total,
        "conversation_total": len(all_inbox_items),
        "overview_items": _build_messages_overview_items(all_inbox_items),
        "reaction_emojis": MESSAGE_REACTION_EMOJIS,
    }

    payload = {
        "ok": True,
        "unread_total": unread_total,
        "conversation_total": len(all_inbox_items),
        "contact_list_html": render_to_string("app/partials/messages_contact_list.html", context, request=request),
    }

    if selected_conversation:
        message_window = _build_message_window(selected_conversation, request.user, before_id=message_before_id)
        message_items = message_window["message_items"]
        pinned_message_items = _build_pinned_message_items(selected_conversation, request.user)
        current_member = _get_conversation_member(selected_conversation, request.user)
        context.update(
            {
                "message_items": message_items,
                "has_older_messages": message_window["has_older_messages"],
                "oldest_message_id": message_window["oldest_message_id"],
                "pinned_message_items": pinned_message_items,
                "current_member_state": _build_current_member_state(current_member, request.user),
            }
        )
        payload.update(
            {
                "selected_conversation_id": selected_conversation.id,
                "message_stream_html": render_to_string("app/partials/messages_stream.html", context, request=request),
                "pinned_messages_html": render_to_string("app/partials/messages_pinned.html", context, request=request),
                "last_message_id": message_items[-1]["message"].id if message_items else None,
            }
        )
    else:
        payload["overview_html"] = render_to_string("app/partials/messages_overview.html", context, request=request)

    return JsonResponse(payload)


def _get_user_conversation(user, conversation_id):
    if not conversation_id:
        return None
    return Conversation.visible_for(user).filter(pk=conversation_id).first()


def _get_conversation_member(conversation, user):
    if not conversation:
        return None
    return next(
        (member for member in conversation.member_rows.all() if member.user_id == user.id),
        None,
    )


def _apply_conversation_member_action(conversation, user, action):
    member = _get_conversation_member(conversation, user)
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


def _build_current_member_state(member, user):
    if not member:
        return {
            "is_muted": False,
            "muted_until_label": "",
            "is_blocked": False,
        }

    is_muted = member.is_muted
    return {
        "is_muted": is_muted,
        "muted_until_label": format_user_datetime(member.muted_until, user) if is_muted else "",
        "is_blocked": member.is_blocked,
    }


def _build_inbox_items(conversations, user):
    items = []
    current_members = current_members_by_conversation(conversations, user)
    last_messages = last_messages_by_conversation(conversations)
    unread_counts = unread_counts_by_conversation(conversations, user, current_members)

    for conversation in conversations:
        last_message = last_messages.get(conversation.id)
        member = current_members.get(conversation.id)
        participants = list(conversation.member_rows.all())
        items.append(
            {
                "conversation": conversation,
                "title": conversation.display_title_for(user),
                "avatar": conversation.avatar_for(user),
                "avatar_url": _conversation_avatar_url_for(conversation, user),
                "preview": _conversation_preview(last_message, user),
                "time": _conversation_time_label(last_message.created_at, user) if last_message else "Neu",
                "unread": unread_counts.get(conversation.id, 0),
                "is_group": conversation.is_group,
                "is_muted": bool(member and member.is_muted),
                "is_blocked": bool(member and member.is_blocked),
                "participant_text": ", ".join(Conversation.display_name_for_user(row.user) for row in participants),
                "last_message_body": last_message.display_body if last_message else "",
            }
        )
    return items


def _filter_inbox_items(items, query, current_filter):
    if current_filter == "unread":
        items = [item for item in items if item["unread"] > 0]
    elif current_filter == "groups":
        items = [item for item in items if item["is_group"]]

    if query:
        normalized_query = query.casefold()
        items = [
            item
            for item in items
            if normalized_query in item["title"].casefold()
            or normalized_query in item["preview"].casefold()
            or normalized_query in item["participant_text"].casefold()
            or normalized_query in item["last_message_body"].casefold()
        ]

    return items


def _build_messages_overview_items(items):
    unread_items = [item for item in items if item["unread"] > 0]
    return unread_items[:5] or items[:5]


def _build_message_window(conversation, user, before_id=None):
    if not conversation:
        return {
            "message_items": [],
            "has_older_messages": False,
            "oldest_message_id": "",
        }

    messages, has_older_messages = _paginated_conversation_messages(conversation, before_id=before_id)
    message_items = _build_message_items(conversation, user, messages)
    return {
        "message_items": message_items,
        "has_older_messages": has_older_messages,
        "oldest_message_id": message_items[0]["message"].id if message_items else "",
    }


def _paginated_conversation_messages(conversation, before_id=None):
    messages = conversation.messages.select_related("sender", "sender__profile", "pinned_by").prefetch_related(
        "reactions__user",
        "reactions__user__profile",
    )
    before_id = _coerce_positive_int(before_id)
    if before_id:
        anchor = conversation.messages.filter(pk=before_id).only("id", "created_at").first()
        if anchor:
            messages = messages.filter(
                Q(created_at__lt=anchor.created_at)
                | Q(created_at=anchor.created_at, id__lt=anchor.id)
            )

    newest_first = list(messages.order_by("-created_at", "-id")[: MESSAGE_STREAM_PAGE_SIZE + 1])
    has_older_messages = len(newest_first) > MESSAGE_STREAM_PAGE_SIZE
    page_messages = newest_first[:MESSAGE_STREAM_PAGE_SIZE]
    return list(reversed(page_messages)), has_older_messages


def _coerce_positive_int(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _build_message_items(conversation, user, messages):
    items = []
    last_date = None
    for message in messages:
        message_date = localtime_for_user(message.created_at, user).date()
        items.append(
            {
                "message": message,
                "side": "out" if message.sender_id == user.id else "in",
                "time": format_user_time(message.created_at, user),
                "date_label": _date_separator_label(message.created_at, user) if message_date != last_date else "",
                "sender_name": Conversation.display_name_for_user(message.sender),
                "sender_avatar": Conversation.initials_for_user(message.sender),
                "sender_avatar_url": _profile_image_url_for_user(message.sender),
                "display_body": message.display_body,
                "is_deleted": message.is_deleted,
                "is_pinned": message.is_pinned,
                "can_delete": message.sender_id == user.id and not message.is_deleted,
                "can_react": not message.is_deleted,
                "read_receipt": _message_read_receipt(message, conversation, user) if message.sender_id == user.id else "",
                "read_receipt_is_read": _message_is_read_by_others(message, conversation, user) if message.sender_id == user.id else False,
                "reactions": _build_reaction_items(message, user),
            }
        )
        last_date = message_date
    return items


def _other_conversation_members(conversation, user):
    return [member for member in conversation.member_rows.all() if member.user_id != user.id]


def _message_read_count(message, conversation, user):
    return sum(
        1
        for member in _other_conversation_members(conversation, user)
        if member.last_read_at and member.last_read_at >= message.created_at
    )


def _message_is_read_by_others(message, conversation, user):
    return _message_read_count(message, conversation, user) > 0


def _message_read_receipt(message, conversation, user):
    other_members = _other_conversation_members(conversation, user)
    if not other_members:
        return ""

    read_count = _message_read_count(message, conversation, user)
    total_count = len(other_members)

    if conversation.is_group:
        if read_count:
            return f"Gelesen von {read_count}/{total_count}"
        return "Gesendet"

    return "Gelesen" if read_count else "Gesendet"


def _build_pinned_message_items(conversation, user):
    pinned_messages = conversation.messages.filter(is_pinned=True, is_deleted=False).select_related(
        "sender",
        "sender__profile",
        "pinned_by",
        "pinned_by__profile",
    ).order_by("-pinned_at", "-id")[:5]
    return [
        {
            "message": message,
            "sender_name": Conversation.display_name_for_user(message.sender),
            "pinned_by_name": Conversation.display_name_for_user(message.pinned_by) if message.pinned_by else "Unbekannt",
            "preview": message.display_body[:140],
        }
        for message in pinned_messages
    ]


def _build_reaction_items(message, user):
    grouped = []
    for emoji in MESSAGE_REACTION_EMOJIS:
        matching_reactions = [reaction for reaction in message.reactions.all() if reaction.emoji == emoji]
        if not matching_reactions:
            continue
        grouped.append(
            {
                "emoji": emoji,
                "count": len(matching_reactions),
                "is_own": any(reaction.user_id == user.id for reaction in matching_reactions),
                "title": ", ".join(Conversation.display_name_for_user(reaction.user) for reaction in matching_reactions),
            }
        )
    return grouped


def _delete_chat_message_for_user(message, user):
    if message.sender_id != user.id or message.is_deleted:
        return
    message.body = ""
    message.is_deleted = True
    message.deleted_at = timezone.now()
    message.is_pinned = False
    message.pinned_at = None
    message.pinned_by = None
    message.reactions.all().delete()
    message.save(update_fields=["body", "is_deleted", "deleted_at", "is_pinned", "pinned_at", "pinned_by"])


def _toggle_chat_message_pin(message, user):
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


def _toggle_chat_message_reaction(message, user, emoji):
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


def _build_member_items(conversation, user):
    return [
        {
            "name": Conversation.display_name_for_user(member.user),
            "avatar": Conversation.initials_for_user(member.user),
            "avatar_url": _profile_image_url_for_user(member.user),
            "status": "Du" if member.user_id == user.id else "Account",
        }
        for member in conversation.member_rows.select_related("user").all()
    ]


def _conversation_avatar_url_for(conversation, user):
    if not conversation or conversation.is_group or conversation.title:
        return ""
    other_user = next(
        (member.user for member in conversation.member_rows.all() if member.user_id != user.id),
        None,
    )
    return _profile_image_url_for_user(other_user or user)


def _profile_image_url_for_user(user):
    if not user:
        return ""
    profile = getattr(user, "profile", None)
    profile_image = getattr(profile, "profile_image", None)
    if not profile_image:
        return ""
    try:
        return profile_image.url
    except ValueError:
        return ""


def _conversation_preview(last_message, user):
    if not last_message:
        return "Noch keine Nachrichten"
    body = last_message.display_body
    if last_message.sender_id == user.id:
        return f"Du: {body}"
    return f"{Conversation.display_name_for_user(last_message.sender)}: {body}"


def _conversation_time_label(value, user):
    local_value = localtime_for_user(value, user)
    now = localtime_for_user(profile_or_user=user)
    if local_value.date() == now.date():
        return format_user_time(value, user)
    if local_value.date() == (now.date() - timedelta(days=1)):
        return "Gestern"
    return format_user_date(value, user)


def _date_separator_label(value, user):
    local_value = localtime_for_user(value, user)
    now = localtime_for_user(profile_or_user=user)
    if local_value.date() == now.date():
        return "Heute"
    if local_value.date() == (now.date() - timedelta(days=1)):
        return "Gestern"
    return format_user_date(value, user)
