from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from app.models import (
    ChatMessage,
    ChatMessageAttachment,
    ChatMessageReaction,
    Conversation,
    ConversationMember,
    Profile,
)
from app.tests.helpers import PNG_1X1_BYTES
from app.views.message_views import _build_inbox_items


class MessagesPageTests(TestCase):
    def setUp(self):
        self.mira = User.objects.create_user(
            username="mira@example.com",
            email="mira@example.com",
            password="secret-12345",
            first_name="Mira",
        )
        self.lukas = User.objects.create_user(
            username="lukas@example.com",
            email="lukas@example.com",
            password="secret-12345",
            first_name="Lukas",
        )
        self.anna = User.objects.create_user(
            username="anna@example.com",
            email="anna@example.com",
            password="secret-12345",
            first_name="Anna",
        )
        Profile.objects.create(user=self.mira, display_name="Mira")
        Profile.objects.create(user=self.lukas, display_name="Lukas")
        Profile.objects.create(user=self.anna, display_name="Anna")

    def test_messages_page_requires_login(self):
        response = self.client.get("/messages/")

        self.assertRedirects(response, "/login/?next=/messages/")

    def test_messages_empty_state_offers_direct_start_action(self):
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/messages/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-new-chat-card")
        self.assertContains(response, "data-open-new-chat", count=2)
        self.assertContains(response, "Neue Unterhaltung starten", count=2)
        self.assertNotContains(response, "Starte links")
        self.assertNotContains(response, "Starte oben")

    def test_start_conversation_excludes_inactive_users(self):
        self.anna.is_active = False
        self.anna.save(update_fields=["is_active"])
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/messages/")
        self.assertNotContains(response, "anna@example.com")

        response = self.client.post(
            "/messages/",
            {"form_name": "start_conversation", "recipient": str(self.anna.id), "body": "Hallo"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Conversation.objects.exists())

    def test_user_can_start_direct_conversation_and_send_message(self):
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/messages/",
            {
                "form_name": "start_conversation",
                "recipient": str(self.lukas.id),
                "body": "Hey Lukas!",
            },
        )

        conversation = Conversation.objects.get()
        self.assertRedirects(response, f"/messages/{conversation.id}/")
        self.assertEqual(conversation.participants.count(), 2)
        self.assertTrue(conversation.participants.filter(pk=self.mira.pk).exists())
        self.assertTrue(conversation.participants.filter(pk=self.lukas.pk).exists())
        self.assertEqual(conversation.messages.get().body, "Hey Lukas!")

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message",
                "conversation_id": str(conversation.id),
                "body": "Noch eine Nachricht",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        self.assertEqual(conversation.messages.count(), 2)
        self.assertTrue(conversation.messages.filter(body="Noch eine Nachricht", sender=self.mira).exists())

        response = self.client.post(
            "/messages/",
            {
                "form_name": "start_conversation",
                "recipient": str(self.lukas.id),
                "body": "Nochmal ueber Neue Unterhaltung",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(conversation.messages.count(), 3)

    def test_non_member_cannot_open_conversation(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        ChatMessage.objects.create(conversation=conversation, sender=self.mira, body="Privat")
        self.client.login(username="anna@example.com", password="secret-12345")

        response = self.client.get(f"/messages/{conversation.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Privat")
        self.assertContains(response, "Nachrichtenübersicht")

    def test_inbox_shows_unread_message_until_opened(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        ChatMessage.objects.create(conversation=conversation, sender=self.lukas, body="Neue Antwort")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/messages/?filter=unread")

        self.assertContains(response, "Neue Antwort")
        self.assertContains(response, "unread-badge")

        response = self.client.get(f"/messages/{conversation.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Neue Antwort")
        self.assertFalse(
            ConversationMember.objects.get(conversation=conversation, user=self.mira).unread_count()
        )

    def test_messages_root_shows_overview_without_auto_opening_chat(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        ChatMessage.objects.create(conversation=conversation, sender=self.lukas, body="Neue Antwort")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/messages/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nachrichtenübersicht")
        self.assertContains(response, "Neue Nachrichten")
        self.assertContains(response, "Lukas: Neue Antwort")
        self.assertNotContains(response, 'name="form_name" value="message"')
        self.assertTrue(
            ConversationMember.objects.get(conversation=conversation, user=self.mira).unread_count()
        )

    def test_message_search_matches_older_message_bodies(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        ChatMessage.objects.create(conversation=conversation, sender=self.lukas, body="Projekt Alpha")
        ChatMessage.objects.create(
            conversation=conversation, sender=self.lukas, body="Normale letzte Nachricht"
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/messages/?q=Alpha")

        self.assertContains(response, "Lukas")
        self.assertContains(response, "Normale letzte Nachricht")

    def test_inbox_items_use_bounded_queries_for_message_counts(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        for index in range(60):
            ChatMessage.objects.create(
                conversation=conversation,
                sender=self.lukas,
                body=f"Nachricht {index}",
            )
        conversations = list(Conversation.visible_for(self.mira))

        with self.assertNumQueries(3):
            items = _build_inbox_items(conversations, self.mira)

        self.assertEqual(items[0]["unread"], 60)
        self.assertIn("Nachricht 59", items[0]["preview"])

    def test_message_detail_uses_paginated_history_window(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        for index in range(60):
            ChatMessage.objects.create(
                conversation=conversation,
                sender=self.lukas,
                body=f"Nachricht {index}",
            )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get(f"/messages/{conversation.id}/")

        message_bodies = [item["message"].body for item in response.context["message_items"]]
        self.assertEqual(len(message_bodies), 50)
        self.assertEqual(message_bodies[0], "Nachricht 10")
        self.assertEqual(message_bodies[-1], "Nachricht 59")
        self.assertTrue(response.context["has_older_messages"])

        oldest_message_id = response.context["oldest_message_id"]
        response = self.client.get(f"/messages/{conversation.id}/?before={oldest_message_id}")
        older_message_bodies = [item["message"].body for item in response.context["message_items"]]

        self.assertEqual(older_message_bodies, [f"Nachricht {index}" for index in range(10)])
        self.assertFalse(response.context["has_older_messages"])

    def test_user_can_react_to_message(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        message = ChatMessage.objects.create(conversation=conversation, sender=self.mira, body="Gute Idee")
        self.client.login(username="lukas@example.com", password="secret-12345")

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message_action",
                "conversation_id": str(conversation.id),
                "message_id": str(message.id),
                "action": "reaction",
                "emoji": "👍",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        reaction = ChatMessageReaction.objects.get(message=message, user=self.lukas)
        self.assertEqual(reaction.emoji, "👍")

    def test_user_can_pin_and_unpin_message(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        message = ChatMessage.objects.create(conversation=conversation, sender=self.lukas, body="Wichtig")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message_action",
                "conversation_id": str(conversation.id),
                "message_id": str(message.id),
                "action": "pin",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        message.refresh_from_db()
        self.assertTrue(message.is_pinned)
        self.assertEqual(message.pinned_by, self.mira)

        response = self.client.get(f"/messages/{conversation.id}/")
        self.assertContains(response, "Angepinnt")

    def test_own_message_can_be_deleted_with_placeholder(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        message = ChatMessage.objects.create(conversation=conversation, sender=self.mira, body="Soll weg")
        ChatMessageReaction.objects.create(message=message, user=self.lukas, emoji="❤️")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message_action",
                "conversation_id": str(conversation.id),
                "message_id": str(message.id),
                "action": "delete",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        message.refresh_from_db()
        self.assertTrue(message.is_deleted)
        self.assertEqual(message.body, "")
        self.assertFalse(message.reactions.exists())

        response = self.client.get(f"/messages/{conversation.id}/")
        self.assertContains(response, "Diese Nachricht wurde gelöscht.")
        self.assertNotContains(response, "Soll weg")

    def test_other_user_cannot_delete_foreign_message(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        message = ChatMessage.objects.create(conversation=conversation, sender=self.mira, body="Bleibt da")
        self.client.login(username="lukas@example.com", password="secret-12345")

        self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message_action",
                "conversation_id": str(conversation.id),
                "message_id": str(message.id),
                "action": "delete",
            },
        )

        message.refresh_from_db()
        self.assertFalse(message.is_deleted)
        self.assertEqual(message.body, "Bleibt da")

    def test_user_can_mute_and_unmute_conversation(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "member_action",
                "conversation_id": str(conversation.id),
                "action": "mute_8h",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        membership = ConversationMember.objects.get(conversation=conversation, user=self.mira)
        self.assertIsNotNone(membership.muted_until)
        self.assertTrue(membership.muted_until > timezone.now())

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "member_action",
                "conversation_id": str(conversation.id),
                "action": "unmute",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        membership.refresh_from_db()
        self.assertIsNone(membership.muted_until)

    def test_blocked_conversation_disables_sending_until_unblocked(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "member_action",
                "conversation_id": str(conversation.id),
                "action": "block",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        membership = ConversationMember.objects.get(conversation=conversation, user=self.mira)
        self.assertTrue(membership.is_blocked)

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message",
                "conversation_id": str(conversation.id),
                "body": "Soll nicht gesendet werden",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        self.assertFalse(conversation.messages.filter(body="Soll nicht gesendet werden").exists())

        self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "member_action",
                "conversation_id": str(conversation.id),
                "action": "unblock",
            },
        )
        membership.refresh_from_db()
        self.assertFalse(membership.is_blocked)

    def test_being_blocked_prevents_sending_and_hides_compose_bar(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)

        self.client.login(username="lukas@example.com", password="secret-12345")
        self.client.post(
            f"/messages/{conversation.id}/",
            {"form_name": "member_action", "conversation_id": str(conversation.id), "action": "block"},
        )
        self.client.logout()

        self.client.login(username="mira@example.com", password="secret-12345")
        response = self.client.get(f"/messages/{conversation.id}/")
        self.assertContains(response, "Diese Nachricht kann derzeit nicht gesendet werden.")
        self.assertNotContains(response, 'class="compose-bar"')

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message",
                "conversation_id": str(conversation.id),
                "body": "Sollte nicht ankommen",
            },
        )
        self.assertRedirects(response, f"/messages/{conversation.id}/")
        self.assertFalse(conversation.messages.filter(body="Sollte nicht ankommen").exists())

        response = self.client.post(
            "/messages/",
            {"form_name": "start_conversation", "recipient": str(self.lukas.id), "body": "Auch nicht"},
        )
        self.assertRedirects(response, f"/messages/{conversation.id}/")
        self.assertFalse(conversation.messages.filter(body="Auch nicht").exists())

    def test_start_conversation_with_multiple_recipients_creates_group(self):
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/messages/",
            {
                "form_name": "start_conversation",
                "recipient": [str(self.lukas.id), str(self.anna.id)],
                "title": "Projektteam",
                "body": "Hallo zusammen!",
            },
        )

        conversation = Conversation.objects.get()
        self.assertRedirects(response, f"/messages/{conversation.id}/")
        self.assertTrue(conversation.is_group)
        self.assertEqual(conversation.title, "Projektteam")
        self.assertEqual(conversation.participants.count(), 3)
        self.assertEqual(conversation.messages.get().body, "Hallo zusammen!")

    def test_group_avatar_falls_back_to_gr_without_title(self):
        conversation = Conversation.objects.create(created_by=self.mira, is_group=True)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        ConversationMember.objects.create(conversation=conversation, user=self.anna)

        self.assertEqual(conversation.avatar_for(self.mira), "GR")

    def test_member_can_add_and_leave_group(self):
        conversation = Conversation.objects.create(created_by=self.mira, title="Team", is_group=True)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)

        self.client.login(username="mira@example.com", password="secret-12345")
        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "member_action",
                "conversation_id": str(conversation.id),
                "action": "add_member",
                "new_member": str(self.anna.id),
            },
        )
        self.assertRedirects(response, f"/messages/{conversation.id}/")
        self.assertTrue(ConversationMember.objects.filter(conversation=conversation, user=self.anna).exists())

        self.client.logout()
        self.client.login(username="lukas@example.com", password="secret-12345")
        response = self.client.post(
            f"/messages/{conversation.id}/",
            {"form_name": "member_action", "conversation_id": str(conversation.id), "action": "leave_group"},
        )
        self.assertRedirects(response, "/messages/")
        self.assertFalse(
            ConversationMember.objects.filter(conversation=conversation, user=self.lukas).exists()
        )

    def test_leave_group_action_is_ignored_for_direct_conversations(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)

        self.client.login(username="mira@example.com", password="secret-12345")
        response = self.client.post(
            f"/messages/{conversation.id}/",
            {"form_name": "member_action", "conversation_id": str(conversation.id), "action": "leave_group"},
        )
        self.assertRedirects(response, f"/messages/{conversation.id}/")
        self.assertTrue(ConversationMember.objects.filter(conversation=conversation, user=self.mira).exists())

    def test_sending_attachment_without_body_is_valid_and_downloadable(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)

        self.client.login(username="mira@example.com", password="secret-12345")
        with TemporaryDirectory() as private_root, override_settings(PRIVATE_MEDIA_ROOT=private_root):
            upload = SimpleUploadedFile("moon.png", PNG_1X1_BYTES, content_type="image/png")
            response = self.client.post(
                f"/messages/{conversation.id}/",
                {
                    "form_name": "message",
                    "conversation_id": str(conversation.id),
                    "body": "",
                    "attachment": upload,
                },
            )
            self.assertRedirects(response, f"/messages/{conversation.id}/")

            message = conversation.messages.get()
            attachment = message.attachment
            self.assertEqual(attachment.kind, "image")
            self.assertEqual(attachment.uploaded_by, self.mira)

            download_url = f"/messages/attachments/{attachment.file_id}/inline/"
            response = self.client.get(download_url)
            self.assertEqual(response.status_code, 200)
            response.close()

            self.client.logout()
            self.client.login(username="anna@example.com", password="secret-12345")
            response = self.client.get(download_url)
            self.assertEqual(response.status_code, 404)

    def test_message_without_body_or_attachment_is_invalid(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)

        self.client.login(username="mira@example.com", password="secret-12345")
        response = self.client.post(
            f"/messages/{conversation.id}/",
            {"form_name": "message", "conversation_id": str(conversation.id), "body": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(conversation.messages.exists())

    def test_deleting_message_removes_attachment(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)

        self.client.login(username="mira@example.com", password="secret-12345")
        with TemporaryDirectory() as private_root, override_settings(PRIVATE_MEDIA_ROOT=private_root):
            upload = SimpleUploadedFile("moon.png", PNG_1X1_BYTES, content_type="image/png")
            self.client.post(
                f"/messages/{conversation.id}/",
                {
                    "form_name": "message",
                    "conversation_id": str(conversation.id),
                    "body": "",
                    "attachment": upload,
                },
            )
            message = conversation.messages.get()

            self.client.post(
                f"/messages/{conversation.id}/",
                {
                    "form_name": "message_action",
                    "conversation_id": str(conversation.id),
                    "message_id": str(message.id),
                    "action": "delete",
                },
            )

            self.assertFalse(ChatMessageAttachment.objects.filter(message=message).exists())

    def test_replying_to_message_links_and_renders_quote(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        original = ChatMessage.objects.create(
            conversation=conversation, sender=self.lukas, body="Erste Nachricht"
        )

        self.client.login(username="mira@example.com", password="secret-12345")
        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message",
                "conversation_id": str(conversation.id),
                "body": "Antwort darauf",
                "reply_to_id": str(original.id),
            },
        )
        self.assertRedirects(response, f"/messages/{conversation.id}/")

        reply = conversation.messages.get(body="Antwort darauf")
        self.assertEqual(reply.reply_to_id, original.id)

        response = self.client.get(f"/messages/{conversation.id}/")
        self.assertContains(response, "Erste Nachricht")
        self.assertContains(response, f'href="#message-{original.id}"')

    def test_replying_to_deleted_message_shows_placeholder(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        original = ChatMessage.objects.create(
            conversation=conversation,
            sender=self.lukas,
            body="",
            is_deleted=True,
        )
        ChatMessage.objects.create(
            conversation=conversation,
            sender=self.mira,
            body="Antwort auf geloeschte Nachricht",
            reply_to=original,
        )

        self.client.login(username="mira@example.com", password="secret-12345")
        response = self.client.get(f"/messages/{conversation.id}/")
        self.assertContains(response, "Diese Nachricht wurde gelöscht.")

    def test_typing_ping_sets_typing_until_and_appears_in_live_updates(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)

        self.client.login(username="mira@example.com", password="secret-12345")
        response = self.client.post(f"/messages/{conversation.id}/typing/")
        self.assertEqual(response.status_code, 200)

        member = ConversationMember.objects.get(conversation=conversation, user=self.mira)
        self.assertIsNotNone(member.typing_until)
        self.assertGreater(member.typing_until, timezone.now())

        self.client.logout()
        self.client.login(username="lukas@example.com", password="secret-12345")
        response = self.client.get(f"/messages/{conversation.id}/live/")
        self.assertEqual(response.json()["typing_label"], "Mira schreibt …")

        member.typing_until = timezone.now() - timedelta(seconds=1)
        member.save(update_fields=["typing_until"])
        response = self.client.get(f"/messages/{conversation.id}/live/")
        self.assertEqual(response.json()["typing_label"], "")


class MessageReadReceiptTests(TestCase):
    def test_outgoing_message_shows_read_receipt_after_recipient_opened_chat(self):
        sender = User.objects.create_user(
            username="sender@example.com", email="sender@example.com", password="secret-12345"
        )
        recipient = User.objects.create_user(
            username="recipient@example.com", email="recipient@example.com", password="secret-12345"
        )
        Profile.objects.create(user=sender, display_name="Sender")
        Profile.objects.create(user=recipient, display_name="Recipient")

        conversation = Conversation.objects.create(created_by=sender)
        ConversationMember.objects.create(conversation=conversation, user=sender, last_read_at=timezone.now())
        ConversationMember.objects.create(conversation=conversation, user=recipient)

        ChatMessage.objects.create(conversation=conversation, sender=sender, body="Hallo")

        self.client.login(username="sender@example.com", password="secret-12345")
        response = self.client.get(f"/messages/{conversation.id}/")
        self.assertContains(response, "Gesendet")
        self.client.logout()

        self.client.login(username="recipient@example.com", password="secret-12345")
        self.client.get(f"/messages/{conversation.id}/")
        self.client.logout()

        self.client.login(username="sender@example.com", password="secret-12345")
        response = self.client.get(f"/messages/{conversation.id}/")
        self.assertContains(response, "Gelesen")


class MessageLiveUpdateTests(TestCase):
    def test_live_updates_return_new_messages_and_mark_chat_as_read(self):
        sender = User.objects.create_user(
            username="sender-live@example.com", email="sender-live@example.com", password="secret-12345"
        )
        recipient = User.objects.create_user(
            username="recipient-live@example.com", email="recipient-live@example.com", password="secret-12345"
        )
        Profile.objects.create(user=sender, display_name="Sender Live")
        Profile.objects.create(user=recipient, display_name="Recipient Live")

        conversation = Conversation.objects.create(created_by=sender)
        ConversationMember.objects.create(conversation=conversation, user=sender, last_read_at=timezone.now())
        recipient_member = ConversationMember.objects.create(conversation=conversation, user=recipient)
        ChatMessage.objects.create(conversation=conversation, sender=sender, body="Neue Live-Nachricht")

        self.client.login(username="recipient-live@example.com", password="secret-12345")
        response = self.client.get(f"/messages/{conversation.id}/live/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertIn("Neue Live-Nachricht", response.json()["message_stream_html"])

        recipient_member.refresh_from_db()
        self.assertIsNotNone(recipient_member.last_read_at)

    def test_live_updates_short_circuit_when_fingerprint_is_unchanged(self):
        sender = User.objects.create_user(
            username="sender-fp@example.com", email="sender-fp@example.com", password="secret-12345"
        )
        recipient = User.objects.create_user(
            username="recipient-fp@example.com", email="recipient-fp@example.com", password="secret-12345"
        )
        third = User.objects.create_user(
            username="third-fp@example.com", email="third-fp@example.com", password="secret-12345"
        )
        conversation = Conversation.objects.create(created_by=sender, is_group=True)
        ConversationMember.objects.create(conversation=conversation, user=sender)
        ConversationMember.objects.create(conversation=conversation, user=recipient)
        message = ChatMessage.objects.create(conversation=conversation, sender=sender, body="Erste Nachricht")
        url = f"/messages/{conversation.id}/live/"
        self.client.login(username="recipient-fp@example.com", password="secret-12345")
        # Freeze time so the fingerprint's minute bucket can't roll over between two polls.
        frozen_now = patch("django.utils.timezone.now", return_value=timezone.now())
        frozen_now.start()
        self.addCleanup(frozen_now.stop)

        def poll_until_stable():
            fingerprint = self.client.get(url, {"_": "1"}).json()["fingerprint"]
            payload = self.client.get(url, {"fp": fingerprint, "_": "2"}).json()
            self.assertEqual(payload, {"ok": True, "unchanged": True, "fingerprint": fingerprint})
            return fingerprint

        changes = [
            lambda: ChatMessage.objects.create(
                conversation=conversation, sender=sender, body="Zweite Nachricht"
            ),
            lambda: ChatMessageReaction.objects.create(message=message, user=sender, emoji="❤️"),
            lambda: ChatMessage.objects.filter(pk=message.pk).update(
                is_pinned=True, pinned_at=timezone.now()
            ),
            lambda: ConversationMember.objects.create(conversation=conversation, user=third),
        ]
        for change in changes:
            fingerprint = poll_until_stable()
            change()
            payload = self.client.get(url, {"fp": fingerprint}).json()
            self.assertNotIn("unchanged", payload)
            self.assertIn("message_stream_html", payload)
            self.assertNotEqual(payload["fingerprint"], fingerprint)

        # Other query parameters (e.g. a search) are part of the fingerprint.
        fingerprint = poll_until_stable()
        self.assertNotIn("unchanged", self.client.get(url, {"fp": fingerprint, "q": "Erste"}).json())

    def test_mark_read_only_writes_when_a_newer_message_exists(self):
        sender = User.objects.create_user(
            username="sender-read@example.com", email="sender-read@example.com", password="secret-12345"
        )
        recipient = User.objects.create_user(
            username="recipient-read@example.com", email="recipient-read@example.com", password="secret-12345"
        )
        conversation = Conversation.objects.create(created_by=sender)
        ConversationMember.objects.create(conversation=conversation, user=sender)
        recipient_member = ConversationMember.objects.create(conversation=conversation, user=recipient)
        ChatMessage.objects.create(conversation=conversation, sender=sender, body="Hallo")

        conversation.mark_read_for(recipient)
        recipient_member.refresh_from_db()
        first_read_at = recipient_member.last_read_at
        self.assertIsNotNone(first_read_at)

        conversation.mark_read_for(recipient)
        recipient_member.refresh_from_db()
        self.assertEqual(recipient_member.last_read_at, first_read_at)

        newer = ChatMessage.objects.create(conversation=conversation, sender=sender, body="Noch was")
        conversation.mark_read_for(recipient)
        recipient_member.refresh_from_db()
        self.assertGreaterEqual(recipient_member.last_read_at, newer.created_at)

    def test_live_updates_report_compose_blocked_state(self):
        sender = User.objects.create_user(
            username="sender-block@example.com", email="sender-block@example.com", password="secret-12345"
        )
        recipient = User.objects.create_user(
            username="recipient-block@example.com",
            email="recipient-block@example.com",
            password="secret-12345",
        )
        Profile.objects.create(user=sender, display_name="Sender Block")
        Profile.objects.create(user=recipient, display_name="Recipient Block")

        conversation = Conversation.objects.create(created_by=sender)
        ConversationMember.objects.create(conversation=conversation, user=sender)
        ConversationMember.objects.create(conversation=conversation, user=recipient)

        self.client.login(username="sender-block@example.com", password="secret-12345")
        response = self.client.get(f"/messages/{conversation.id}/live/")
        payload = response.json()
        self.assertFalse(payload["compose_blocked"])
        self.assertIn('class="compose-bar"', payload["compose_html"])

        self.client.login(username="recipient-block@example.com", password="secret-12345")
        self.client.post(
            f"/messages/{conversation.id}/",
            {"form_name": "member_action", "conversation_id": str(conversation.id), "action": "block"},
        )

        self.client.login(username="sender-block@example.com", password="secret-12345")
        response = self.client.get(f"/messages/{conversation.id}/live/")
        payload = response.json()
        self.assertTrue(payload["compose_blocked"])
        self.assertIn("Diese Nachricht kann derzeit nicht gesendet werden.", payload["compose_html"])

    def test_overview_live_updates_return_contact_list(self):
        user = User.objects.create_user(
            username="overview-live@example.com", email="overview-live@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Overview Live")

        self.client.login(username="overview-live@example.com", password="secret-12345")
        response = self.client.get("/messages/live/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertIn("contact_list_html", response.json())
        self.assertIn("overview_html", response.json())
