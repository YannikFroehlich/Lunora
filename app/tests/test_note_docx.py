import io
import uuid
import zipfile
from xml.etree import ElementTree

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.test import Client, TestCase
from PIL import Image

from app.models import Note, NoteAttachment, NoteShare, Profile, SystemSettings
from app.services.docx_import import docx_to_note
from app.services.note_content import document_plain_text, validate_note_document
from app.services.note_docx import render_note_docx
from app.services.note_html import render_note_html
from app.services.note_markdown import render_note_markdown
from app.services.note_pdf import NotePdfRenderer
from app.services.notes import create_note

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
PACKAGE_RELS = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"


def text(value, *marks):
    node = {"type": "text", "text": value}
    if marks:
        node["marks"] = list(marks)
    return node


def paragraph(*content, **attrs):
    node = {"type": "paragraph", "content": list(content)}
    if attrs:
        node["attrs"] = attrs
    return node


def item(*content):
    return {"type": "listItem", "content": list(content)}


def cell(value, *, kind="tableCell", **attrs):
    return {"type": kind, "attrs": attrs, "content": [paragraph(text(value))]}


def row(*cells):
    return {"type": "tableRow", "content": list(cells)}


def table(*rows):
    return {"type": "table", "content": list(rows)}


def image_bytes(image_format):
    output = io.BytesIO()
    Image.new("RGB", (40, 20), (200, 30, 30)).save(output, image_format)
    return output.getvalue()


class NoteDocxTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner@example.com", email="owner@example.com", password="secret-12345"
        )
        Profile.objects.create(user=self.owner, display_name="Owner", timezone_name="Europe/Berlin")
        self.note = create_note(self.owner, title="Bericht")

    def export(self, *content):
        self.note.document = {"type": "doc", "content": list(content)}
        self.note.save(update_fields=["document"])
        return render_note_docx(Note.objects.get(pk=self.note.pk)).getvalue()

    def parts(self, data):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return {name: archive.read(name) for name in archive.namelist()}

    def body(self, data):
        return ElementTree.fromstring(self.parts(data)["word/document.xml"]).find(f"{W}body")

    def attach(self, data, *, name="bild.png"):
        attachment = NoteAttachment(
            note=self.note,
            uploaded_by=self.owner,
            file_id=uuid.uuid4(),
            kind=NoteAttachment.KIND_IMAGE,
            original_name=name,
            content_type="image/png",
            size=len(data),
        )
        attachment.file.save(name, ContentFile(data), save=False)
        attachment.save()
        return str(attachment.file_id)


class NoteDocxRenderTests(NoteDocxTestCase):
    def test_note_survives_a_round_trip_through_the_word_import(self):
        link = {"type": "link", "attrs": {"href": "https://example.com/?a=1&b=2"}}
        color = {"type": "textStyle", "attrs": {"color": "#ff0000"}}
        data = self.export(
            {"type": "heading", "attrs": {"level": 1}, "content": [text("Kapitel")]},
            paragraph(text("fett", {"type": "bold"}, color), text(" Link", link), textAlign="center"),
            {
                "type": "bulletList",
                "content": [
                    item(
                        paragraph(text("A")),
                        {"type": "bulletList", "content": [item(paragraph(text("A1")))]},
                    ),
                    item(paragraph(text("B"))),
                ],
            },
            {
                "type": "orderedList",
                "attrs": {"start": 3, "type": "a"},
                "content": [item(paragraph(text("c")))],
            },
            {"type": "blockquote", "content": [paragraph(text("Zitat"))]},
            {"type": "codeBlock", "content": [text("a<b\nc")]},
        )

        title, document, images, skipped = docx_to_note(data, "datei")

        validate_note_document(document)
        self.assertEqual((title, images, skipped), ("Bericht", [], 0))
        content = document["content"]
        self.assertEqual(
            [node["type"] for node in content],
            ["heading", "paragraph", "bulletList", "orderedList", "blockquote", "paragraph"],
        )
        self.assertEqual(content[1]["attrs"]["textAlign"], "center")
        first_marks = {mark["type"]: mark.get("attrs") for mark in content[1]["content"][0]["marks"]}
        self.assertIn("bold", first_marks)
        self.assertEqual(first_marks["textStyle"]["color"], "#ff0000")
        link_marks = {mark["type"]: mark.get("attrs") for mark in content[1]["content"][1]["marks"]}
        self.assertEqual(link_marks["link"]["href"], "https://example.com/?a=1&b=2")
        self.assertEqual(content[2]["content"][0]["content"][1]["type"], "bulletList")
        self.assertEqual(content[3]["attrs"], {"start": 3, "type": "a"})
        self.assertEqual(content[5]["content"][0]["text"], "a<b")
        self.assertEqual(content[5]["content"][1]["type"], "hardBreak")

    def test_form_fields_survive_every_export(self):
        def field(placeholder="", value=""):
            return {"type": "formField", "attrs": {"placeholder": placeholder, "value": value}}

        data = self.export(
            paragraph(text("Name: "), field("Name"), text(" Klasse: "), field("Klasse", "10b"))
        )

        _title, document, _images, _skipped = docx_to_note(data, "datei")
        fields = [node for node in document["content"][0]["content"] if node["type"] == "formField"]
        # Word keeps no hint once a value is typed, so the filled field comes back without one.
        self.assertEqual(fields, [field("Name"), field(value="10b")])
        note = Note.objects.get(pk=self.note.pk)
        self.assertEqual(document_plain_text(note.document), "Name:  Klasse: 10b")
        self.assertIn("Name: \\_\\_\\_\\_\\_\\_\\_\\_\\_\\_ Klasse: 10b", render_note_markdown(note))
        self.assertIn(
            '<span class="form-field">__________</span>', render_note_html(note, attachment_url=str)
        )
        self.assertIn('<span class="form-field">10b</span>', render_note_html(note, attachment_url=str))
        self.assertEqual(
            NotePdfRenderer(note).inline_markup(note.document["content"][0]["content"]),
            "Name: __________ Klasse: 10b",
        )

    def test_form_field_attributes_are_validated(self):
        for attrs in (
            {"placeholder": "", "value": 3},
            {"placeholder": "x" * 201, "value": ""},
            {"placeholder": "", "value": "x" * 2001},
            {"placeholder": "", "value": "", "onclick": "x"},
        ):
            with self.subTest(attrs=attrs), self.assertRaises(ValidationError):
                validate_note_document(
                    {"type": "doc", "content": [paragraph({"type": "formField", "attrs": attrs})]}
                )
        with self.assertRaises(ValidationError):
            validate_note_document(
                {
                    "type": "doc",
                    "content": [paragraph({"type": "formField", "marks": [{"type": "bold"}]})],
                }
            )

    def test_every_part_is_well_formed_and_declared(self):
        parts = self.parts(self.export(paragraph(text("Inhalt"))))

        for name, part in parts.items():
            with self.subTest(part=name):
                ElementTree.fromstring(part)
        self.assertEqual(
            set(parts),
            {
                "[Content_Types].xml",
                "_rels/.rels",
                "docProps/core.xml",
                "word/document.xml",
                "word/styles.xml",
                "word/numbering.xml",
                "word/_rels/document.xml.rels",
            },
        )

    def test_run_properties_keep_the_order_word_requires(self):
        marks = [
            {"type": "underline"},
            {"type": "highlight", "attrs": {"color": "#00ff00"}},
            {"type": "italic"},
            {"type": "bold"},
            {"type": "superscript"},
            {"type": "strike"},
            {"type": "link", "attrs": {"href": "https://example.com/"}},
            {
                "type": "textStyle",
                "attrs": {"color": "#112233", "fontFamily": "Georgia", "fontSize": "24px"},
            },
        ]

        body = self.body(self.export(paragraph(text("x", *marks))))

        properties = next(body.iter(f"{W}rPr"))
        self.assertEqual(
            [child.tag.removeprefix(W) for child in properties],
            ["rStyle", "rFonts", "b", "i", "strike", "color", "sz", "szCs", "u", "shd", "vertAlign"],
        )

    def test_text_is_escaped_and_unwritable_characters_are_dropped(self):
        attack = '</w:t></w:r><w:r><w:t>"böse"&'
        data = self.export(paragraph(text(f"{attack}\x01￾")))

        self.assertEqual(self.body(data).findall(f"{W}p")[1].find(f"{W}r/{W}t").text, attack)

    def test_unsafe_values_in_the_document_are_not_written(self):
        link = {"type": "link", "attrs": {"href": "javascript:alert(1)"}}
        style = {"type": "textStyle", "attrs": {"color": 'red"/><w:evil/>', "fontFamily": 'Arial"/>'}}

        parts = self.parts(self.export(paragraph(text("x", link, style), backgroundColor='"><x/>')))

        self.assertNotIn(b"javascript", parts["word/_rels/document.xml.rels"])
        self.assertNotIn(b"evil", parts["word/document.xml"])
        self.assertNotIn(b"<x/>", parts["word/document.xml"])

    def test_rowspan_writes_continuation_cells_and_cells_end_with_a_paragraph(self):
        inner = table(row(cell("innen")))
        body = self.body(
            self.export(
                table(
                    row(cell("a", rowspan=2), cell("b", colspan=2, backgroundColor="#d9e2f3")),
                    row(cell("c"), {"type": "tableCell", "content": [paragraph(text("d")), inner]}),
                )
            )
        )

        tbl = body.find(f"{W}tbl")
        rows = tbl.findall(f"{W}tr")
        self.assertEqual(len(tbl.findall(f"{W}tblGrid/{W}gridCol")), 3)
        first_props = rows[0].findall(f"{W}tc")[0].find(f"{W}tcPr")
        self.assertEqual(first_props.find(f"{W}vMerge").get(f"{W}val"), "restart")
        continuation = rows[1].findall(f"{W}tc")[0]
        self.assertIsNone(continuation.find(f"{W}tcPr/{W}vMerge").get(f"{W}val"))
        self.assertEqual(rows[0].findall(f"{W}tc")[1].find(f"{W}tcPr/{W}gridSpan").get(f"{W}val"), "2")
        for tc in tbl.iter(f"{W}tc"):
            self.assertEqual(list(tc)[-1].tag, f"{W}p")

    def test_consecutive_tables_stay_separate(self):
        tags = [child.tag for child in self.body(self.export(table(row(cell("a"))), table(row(cell("b")))))]

        self.assertEqual(tags, [f"{W}p", f"{W}tbl", f"{W}p", f"{W}tbl", f"{W}sectPr"])

    def test_images_are_embedded_and_webp_becomes_png(self):
        png_id = self.attach(image_bytes("PNG"))
        webp_id = self.attach(image_bytes("WEBP"), name="bild.webp")

        parts = self.parts(
            self.export(
                {"type": "noteImage", "attrs": {"attachmentId": png_id, "alt": 'a"<b', "width": 200}},
                {"type": "noteImage", "attrs": {"attachmentId": webp_id, "width": 3000}},
                {"type": "noteImage", "attrs": {"attachmentId": png_id, "width": 200}},
            )
        )

        media = [name for name in parts if name.startswith("word/media/")]
        self.assertEqual(sorted(media), ["word/media/image1.png", "word/media/image2.png"])
        for name in media:
            Image.open(io.BytesIO(parts[name])).verify()
        rels = ElementTree.fromstring(parts["word/_rels/document.xml.rels"]).findall(PACKAGE_RELS)
        rel_ids = [rel.get("Id") for rel in rels]
        self.assertEqual(len(rel_ids), len(set(rel_ids)))
        self.assertIn(b"a&quot;&lt;b", parts["word/document.xml"])
        # 3000 px would not fit the page, so the picture is scaled to the text width.
        self.assertIn(b'cx="6120130"', parts["word/document.xml"])

    def test_missing_files_become_a_note_in_the_text(self):
        data = self.export(
            {"type": "noteImage", "attrs": {"attachmentId": str(uuid.uuid4()), "alt": "Diagramm"}},
            {"type": "noteAttachment", "attrs": {"attachmentId": str(uuid.uuid4()), "name": "plan.pdf"}},
        )

        rendered = "".join(self.body(data).itertext())

        self.assertIn("[Bild nicht verfügbar: Diagramm]", rendered)
        self.assertIn("Anhang: plan.pdf", rendered)


class NoteDocxExportViewTests(NoteDocxTestCase):
    def url(self):
        return f"/notes/{self.note.pk}/export/docx/"

    def test_export_downloads_a_word_file_for_those_with_access(self):
        self.client.login(username="owner@example.com", password="secret-12345")

        response = self.client.get(self.url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertIn("bericht.docx", response["Content-Disposition"])
        self.assertEqual(response["Cache-Control"], "private, no-store")
        data = b"".join(response.streaming_content)
        response.close()
        self.assertEqual(docx_to_note(data, "datei")[0], "Bericht")

    def test_export_respects_access_and_the_feature_flag(self):
        reader = User.objects.create_user(
            username="r@example.com", email="r@example.com", password="secret-12345"
        )
        stranger = User.objects.create_user(
            username="s@example.com", email="s@example.com", password="secret-12345"
        )
        NoteShare.objects.create(note=self.note, user=reader, role=NoteShare.ROLE_READER)

        reader_client = Client()
        reader_client.force_login(reader)
        response = reader_client.get(self.url())
        self.assertEqual(response.status_code, 200)
        response.close()

        stranger_client = Client()
        stranger_client.force_login(stranger)
        self.assertEqual(stranger_client.get(self.url()).status_code, 404)
        self.assertRedirects(Client().get(self.url()), f"/login/?next={self.url()}")

        SystemSettings.objects.update_or_create(pk=1, defaults={"notes_enabled": False})
        self.assertEqual(reader_client.get(self.url()).status_code, 503)
