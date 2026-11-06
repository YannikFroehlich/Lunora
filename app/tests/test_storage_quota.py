from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from app.models import (
    AuditLogEntry,
    ChatMessage,
    ChatMessageAttachment,
    Conversation,
    ConversationMember,
    NoteAttachment,
    SystemSettings,
)
from app.services.docx_import import import_docx_note
from app.services.notes import create_attachment, create_note, duplicate_note
from app.services.storage_quota import ensure_storage_available, storage_summary, user_storage_used
from app.tests.helpers import PNG_1X1_BYTES

MB = 1024 * 1024


def png_upload():
    return SimpleUploadedFile("moon.png", PNG_1X1_BYTES, content_type="image/png")


def set_limit(megabytes):
    SystemSettings.objects.update_or_create(pk=1, defaults={"user_storage_limit_mb": megabytes})


class StorageQuotaTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("mira", "mira@example.com", "pw")
        self.other = User.objects.create_user("lukas", "lukas@example.com", "pw")
        self.note = create_note(self.user, title="Mond")

    def fill_storage(self, megabytes, user=None):
        """A row of that size, without writing that much to disk."""
        return NoteAttachment.objects.create(
            note=self.note,
            uploaded_by=user or self.user,
            kind=NoteAttachment.KIND_FILE,
            original_name="big.bin",
            content_type="application/octet-stream",
            size=megabytes * MB,
            file=SimpleUploadedFile("big.bin", b"x"),
        )


class StorageQuotaServiceTests(StorageQuotaTestCase):
    def test_counts_note_and_chat_files_of_the_uploader_only(self):
        self.fill_storage(3)
        self.fill_storage(5, user=self.other)
        conversation = Conversation.objects.create(created_by=self.user)
        message = ChatMessage.objects.create(conversation=conversation, sender=self.user, body="")
        ChatMessageAttachment.objects.create(
            message=message,
            uploaded_by=self.user,
            kind=ChatMessageAttachment.KIND_FILE,
            original_name="a.bin",
            content_type="application/octet-stream",
            size=2 * MB,
            file=SimpleUploadedFile("a.bin", b"x"),
        )
        self.assertEqual(user_storage_used(self.user), 5 * MB)

    def test_exactly_full_is_allowed_one_byte_more_is_not(self):
        set_limit(10)
        self.fill_storage(4)
        ensure_storage_available(self.user, 6 * MB)
        with self.assertRaises(ValidationError):
            ensure_storage_available(self.user, 6 * MB + 1)

    def test_zero_means_no_limit(self):
        set_limit(0)
        self.fill_storage(5000)
        ensure_storage_available(self.user, 5000 * MB)
        self.assertEqual(storage_summary(self.user), {"used": 5000 * MB, "limit": None, "percent": 0})

    def test_nothing_new_passes_even_when_over_the_limit(self):
        set_limit(1)
        self.fill_storage(5)
        ensure_storage_available(self.user, 0)
        ensure_storage_available(self.user, None)
        self.assertEqual(storage_summary(self.user)["percent"], 100)


class StorageQuotaUploadPathTests(StorageQuotaTestCase):
    def setUp(self):
        super().setUp()
        set_limit(1)

    def test_note_attachment_is_rejected_when_full(self):
        self.fill_storage(1)
        with self.assertRaises(ValidationError):
            create_attachment(self.user, self.note.id, png_upload(), kind="image")
        self.assertEqual(self.note.attachments.count(), 1)

    def test_note_attachment_is_stored_with_room_left(self):
        attachment = create_attachment(self.user, self.note.id, png_upload(), kind="image")
        self.assertEqual(attachment.size, len(PNG_1X1_BYTES))

    def test_upload_api_answers_400_with_the_message(self):
        self.fill_storage(1)
        self.client.force_login(self.user)
        response = self.client.post(
            f"/notes/api/{self.note.id}/attachments/", {"file": png_upload(), "kind": "image"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Speicher", response.json()["error"])

    def test_duplicating_a_note_counts_the_copied_files(self):
        attachment = create_attachment(self.user, self.note.id, png_upload(), kind="image")
        document = {
            "type": "doc",
            "content": [
                {
                    "type": "image",
                    "attrs": {"attachmentId": str(attachment.file_id), "alt": "", "width": 720},
                }
            ],
        }
        self.fill_storage(1)
        with self.assertRaises(ValidationError):
            duplicate_note(self.user, self.note.id, document=document)
        self.assertEqual(self.user.owned_notes.count(), 1)

    def test_word_import_counts_its_images(self):
        self.fill_storage(1)
        images = [("00000000-0000-0000-0000-000000000001", "a.png", "image/png", b"x" * 10)]
        with self.assertRaises(ValidationError):
            self._import_with_images(images)

    def test_word_import_without_images_works_when_full(self):
        self.fill_storage(1)
        note, _skipped = self._import_with_images([])
        self.assertEqual(note.title, "Titel")

    def _import_with_images(self, images):
        document = {"type": "doc", "content": [{"type": "paragraph"}]}
        with patch("app.services.docx_import.docx_to_note", return_value=("Titel", document, images, 0)):
            return import_docx_note(self.user, "a.docx", b"")


class StorageQuotaChatTests(StorageQuotaTestCase):
    def setUp(self):
        super().setUp()
        set_limit(1)
        self.conversation = Conversation.objects.create(created_by=self.user)
        ConversationMember.objects.create(conversation=self.conversation, user=self.user)
        ConversationMember.objects.create(conversation=self.conversation, user=self.other)
        self.client.force_login(self.user)

    def test_chat_attachment_is_rejected_when_full_and_no_message_is_created(self):
        self.fill_storage(1)
        response = self.client.post(
            f"/messages/{self.conversation.id}/",
            {
                "form_name": "message",
                "conversation_id": self.conversation.id,
                "body": "",
                "attachment": png_upload(),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dein Speicher reicht")
        self.assertFalse(self.conversation.messages.exists())

    def test_text_message_still_goes_out_when_full(self):
        self.fill_storage(1)
        self.client.post(
            f"/messages/{self.conversation.id}/",
            {"form_name": "message", "conversation_id": self.conversation.id, "body": "Hallo"},
        )
        self.assertEqual(self.conversation.messages.get().body, "Hallo")

    def test_forwarding_an_attachment_is_rejected_when_full(self):
        target = Conversation.objects.create(created_by=self.user)
        ConversationMember.objects.create(conversation=target, user=self.user)
        ConversationMember.objects.create(conversation=target, user=self.other)
        message = ChatMessage.objects.create(conversation=self.conversation, sender=self.other, body="")
        ChatMessageAttachment.objects.create(
            message=message,
            uploaded_by=self.other,
            kind=ChatMessageAttachment.KIND_IMAGE,
            original_name="moon.png",
            content_type="image/png",
            size=len(PNG_1X1_BYTES),
            file=png_upload(),
        )
        self.fill_storage(1)
        response = self.client.post(
            f"/messages/{self.conversation.id}/",
            {
                "form_name": "message_forward",
                "conversation_id": self.conversation.id,
                "message_id": message.id,
                "target_conversation_id": target.id,
            },
            follow=True,
        )
        self.assertContains(response, "Dein Speicher reicht")
        self.assertFalse(target.messages.exists())


class StorageLimitAdministrationTests(StorageQuotaTestCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser("admin", "admin@example.com", "pw")

    def post_limit(self, value):
        return self.client.post(
            "/administration/", {"form_name": "storage_limit", "user_storage_limit_mb": value}
        )

    def test_superuser_changes_the_limit_and_it_is_logged(self):
        self.client.force_login(self.admin)
        response = self.post_limit(500)
        self.assertRedirects(response, "/administration/")
        settings_obj = SystemSettings.objects.get(pk=1)
        self.assertEqual(settings_obj.user_storage_limit_mb, 500)
        self.assertEqual(settings_obj.updated_by, self.admin)
        entry = AuditLogEntry.objects.get(action=AuditLogEntry.ACTION_SETTINGS_CHANGED)
        self.assertIn("500 MB", entry.summary)

    def test_negative_limit_is_rejected(self):
        self.client.force_login(self.admin)
        set_limit(700)
        response = self.post_limit(-1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SystemSettings.objects.get(pk=1).user_storage_limit_mb, 700)

    def test_normal_user_cannot_change_the_limit(self):
        set_limit(700)
        self.client.force_login(self.user)
        self.assertEqual(self.post_limit(1).status_code, 403)
        self.assertEqual(SystemSettings.objects.get(pk=1).user_storage_limit_mb, 700)

    def test_saving_the_switches_keeps_the_limit(self):
        set_limit(700)
        self.client.force_login(self.admin)
        self.client.post("/administration/", {"form_name": "system_settings", "normal_login_enabled": "on"})
        self.assertEqual(SystemSettings.objects.get(pk=1).user_storage_limit_mb, 700)

    def test_settings_page_shows_the_usage(self):
        set_limit(10)
        self.fill_storage(5)
        self.client.force_login(self.user)
        response = self.client.get("/settings/")
        self.assertEqual(response.context["storage"]["percent"], 50)
        self.assertContains(response, 'id="settings-panel-storage"')
