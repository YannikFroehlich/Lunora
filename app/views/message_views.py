import hashlib
from datetime import timedelta
from urllib.parse import urlencode

from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import content_disposition_header

from app.forms import ChatPollForm, ConversationStartForm, MessageForm
from app.models import (
    ChatMessage,
    ChatMessageAttachment,
    ChatMessageReaction,
    ChatPoll,
    ChatPollVote,
    Conversation,
    ConversationMember,
    ScheduledChatMessage,
)
from app.services.chat import (
    MESSAGE_REACTION_EMOJIS,
    apply_member_action,
    apply_mentions,
    attach_file,
    blocked_by_other_side,
    can_edit_message,
    can_send,
    cancel_scheduled_message,
    close_poll,
    conversation_member,
    create_poll,
    delete_message,
    edit_message,
    forward_message,
    forward_targets,
    mark_conversation_mentions_read,
    mention_candidates,
    other_members,
    poll_item,
    poll_prefetch,
    render_message_body,
    schedule_bounds,
    schedule_message,
    scheduled_message_items,
    send_due_scheduled_messages,
    send_scheduled_message_now,
    toggle_pin,
    toggle_poll_vote,
    toggle_reaction,
)
from app.services.message_queries import (
    current_members_by_conversation,
    last_messages_by_conversation,
    unread_state_by_conversation,
)
from app.services.note_search import build_snippet, parse_search_query
from app.services.storage_quota import ensure_storage_available
from app.services.system_settings import disabled_feature_response, feature_enabled
from app.services.user_preferences import (
    format_user_date,
    format_user_datetime,
    format_user_time,
    localtime_for_user,
)

MESSAGE_STREAM_PAGE_SIZE = 50
CHAT_SEARCH_LIMIT = 30
# Newer messages kept below a search hit when the stream jumps to it.
CHAT_SEARCH_NEWER_CONTEXT = 5


@login_required
def messages(request, conversation_id=None):
    if not feature_enabled("messages"):
        return disabled_feature_response(request, "messages")

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
            message_form = MessageForm(request.POST, request.FILES, user=request.user)
            if not selected_conversation:
                django_messages.error(request, "Diese Unterhaltung wurde nicht gefunden.")
                return redirect("messages")
            current_member = conversation_member(selected_conversation, request.user)
            if current_member and current_member.is_blocked:
                django_messages.error(
                    request,
                    "Du hast diesen Chat blockiert. Hebe die Blockierung auf, um wieder zu schreiben.",
                )
                return redirect("messages_detail", conversation_id=selected_conversation.id)
            if blocked_by_other_side(selected_conversation, request.user):
                django_messages.error(request, "Diese Nachricht konnte nicht gesendet werden.")
                return redirect("messages_detail", conversation_id=selected_conversation.id)
            if message_form.is_valid():
                reply_to = None
                reply_to_id = _coerce_positive_int(request.POST.get("reply_to_id"))
                if reply_to_id:
                    reply_to = selected_conversation.messages.filter(pk=reply_to_id, is_deleted=False).first()
                send_at = message_form.cleaned_data.get("send_at")
                if send_at:
                    try:
                        schedule_message(
                            selected_conversation,
                            request.user,
                            message_form.cleaned_data["body"],
                            send_at,
                            reply_to=reply_to,
                        )
                    except ValueError as error:
                        django_messages.error(request, str(error))
                    else:
                        django_messages.success(
                            request,
                            f"Nachricht für {format_user_datetime(send_at, request.user)} geplant.",
                        )
                    return redirect("messages_detail", conversation_id=selected_conversation.id)
                upload = message_form.cleaned_data.get("attachment")
                try:
                    with transaction.atomic():
                        # Checked again under the lock: the form's check can't see parallel uploads.
                        ensure_storage_available(request.user, upload.size if upload else 0)
                        message = message_form.save(commit=False)
                        message.conversation = selected_conversation
                        message.sender = request.user
                        message.reply_to = reply_to
                        message.save()
                        if upload:
                            attach_file(
                                message, request.user, upload, message_form.cleaned_data.get("voice_duration")
                            )
                except ValidationError as error:
                    django_messages.error(request, " ".join(error.messages))
                    return redirect("messages_detail", conversation_id=selected_conversation.id)
                selected_conversation.updated_at = message.created_at
                selected_conversation.save(update_fields=["updated_at"])
                apply_mentions(message)
                selected_conversation.mark_read_for(request.user)
                return redirect("messages_detail", conversation_id=selected_conversation.id)

        elif form_name == "start_conversation":
            start_form = ConversationStartForm(request.POST, user=request.user)
            if start_form.is_valid():
                recipients = list(start_form.cleaned_data["recipient"])
                if len(recipients) == 1:
                    recipient = recipients[0]
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
                else:
                    selected_conversation = Conversation.objects.create(
                        created_by=request.user,
                        title=start_form.cleaned_data.get("title", ""),
                        is_group=True,
                    )
                    ConversationMember.objects.bulk_create(
                        [ConversationMember(conversation=selected_conversation, user=request.user)]
                        + [
                            ConversationMember(conversation=selected_conversation, user=recipient)
                            for recipient in recipients
                        ]
                    )

                body = start_form.cleaned_data.get("body")
                if body and not blocked_by_other_side(selected_conversation, request.user):
                    message = ChatMessage.objects.create(
                        conversation=selected_conversation,
                        sender=request.user,
                        body=body,
                    )
                    selected_conversation.updated_at = message.created_at
                    selected_conversation.save(update_fields=["updated_at"])
                    apply_mentions(message)
                elif body:
                    django_messages.error(request, "Diese Nachricht konnte nicht gesendet werden.")
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
                delete_message(message, request.user)
            elif action == "pin":
                toggle_pin(message, request.user)
            elif action == "reaction":
                toggle_reaction(message, request.user, request.POST.get("emoji"))

            return redirect("messages_detail", conversation_id=selected_conversation.id)

        elif form_name == "message_forward":
            source_conversation = _get_user_conversation(
                request.user, request.POST.get("conversation_id") or conversation_id
            )
            if not source_conversation:
                django_messages.error(request, "Diese Unterhaltung wurde nicht gefunden.")
                return redirect("messages")
            message = (
                source_conversation.messages.filter(pk=request.POST.get("message_id"), is_deleted=False)
                .select_related("attachment")
                .first()
            )
            target = _get_user_conversation(request.user, request.POST.get("target_conversation_id"))
            is_poll = bool(message and ChatPoll.objects.filter(message=message).exists())
            if not message or is_poll or not target or target.pk == source_conversation.pk:
                django_messages.error(request, "Die Nachricht konnte nicht weitergeleitet werden.")
                return redirect("messages_detail", conversation_id=source_conversation.id)
            target_member = conversation_member(target, request.user)
            if (target_member and target_member.is_blocked) or blocked_by_other_side(target, request.user):
                django_messages.error(request, "In diese Unterhaltung kann derzeit nicht geschrieben werden.")
                return redirect("messages_detail", conversation_id=source_conversation.id)
            attachment = getattr(message, "attachment", None)
            try:
                with transaction.atomic():
                    ensure_storage_available(request.user, attachment.size if attachment else 0)
                    forwarded = forward_message(message, target, request.user)
            except ValidationError as error:
                django_messages.error(request, " ".join(error.messages))
                return redirect("messages_detail", conversation_id=source_conversation.id)
            target.updated_at = forwarded.created_at
            target.save(update_fields=["updated_at"])
            target.mark_read_for(request.user)
            django_messages.success(
                request, f"Nachricht an „{target.display_title_for(request.user)}“ weitergeleitet."
            )
            return redirect("messages_detail", conversation_id=target.id)

        elif form_name in {"poll_create", "poll_vote", "poll_close", "scheduled_action", "message_edit"}:
            selected_conversation = _get_user_conversation(
                request.user,
                request.POST.get("conversation_id") or conversation_id,
            )
            if not selected_conversation:
                django_messages.error(request, "Diese Unterhaltung wurde nicht gefunden.")
                return redirect("messages")
            _handle_chat_tool_post(request, form_name, selected_conversation)
            return redirect("messages_detail", conversation_id=selected_conversation.id)

        elif form_name == "member_action":
            selected_conversation = _get_user_conversation(
                request.user,
                request.POST.get("conversation_id") or conversation_id,
            )
            if not selected_conversation:
                return redirect("messages")

            action = request.POST.get("action")
            if action == "add_member" and selected_conversation.is_group:
                new_member_id = request.POST.get("new_member")
                new_member = User.objects.filter(pk=new_member_id, is_active=True).first()
                if new_member:
                    ConversationMember.objects.get_or_create(
                        conversation=selected_conversation,
                        user=new_member,
                        defaults={"is_archived": False},
                    )
                return redirect("messages_detail", conversation_id=selected_conversation.id)

            if action == "leave_group" and selected_conversation.is_group:
                ConversationMember.objects.filter(
                    conversation=selected_conversation, user=request.user
                ).delete()
                ScheduledChatMessage.objects.filter(
                    conversation=selected_conversation, sender=request.user
                ).delete()
                return redirect("messages")

            apply_member_action(selected_conversation, request.user, action)
            return redirect("messages_detail", conversation_id=selected_conversation.id)

        elif form_name == "archive":
            selected_conversation = _get_user_conversation(
                request.user,
                request.POST.get("conversation_id") or conversation_id,
            )
            if selected_conversation:
                ConversationMember.objects.filter(
                    conversation=selected_conversation, user=request.user
                ).update(is_archived=True)
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
        send_due_scheduled_messages(conversation=selected_conversation)
        selected_conversation.mark_read_for(request.user)
        mark_conversation_mentions_read(selected_conversation, request.user)
        selected_conversation = _get_user_conversation(request.user, selected_conversation.id)
        all_conversations = list(Conversation.visible_for(request.user))

    query = request.GET.get("q", "").strip()
    current_filter = request.GET.get("filter", "all")
    message_before_id = _message_before_id(request, selected_conversation)
    chat_search_query = request.GET.get("find", "").strip()
    all_inbox_items = _build_inbox_items(all_conversations, request.user)
    message_match_ids = _conversation_ids_matching_message_query(all_conversations, query)
    inbox_items = _filter_inbox_items(all_inbox_items, query, current_filter, message_match_ids)
    overview_items = _build_messages_overview_items(all_inbox_items)

    message_window = _build_message_window(selected_conversation, request.user, before_id=message_before_id)
    message_items = message_window["message_items"]
    pinned_message_items = (
        _build_pinned_message_items(selected_conversation, request.user) if selected_conversation else []
    )
    selected_members = (
        _build_member_items(selected_conversation, request.user) if selected_conversation else []
    )
    current_member = (
        conversation_member(selected_conversation, request.user) if selected_conversation else None
    )
    current_member_state = _build_current_member_state(current_member, request.user, selected_conversation)
    available_recipients = (
        _available_group_recipients(selected_conversation)
        if selected_conversation and selected_conversation.is_group
        else []
    )

    context = {
        "active_page": "messages",
        "conversations": inbox_items,
        "current_filter": current_filter,
        "query": query,
        "selected_conversation": selected_conversation,
        "selected_title": selected_conversation.display_title_for(request.user)
        if selected_conversation
        else "",
        "selected_avatar": selected_conversation.avatar_for(request.user) if selected_conversation else "",
        "selected_avatar_url": _conversation_avatar_url_for(selected_conversation, request.user)
        if selected_conversation
        else "",
        "selected_members": selected_members,
        "message_items": message_items,
        "has_older_messages": message_window["has_older_messages"],
        "oldest_message_id": message_window["oldest_message_id"],
        "showing_history": message_window["showing_history"],
        "chat_search_query": chat_search_query,
        "chat_search_results": _search_conversation_messages(
            selected_conversation, request.user, chat_search_query
        )
        if selected_conversation
        else [],
        "pinned_message_items": pinned_message_items,
        "reaction_emojis": MESSAGE_REACTION_EMOJIS,
        "message_form": message_form,
        "start_form": start_form,
        "unread_total": sum(item["unread"] for item in all_inbox_items),
        "conversation_total": len(all_inbox_items),
        "overview_items": overview_items,
        "current_member_state": current_member_state,
        "available_recipients": available_recipients,
    }
    context["scheduled_items"] = scheduled_message_items(selected_conversation, request.user)
    context["schedule_bounds"] = schedule_bounds(request.user)
    # The forward and poll dialogs and the @ suggestions live outside the live-updated partials,
    # so only the full page needs these.
    context["forward_targets"] = (
        forward_targets(request.user, selected_conversation) if selected_conversation else []
    )
    context["poll_form"] = ChatPollForm()
    context["poll_option_slots"] = range(1, 5)
    context["mention_candidates"] = mention_candidates(selected_conversation, request.user)
    return render(request, "app/messages.html", context)


@login_required
def messages_live_updates(request, conversation_id=None):
    if not feature_enabled("messages"):
        return disabled_feature_response(request, "messages", json_response=True)

    if request.method != "GET":
        return JsonResponse({"ok": False, "error": "Nur GET ist erlaubt."}, status=405)

    selected_conversation = _get_user_conversation(request.user, conversation_id) if conversation_id else None
    if conversation_id and not selected_conversation:
        return JsonResponse({"ok": False, "error": "Diese Unterhaltung wurde nicht gefunden."}, status=404)

    if selected_conversation:
        send_due_scheduled_messages(conversation=selected_conversation)
        selected_conversation.mark_read_for(request.user)
        selected_conversation = _get_user_conversation(request.user, selected_conversation.id)

    typing_label = _typing_label(selected_conversation, request.user)
    fingerprint = _live_updates_fingerprint(request, typing_label)
    if request.GET.get("fp") == fingerprint:
        return JsonResponse({"ok": True, "unchanged": True, "fingerprint": fingerprint})
    if selected_conversation:
        mark_conversation_mentions_read(selected_conversation, request.user)

    all_conversations = list(Conversation.visible_for(request.user))
    query = request.GET.get("q", "").strip()
    current_filter = request.GET.get("filter", "all")
    message_before_id = _message_before_id(request, selected_conversation)
    all_inbox_items = _build_inbox_items(all_conversations, request.user)
    message_match_ids = _conversation_ids_matching_message_query(all_conversations, query)
    inbox_items = _filter_inbox_items(all_inbox_items, query, current_filter, message_match_ids)
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
        "message_form": MessageForm(),
    }

    payload = {
        "ok": True,
        "fingerprint": fingerprint,
        "unread_total": unread_total,
        "conversation_total": len(all_inbox_items),
        "contact_list_html": render_to_string(
            "app/partials/messages_contact_list.html", context, request=request
        ),
    }

    if selected_conversation:
        message_window = _build_message_window(
            selected_conversation, request.user, before_id=message_before_id
        )
        message_items = message_window["message_items"]
        pinned_message_items = _build_pinned_message_items(selected_conversation, request.user)
        current_member = conversation_member(selected_conversation, request.user)
        context.update(
            {
                "message_items": message_items,
                "has_older_messages": message_window["has_older_messages"],
                "oldest_message_id": message_window["oldest_message_id"],
                "showing_history": message_window["showing_history"],
                "pinned_message_items": pinned_message_items,
                "current_member_state": _build_current_member_state(
                    current_member, request.user, selected_conversation
                ),
                "scheduled_items": scheduled_message_items(selected_conversation, request.user),
                "schedule_bounds": schedule_bounds(request.user),
            }
        )
        current_member_state = context["current_member_state"]
        payload.update(
            {
                "selected_conversation_id": selected_conversation.id,
                "message_stream_html": render_to_string(
                    "app/partials/messages_stream.html", context, request=request
                ),
                "pinned_messages_html": render_to_string(
                    "app/partials/messages_pinned.html", context, request=request
                ),
                "compose_html": render_to_string(
                    "app/partials/messages_compose.html", context, request=request
                ),
                "scheduled_messages_html": render_to_string(
                    "app/partials/messages_scheduled.html", context, request=request
                ),
                "compose_blocked": bool(
                    current_member_state["is_blocked"] or current_member_state["blocked_by_recipient"]
                ),
                "last_message_id": message_items[-1]["message"].id if message_items else None,
                "typing_label": typing_label,
            }
        )
    else:
        payload["overview_html"] = render_to_string(
            "app/partials/messages_overview.html", context, request=request
        )

    return JsonResponse(payload)


@login_required
def chat_typing_ping(request, conversation_id):
    if not feature_enabled("messages"):
        return disabled_feature_response(request, "messages", json_response=True)

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Nur POST ist erlaubt."}, status=405)

    conversation = _get_user_conversation(request.user, conversation_id)
    if not conversation:
        return JsonResponse({"ok": False, "error": "Diese Unterhaltung wurde nicht gefunden."}, status=404)

    ConversationMember.objects.filter(conversation=conversation, user=request.user).update(
        typing_until=timezone.now() + timedelta(seconds=6)
    )
    return JsonResponse({"ok": True})


@login_required
def chat_attachment_download(request, file_id, disposition):
    if not feature_enabled("messages"):
        return disabled_feature_response(request, "messages", json_response=True)

    attachment = (
        ChatMessageAttachment.objects.select_related("message__conversation").filter(file_id=file_id).first()
    )
    if not attachment or disposition not in {"inline", "download"}:
        return JsonResponse({"ok": False, "error": "Datei nicht gefunden."}, status=404)

    if not Conversation.visible_for(request.user).filter(pk=attachment.message.conversation_id).exists():
        return JsonResponse({"ok": False, "error": "Datei nicht gefunden."}, status=404)

    content_type = attachment.content_type or "application/octet-stream"
    byte_range = _requested_byte_range(request, attachment.size) if disposition == "inline" else None
    if byte_range == "invalid":
        response = HttpResponse(status=416)
        response["Content-Range"] = f"bytes */{attachment.size}"
        return response
    if byte_range:
        # Safari only plays <audio> from servers that answer Range requests with 206.
        start, end = byte_range
        with attachment.file.open("rb") as handle:
            handle.seek(start)
            chunk = handle.read(end - start + 1)
        response = HttpResponse(chunk, status=206, content_type=content_type)
        response["Content-Range"] = f"bytes {start}-{end}/{attachment.size}"
    else:
        response = FileResponse(attachment.file.open("rb"), content_type=content_type)
    response["Accept-Ranges"] = "bytes" if disposition == "inline" else "none"
    response["Content-Disposition"] = content_disposition_header(
        disposition == "download",
        attachment.original_name,
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _handle_chat_tool_post(request, form_name, conversation):
    """Poll and scheduled-message POST branches of ``messages()``; feedback goes to the flash."""
    user = request.user
    if form_name == "message_edit":
        message = (
            conversation.messages.filter(pk=_coerce_positive_int(request.POST.get("message_id")), sender=user)
            .select_related("poll", "attachment")
            .first()
        )
        if message is None:
            django_messages.error(request, "Diese Nachricht wurde nicht gefunden.")
        elif not can_send(conversation, user):
            django_messages.error(request, "In diese Unterhaltung kann derzeit nicht geschrieben werden.")
        else:
            try:
                edit_message(message, user, request.POST.get("body"))
            except ValueError as error:
                django_messages.error(request, str(error))
        return

    if form_name == "poll_create":
        form = ChatPollForm(request.POST)
        if not conversation.is_group:
            django_messages.error(request, "Umfragen gibt es nur in Gruppenchats.")
        elif not can_send(conversation, user):
            django_messages.error(request, "In diese Unterhaltung kann derzeit nicht geschrieben werden.")
        elif not form.is_valid():
            errors = [error for field_errors in form.errors.values() for error in field_errors]
            django_messages.error(
                request, errors[0] if errors else "Die Umfrage konnte nicht erstellt werden."
            )
        else:
            create_poll(
                conversation,
                user,
                form.cleaned_data["question"],
                form.cleaned_data["options"],
                allows_multiple=form.cleaned_data["allows_multiple"],
            )
            conversation.mark_read_for(user)
        return

    if form_name in {"poll_vote", "poll_close"}:
        poll = (
            ChatPoll.objects.select_related("message")
            .filter(pk=_coerce_positive_int(request.POST.get("poll_id")), message__conversation=conversation)
            .first()
        )
        if poll is None:
            django_messages.error(request, "Diese Umfrage wurde nicht gefunden.")
        elif form_name == "poll_close":
            close_poll(poll, user)
        elif not can_send(conversation, user):
            django_messages.error(request, "In dieser Unterhaltung kannst du derzeit nicht abstimmen.")
        elif not toggle_poll_vote(poll, user, _coerce_positive_int(request.POST.get("option_id"))):
            django_messages.error(request, "Diese Umfrage ist beendet.")
        return

    scheduled_id = _coerce_positive_int(request.POST.get("scheduled_id"))
    action = request.POST.get("action")
    if action == "send_now":
        if send_scheduled_message_now(conversation, user, scheduled_id) is None:
            django_messages.error(request, "Die geplante Nachricht konnte nicht gesendet werden.")
        else:
            conversation.mark_read_for(user)
    elif action == "cancel" and cancel_scheduled_message(conversation, user, scheduled_id):
        django_messages.success(request, "Geplante Nachricht gelöscht.")


def _requested_byte_range(request, size):
    """Parse a single ``bytes=start-end`` range; ``None`` serves the whole file."""
    header = request.headers.get("Range", "")
    if not header.startswith("bytes=") or "," in header or size <= 0:
        return None
    start_text, _, end_text = header[len("bytes=") :].strip().partition("-")
    try:
        if start_text:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
        else:
            suffix = int(end_text)
            if suffix <= 0:
                return "invalid"
            start, end = max(size - suffix, 0), size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        return "invalid"
    return start, min(end, size - 1)


def _get_user_conversation(user, conversation_id):
    if not conversation_id:
        return None
    return Conversation.visible_for(user).filter(pk=conversation_id).first()


def _live_updates_fingerprint(request, typing_label):
    """Cheap digest of the state the live partials render from, so idle polls skip all rendering."""
    conversation_ids = ConversationMember.objects.filter(user=request.user).values("conversation_id")
    message_state = ChatMessage.objects.filter(conversation_id__in=conversation_ids).aggregate(
        count=Count("id"),
        last_id=Max("id"),
        edited=Max("edited_at"),
        deleted=Count("id", filter=Q(is_deleted=True)),
        pinned=Count("id", filter=Q(is_pinned=True)),
        pinned_at=Max("pinned_at"),
    )
    reaction_state = ChatMessageReaction.objects.filter(
        message__conversation_id__in=conversation_ids
    ).aggregate(count=Count("id"), last_id=Max("id"))
    poll_state = (
        ChatPollVote.objects.filter(poll__message__conversation_id__in=conversation_ids).aggregate(
            count=Count("id"), last_id=Max("id")
        ),
        ChatPoll.objects.filter(message__conversation_id__in=conversation_ids).aggregate(
            closed=Count("id", filter=Q(closed_at__isnull=False))
        ),
    )
    scheduled_state = ScheduledChatMessage.objects.filter(sender=request.user).aggregate(
        count=Count("id"), last_id=Max("id")
    )
    member_state = list(
        ConversationMember.objects.filter(conversation_id__in=conversation_ids)
        .order_by("id")
        .values_list("id", "is_archived", "is_blocked", "muted_until", "last_read_at")
    )
    conversation_state = list(
        Conversation.objects.filter(id__in=conversation_ids).order_by("id").values_list("id", "updated_at")
    )
    # "_" is the clients' cache-busting timestamp and must not make every poll look changed.
    params = sorted((key, value) for key, value in request.GET.items() if key not in {"fp", "_"})
    # ponytail: the minute bucket caps staleness of untracked inputs (profile names/avatars, mute
    # expiry, "Heute"/"Gestern" labels) at ~60s; track them explicitly if that ever matters.
    minute = timezone.now().replace(second=0, microsecond=0)
    raw = repr(
        (
            message_state,
            reaction_state,
            poll_state,
            scheduled_state,
            member_state,
            conversation_state,
            params,
            typing_label,
            minute,
        )
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _typing_label(conversation, user):
    if not conversation:
        return ""
    now = timezone.now()
    typing_users = [
        member.user
        for member in other_members(conversation, user)
        if member.typing_until and member.typing_until > now
    ]
    if not typing_users:
        return ""
    names = [Conversation.display_name_for_user(typing_user) for typing_user in typing_users[:2]]
    if len(typing_users) == 1:
        return f"{names[0]} schreibt …"
    if len(typing_users) == 2:
        return f"{names[0]} und {names[1]} schreiben …"
    return f"{names[0]}, {names[1]} und weitere schreiben …"


def _build_current_member_state(member, user, conversation=None):
    if not member:
        return {
            "is_muted": False,
            "muted_until_label": "",
            "is_blocked": False,
            "blocked_by_recipient": False,
        }

    is_muted = member.is_muted
    return {
        "is_muted": is_muted,
        "muted_until_label": format_user_datetime(member.muted_until, user) if is_muted else "",
        "is_blocked": member.is_blocked,
        "blocked_by_recipient": blocked_by_other_side(conversation, user),
    }


def _build_inbox_items(conversations, user):
    items = []
    current_members = current_members_by_conversation(conversations, user)
    last_messages = last_messages_by_conversation(conversations)
    unread_state = unread_state_by_conversation(conversations, user, current_members)

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
                "unread": unread_state.get(conversation.id, (0, False))[0],
                "has_unread_mention": unread_state.get(conversation.id, (0, False))[1],
                "is_group": conversation.is_group,
                "is_muted": bool(member and member.is_muted),
                "is_blocked": bool(member and member.is_blocked),
                "participant_text": ", ".join(
                    Conversation.display_name_for_user(row.user) for row in participants
                ),
                "last_message_body": _message_preview_text(last_message) if last_message else "",
            }
        )
    return items


def _filter_inbox_items(items, query, current_filter, message_match_ids=None):
    if current_filter == "unread":
        items = [item for item in items if item["unread"] > 0]
    elif current_filter == "groups":
        items = [item for item in items if item["is_group"]]

    if query:
        normalized_query = query.casefold()
        message_match_ids = message_match_ids or set()
        items = [
            item
            for item in items
            if normalized_query in item["title"].casefold()
            or normalized_query in item["preview"].casefold()
            or normalized_query in item["participant_text"].casefold()
            or normalized_query in item["last_message_body"].casefold()
            or item["conversation"].id in message_match_ids
        ]

    return items


def _conversation_ids_matching_message_query(conversations, query):
    query = query.strip()
    if not query:
        return set()

    conversation_ids = [conversation.id for conversation in conversations]
    if not conversation_ids:
        return set()

    return set(
        ChatMessage.objects.filter(
            conversation_id__in=conversation_ids,
            is_deleted=False,
            body__icontains=query,
        ).values_list("conversation_id", flat=True)
    )


def _build_messages_overview_items(items):
    unread_items = [item for item in items if item["unread"] > 0]
    return unread_items[:5] or items[:5]


def _build_message_window(conversation, user, before_id=None):
    if not conversation:
        return {
            "message_items": [],
            "has_older_messages": False,
            "oldest_message_id": "",
            "showing_history": False,
        }

    messages, has_older_messages = _paginated_conversation_messages(conversation, before_id=before_id)
    message_items = _build_message_items(conversation, user, messages)
    return {
        "message_items": message_items,
        "has_older_messages": has_older_messages,
        "oldest_message_id": message_items[0]["message"].id if message_items else "",
        # An older page ends before the newest messages, so the stream offers a way back to them.
        "showing_history": bool(_coerce_positive_int(before_id)),
    }


def _message_before_id(request, conversation):
    """The page boundary from ?before=, or from ?around= (a search hit) with a few newer messages kept."""
    around_id = _coerce_positive_int(request.GET.get("around"))
    if not (around_id and conversation):
        return request.GET.get("before", "").strip()
    target = conversation.messages.filter(pk=around_id).only("id", "created_at").first()
    if target is None:
        return ""
    newer_ids = list(
        conversation.messages.filter(
            Q(created_at__gt=target.created_at) | Q(created_at=target.created_at, id__gt=target.id)
        )
        .order_by("created_at", "id")
        .values_list("id", flat=True)[: CHAT_SEARCH_NEWER_CONTEXT + 1]
    )
    # Few newer messages: the hit is already on the newest page.
    return str(newer_ids[CHAT_SEARCH_NEWER_CONTEXT]) if len(newer_ids) > CHAT_SEARCH_NEWER_CONTEXT else ""


def _search_conversation_messages(conversation, user, query):
    """Newest messages of one conversation matching the query, each linking to its place in the stream.

    Same syntax as the note search: words AND, "phrases", -exclusions; matched as substrings.
    """
    parsed = parse_search_query(query)
    if not parsed:
        return []
    matches = conversation.messages.filter(is_deleted=False).select_related("sender", "sender__profile")
    for fragment in parsed.terms + parsed.phrases:
        matches = matches.filter(body__icontains=fragment)
    for fragment in parsed.exclusions:
        matches = matches.exclude(body__icontains=fragment)

    detail_url = reverse("messages_detail", args=[conversation.id])
    return [
        {
            "sender_name": Conversation.display_name_for_user(message.sender),
            "time_label": _conversation_time_label(message.created_at, user),
            "snippet": build_snippet(message.body, parsed, length=140),
            "url": f"{detail_url}?{urlencode({'find': query, 'around': message.id})}#hit-{message.id}",
        }
        for message in matches.order_by("-created_at", "-id")[:CHAT_SEARCH_LIMIT]
    ]


def _paginated_conversation_messages(conversation, before_id=None):
    messages = conversation.messages.select_related(
        "sender",
        "sender__profile",
        "pinned_by",
        "attachment",
        "reply_to",
        "reply_to__sender",
        "reply_to__sender__profile",
        "reply_to__attachment",
        "reply_to__poll",
        "poll",
    ).prefetch_related(
        "reactions__user",
        "reactions__user__profile",
        "mentioned_users__profile",
        *poll_prefetch(),
    )
    before_id = _coerce_positive_int(before_id)
    if before_id:
        anchor = conversation.messages.filter(pk=before_id).only("id", "created_at").first()
        if anchor:
            messages = messages.filter(
                Q(created_at__lt=anchor.created_at) | Q(created_at=anchor.created_at, id__lt=anchor.id)
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
    now = timezone.now()
    for message in messages:
        message_date = localtime_for_user(message.created_at, user).date()
        items.append(
            {
                "message": message,
                "side": "out" if message.sender_id == user.id else "in",
                "time": format_user_time(message.created_at, user),
                "date_label": _date_separator_label(message.created_at, user)
                if message_date != last_date
                else "",
                "sender_name": Conversation.display_name_for_user(message.sender),
                "sender_avatar": Conversation.initials_for_user(message.sender),
                "sender_avatar_url": _profile_image_url_for_user(message.sender),
                "display_body": message.display_body,
                "body_html": render_message_body(message, user),
                "poll": poll_item(message, user),
                "is_mentioned": not message.is_deleted
                and any(mentioned.pk == user.id for mentioned in message.mentioned_users.all()),
                "is_deleted": message.is_deleted,
                "is_pinned": message.is_pinned,
                "is_forwarded": message.is_forwarded and not message.is_deleted,
                "can_delete": message.sender_id == user.id and not message.is_deleted,
                "can_react": not message.is_deleted,
                "can_forward": not message.is_deleted and getattr(message, "poll", None) is None,
                "can_edit": can_edit_message(message, user, now),
                "is_edited": bool(message.edited_at) and not message.is_deleted,
                "read_receipt": _message_read_receipt(message, conversation, user)
                if message.sender_id == user.id
                else "",
                "read_receipt_is_read": _message_is_read_by_others(message, conversation, user)
                if message.sender_id == user.id
                else False,
                "reactions": _build_reaction_items(message, user),
                "attachment": _build_attachment_item(message),
                "reply_preview": _build_reply_preview(message),
                "preview_text": _message_preview_text(message)[:140],
            }
        )
        last_date = message_date
    return items


def _build_reply_preview(message):
    original = message.reply_to
    if not original:
        return None
    return {
        "message_id": original.id,
        "sender_name": Conversation.display_name_for_user(original.sender),
        "text": _message_preview_text(original)[:140],
    }


def _message_read_count(message, conversation, user):
    return sum(
        1
        for member in other_members(conversation, user)
        if member.last_read_at and member.last_read_at >= message.created_at
    )


def _message_is_read_by_others(message, conversation, user):
    return _message_read_count(message, conversation, user) > 0


def _message_read_receipt(message, conversation, user):
    others = other_members(conversation, user)
    if not others:
        return ""

    read_count = _message_read_count(message, conversation, user)
    total_count = len(others)

    if conversation.is_group:
        if read_count:
            return f"Gelesen von {read_count}/{total_count}"
        return "Gesendet"

    return "Gelesen" if read_count else "Gesendet"


def _build_pinned_message_items(conversation, user):
    pinned_messages = (
        conversation.messages.filter(is_pinned=True, is_deleted=False)
        .select_related(
            "sender",
            "sender__profile",
            "pinned_by",
            "pinned_by__profile",
            "attachment",
            "poll",
        )
        .order_by("-pinned_at", "-id")[:5]
    )
    return [
        {
            "message": message,
            "sender_name": Conversation.display_name_for_user(message.sender),
            "pinned_by_name": Conversation.display_name_for_user(message.pinned_by)
            if message.pinned_by
            else "Unbekannt",
            "preview": _message_preview_text(message)[:140],
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
                "title": ", ".join(
                    Conversation.display_name_for_user(reaction.user) for reaction in matching_reactions
                ),
            }
        )
    return grouped


def _format_duration(seconds):
    if seconds is None:
        return ""
    return f"{seconds // 60}:{seconds % 60:02d}"


def _format_file_size(size_bytes):
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.0f} KB"
    return f"{size_bytes} B"


def _build_attachment_item(message):
    attachment = getattr(message, "attachment", None)
    if not attachment or message.is_deleted:
        return None
    return {
        "kind": attachment.kind,
        "name": attachment.original_name,
        "size_label": _format_file_size(attachment.size),
        "duration_label": _format_duration(attachment.duration_seconds),
        "inline_url": reverse("chat_attachment_download", args=[attachment.file_id, "inline"]),
        "download_url": reverse("chat_attachment_download", args=[attachment.file_id, "download"]),
    }


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


def _available_group_recipients(conversation):
    member_ids = [member.user_id for member in conversation.member_rows.all()]
    return list(
        User.objects.filter(is_active=True)
        .exclude(pk__in=member_ids)
        .order_by("first_name", "email", "username")
    )


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
    body = _message_preview_text(last_message)
    if last_message.sender_id == user.id:
        return f"Du: {body}"
    return f"{Conversation.display_name_for_user(last_message.sender)}: {body}"


def _message_preview_text(message):
    if message.is_deleted:
        return message.display_body
    if getattr(message, "poll", None) is not None:
        return f"📊 Umfrage: {message.body}"
    if message.body:
        return message.body
    attachment = getattr(message, "attachment", None)
    if attachment:
        if attachment.kind == ChatMessageAttachment.KIND_IMAGE:
            return "📷 Bild"
        if attachment.kind == ChatMessageAttachment.KIND_AUDIO:
            return "🎤 Sprachnachricht"
        return f"📎 {attachment.original_name}"
    return ""


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
