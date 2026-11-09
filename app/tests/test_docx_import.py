import io
import zipfile

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from app.models import Note, NoteAttachment, Profile
from app.services.docx_import import _Converter, _note_image, docx_to_note
from app.services.note_content import validate_note_document
from app.services.notes import save_note

NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
    'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"'
)

STYLES = f"""<w:styles {NS}>
<w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val="24"/></w:rPr></w:rPrDefault></w:docDefaults>
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


def build_docx(body=BODY, extra=None, styles=STYLES, numbering=NUMBERING):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/document.xml", f"<w:document {NS}><w:body>{body}<w:sectPr/></w:body></w:document>"
        )
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/numbering.xml", numbering)
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
        self.assertEqual(
            _marks(content[0]["content"][0])["textStyle"],
            {"color": "#2f5496", "fontSize": "24px"},
            "headings keep their style's colour and size",
        )

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

    def test_shading_borders_and_spacing_lines_carry_over(self):
        callout = (
            '<w:tcBorders><w:top w:val="none"/><w:left w:val="single" w:sz="24" w:color="548235"/>'
            '<w:bottom w:val="nil"/><w:right w:val="none"/></w:tcBorders><w:shd w:fill="E2EFDA"/>'
            '<w:tcW w:type="dxa" w:w="9000"/>'
        )
        grid_cell = (
            '<w:tcBorders><w:top w:val="single" w:sz="4" w:color="BFBFBF"/>'
            '<w:left w:val="single" w:sz="4" w:color="BFBFBF"/></w:tcBorders><w:shd w:fill="FFFFFF"/>'
        )
        body = "".join(
            [
                '<w:p><w:pPr><w:pStyle w:val="berschrift1"/><w:shd w:fill="1F4E79"/></w:pPr>'
                '<w:r><w:rPr><w:color w:val="FFFFFF"/></w:rPr><w:t>Kapitel</w:t></w:r></w:p>',
                '<w:p><w:pPr><w:pBdr><w:left w:val="single" w:sz="18" w:color="2E75B6"/>'
                '<w:bottom w:val="single" w:color="auto"/></w:pBdr><w:shd w:fill="F2F2F2"/></w:pPr>'
                '<w:r><w:rPr><w:color w:val="000000"/><w:sz w:val="11"/></w:rPr><w:t>Formel</w:t></w:r></w:p>',
                '<w:p><w:r><w:rPr><w:sz w:val="8"/></w:rPr><w:t></w:t></w:r></w:p>',
                "<w:p/>",
                "<w:tbl><w:tr>" + _cell("Merke", callout) + _cell("Zelle", grid_cell) + "</w:tr></w:tbl>",
            ]
        )
        _title, document, _images, _skipped = docx_to_note(build_docx(body), "datei")

        validate_note_document(document)
        heading, formula, empty, table = document["content"]
        self.assertEqual(heading["attrs"], {"level": 1, "backgroundColor": "#1f4e79"})
        self.assertEqual(_marks(heading["content"][0])["textStyle"]["color"], "#ffffff")
        self.assertEqual(
            formula["attrs"],
            {"backgroundColor": "#f2f2f2", "borderLeft": "#2e75b6", "borderBottom": "#000000"},
        )
        self.assertEqual(
            _marks(formula["content"][0])["textStyle"],
            {"color": "#000000", "fontSize": "10px"},
            "black text stays black on the white page; 5.5 pt maps to the smallest size",
        )
        self.assertEqual(empty, {"type": "paragraph"}, "only tiny empty spacing lines are dropped")
        callout_cell, plain_cell = table["content"][0]["content"]
        self.assertEqual(
            callout_cell["attrs"],
            {
                "colwidth": [600],
                "backgroundColor": "#e2efda",
                "borderColor": "none",
                "borderLeft": "#548235",
            },
        )
        self.assertEqual(plain_cell["attrs"], {"borderColor": "#bfbfbf"}, "white fills are the paper")

    def test_body_text_keeps_words_size_on_the_a4_page(self):
        data = build_docx(
            _para("Fließtext"),
            styles=STYLES.replace('<w:sz w:val="24"/>', '<w:sz w:val="20"/>', 1),
        )
        _title, document, _images, _skipped = docx_to_note(data, "datei")

        text = document["content"][0]["content"][0]
        self.assertEqual(_marks(text)["textStyle"], {"fontSize": "14px"}, "10 pt is 13.3 px at 100 % zoom")

    def test_long_tables_are_split_into_tables_the_editor_allows(self):
        rows = "".join(f"<w:tr>{_cell(str(index))}</w:tr>" for index in range(45))
        _title, document, _images, _skipped = docx_to_note(build_docx(f"<w:tbl>{rows}</w:tbl>"), "datei")

        validate_note_document(document)
        self.assertEqual([len(table["content"]) for table in document["content"]], [20, 20, 5])

    def test_form_fields_become_fill_in_fields_with_their_hint_and_value(self):
        def control(content, placeholder=False, extra_pr=""):
            pr = '<w:alias w:val="Eingabe"/><w:tag w:val="Eingabe"/>' + extra_pr
            if placeholder:
                pr += "<w:showingPlcHdr/>"
            return f"<w:sdt><w:sdtPr>{pr}</w:sdtPr><w:sdtContent>{content}</w:sdtContent></w:sdt>"

        def run(text, rpr=""):
            return f'<w:r><w:rPr>{rpr}</w:rPr><w:t xml:space="preserve">{text}</w:t></w:r>'

        def field(data, instruction, result=""):
            return (
                f'<w:r><w:fldChar w:fldCharType="begin"><w:ffData>{data}</w:ffData></w:fldChar></w:r>'
                f'<w:r><w:instrText xml:space="preserve"> {instruction} </w:instrText></w:r>'
                '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
                f'{result}<w:r><w:fldChar w:fldCharType="end"/></w:r>'
            )

        grey = '<w:rStyle w:val="Platzhaltertext"/><w:color w:val="808080"/><w:i/>'
        body = "".join(
            [
                "<w:p>"
                + run("Name: ")
                + control(run("Name", grey), placeholder=True)
                + run(" Klasse: ")
                + control(run("10b"))
                + run(" Quelle: ")
                + control(run("(Müller 2020)"), extra_pr="<w:citation/>")
                + run(" Datum: ")
                + control(
                    run("Datum", grey),
                    placeholder=True,
                    extra_pr='<w:date><w:dateFormat w:val="dd.MM.yyyy"/></w:date>',
                )
                + "</w:p>",
                "<w:p>"
                + control(run("☒"), extra_pr='<w14:checkbox><w14:checked w14:val="1"/></w14:checkbox>')
                + run(" erledigt ")
                + control('<w:r><w:sym w:font="Wingdings" w:char="F0A8"/></w:r>', extra_pr="<w14:checkbox/>")
                + run(" offen")
                + "</w:p>",
                control(_para("Klicken oder tippen Sie hier, um Text einzugeben."), placeholder=True),
                control(_para("Eingetragener Absatz")),
                "<w:p>" + control("<w:r><w:t>zwei</w:t><w:br/><w:t>Zeilen</w:t></w:r>") + "</w:p>",
                "<w:p>"
                + run("Vorname: ")
                + field("<w:textInput/>", "FORMTEXT", run(" " * 5))
                + run(" Ort: ")
                + field("<w:textInput/>", "FORMTEXT", run("Os") + run("nabrück", "<w:b/>"))
                + run(" Haken: ")
                + field('<w:checkBox><w:default w:val="1"/></w:checkBox>', "FORMCHECKBOX")
                + run(" Farbe: ")
                + field(
                    '<w:ddList><w:result w:val="1"/><w:listEntry w:val="rot"/><w:listEntry w:val="blau"/></w:ddList>',
                    "FORMDROPDOWN",
                )
                + "</w:p>",
                "<w:tbl><w:tr>"
                + _cell("Kopf")
                + "</w:tr>"
                + control("<w:tr>" + control(_cell("Wiederholt")) + "</w:tr>")
                + "</w:tbl>",
            ]
        )

        _title, document, _images, _skipped = docx_to_note(build_docx(body=body), "Arbeitsblatt")

        validate_note_document(document)

        def shown(block):
            parts = []
            for node in block.get("content", []):
                attrs = node.get("attrs", {})
                if node["type"] == "formField":
                    parts.append(f"[{attrs['placeholder']}|{attrs['value']}]")
                else:
                    parts.append(node.get("text", " / "))
            return "".join(parts)

        self.assertEqual(
            [shown(block) for block in document["content"] if block["type"] == "paragraph"],
            [
                "Name: [Name|] Klasse: [|10b] Quelle: (Müller 2020) Datum: [Datum|]",
                "☒ erledigt ☐ offen",
                "[Klicken oder tippen Sie hier, um Text einzugeben.|]",
                "Eingetragener Absatz",
                "zwei / Zeilen",
                "Vorname: [|] Ort: [|Osnabrück] Haken: ☒ Farbe: [|blau]",
            ],
        )
        table = document["content"][-1]
        self.assertEqual(len(table["content"]), 2, "rows inside a repeating section are kept")
        self.assertEqual(table["content"][1]["content"][0]["content"][0]["content"][0]["text"], "Wiederholt")

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

    def test_doctype_cannot_hide_behind_utf16(self):
        def convert(doctype):
            xml = (
                f'<?xml version="1.0" encoding="UTF-16"?>{doctype}'
                f"<w:document {NS}><w:body>{_para('Text')}</w:body></w:document>"
            )
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr("word/document.xml", xml.encode("utf-16"))
            return docx_to_note(output.getvalue(), "datei")

        with self.assertRaisesMessage(ValidationError, "nicht erlaubte XML"):
            convert('<!DOCTYPE x [<!ENTITY a "aaaa">]>')
        self.assertEqual(convert("")[1]["content"][0]["content"][0]["text"], "Text")

    def test_nested_text_boxes_are_converted_once(self):
        nested = "<w:p/>"
        for _level in range(14):
            nested = f"<w:p><w:r><w:drawing><w:txbxContent>{nested}</w:txbxContent></w:drawing></w:r></w:p>"

        _title, document, _images, _skipped = docx_to_note(build_docx(nested), "datei")

        self.assertEqual(len(document["content"]), 15)

    def test_only_word_list_levels_are_kept(self):
        levels = "".join(f'<w:lvl w:ilvl="{level}"/>' for level in range(500))
        numbering = (
            f'<w:numbering {NS}><w:abstractNum w:abstractNumId="0">{levels}</w:abstractNum>'
            '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
        )

        with zipfile.ZipFile(io.BytesIO(build_docx(numbering=numbering))) as archive:
            kept = _Converter(archive).numbering["1"]

        self.assertEqual(set(kept), {str(level) for level in range(9)})

    def test_digits_int_refuses_count_as_a_broken_file(self):
        numbering = (
            f'<w:numbering {NS}><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0">'
            '<w:start w:val="²"/></w:lvl></w:abstractNum></w:numbering>'
        )

        with self.assertRaisesMessage(ValidationError, "beschädigt"):
            docx_to_note(build_docx(numbering=numbering), "datei")

    def test_only_expected_image_formats_are_converted(self):
        def encoded(image_format):
            output = io.BytesIO()
            Image.new("RGB", (10, 10)).save(output, image_format)
            return output.getvalue()

        self.assertEqual(_note_image("bild.tiff", encoded("TIFF"))[0], "bild.png")
        self.assertEqual(_note_image("bild.bmp", encoded("BMP"))[0], "bild.png")
        self.assertIsNone(_note_image("bild.ppm", encoded("PPM")))


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
