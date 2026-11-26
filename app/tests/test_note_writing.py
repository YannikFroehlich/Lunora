"""Hint boxes, collapsible sections, voice notes, template placeholders, meeting notes from events,
flashcards from notes and articles from the web."""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase
from django.utils import timezone

from app.models import (
    CalendarEvent,
    CalendarEventAttendee,
    Note,
    NoteShare,
    NoteTemplate,
    VocabularyList,
)
from app.services.article_import import parse_article
from app.services.note_content import document_plain_text, validate_note_document, validate_note_steps
from app.services.note_markdown import render_note_markdown
from app.services.notes import create_note, fill_placeholders
from app.services.user_preferences import localtime_for_user
from app.services.vocabulary import cards_from_note, flashcards_from_text


def text(value):
    return {"type": "text", "text": value}


def paragraph(value):
    return {"type": "paragraph", "content": [text(value)]}


BOXES = {
    "type": "doc",
    "content": [
        {"type": "callout", "attrs": {"tone": "warning"}, "content": [paragraph("Achtung, heiß")]},
        {
            "type": "details",
            "attrs": {"summary": "Lösung", "open": False},
            "content": [paragraph("Zweiundvierzig")],
        },
    ],
}


class BlockValidationTests(SimpleTestCase):
    def test_callout_and_details_pass_and_count_as_text(self):
        validate_note_document(BOXES)

        self.assertEqual(document_plain_text(BOXES), "Achtung, heiß\nLösung\nZweiundvierzig")

    def test_bad_attributes_are_refused(self):
        for attrs, node_type in (
            ({"tone": "danger"}, "callout"),
            ({"summary": 5}, "details"),
            ({"summary": "x" * 201}, "details"),
            ({"open": "ja"}, "details"),
            ({"color": "red"}, "callout"),
        ):
            document = {
                "type": "doc",
                "content": [{"type": node_type, "attrs": attrs, "content": [paragraph("x")]}],
            }
            with self.subTest(attrs=attrs), self.assertRaises(ValidationError):
                validate_note_document(document)

    def test_live_editing_steps_may_change_them(self):
        validate_note_steps([{"stepType": "attr", "pos": 3, "attr": "tone", "value": "tip"}])
        validate_note_steps([{"stepType": "attr", "pos": 3, "attr": "open", "value": False}])
        with self.assertRaises(ValidationError):
            validate_note_steps([{"stepType": "attr", "pos": 3, "attr": "tone", "value": "rot"}])


class NoteWritingBase(TestCase):
    def setUp(self):
        self.lea = User.objects.create_user(
            username="lea@example.com", email="lea@example.com", password="pw"
        )
        self.tom = User.objects.create_user(
            username="tom@example.com", email="tom@example.com", password="pw"
        )
        self.client.force_login(self.lea)

    def note(self, document, title="Notiz"):
        note = Note.objects.create(owner=self.lea, title=title, document=document)
        note.plain_text = document_plain_text(document)
        note.save(update_fields=["plain_text"])
        return note


class BlockExportTests(NoteWritingBase):
    def test_every_export_shows_the_boxes(self):
        note = self.note(BOXES)

        markdown = render_note_markdown(note)
        self.assertIn("> [!WARNING]\n> Achtung, heiß", markdown)
        self.assertIn("<details>\n<summary>Lösung</summary>\n\nZweiundvierzig\n\n</details>", markdown)
        for export in ("pdf", "docx"):
            response = self.client.get(f"/notes/{note.pk}/export/{export}/")
            self.assertEqual(response.status_code, 200, export)
            response.close()

        response = self.client.get(f"/notes/{note.pk}/present/")
        self.assertContains(
            response, '<aside class="note-callout is-warning" aria-label="Warnung">', html=False
        )
        self.assertContains(response, '<details class="note-details"><summary>Lösung</summary>', html=False)


class VoiceNoteTests(NoteWritingBase):
    WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 60

    def test_audio_uploads_play_inline_with_ranges(self):
        note = self.note({"type": "doc", "content": [{"type": "paragraph"}]})
        upload = SimpleUploadedFile("Sprachnotiz 21.11.26, 14.30.webm", self.WEBM, content_type="video/webm")

        response = self.client.post(f"/notes/api/{note.pk}/attachments/", {"file": upload, "kind": "file"})

        self.assertEqual(response.status_code, 201, response.content)
        file_id = response.json()["attachment"]["id"]
        partial = self.client.get(f"/notes/attachments/{file_id}/inline/", HTTP_RANGE="bytes=0-3")
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial["Content-Type"], "audio/webm")
        self.assertEqual(partial.content, self.WEBM[:4])

    def test_fake_audio_is_refused(self):
        note = self.note({"type": "doc", "content": [{"type": "paragraph"}]})
        upload = SimpleUploadedFile("trick.mp3", b"<html>", content_type="audio/mpeg")

        response = self.client.post(f"/notes/api/{note.pk}/attachments/", {"file": upload, "kind": "file"})

        self.assertEqual(response.status_code, 400)


class PlaceholderTests(NoteWritingBase):
    def test_own_templates_fill_date_week_and_title(self):
        now = localtime_for_user(timezone.now(), self.lea)
        template = NoteTemplate.objects.create(
            owner=self.lea,
            name="Bericht KW {{kw}}",
            document={"type": "doc", "content": [paragraph("{{titel}} vom {{ Datum }}, {{unbekannt}}")]},
        )

        response = self.client.post(
            "/notes/api/create/",
            data=json.dumps({"custom_template_id": template.pk}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        note = Note.objects.get(pk=response.json()["note"]["id"])
        title = f"Bericht KW {now.isocalendar().week}"
        self.assertEqual(note.title, title)
        self.assertIn(f"{title} vom {now:%d.%m.%Y}, {{{{unbekannt}}}}", note.plain_text)

    def test_built_in_templates_and_imports_keep_working(self):
        self.assertEqual(create_note(self.lea).title, "Unbenannte Notiz")
        self.assertEqual(fill_placeholders("{{jahr}}", {"jahr": "2026"}), "2026")


class MeetingNoteTests(NoteWritingBase):
    def setUp(self):
        super().setUp()
        self.ben = User.objects.create_user(
            username="ben@example.com", email="ben@example.com", password="pw"
        )
        start = timezone.now() + timedelta(days=1)
        self.event = CalendarEvent.objects.create(
            user=self.lea,
            title="Planung",
            description="- Budget\n- Termine",
            location="Raum 3",
            start_at=start,
            end_at=start + timedelta(hours=1),
        )
        CalendarEventAttendee.objects.create(event=self.event, user=self.tom, status="accepted")
        CalendarEventAttendee.objects.create(event=self.event, user=self.ben, status="declined")

    def test_note_is_made_once_and_shared_with_who_takes_part(self):
        response = self.client.post(f"/calendar/events/{self.event.pk}/", {"form_name": "event_meeting_note"})

        self.event.refresh_from_db()
        note = self.event.note
        self.assertRedirects(response, f"/notes/{note.pk}/", fetch_redirect_response=False)
        self.assertEqual(note.owner, self.lea)
        self.assertEqual(
            set(note.shares.values_list("user__username", "role")), {("tom@example.com", "editor")}
        )
        self.assertIn("Raum 3", note.plain_text)
        self.assertIn("Budget\nTermine", note.plain_text)
        self.assertEqual(note.checklist_total, 1)

        tom = Client()
        tom.force_login(self.tom)
        self.assertContains(tom.get(f"/calendar/events/{self.event.pk}/"), f"/notes/{note.pk}/")
        tom.post(f"/calendar/events/{self.event.pk}/", {"form_name": "event_meeting_note"})
        self.assertEqual(Note.objects.count(), 1)
        self.assertContains(tom.get(f"/notes/{note.pk}/"), f"/calendar/events/{self.event.pk}/")

    def test_someone_without_access_to_the_note_gets_told(self):
        self.client.post(f"/calendar/events/{self.event.pk}/", {"form_name": "event_meeting_note"})
        NoteShare.objects.filter(user=self.tom).delete()
        tom = Client()
        tom.force_login(self.tom)

        response = tom.post(
            f"/calendar/events/{self.event.pk}/", {"form_name": "event_meeting_note"}, follow=True
        )

        self.assertContains(response, "schon eine Notiz von")
        self.assertEqual(Note.objects.count(), 1)


class FlashcardTests(NoteWritingBase):
    def test_lines_with_two_colons_become_cards_once(self):
        note = self.note(
            {
                "type": "doc",
                "content": [
                    paragraph("Hund :: dog"),
                    paragraph("Keine Karte"),
                    {
                        "type": "bulletList",
                        "content": [{"type": "listItem", "content": [paragraph("Katze::cat")]}],
                    },
                ],
            },
            title="Englisch",
        )

        response = self.client.post(
            f"/notes/api/{note.pk}/actions/",
            data=json.dumps({"action": "flashcards"}),
            content_type="application/json",
        )

        self.assertEqual(response.json()["added"], 2)
        cards = VocabularyList.objects.get(owner=self.lea, title="Englisch").cards
        self.assertEqual(list(cards.values_list("term", "translation")), [("Hund", "dog"), ("Katze", "cat")])
        note.plain_text += "\nMaus :: mouse"
        self.assertEqual(cards_from_note(self.lea, note)[1], 1)
        self.assertEqual(flashcards_from_text("a :: \n :: b\nc ::d"), [("c", "d")])

    def test_a_note_without_cards_explains_the_format(self):
        note = self.note({"type": "doc", "content": [paragraph("nichts")]})

        response = self.client.post(
            f"/notes/api/{note.pk}/actions/",
            data=json.dumps({"action": "flashcards"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Frage :: Antwort", response.json()["error"])


ARTICLE = """<html><head><title>Seite</title><meta property="og:title" content="Wie Bienen tanzen"></head>
<body><nav><a href="/">Start</a></nav><aside><p>Werbung</p></aside>
<article><h1>Wie Bienen tanzen</h1><p>Bienen  zeigen
sich Futterquellen.</p><h2>Der Schwänzeltanz</h2><ul><li>Richtung</li><li>Entfernung</li></ul>
<script>track()</script><blockquote>Erstaunlich.</blockquote></article>
<article><p>Kurz</p></article></body></html>"""


class ArticleImportTests(NoteWritingBase):
    def test_the_main_article_becomes_the_document(self):
        title, document = parse_article(ARTICLE, "https://example.com/bienen")

        self.assertEqual(title, "Wie Bienen tanzen")
        validate_note_document(document)
        types = [node["type"] for node in document["content"]]
        self.assertEqual(
            types, ["paragraph", "heading", "bulletList", "blockquote", "horizontalRule", "paragraph"]
        )
        plain = document_plain_text(document)
        self.assertIn("Bienen zeigen sich Futterquellen.", plain)
        self.assertNotIn("Werbung", plain)
        self.assertNotIn("track", plain)
        self.assertNotIn("Kurz", plain)
        source = document["content"][-1]["content"][1]
        self.assertEqual(source["marks"][0]["attrs"]["href"], "https://example.com/bienen")

    def test_import_creates_the_note(self):
        with patch("app.services.article_import.safe_fetch.fetch", return_value=ARTICLE):
            response = self.client.post("/notes/import/article/", {"url": "https://example.com/bienen"})

        note = Note.objects.get(owner=self.lea)
        self.assertRedirects(response, f"/notes/{note.pk}/", fetch_redirect_response=False)
        self.assertEqual(note.title, "Wie Bienen tanzen")

    def test_internal_addresses_are_refused(self):
        response = self.client.post("/notes/import/article/", {"url": "http://127.0.0.1/"}, follow=True)

        self.assertContains(response, "öffentlichen Server")
        self.assertFalse(Note.objects.exists())
