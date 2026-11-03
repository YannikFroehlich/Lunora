from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from app.models import (
    ChatMessage,
    ChatMessageAttachment,
    ChatMessageReaction,
    ChatPoll,
    Conversation,
    ConversationMember,
    Profile,
    ScheduledChatMessage,
    UserNotification,
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

    def test_chat_search_lists_matching_messages_of_this_conversation_only(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        ChatMessage.objects.create(conversation=conversation, sender=self.lukas, body="Treffen am Bahnhof")
        ChatMessage.objects.create(conversation=conversation, sender=self.mira, body="Bahnhof oder Rathaus?")
        ChatMessage.objects.create(
            conversation=conversation, sender=self.mira, body="Bahnhof", is_deleted=True
        )
        ChatMessage.objects.create(conversation=conversation, sender=self.lukas, body="Kein Treffer")
        other = Conversation.objects.create(created_by=self.anna)
        ConversationMember.objects.create(conversation=other, user=self.anna)
        ConversationMember.objects.create(conversation=other, user=self.mira)
        ChatMessage.objects.create(conversation=other, sender=self.anna, body="Bahnhof bei Anna")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get(f"/messages/{conversation.id}/", {"find": "bahnhof -rathaus"})

        [result] = response.context["chat_search_results"]
        self.assertEqual(result["sender_name"], "Lukas")
        self.assertIn({"text": "Bahnhof", "match": True}, result["snippet"])
        self.assertIn("around=", result["url"])
        self.assertContains(response, "1 Treffer für")

    def test_chat_search_hit_opens_the_stream_around_the_message(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        messages = [
            ChatMessage.objects.create(
                conversation=conversation, sender=self.lukas, body=f"Nachricht {index}"
            )
            for index in range(120)
        ]
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get(f"/messages/{conversation.id}/", {"around": messages[20].id})

        bodies = [item["message"].body for item in response.context["message_items"]]
        self.assertEqual(bodies[-1], "Nachricht 25")
        self.assertIn("Nachricht 20", bodies)
        self.assertTrue(response.context["showing_history"])
        self.assertContains(response, "Zu den neuesten Nachrichten")

        live = self.client.get(f"/messages/{conversation.id}/live/", {"around": messages[20].id}).json()
        self.assertIn(f'id="message-{messages[20].id}"', live["message_stream_html"])

        # A hit among the newest messages keeps the normal newest page.
        response = self.client.get(f"/messages/{conversation.id}/", {"around": messages[118].id})
        self.assertFalse(response.context["showing_history"])
        self.assertEqual(response.context["message_items"][-1]["message"].body, "Nachricht 119")

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

    def _send_voice(self, conversation, name, data, content_type, duration="7"):
        upload = SimpleUploadedFile(name, data, content_type=content_type)
        return self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message",
                "conversation_id": str(conversation.id),
                "body": "",
                "voice_duration": duration,
                "attachment": upload,
            },
        )

    def _direct_conversation(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        return conversation

    def test_voice_message_is_stored_as_audio_and_played_inline(self):
        conversation = self._direct_conversation()
        webm = b"\x1a\x45\xdf\xa3" + b"\x00" * 60
        self.client.login(username="mira@example.com", password="secret-12345")
        with TemporaryDirectory() as private_root, override_settings(PRIVATE_MEDIA_ROOT=private_root):
            response = self._send_voice(conversation, "Sprachnachricht.webm", webm, "video/webm")
            self.assertRedirects(response, f"/messages/{conversation.id}/")

            attachment = conversation.messages.get().attachment
            self.assertEqual(attachment.kind, "audio")
            self.assertEqual(attachment.content_type, "audio/webm")
            self.assertEqual(attachment.duration_seconds, 7)

            page = self.client.get(f"/messages/{conversation.id}/")
            self.assertContains(page, "<audio controls")
            self.assertContains(page, "0:07")

            url = f"/messages/attachments/{attachment.file_id}/inline/"
            partial = self.client.get(url, HTTP_RANGE="bytes=0-3")
            self.assertEqual(partial.status_code, 206)
            self.assertEqual(partial["Content-Range"], f"bytes 0-3/{len(webm)}")
            self.assertEqual(partial.content, webm[:4])

            suffix = self.client.get(url, HTTP_RANGE="bytes=-2")
            self.assertEqual(suffix.status_code, 206)
            self.assertEqual(suffix.content, webm[-2:])

            unsatisfiable = self.client.get(url, HTTP_RANGE=f"bytes={len(webm) + 5}-")
            self.assertEqual(unsatisfiable.status_code, 416)

            full = self.client.get(url)
            self.assertEqual(full.status_code, 200)
            self.assertEqual(full["Accept-Ranges"], "bytes")
            full.close()

    def test_voice_message_with_wrong_signature_is_rejected(self):
        conversation = self._direct_conversation()
        self.client.login(username="mira@example.com", password="secret-12345")
        with TemporaryDirectory() as private_root, override_settings(PRIVATE_MEDIA_ROOT=private_root):
            response = self._send_voice(conversation, "Sprachnachricht.ogg", b"not audio", "audio/ogg")
            self.assertEqual(response.status_code, 200)
            self.assertFalse(conversation.messages.exists())

            response = self._send_voice(conversation, "fake.m4a", b"\x00\x00\x00\x18ftypM4A ", "text/html")
            self.assertEqual(response.status_code, 200)
            self.assertFalse(conversation.messages.exists())

    def test_voice_message_duration_is_capped(self):
        conversation = self._direct_conversation()
        self.client.login(username="mira@example.com", password="secret-12345")
        with TemporaryDirectory() as private_root, override_settings(PRIVATE_MEDIA_ROOT=private_root):
            response = self._send_voice(conversation, "a.ogg", b"OggS" + b"\x00" * 20, "audio/ogg", "9999")
            self.assertEqual(response.status_code, 200)
            self.assertFalse(conversation.messages.exists())

            response = self._send_voice(conversation, "a.mp3", b"ID3" + b"\x00" * 20, "audio/mpeg")
            self.assertRedirects(response, f"/messages/{conversation.id}/")
            self.assertEqual(conversation.messages.get().attachment.content_type, "audio/mpeg")

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


class MessageForwardTests(TestCase):
    def setUp(self):
        self.mira, self.lukas, self.anna, self.ben = (
            User.objects.create_user(
                username=f"{name}@example.com", email=f"{name}@example.com", password="secret-12345"
            )
            for name in ("mira", "lukas", "anna", "ben")
        )
        for user, name in (
            (self.mira, "Mira"),
            (self.lukas, "Lukas"),
            (self.anna, "Anna"),
            (self.ben, "Ben"),
        ):
            Profile.objects.create(user=user, display_name=name)
        self.source = self.direct(self.mira, self.lukas)
        self.target = self.direct(self.mira, self.anna)
        self.client.login(username="mira@example.com", password="secret-12345")

    def direct(self, *users):
        conversation = Conversation.objects.create(created_by=users[0])
        for user in users:
            ConversationMember.objects.create(conversation=conversation, user=user)
        return conversation

    def forward(self, message, target, source=None):
        source = source or self.source
        return self.client.post(
            f"/messages/{source.id}/",
            {
                "form_name": "message_forward",
                "conversation_id": source.id,
                "message_id": message.id,
                "target_conversation_id": target.id,
            },
        )

    def test_forwarding_sends_a_copy_marked_as_forwarded(self):
        original = self.source.messages.create(sender=self.lukas, body="Treffen um 18 Uhr")

        response = self.forward(original, self.target)

        self.assertRedirects(response, f"/messages/{self.target.id}/")
        forwarded = self.target.messages.get()
        self.assertEqual(forwarded.body, "Treffen um 18 Uhr")
        self.assertEqual(forwarded.sender, self.mira)
        self.assertTrue(forwarded.is_forwarded)
        self.assertIsNone(forwarded.reply_to)

        # Anna sees the label but not who wrote the original.
        self.client.logout()
        self.client.login(username="anna@example.com", password="secret-12345")
        page = self.client.get(f"/messages/{self.target.id}/")
        self.assertContains(page, "Weitergeleitet")
        self.assertNotContains(page, "Lukas")

    def test_forwarded_attachment_is_an_independent_copy(self):
        with TemporaryDirectory() as private_root, override_settings(PRIVATE_MEDIA_ROOT=private_root):
            original = self.source.messages.create(sender=self.lukas, body="")
            ChatMessageAttachment.objects.create(
                message=original,
                uploaded_by=self.lukas,
                kind="audio",
                original_name="Sprachnachricht.ogg",
                content_type="audio/ogg",
                size=24,
                duration_seconds=4,
                file=SimpleUploadedFile("Sprachnachricht.ogg", b"OggS" + b"\x00" * 20),
            )

            self.forward(original, self.target)

            copy = self.target.messages.get().attachment
            self.assertNotEqual(copy.file_id, original.attachment.file_id)
            self.assertEqual((copy.kind, copy.duration_seconds, copy.content_type), ("audio", 4, "audio/ogg"))
            original.attachment.file.delete(save=False)
            original.attachment.delete()
            response = self.client.get(f"/messages/attachments/{copy.file_id}/download/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b"".join(response.streaming_content), b"OggS" + b"\x00" * 20)
            response.close()

    def test_only_reachable_conversations_and_messages_can_be_forwarded(self):
        original = self.source.messages.create(sender=self.lukas, body="Geheim")
        foreign = self.direct(self.anna, self.ben)

        self.forward(original, foreign)
        self.assertFalse(foreign.messages.exists())

        self.forward(original, self.source)
        self.assertEqual(self.source.messages.count(), 1)

        original.is_deleted = True
        original.save()
        self.forward(original, self.target)
        self.assertFalse(self.target.messages.exists())

        foreign_message = foreign.messages.create(sender=self.anna, body="Nicht für Mira")
        response = self.forward(foreign_message, self.target, source=foreign)
        self.assertRedirects(response, "/messages/")
        self.assertFalse(self.target.messages.exists())

    def test_blocked_conversations_are_neither_offered_nor_accepted(self):
        original = self.source.messages.create(sender=self.lukas, body="Hallo")
        ConversationMember.objects.filter(conversation=self.target, user=self.anna).update(is_blocked=True)

        page = self.client.get(f"/messages/{self.source.id}/")
        self.assertEqual(page.context["forward_targets"], [])
        self.assertNotContains(page, 'data-menu-action="forward"')

        self.forward(original, self.target)
        self.assertFalse(self.target.messages.exists())

    def test_forward_targets_list_the_other_conversations(self):
        group = Conversation.objects.create(created_by=self.mira, is_group=True, title="Team")
        for user in (self.mira, self.lukas, self.ben):
            ConversationMember.objects.create(conversation=group, user=user)

        page = self.client.get(f"/messages/{self.source.id}/")

        titles = [target["title"] for target in page.context["forward_targets"]]
        self.assertCountEqual(titles, ["Anna", "Team"])
        self.assertContains(page, 'data-menu-action="forward"')


class ChatToolsTestMixin:
    def setUp(self):
        self.mira, self.lukas, self.anna = (
            User.objects.create_user(
                username=f"{name}@example.com", email=f"{name}@example.com", password="secret-12345"
            )
            for name in ("mira", "lukas", "anna")
        )
        for user, name in ((self.mira, "Mira"), (self.lukas, "Lukas"), (self.anna, "Anna Berg")):
            Profile.objects.create(user=user, display_name=name, timezone_name="Europe/Berlin")
        self.group = Conversation.objects.create(created_by=self.mira, title="Team", is_group=True)
        for user in (self.mira, self.lukas, self.anna):
            ConversationMember.objects.create(conversation=self.group, user=user)
        self.client.login(username="mira@example.com", password="secret-12345")

    def send(self, body, conversation=None, follow=False, **extra):
        conversation = conversation or self.group
        return self.client.post(
            f"/messages/{conversation.id}/",
            {"form_name": "message", "conversation_id": conversation.id, "body": body, **extra},
            follow=follow,
        )

    def direct(self, *users):
        conversation = Conversation.objects.create(created_by=users[0])
        for user in users:
            ConversationMember.objects.create(conversation=conversation, user=user)
        return conversation


class ChatMentionTests(ChatToolsTestMixin, TestCase):
    def test_mention_stores_user_and_creates_inbox_notification(self):
        self.send("@Lukas hast du morgen Zeit?")

        message = ChatMessage.objects.get()
        self.assertEqual(list(message.mentioned_users.all()), [self.lukas])
        notification = UserNotification.objects.get(kind=UserNotification.KIND_CHAT_MENTION)
        self.assertEqual(notification.recipient, self.lukas)
        self.assertEqual(notification.actor, self.mira)
        self.assertEqual(notification.title, "Mira hat dich in „Team“ erwähnt")
        self.assertEqual(notification.url, f"/messages/{self.group.id}/?around={message.id}#hit-{message.id}")

    def test_longest_display_name_wins_and_word_boundaries_count(self):
        Profile.objects.filter(user=self.lukas).update(display_name="Anna")

        self.send("Frage an @Anna Berg, nicht an mail@Anna oder @Annabell")

        self.assertEqual(list(ChatMessage.objects.get().mentioned_users.all()), [self.anna])

    def test_mention_all_notifies_every_other_member(self):
        self.send("@alle Treffen um 10")

        recipients = set(
            UserNotification.objects.filter(kind=UserNotification.KIND_CHAT_MENTION).values_list(
                "recipient_id", flat=True
            )
        )
        self.assertEqual(recipients, {self.lukas.id, self.anna.id})

    def test_muted_member_still_gets_mention_but_blocked_member_does_not(self):
        ConversationMember.objects.filter(conversation=self.group, user=self.lukas).update(
            muted_until=timezone.now() + timedelta(days=7)
        )
        ConversationMember.objects.filter(conversation=self.group, user=self.anna).update(is_blocked=True)

        self.send("@Lukas und @Anna Berg, bitte schauen")

        self.assertEqual(
            list(UserNotification.objects.values_list("recipient_id", flat=True)),
            [self.lukas.id],
        )

    def test_direct_chats_have_no_mentions(self):
        direct = self.direct(self.mira, self.lukas)

        self.send("@Lukas hallo", conversation=direct)

        self.assertFalse(ChatMessage.objects.get().mentioned_users.exists())
        self.assertFalse(UserNotification.objects.exists())

    def test_rendered_mentions_are_highlighted_and_escaped(self):
        self.send("<b>@Lukas</b> schau mal")
        self.client.login(username="lukas@example.com", password="secret-12345")

        response = self.client.get(f"/messages/{self.group.id}/")

        self.assertContains(response, '&lt;b&gt;<span class="chat-mention is-self">@Lukas</span>&lt;/b&gt;')
        self.assertContains(response, "is-mentioned")
        self.assertNotContains(response, "<b>@Lukas</b>")

    def test_contact_list_marks_unread_mention_until_conversation_is_opened(self):
        self.send("@Lukas kurze Frage")
        self.client.login(username="lukas@example.com", password="secret-12345")

        overview = self.client.get("/messages/")
        self.assertContains(overview, "unread-badge has-mention")
        self.assertContains(overview, "Du wurdest erwähnt")

        self.client.get(f"/messages/{self.group.id}/")

        self.assertIsNotNone(UserNotification.objects.get(recipient=self.lukas).read_at)
        self.assertNotContains(self.client.get("/messages/"), "unread-badge has-mention")

    def test_deleting_a_message_removes_its_mention_notifications(self):
        self.send("@alle bitte ignorieren")
        message = ChatMessage.objects.get()

        self.client.post(
            f"/messages/{self.group.id}/",
            {
                "form_name": "message_action",
                "conversation_id": self.group.id,
                "message_id": message.id,
                "action": "delete",
            },
        )

        self.assertFalse(UserNotification.objects.exists())
        self.assertFalse(message.mentioned_users.exists())

    def test_mention_is_mailed_through_inbox_email_delivery(self):
        from django.core import mail

        from app.services.notifications import send_pending_user_notification_emails

        Profile.objects.filter(user=self.lukas).update(notify_email=True)
        self.send("@Lukas Mail-Test")

        result = send_pending_user_notification_emails()

        self.assertEqual(result["sent"], 1)
        self.assertEqual(mail.outbox[0].to, ["lukas@example.com"])
        self.assertIn("Mail-Test", mail.outbox[0].body)

    def test_messages_category_preference_can_turn_off_mention_emails(self):
        from django.core import mail

        from app.models import NotificationPreference
        from app.services.notifications import send_pending_user_notification_emails

        Profile.objects.filter(user=self.lukas).update(notify_email=True)
        NotificationPreference.objects.create(
            user=self.lukas, category=NotificationPreference.CATEGORY_MESSAGES, email_enabled=False
        )
        self.send("@Lukas ohne Mail")

        send_pending_user_notification_emails()

        self.assertEqual(mail.outbox, [])

    def test_group_page_offers_mention_candidates(self):
        response = self.client.get(f"/messages/{self.group.id}/")

        self.assertEqual(
            [candidate["name"] for candidate in response.context["mention_candidates"]],
            ["Anna Berg", "Lukas", "alle"],
        )
        self.assertContains(response, 'id="chat-mention-candidates"')


class ChatPollTests(ChatToolsTestMixin, TestCase):
    def create_poll(
        self, question="Pizza oder Pasta?", options=("Pizza", "Pasta"), conversation=None, **extra
    ):
        conversation = conversation or self.group
        return self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "poll_create",
                "conversation_id": conversation.id,
                "question": question,
                "option": list(options),
                **extra,
            },
        )

    def vote(self, poll, option):
        return self.client.post(
            f"/messages/{self.group.id}/",
            {
                "form_name": "poll_vote",
                "conversation_id": self.group.id,
                "poll_id": poll.id,
                "option_id": option.id,
            },
        )

    def test_creating_a_poll_posts_a_message_with_options(self):
        response = self.create_poll(options=["Pizza", " pizza ", "Pasta", ""])

        self.assertRedirects(response, f"/messages/{self.group.id}/")
        poll = ChatPoll.objects.get()
        self.assertEqual(poll.message.body, "Pizza oder Pasta?")
        self.assertEqual(poll.message.sender, self.mira)
        self.assertEqual(list(poll.options.values_list("text", flat=True)), ["Pizza", "Pasta"])
        self.assertFalse(poll.allows_multiple)

    def test_poll_needs_two_distinct_options(self):
        self.create_poll(options=["Pizza", "pizza"], follow=True)

        self.assertFalse(ChatPoll.objects.exists())
        self.assertFalse(ChatMessage.objects.exists())

    def test_poll_validation_error_is_shown(self):
        response = self.client.post(
            f"/messages/{self.group.id}/",
            {
                "form_name": "poll_create",
                "conversation_id": self.group.id,
                "question": "Ja?",
                "option": ["Ja"],
            },
            follow=True,
        )

        self.assertContains(response, "Gib mindestens zwei verschiedene Antworten an.")

    def test_polls_only_exist_in_group_chats(self):
        direct = self.direct(self.mira, self.lukas)

        self.create_poll(conversation=direct)

        self.assertFalse(ChatPoll.objects.exists())

    def test_single_choice_vote_moves_and_retracts(self):
        self.create_poll()
        poll = ChatPoll.objects.get()
        pizza, pasta = poll.options.all()

        self.vote(poll, pizza)
        self.vote(poll, pasta)
        self.assertEqual(list(poll.votes.values_list("option_id", flat=True)), [pasta.id])

        self.vote(poll, pasta)
        self.assertFalse(poll.votes.exists())

    def test_multiple_choice_poll_keeps_several_votes(self):
        self.create_poll(allows_multiple="on")
        poll = ChatPoll.objects.get()
        pizza, pasta = poll.options.all()

        self.vote(poll, pizza)
        self.vote(poll, pasta)

        self.assertEqual(poll.votes.count(), 2)

    def test_only_the_creator_closes_and_closed_polls_take_no_votes(self):
        self.create_poll()
        poll = ChatPoll.objects.get()
        close = {"form_name": "poll_close", "conversation_id": self.group.id, "poll_id": poll.id}

        self.client.login(username="lukas@example.com", password="secret-12345")
        self.client.post(f"/messages/{self.group.id}/", close)
        poll.refresh_from_db()
        self.assertFalse(poll.is_closed)

        self.client.login(username="mira@example.com", password="secret-12345")
        self.client.post(f"/messages/{self.group.id}/", close)
        poll.refresh_from_db()
        self.assertTrue(poll.is_closed)

        response = self.vote(poll, poll.options.first())
        self.assertFalse(poll.votes.exists())
        self.assertRedirects(response, f"/messages/{self.group.id}/")

    def test_non_members_cannot_vote(self):
        self.create_poll()
        poll = ChatPoll.objects.get()
        outsider = User.objects.create_user(
            username="ben@example.com", email="ben@example.com", password="x-12345"
        )
        self.client.force_login(outsider)

        self.vote(poll, poll.options.first())

        self.assertFalse(poll.votes.exists())

    def test_poll_renders_results_and_preview(self):
        self.create_poll()
        poll = ChatPoll.objects.get()
        self.vote(poll, poll.options.first())

        response = self.client.get(f"/messages/{self.group.id}/")

        self.assertContains(response, 'class="chat-poll"')
        self.assertContains(response, 'aria-pressed="true"')
        self.assertContains(response, "--poll-share: 100%")
        self.assertContains(response, "1 Person abgestimmt")
        self.assertContains(response, "Umfrage beenden")
        self.assertContains(response, "📊 Umfrage: Pizza oder Pasta?")
        self.assertContains(response, 'data-can-forward="false"')

    def test_polls_cannot_be_forwarded(self):
        self.create_poll()
        poll = ChatPoll.objects.get()
        target = self.direct(self.mira, self.anna)

        self.client.post(
            f"/messages/{self.group.id}/",
            {
                "form_name": "message_forward",
                "conversation_id": self.group.id,
                "message_id": poll.message_id,
                "target_conversation_id": target.id,
            },
        )

        self.assertFalse(target.messages.exists())

    def test_deleting_the_message_removes_the_poll(self):
        self.create_poll()
        poll = ChatPoll.objects.get()

        self.client.post(
            f"/messages/{self.group.id}/",
            {
                "form_name": "message_action",
                "conversation_id": self.group.id,
                "message_id": poll.message_id,
                "action": "delete",
            },
        )

        self.assertFalse(ChatPoll.objects.exists())

    def test_vote_changes_the_live_update_fingerprint(self):
        self.create_poll()
        poll = ChatPoll.objects.get()
        url = f"/messages/{self.group.id}/live/"
        first = self.client.get(url).json()["fingerprint"]

        self.vote(poll, poll.options.first())

        self.assertNotEqual(self.client.get(url, {"fp": first}).json().get("fingerprint"), first)


class ScheduledChatMessageTests(ChatToolsTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        berlin = ZoneInfo("Europe/Berlin")
        # Two days ahead, in the sender's own timezone, as the datetime-local field sends it.
        self.at = (timezone.now() + timedelta(days=2)).astimezone(berlin).replace(second=0, microsecond=0)
        self.at_value = self.at.strftime("%Y-%m-%dT%H:%M")
        self.later_value = (self.at + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")

    def test_send_at_schedules_instead_of_sending(self):
        response = self.send("Gute Reise!", send_at=self.at_value, follow=True)

        scheduled = ScheduledChatMessage.objects.get()
        self.assertEqual(scheduled.body, "Gute Reise!")
        self.assertEqual(scheduled.sender, self.mira)
        # Read in the sender's timezone (Europe/Berlin), not UTC.
        self.assertEqual(scheduled.send_at, self.at)
        self.assertFalse(ChatMessage.objects.exists())
        self.assertContains(response, "Nachricht für")
        self.assertContains(response, "Geplant (1)")

    def test_past_times_and_attachments_are_rejected(self):
        past = (timezone.localtime() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
        response = self.send("Zu spät", send_at=past)
        self.assertContains(response, "mindestens eine Minute in der Zukunft")

        upload = SimpleUploadedFile("bild.png", PNG_1X1_BYTES, content_type="image/png")
        response = self.send("Mit Bild", send_at=self.at_value, attachment=upload)
        self.assertContains(response, "Nur Textnachrichten können geplant werden")

        self.assertFalse(ScheduledChatMessage.objects.exists())
        self.assertFalse(ChatMessage.objects.exists())

    def test_due_messages_are_delivered_once_with_mentions(self):
        from app.services.chat import send_due_scheduled_messages

        self.send("@Lukas Erinnerung", send_at=self.at_value)
        later = self.at + timedelta(minutes=1)

        self.assertEqual(send_due_scheduled_messages(now=later), {"sent": 1, "skipped": 0})
        self.assertEqual(send_due_scheduled_messages(now=later), {"sent": 0, "skipped": 0})

        message = ChatMessage.objects.get()
        self.assertEqual(message.body, "@Lukas Erinnerung")
        self.assertEqual(list(message.mentioned_users.all()), [self.lukas])
        self.assertFalse(ScheduledChatMessage.objects.exists())

    def test_messages_not_yet_due_stay_scheduled(self):
        from app.services.chat import send_due_scheduled_messages

        self.send("Später", send_at=self.at_value)

        self.assertEqual(send_due_scheduled_messages()["sent"], 0)
        self.assertTrue(ScheduledChatMessage.objects.exists())

    def test_sender_who_cannot_write_anymore_loses_the_message(self):
        from app.services.chat import send_due_scheduled_messages

        direct = self.direct(self.mira, self.lukas)
        ScheduledChatMessage.objects.create(
            conversation=direct, sender=self.mira, body="Hallo", send_at=timezone.now() - timedelta(minutes=1)
        )
        ConversationMember.objects.filter(conversation=direct, user=self.lukas).update(is_blocked=True)

        self.assertEqual(send_due_scheduled_messages(), {"sent": 0, "skipped": 1})
        self.assertFalse(ChatMessage.objects.exists())
        self.assertFalse(ScheduledChatMessage.objects.exists())

    def test_leaving_a_group_drops_own_scheduled_messages(self):
        self.send("Später", send_at=self.at_value)

        self.client.post(
            f"/messages/{self.group.id}/",
            {"form_name": "member_action", "conversation_id": self.group.id, "action": "leave_group"},
        )

        self.assertFalse(ScheduledChatMessage.objects.exists())

    def test_opening_the_chat_delivers_its_due_messages(self):
        ScheduledChatMessage.objects.create(
            conversation=self.group,
            sender=self.lukas,
            body="Fällig",
            send_at=timezone.now() - timedelta(minutes=1),
        )

        response = self.client.get(f"/messages/{self.group.id}/")

        self.assertContains(response, "Fällig")
        self.assertEqual(ChatMessage.objects.get().sender, self.lukas)

    def test_send_now_and_cancel_only_touch_own_messages(self):
        self.send("Eins", send_at=self.at_value)
        self.send("Zwei", send_at=self.later_value)
        first, second = ScheduledChatMessage.objects.all()
        action = {"form_name": "scheduled_action", "conversation_id": self.group.id}

        self.client.login(username="lukas@example.com", password="secret-12345")
        self.client.post(
            f"/messages/{self.group.id}/", {**action, "scheduled_id": first.id, "action": "cancel"}
        )
        self.assertEqual(ScheduledChatMessage.objects.count(), 2)

        self.client.login(username="mira@example.com", password="secret-12345")
        self.client.post(
            f"/messages/{self.group.id}/", {**action, "scheduled_id": first.id, "action": "send_now"}
        )
        self.client.post(
            f"/messages/{self.group.id}/", {**action, "scheduled_id": second.id, "action": "cancel"}
        )

        self.assertEqual(list(ChatMessage.objects.values_list("body", flat=True)), ["Eins"])
        self.assertFalse(ScheduledChatMessage.objects.exists())

    def test_scheduled_list_is_private_to_the_sender(self):
        self.send("Geheim geplant", send_at=self.at_value)
        self.client.login(username="lukas@example.com", password="secret-12345")

        response = self.client.get(f"/messages/{self.group.id}/")

        self.assertNotContains(response, "Geheim geplant")

    def test_scheduled_tasks_deliver_due_messages(self):
        from app.services.scheduled_tasks import run_scheduled_tasks

        ScheduledChatMessage.objects.create(
            conversation=self.group, sender=self.mira, body="Automatisch", send_at=timezone.now()
        )

        result = run_scheduled_tasks()

        self.assertEqual(result["scheduled_messages"]["sent"], 1)
        self.assertTrue(ChatMessage.objects.filter(body="Automatisch").exists())
