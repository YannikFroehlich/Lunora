import io
import zipfile

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from app.models import Note, NoteAttachment, Profile
from app.services.docx_import import docx_to_note
from app.services.note_content import validate_note_document
from app.services.notes import save_note

NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"'
)

STYLES = f"""<w:styles {NS}>
<w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Standard"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Titel"><w:name w:val="Title"/><w:basedOn w:val="Standard"/></w:style>
<w:style w:type="paragraph" w:styleId="berschrift1"><w:name w:val="heading 1"/>
  <w:rPr><w:color w:val="2F5496"/><w:sz w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="berschrift2"><w:name w:val="heading 2"/></w:style>
<w:style w:type="paragraph" w:styleId="Zitat"><w:name w:val="Quote"/></w:style>
<w:style w:type="character" w:styleId="Fett"><w:name w:val="Strong"/><w:rPr><w:b/></w:rPr></w:style>
</w:styles>"""

NUMBERING = f"""<w:numbering {NS}>
<w:abstractNum w:abstractNumId="0">
  <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/></w:lvl>
  <w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="bullet"/></w:lvl>
</w:abstractNum>
<w:abstractNum w:abstractNumId="1">
  <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/></w:lvl>
  <w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/></w:lvl>
</w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>"""

RELS = """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.com/" TargetMode="External"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="javascript:alert(1)" TargetMode="External"/>
<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image2.gif"/>
</Relationships>"""


def _para(text, style=None, num=None, rpr="", jc=None):
    ppr = ""
    if style:
        ppr += f'<w:pStyle w:val="{style}"/>'
    if num:
        ppr += f'<w:numPr><w:ilvl w:val="{num[1]}"/><w:numId w:val="{num[0]}"/></w:numPr>'
    if jc:
        ppr += f'<w:jc w:val="{jc}"/>'
    return f'<w:p><w:pPr>{ppr}</w:pPr><w:r><w:rPr>{rpr}</w:rPr><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def _drawing(rel_id, cx=1905000):
    return (
        f'<w:r><w:drawing><wp:inline><wp:extent cx="{cx}" cy="952500"/>'
        '<wp:docPr id="1" name="Bild 1" descr="Ein Diagramm"/>'
        "<a:graphic><a:graphicData><pic:pic><pic:blipFill>"
        f'<a:blip r:embed="{rel_id}"/></pic:blipFill></pic:pic></a:graphicData></a:graphic>'
        "</wp:inline></w:drawing></w:r>"
    )


def _cell(text, tcpr=""):
    return f"<w:tc><w:tcPr>{tcpr}</w:tcPr>{_para(text)}</w:tc>"


BODY = "".join(
    [
        _para("Projektbericht", style="Titel"),
        _para("Einleitung", style="berschrift1"),
        '<w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
        '<w:r><w:t xml:space="preserve">Normal, </w:t></w:r>'
        "<w:r><w:rPr><w:b/><w:i/></w:rPr><w:t>fett-kursiv</w:t></w:r>"
        '<w:r><w:rPr><w:u w:val="single"/><w:strike/></w:rPr><w:t xml:space="preserve"> unter-durch</w:t></w:r>'
        '<w:r><w:rPr><w:rStyle w:val="Fett"/><w:color w:val="FF0000"/><w:sz w:val="28"/></w:rPr><w:t> rot</w:t></w:r>'
        '<w:r><w:rPr><w:highlight w:val="yellow"/><w:rFonts w:ascii="Courier New"/></w:rPr><w:t> code</w:t></w:r>'
        '<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>2</w:t></w:r>'
        "<w:r><w:br/><w:t>zweite Zeile</w:t></w:r>"
        "</w:p>",
        '<w:p><w:hyperlink r:id="rId2"><w:r><w:t>Webseite</w:t></w:r></w:hyperlink>'
        '<w:hyperlink r:id="rId3"><w:r><w:t xml:space="preserve"> böse</w:t></w:r></w:hyperlink></w:p>',
        _para("Punkt A", num=("1", "0")),
        _para("Unterpunkt", num=("1", "1")),
        _para("Punkt B", num=("1", "0")),
        _para("Schritt eins", num=("2", "0")),
        _para("Teil a", num=("2", "1")),
        _para("Zwischentext"),
        _para("Schritt zwei", num=("2", "0")),
        "<w:p>" + _drawing("rId1") + "</w:p>",
        "<w:p>" + _drawing("rId4", cx=95250) + "</w:p>",
        _para("Weise Worte", style="Zitat"),
        _para("Details", style="berschrift2"),
        '<w:tbl><w:tblGrid><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/></w:tblGrid>'
        "<w:tr><w:trPr><w:tblHeader/></w:trPr>" + _cell("Name") + _cell("Wert") + "</w:tr>"
        "<w:tr>" + _cell("Breit", '<w:gridSpan w:val="2"/><w:shd w:fill="D9E2F3"/>') + "</w:tr>"
        "<w:tr>" + _cell("Hoch", '<w:vMerge w:val="restart"/>') + _cell("1") + "</w:tr>"
        "<w:tr>" + _cell("", "<w:vMerge/>") + _cell("2") + "</w:tr>"
        "</w:tbl>",
    ]
)


def _png(width=40, height=20):
    output = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(output, "PNG")
    return output.getvalue()


def _gif():
    output = io.BytesIO()
    Image.new("P", (10, 10)).save(output, "GIF")
    return output.getvalue()


def build_docx(body=BODY, extra=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/document.xml", f"<w:document {NS}><w:body>{body}<w:sectPr/></w:body></w:document>"
        )
        archive.writestr("word/styles.xml", STYLES)
        archive.writestr("word/numbering.xml", NUMBERING)
        archive.writestr("word/_rels/document.xml.rels", RELS)
        archive.writestr("word/media/image1.png", _png())
        archive.writestr("word/media/image2.gif", _gif())
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return output.getvalue()


def _types(nodes):
    return [node["type"] for node in nodes]


def _marks(node):
    return {mark["type"]: mark.get("attrs") for mark in node.get("marks", [])}


class DocxConversionTests(TestCase):
    def test_typical_document_converts_to_a_valid_note(self):
        title, document, images, skipped = docx_to_note(build_docx(), "datei")

        validate_note_document(document)
        self.assertEqual(title, "Projektbericht")
        self.assertEqual(skipped, 0)
        self.assertEqual(len(images), 2)
        self.assertTrue(images[1][1].endswith(".png"), "GIF is converted to PNG")
        content = document["content"]
        self.assertEqual(
            _types(content),
            [
                "heading",
                "paragraph",
                "paragraph",
                "bulletList",
                "orderedList",
                "paragraph",
                "orderedList",
                "noteImage",
                "noteImage",
                "blockquote",
                "heading",
                "table",
            ],
        )
        self.assertEqual(content[0]["attrs"], {"level": 1})
        self.assertNotIn("marks", content[0]["content"][0], "heading style colours stay out")

        paragraph = content[1]
        self.assertEqual(paragraph["attrs"], {"textAlign": "center"})
        runs = paragraph["content"]
        self.assertEqual(runs[0], {"type": "text", "text": "Normal, "})
        self.assertEqual(set(_marks(runs[1])), {"bold", "italic"})
        self.assertEqual(set(_marks(runs[2])), {"underline", "strike"})
        self.assertEqual(_marks(runs[3])["textStyle"], {"color": "#ff0000", "fontSize": "18px"})
        self.assertIn("bold", _marks(runs[3]))
        self.assertEqual(_marks(runs[4])["highlight"], {"color": "#ffff00"})
        self.assertEqual(_marks(runs[4])["textStyle"], {"fontFamily": "Courier New"})
        self.assertIn("superscript", _marks(runs[5]))
        self.assertEqual(runs[6], {"type": "hardBreak"})

        links = content[2]["content"]
        self.assertEqual(_marks(links[0])["link"], {"href": "https://example.com/"})
        self.assertNotIn("marks", links[1], "unsafe link targets keep only their text")

        bullets = content[3]
        self.assertEqual(len(bullets["content"]), 2)
        self.assertEqual(_types(bullets["content"][0]["content"]), ["paragraph", "bulletList"])

        steps = content[4]
        self.assertNotIn("attrs", steps)
        self.assertEqual(steps["content"][0]["content"][1]["attrs"], {"type": "a"})
        self.assertEqual(content[6]["attrs"], {"start": 2}, "numbering continues after the interruption")

        image = content[7]["attrs"]
        self.assertEqual(image["attachmentId"], images[0][0])
        self.assertEqual(image["alt"], "Ein Diagramm")
        self.assertEqual(image["width"], 200)
        self.assertEqual(content[8]["attrs"]["width"], 120, "small pictures are clamped to the minimum")

        rows = content[11]["content"]
        self.assertEqual(_types(rows[0]["content"]), ["tableHeader", "tableHeader"])
        self.assertEqual(rows[0]["content"][0]["attrs"], {"colwidth": [200]})
        self.assertEqual(
            rows[1]["content"][0]["attrs"],
            {"colspan": 2, "colwidth": [200, 200], "backgroundColor": "#d9e2f3"},
        )
        self.assertEqual(rows[2]["content"][0]["attrs"]["rowspan"], 2)
        self.assertEqual(len(rows[3]["content"]), 1)

    def test_long_tables_are_split_into_tables_the_editor_allows(self):
        rows = "".join(f"<w:tr>{_cell(str(index))}</w:tr>" for index in range(45))
        _title, document, _images, _skipped = docx_to_note(build_docx(f"<w:tbl>{rows}</w:tbl>"), "datei")

        validate_note_document(document)
        self.assertEqual([len(table["content"]) for table in document["content"]], [20, 20, 5])

    def test_unusable_images_are_skipped_and_counted(self):
        rels_body = "<w:p>" + _drawing("rId1") + "</w:p>"
        data = build_docx(rels_body, extra={})
        broken = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(broken, "w") as target:
            for item in source.infolist():
                payload = b"not an image" if item.filename == "word/media/image1.png" else source.read(item)
                target.writestr(item, payload)

        _title, document, images, skipped = docx_to_note(broken.getvalue(), "datei")

        self.assertEqual((images, skipped), ([], 1))
        self.assertEqual(document["content"], [{"type": "paragraph"}])

    def test_hostile_files_are_rejected(self):
        with self.assertRaisesMessage(ValidationError, "kein gültiges Word-Dokument"):
            docx_to_note(b"not a zip", "datei")
        doctype = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><w:document/>'
        bomb = io.BytesIO()
        with zipfile.ZipFile(bomb, "w") as archive:
            archive.writestr("word/document.xml", doctype)
        with self.assertRaisesMessage(ValidationError, "nicht erlaubte XML"):
            docx_to_note(bomb.getvalue(), "datei")
        huge = io.BytesIO()
        with zipfile.ZipFile(huge, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", b" " * (11 * 1024 * 1024))
        with self.assertRaisesMessage(ValidationError, "zu große Bestandteile"):
            docx_to_note(huge.getvalue(), "datei")


class DocxImportViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mira", password="secret-12345")
        Profile.objects.create(user=self.user, display_name="Mira", timezone_name="Europe/Berlin")
        self.client.force_login(self.user)

    def test_notes_page_import_creates_note_with_image_attachments(self):
        response = self.client.post(
            "/notes/import/", {"file": SimpleUploadedFile("Bericht.docx", build_docx())}
        )

        note = Note.objects.get(owner=self.user)
        self.assertRedirects(response, f"/notes/{note.pk}/", fetch_redirect_response=False)
        self.assertEqual(note.title, "Projektbericht")
        self.assertIn("Schritt zwei", note.plain_text)
        attachments = NoteAttachment.objects.filter(note=note)
        self.assertEqual(attachments.count(), 2)
        image_ids = {
            node["attrs"]["attachmentId"] for node in note.document["content"] if node["type"] == "noteImage"
        }
        self.assertEqual(image_ids, {str(item.file_id) for item in attachments})

        download = self.client.get(
            reverse("note_attachment_download", args=[attachments[0].file_id, "inline"])
        )
        self.assertEqual(download.status_code, 200)
        download.close()
        self.assertEqual(self.client.get(f"/notes/{note.pk}/").status_code, 200)
        # The editor saves the imported document back unchanged.
        saved = save_note(
            self.user, note.pk, title=note.title, document=note.document, base_revision=note.revision
        )
        self.assertEqual(saved.revision, note.revision + 1)

    def test_notes_page_import_rejects_other_files(self):
        response = self.client.post(
            "/notes/import/", {"file": SimpleUploadedFile("x.doc", b"\xd0\xcf")}, follow=True
        )

        self.assertFalse(Note.objects.exists())
        self.assertContains(response, "Bitte wähle eine Word-Datei (.docx) aus.")

    def test_settings_import_accepts_docx(self):
        response = self.client.post(
            "/settings/",
            {"form_name": "data_import", "files": [SimpleUploadedFile("Bericht.docx", build_docx())]},
            follow=True,
        )

        self.assertContains(response, "1 Notiz(en) importiert.")
        self.assertEqual(Note.objects.get(owner=self.user).attachments.count(), 2)
