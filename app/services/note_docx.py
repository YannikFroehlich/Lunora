"""Note → Word (.docx), on the stdlib: the counterpart of docx_import.py.

WordprocessingML is written by hand. The order of the children of w:pPr, w:rPr, w:tcPr and w:tblPr is
the schema's, because Word calls a file corrupt otherwise. Like note_html.py this re-checks colours,
fonts, sizes and link schemes against note_content's allowlists instead of trusting the stored JSON,
and escapes every text. Mentions and note links become plain labels, as their targets are private.
"""

import re
import zipfile
from datetime import UTC
from io import BytesIO
from urllib.parse import quote
from xml.sax.saxutils import escape

from django.utils.text import slugify
from PIL import Image, UnidentifiedImageError

from app.services.note_content import (
    ALLOWED_FONT_SIZES,
    ALLOWED_FONTS,
    ALLOWED_LINE_HEIGHTS,
    HEX_COLOR_RE,
    safe_link_href,
)
from app.services.notes import display_name

# Word's own hint for an empty plain-text content control.
FORM_FIELD_HINT = "Klicken oder tippen Sie hier, um Text einzugeben."

CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
ROOT_NAMESPACES = (
    f'xmlns:w="{W_NS}" xmlns:r="{R_NS}" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"'
)
XML_HEADER = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

PAGE_WIDTH = 11906  # A4 in twips (1/20 pt).
PAGE_HEIGHT = 16838
PAGE_MARGIN = 1134  # 20 mm.
TEXT_WIDTH = PAGE_WIDTH - 2 * PAGE_MARGIN
TEXT_HEIGHT = PAGE_HEIGHT - 2 * PAGE_MARGIN
EMU_PER_TWIP = 635
EMU_PER_PIXEL = 9525
TWIPS_PER_PIXEL = 15
LIST_INDENT = 720
MAX_LIST_LEVEL = 8  # Word has nine list levels.
MAX_LIST_START = 32767
MIN_COLUMN_WIDTH = 400
DEFAULT_IMAGE_WIDTH = 720

ALIGNMENTS = {"center": "center", "right": "right", "justify": "both"}
NUMBER_FORMATS = {
    "a": "lowerLetter",
    "A": "upperLetter",
    "i": "lowerRoman",
    "I": "upperRoman",
    None: "decimal",
}
ABSTRACT_NUM_IDS = {number_format: index + 1 for index, number_format in enumerate(NUMBER_FORMATS.values())}
BULLETS = ("•", "◦", "▪")
BULLET_NUM_ID = 1
CODE_SHADING = "F1EDE7"
DEFAULT_HIGHLIGHT = "FFFF00"
# Characters XML 1.0 can't hold at all, plus \r, which Word would show as a stray line break.
UNWRITABLE_RE = re.compile("[^\x09\x0a\x20-퟿-�\U00010000-\U0010ffff]")


def note_docx_filename(note):
    safe_title = slugify(note.title, allow_unicode=True).strip("-_")[:100] or "notiz"
    return f"{safe_title}.docx"


def render_note_docx(note):
    return NoteDocxRenderer(note).render()


def _clean(text):
    return UNWRITABLE_RE.sub("", str(text))


def _attr(text):
    return escape(_clean(text), {'"': "&quot;"})


def _hex(value):
    """A stored colour as Word writes it (RRGGBB), or None."""
    return value[1:].upper() if isinstance(value, str) and HEX_COLOR_RE.fullmatch(value) else None


def _run(text, rpr=""):
    pieces = []
    for piece in re.split(r"([\n\t])", _clean(text)):
        if piece == "\n":
            pieces.append("<w:br/>")
        elif piece == "\t":
            pieces.append("<w:tab/>")
        elif piece:
            pieces.append(f'<w:t xml:space="preserve">{escape(piece)}</w:t>')
    return f"<w:r>{rpr}{''.join(pieces)}</w:r>" if pieces else ""


def _form_field(attrs):
    """A fill-in field as a Word plain-text content control, the counterpart of docx_import's; an
    empty one shows its hint greyed out until something is typed in, like Word's placeholder."""
    if value := str(attrs.get("value") or ""):
        return f"<w:sdt><w:sdtPr><w:text/></w:sdtPr><w:sdtContent>{_run(value)}</w:sdtContent></w:sdt>"
    hint = _run(
        str(attrs.get("placeholder") or "") or FORM_FIELD_HINT, '<w:rPr><w:color w:val="808080"/></w:rPr>'
    )
    return f"<w:sdt><w:sdtPr><w:showingPlcHdr/><w:text/></w:sdtPr><w:sdtContent>{hint}</w:sdtContent></w:sdt>"


def _file_size(size):
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{round(size / 1024)} KB"
    return f"{size / (1024 * 1024):.1f} MB"


class NoteDocxRenderer:
    def __init__(self, note):
        self.note = note
        self.attachments = {str(item.file_id).lower(): item for item in note.attachments.all()}
        self.links = {}  # href → relationship id
        self.images = {}  # attachment id → (relationship id, media name, bytes, width, height), or None
        self.ordered_lists = []  # (number format, start, level); the n-th one is w:num n + 1
        self.drawing_count = 0

    def render(self):
        title = f'<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>{_run(self.note.title)}</w:p>'
        body = "".join(self.blocks(self.note.document.get("content") or []))
        section = (
            f'<w:sectPr><w:pgSz w:w="{PAGE_WIDTH}" w:h="{PAGE_HEIGHT}"/>'
            f'<w:pgMar w:top="{PAGE_MARGIN}" w:right="{PAGE_MARGIN}" w:bottom="{PAGE_MARGIN}" '
            f'w:left="{PAGE_MARGIN}" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
        )
        document = (
            f"{XML_HEADER}<w:document {ROOT_NAMESPACES}><w:body>{title}{body}{section}</w:body></w:document>"
        )
        output = BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", self._content_types())
            archive.writestr("_rels/.rels", self._package_relationships())
            archive.writestr("docProps/core.xml", self._core_properties())
            archive.writestr("word/document.xml", document)
            archive.writestr("word/styles.xml", self._styles())
            archive.writestr("word/numbering.xml", self._numbering())
            archive.writestr("word/_rels/document.xml.rels", self._document_relationships())
            for image in filter(None, self.images.values()):
                archive.writestr(f"word/media/{image[1]}", image[2])
        output.seek(0)
        return output

    # ---- blocks ----

    def blocks(self, nodes, depth=0, quote_block=False, force_bold=False):
        parts = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            rendered = self.block(node, depth, quote_block, force_bold)
            # Word would merge two tables that follow each other.
            if rendered and parts and rendered[0].startswith("<w:tbl>") and parts[-1].startswith("<w:tbl>"):
                parts.append("<w:p/>")
            parts.extend(rendered)
        return parts

    def block(self, node, depth, quote_block, force_bold):
        node_type = node.get("type")
        attrs = node.get("attrs") or {}
        content = node.get("content") or []
        if node_type in {"paragraph", "heading"}:
            return [self.paragraph(node, depth=depth, quote_block=quote_block, force_bold=force_bold)]
        if node_type == "bulletList":
            return self.list_items(content, BULLET_NUM_ID, depth, quote_block, force_bold)
        if node_type == "orderedList":
            num_id = self._ordered_num_id(attrs, depth)
            return self.list_items(content, num_id, depth, quote_block, force_bold)
        if node_type == "taskList":
            return self.task_items(content, depth, quote_block, force_bold)
        if node_type == "blockquote":
            return self.blocks(content, depth, True, force_bold)
        if node_type == "codeBlock":
            text = "".join(
                child.get("text", "")
                for child in content
                if isinstance(child, dict) and child.get("type") == "text"
            )
            return [f'<w:p><w:pPr><w:pStyle w:val="Code"/></w:pPr>{_run(text)}</w:p>']
        if node_type == "horizontalRule":
            return [
                '<w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="CFC4B7"/>'
                "</w:pBdr></w:pPr></w:p>"
            ]
        if node_type == "table":
            return [self.table(content)]
        if node_type == "noteImage":
            return [self.image(attrs)]
        if node_type == "noteAttachment":
            return [self.attachment(attrs)]
        if node_type == "mathBlock":
            return [
                f'<w:p><w:pPr><w:jc w:val="center"/></w:pPr>{self._code_run(attrs.get("latex", ""))}</w:p>'
            ]
        return []

    def paragraph(self, node, *, depth=0, quote_block=False, force_bold=False, num=None, prefix=""):
        attrs = node.get("attrs") or {}
        content = node.get("content") or []
        style = "Quote" if quote_block else None
        if node.get("type") == "heading":
            style = f"Heading{attrs.get('level') if attrs.get('level') in {1, 2, 3} else 1}"
        ppr = self._ppr(style, num, attrs, 0 if num else depth * LIST_INDENT, self._line_height(content))
        return f"<w:p>{ppr}{_run(prefix)}{self.inline(content, force_bold)}</w:p>"

    @staticmethod
    def _ppr(style, num, attrs, indent, line):
        parts = []
        if style:
            parts.append(f'<w:pStyle w:val="{style}"/>')
        if num:
            parts.append(f'<w:numPr><w:ilvl w:val="{num[1]}"/><w:numId w:val="{num[0]}"/></w:numPr>')
        borders = ""
        if color := _hex(attrs.get("borderLeft")):
            borders += f'<w:left w:val="single" w:sz="18" w:space="8" w:color="{color}"/>'
        if color := _hex(attrs.get("borderBottom")):
            borders += f'<w:bottom w:val="single" w:sz="12" w:space="2" w:color="{color}"/>'
        if borders:
            parts.append(f"<w:pBdr>{borders}</w:pBdr>")
        if color := _hex(attrs.get("backgroundColor")):
            parts.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{color}"/>')
        if line:
            parts.append(f'<w:spacing w:line="{line}" w:lineRule="auto"/>')
        if indent:
            parts.append(f'<w:ind w:left="{indent}"/>')
        if alignment := ALIGNMENTS.get(attrs.get("textAlign")):
            parts.append(f'<w:jc w:val="{alignment}"/>')
        return f"<w:pPr>{''.join(parts)}</w:pPr>" if parts else ""

    @staticmethod
    def _line_height(content):
        """The first text style's line height: the editor stores it per run, Word per paragraph."""
        for child in content:
            for mark in (child.get("marks") or []) if isinstance(child, dict) else []:
                value = (
                    (mark.get("attrs") or {}).get("lineHeight") if mark.get("type") == "textStyle" else None
                )
                if value in ALLOWED_LINE_HEIGHTS:
                    return round(240 * float(value))
        return None

    def list_items(self, items, num_id, depth, quote_block, force_bold):
        level = min(depth, MAX_LIST_LEVEL)
        parts = []
        for item in items:
            first, rest = self._split_item(item)
            parts.append(
                self.paragraph(first, quote_block=quote_block, force_bold=force_bold, num=(num_id, level))
            )
            parts.extend(self.blocks(rest, depth + 1, quote_block, force_bold))
        return parts

    def task_items(self, items, depth, quote_block, force_bold):
        parts = []
        for item in items:
            first, rest = self._split_item(item)
            prefix = "☑ " if (item.get("attrs") or {}).get("checked") is True else "☐ "
            parts.append(
                self.paragraph(
                    first, depth=depth, quote_block=quote_block, force_bold=force_bold, prefix=prefix
                )
            )
            parts.extend(self.blocks(rest, depth + 1, quote_block, force_bold))
        return parts

    @staticmethod
    def _split_item(item):
        """A list item's first paragraph (which carries the marker) and the blocks after it."""
        children = [child for child in item.get("content") or [] if isinstance(child, dict)]
        if children and children[0].get("type") == "paragraph":
            return children[0], children[1:]
        return {"type": "paragraph"}, children

    def _ordered_num_id(self, attrs, depth):
        number_format = NUMBER_FORMATS.get(attrs.get("type"), "decimal")
        start = attrs.get("start")
        start = start if isinstance(start, int) and 0 < start <= MAX_LIST_START else 1
        self.ordered_lists.append((number_format, start, min(depth, MAX_LIST_LEVEL)))
        return BULLET_NUM_ID + len(self.ordered_lists)

    # ---- tables ----

    def table(self, rows):
        placed = self._place_cells(rows)
        columns = max((sum(cell["span"] for cell in row) for row in placed), default=1) or 1
        for row in placed:
            row.extend(self._empty_cell() for _ in range(columns - sum(cell["span"] for cell in row)))
        grid = self._column_widths(placed, columns)
        row_xml = []
        for row in placed:
            is_header = bool(row) and all(cell["header"] for cell in row)
            properties = "<w:trPr><w:tblHeader/></w:trPr>" if is_header else ""
            column = 0
            cells = []
            for cell in row:
                cells.append(self._cell_xml(cell, sum(grid[column : column + cell["span"]])))
                column += cell["span"]
            row_xml.append(f"<w:tr>{properties}{''.join(cells)}</w:tr>")
        grid_xml = "".join(f'<w:gridCol w:w="{width}"/>' for width in grid)
        side = '<w:{0} w:val="single" w:sz="4" w:space="0" w:color="BFB6AA"/>'
        borders = "".join(
            side.format(name) for name in ("top", "left", "bottom", "right", "insideH", "insideV")
        )
        return (
            f'<w:tbl><w:tblPr><w:tblW w:w="{sum(grid)}" w:type="dxa"/><w:tblBorders>{borders}</w:tblBorders>'
            '<w:tblLayout w:type="fixed"/></w:tblPr>'
            f"<w:tblGrid>{grid_xml}</w:tblGrid>{''.join(row_xml)}</w:tbl>"
        )

    def _place_cells(self, rows):
        """One list per row with every cell Word needs, including the continuation cells of rowspans."""
        running = {}  # column → (rows still covered, colspan, attrs)
        placed = []
        for row in rows:
            queue = [cell for cell in (row.get("content") or []) if isinstance(cell, dict)]
            cells = []
            column = 0
            while queue or any(key >= column for key in running):
                if column in running:
                    left, span, attrs = running.pop(column)
                    if left > 1:
                        running[column] = (left - 1, span, attrs)
                    cells.append(
                        {"span": span, "attrs": attrs, "merge": "continue", "node": None, "header": False}
                    )
                elif queue:
                    cell = queue.pop(0)
                    attrs = cell.get("attrs") or {}
                    span = self._span(attrs.get("colspan"))
                    rowspan = self._span(attrs.get("rowspan"))
                    if rowspan > 1:
                        running[column] = (rowspan - 1, span, attrs)
                    cells.append(
                        {
                            "span": span,
                            "attrs": attrs,
                            "merge": "restart" if rowspan > 1 else None,
                            "node": cell,
                            "header": cell.get("type") == "tableHeader",
                        }
                    )
                else:
                    cells.append(self._empty_cell())
                column += cells[-1]["span"]
            placed.append(cells)
        return placed

    @staticmethod
    def _empty_cell():
        return {"span": 1, "attrs": {}, "merge": None, "node": None, "header": False}

    @staticmethod
    def _span(value):
        return value if isinstance(value, int) and 1 <= value <= 20 else 1

    @staticmethod
    def _column_widths(placed, columns):
        """Stored widths where cells have them, the rest shared equally, the sum at most the page's."""
        known = [None] * columns
        for row in placed:
            column = 0
            for cell in row:
                widths = cell["attrs"].get("colwidth") if cell["node"] is not None else None
                if isinstance(widths, list) and len(widths) == cell["span"]:
                    for offset, width in enumerate(widths):
                        if isinstance(width, int) and width > 0:
                            known[column + offset] = width * TWIPS_PER_PIXEL
                column += cell["span"]
        taken = sum(width for width in known if width)
        open_columns = known.count(None)
        share = max((TEXT_WIDTH - taken) // open_columns, MIN_COLUMN_WIDTH) if open_columns else 0
        widths = [width or share for width in known]
        total = sum(widths)
        return [width * TEXT_WIDTH // total for width in widths] if total > TEXT_WIDTH else widths

    def _cell_xml(self, cell, width):
        attrs = cell["attrs"]
        parts = [f'<w:tcW w:w="{width}" w:type="dxa"/>']
        if cell["span"] > 1:
            parts.append(f'<w:gridSpan w:val="{cell["span"]}"/>')
        if cell["merge"] == "restart":
            parts.append('<w:vMerge w:val="restart"/>')
        elif cell["merge"] == "continue":
            parts.append("<w:vMerge/>")
        if borders := self._cell_borders(attrs):
            parts.append(f"<w:tcBorders>{borders}</w:tcBorders>")
        if color := _hex(attrs.get("backgroundColor")):
            parts.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{color}"/>')
        content = []
        if cell["node"] is not None:
            content = self.blocks(cell["node"].get("content") or [], force_bold=cell["header"])
        # Word requires a cell to end with a paragraph.
        if not content or not content[-1].startswith("<w:p"):
            content.append("<w:p/>")
        return f"<w:tc><w:tcPr>{''.join(parts)}</w:tcPr>{''.join(content)}</w:tc>"

    @staticmethod
    def _cell_borders(attrs):
        border_color = attrs.get("borderColor")
        if border_color == "none":
            value, color = "nil", None
        else:
            color = _hex(border_color)
            value = "single" if color else None
        left = _hex(attrs.get("borderLeft"))
        if not value and not left:
            return ""

        def side(name):
            if name == "left" and left:
                return f'<w:left w:val="single" w:sz="18" w:space="0" w:color="{left}"/>'
            if not value:
                return ""
            color_attr = f' w:color="{color}"' if color else ""
            size = ' w:sz="4" w:space="0"' if color else ""
            return f'<w:{name} w:val="{value}"{size}{color_attr}/>'

        return "".join(side(name) for name in ("top", "left", "bottom", "right"))

    # ---- files ----

    def image(self, attrs):
        attachment_id = str(attrs.get("attachmentId", "")).lower()
        alt = str(attrs.get("alt") or attrs.get("title") or "")
        image = self._load_image(attachment_id)
        if image is None:
            label = f"[Bild nicht verfügbar: {alt or 'ohne Titel'}]"
            return f"<w:p>{_run(label, '<w:rPr><w:i/></w:rPr>')}</w:p>"
        rel_id, _name, _data, pixel_width, pixel_height = image
        width = attrs.get("width") if isinstance(attrs.get("width"), int) else DEFAULT_IMAGE_WIDTH
        cx = min(width * EMU_PER_PIXEL, TEXT_WIDTH * EMU_PER_TWIP)
        cy = cx * pixel_height // pixel_width
        max_height = TEXT_HEIGHT * EMU_PER_TWIP * 9 // 10
        if cy > max_height:
            cx = cx * max_height // cy
            cy = max_height
        self.drawing_count += 1
        number = self.drawing_count
        return (
            '<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:drawing>'
            '<wp:inline distT="0" distB="0" distL="0" distR="0">'
            f'<wp:extent cx="{cx}" cy="{cy}"/>'
            f'<wp:docPr id="{number}" name="Bild {number}" descr="{_attr(alt)}"/>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:pic><pic:nvPicPr><pic:cNvPr id="{number}" name="Bild {number}"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rel_id}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
            "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
        )

    def _load_image(self, attachment_id):
        if attachment_id in self.images:
            return self.images[attachment_id]
        attachment = self.attachments.get(attachment_id)
        result = None
        if attachment is not None and attachment.kind == "image":
            try:
                with attachment.file.open("rb") as source:
                    data = source.read()
                with Image.open(BytesIO(data), formats=("PNG", "JPEG", "WEBP")) as picture:
                    width, height = picture.size
                    extension = "jpg" if picture.format == "JPEG" else "png"
                    if picture.format == "WEBP":  # Word can't show WebP in every version.
                        converted = BytesIO()
                        picture.save(converted, "PNG")
                        data = converted.getvalue()
            except (OSError, UnidentifiedImageError, ValueError):
                self.images[attachment_id] = None
                return None
            if width > 0 and height > 0:
                number = len(self.images) + 1
                result = (self._next_relationship_id(), f"image{number}.{extension}", data, width, height)
        self.images[attachment_id] = result
        return result

    def attachment(self, attrs):
        attachment = self.attachments.get(str(attrs.get("attachmentId", "")).lower())
        name = attachment.original_name if attachment else str(attrs.get("name") or "Datei")
        label = f"Anhang: {name}" + (f" ({_file_size(attachment.size)})" if attachment else "")
        return f"<w:p>{_run(label, '<w:rPr><w:i/></w:rPr>')}</w:p>"

    # ---- inline content ----

    def inline(self, content, force_bold=False):
        parts = []
        for child in content:
            if not isinstance(child, dict):
                continue
            child_type = child.get("type")
            attrs = child.get("attrs") or {}
            if child_type == "text":
                parts.append(self.text(child, force_bold))
            elif child_type == "hardBreak":
                parts.append("<w:r><w:br/></w:r>")
            elif child_type == "mention":
                parts.append(_run(f"@{attrs.get('label', '')}", "<w:rPr><w:b/></w:rPr>"))
            elif child_type == "noteLink":
                parts.append(_run(attrs.get("label", "")))
            elif child_type == "mathInline":
                parts.append(self._code_run(attrs.get("latex", "")))
            elif child_type == "formField":
                parts.append(_form_field(attrs))
        return "".join(parts)

    @staticmethod
    def _code_run(text):
        return _run(
            text,
            '<w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New" w:cs="Courier New"/>'
            f'<w:shd w:val="clear" w:color="auto" w:fill="{CODE_SHADING}"/></w:rPr>',
        )

    def text(self, node, force_bold):
        marks = {
            mark.get("type"): mark.get("attrs") or {}
            for mark in node.get("marks") or []
            if isinstance(mark, dict)
        }
        rel_id = self._link_id(marks["link"].get("href")) if "link" in marks else None
        run = _run(node.get("text", ""), self._rpr(marks, force_bold, linked=bool(rel_id)))
        return f'<w:hyperlink r:id="{rel_id}">{run}</w:hyperlink>' if rel_id and run else run

    @staticmethod
    def _rpr(marks, force_bold, *, linked):
        style = marks.get("textStyle", {})
        parts = []
        if linked:
            parts.append('<w:rStyle w:val="Hyperlink"/>')
        font = "Courier New" if "code" in marks else style.get("fontFamily")
        # Inter is rarely installed, so it stays the document's default font.
        if font in ALLOWED_FONTS and font != "Inter":
            parts.append(f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:cs="{font}"/>')
        if "bold" in marks or force_bold:
            parts.append("<w:b/>")
        if "italic" in marks:
            parts.append("<w:i/>")
        if "strike" in marks:
            parts.append("<w:strike/>")
        if color := _hex(style.get("color")):
            parts.append(f'<w:color w:val="{color}"/>')
        if style.get("fontSize") in ALLOWED_FONT_SIZES:
            half_points = int(style["fontSize"].removesuffix("px")) * 3 // 2
            parts.append(f'<w:sz w:val="{half_points}"/><w:szCs w:val="{half_points}"/>')
        if "underline" in marks:
            parts.append('<w:u w:val="single"/>')
        shading = None
        if "code" in marks:
            shading = CODE_SHADING
        elif "highlight" in marks:
            shading = _hex(marks["highlight"].get("color")) or DEFAULT_HIGHLIGHT
        elif background := _hex(style.get("backgroundColor")):
            shading = background
        if shading:
            parts.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{shading}"/>')
        if "superscript" in marks:
            parts.append('<w:vertAlign w:val="superscript"/>')
        elif "subscript" in marks:
            parts.append('<w:vertAlign w:val="subscript"/>')
        return f"<w:rPr>{''.join(parts)}</w:rPr>" if parts else ""

    def _link_id(self, href):
        href = str(href or "").strip()
        if not safe_link_href(href):
            return None
        if href not in self.links:
            self.links[href] = self._next_relationship_id()
        return self.links[href]

    def _next_relationship_id(self):
        # rId1 and rId2 are the styles and the numbering; links and images share the rest.
        return f"rId{len(self.links) + sum(1 for image in self.images.values() if image) + 3}"

    # ---- package parts ----

    def _content_types(self):
        return (
            f'{XML_HEADER}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Default Extension="jpg" ContentType="image/jpeg"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.numbering+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.'
            'core-properties+xml"/></Types>'
        )

    @staticmethod
    def _package_relationships():
        return (
            f'{XML_HEADER}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{R_NS}/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/'
            'metadata/core-properties" Target="docProps/core.xml"/></Relationships>'
        )

    def _core_properties(self):
        editor = self.note.last_edited_by or self.note.owner
        modified = self.note.updated_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return (
            f"{XML_HEADER}<cp:coreProperties "
            'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:title>{escape(_clean(self.note.title))}</dc:title>"
            f"<dc:creator>{escape(_clean(display_name(editor)))}</dc:creator>"
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{modified}</dcterms:modified></cp:coreProperties>'
        )

    def _document_relationships(self):
        items = [
            f'<Relationship Id="rId1" Type="{R_NS}/styles" Target="styles.xml"/>',
            f'<Relationship Id="rId2" Type="{R_NS}/numbering" Target="numbering.xml"/>',
        ]
        for href, rel_id in self.links.items():
            target = _attr(quote(href, safe=":/?#[]@!$&'()*+,;=%~-._"))
            items.append(
                f'<Relationship Id="{rel_id}" Type="{R_NS}/hyperlink" Target="{target}" TargetMode="External"/>'
            )
        for image in filter(None, self.images.values()):
            items.append(f'<Relationship Id="{image[0]}" Type="{R_NS}/image" Target="media/{image[1]}"/>')
        return (
            f'{XML_HEADER}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{''.join(items)}</Relationships>"
        )

    def _numbering(self):
        def level(index, number_format, text):
            indent = LIST_INDENT * (index + 1)
            return (
                f'<w:lvl w:ilvl="{index}"><w:start w:val="1"/><w:numFmt w:val="{number_format}"/>'
                f'<w:lvlText w:val="{text}"/><w:lvlJc w:val="left"/>'
                f'<w:pPr><w:ind w:left="{indent}" w:hanging="360"/></w:pPr></w:lvl>'
            )

        levels = range(MAX_LIST_LEVEL + 1)
        bullet = "".join(level(index, "bullet", BULLETS[index % len(BULLETS)]) for index in levels)
        abstracts = [
            f'<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="multilevel"/>{bullet}</w:abstractNum>'
        ]
        for number_format, abstract_id in ABSTRACT_NUM_IDS.items():
            ordered = "".join(level(index, number_format, f"%{index + 1}.") for index in levels)
            abstracts.append(
                f'<w:abstractNum w:abstractNumId="{abstract_id}"><w:multiLevelType w:val="multilevel"/>'
                f"{ordered}</w:abstractNum>"
            )
        nums = [f'<w:num w:numId="{BULLET_NUM_ID}"><w:abstractNumId w:val="0"/></w:num>']
        for offset, (number_format, start, list_level) in enumerate(self.ordered_lists, start=1):
            nums.append(
                f'<w:num w:numId="{BULLET_NUM_ID + offset}">'
                f'<w:abstractNumId w:val="{ABSTRACT_NUM_IDS[number_format]}"/>'
                f'<w:lvlOverride w:ilvl="{list_level}"><w:startOverride w:val="{start}"/></w:lvlOverride></w:num>'
            )
        return f'{XML_HEADER}<w:numbering xmlns:w="{W_NS}">{"".join(abstracts)}{"".join(nums)}</w:numbering>'

    @staticmethod
    def _styles():
        def paragraph_style(style_id, name, ppr="", rpr=""):
            return (
                f'<w:style w:type="paragraph" w:styleId="{style_id}"><w:name w:val="{name}"/>'
                f'<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
                f"<w:pPr>{ppr}</w:pPr><w:rPr>{rpr}</w:rPr></w:style>"
            )

        def heading(level, size, before):
            return paragraph_style(
                f"Heading{level}",
                f"heading {level}",
                f'<w:keepNext/><w:spacing w:before="{before}" w:after="80"/><w:outlineLvl w:val="{level - 1}"/>',
                f'<w:b/><w:sz w:val="{size}"/><w:szCs w:val="{size}"/>',
            )

        font = '<w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Arial" w:cs="Arial"/>'
        return (
            f'{XML_HEADER}<w:styles xmlns:w="{W_NS}">'
            f'<w:docDefaults><w:rPrDefault><w:rPr>{font}<w:sz w:val="24"/><w:szCs w:val="24"/>'
            '<w:lang w:val="de-DE"/></w:rPr></w:rPrDefault>'
            '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr>'
            "</w:pPrDefault></w:docDefaults>"
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
            + paragraph_style(
                "Title", "Title", '<w:spacing w:after="240"/>', '<w:b/><w:sz w:val="52"/><w:szCs w:val="52"/>'
            )
            + heading(1, 44, 320)
            + heading(2, 36, 280)
            + heading(3, 30, 240)
            + paragraph_style(
                "Quote",
                "Quote",
                '<w:pBdr><w:left w:val="single" w:sz="18" w:space="8" w:color="A67C52"/></w:pBdr>'
                '<w:ind w:left="284"/>',
            )
            + paragraph_style(
                "Code",
                "Code",
                f'<w:shd w:val="clear" w:color="auto" w:fill="{CODE_SHADING}"/><w:spacing w:line="240" w:lineRule="auto"/>',
                '<w:rFonts w:ascii="Courier New" w:hAnsi="Courier New" w:cs="Courier New"/>'
                '<w:sz w:val="18"/><w:szCs w:val="18"/>',
            )
            + '<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/>'
            '<w:rPr><w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr></w:style></w:styles>'
        )
