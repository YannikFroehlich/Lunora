"""Word (.docx) → note, on the stdlib: a .docx is a ZIP of WordprocessingML parts.

The upload is untrusted: entries are read with hard size limits (declared sizes can lie), XML with a
DOCTYPE is refused (no entity tricks), and the result still has to pass validate_note_document.
Formatting the note allowlists don't know is mapped to the nearest allowed value or dropped.
Embedded images become regular note image attachments.
"""

import io
import posixpath
import re
import uuid
import zipfile
import zlib
from pathlib import PurePath
from xml.etree import ElementTree

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from PIL import Image

from app.models import NoteAttachment
from app.services.note_content import (
    ALLOWED_FONT_SIZES,
    document_plain_text,
    safe_link_href,
    validate_note_document,
)
from app.services.note_files import (
    NOTE_IMAGE_MAX_HEIGHT,
    NOTE_IMAGE_MAX_WIDTH,
    NOTE_TOTAL_ATTACHMENT_BYTES,
    validate_note_upload,
)
from app.services.notes import create_note

MAX_DOCX_BYTES = 20 * 1024 * 1024
MAX_DOCX_ENTRIES = 2000
MAX_XML_PART_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGES = 200
# Pixels Pillow may decode when an image has to be converted or scaled down.
MAX_CONVERT_PIXELS = 40_000_000
MAX_TABLE_SIZE = 20

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
V = "{urn:schemas-microsoft-com:vml}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"

HEADING_STYLE_RE = re.compile(r"^heading (\d)$")
ALIGNMENTS = {
    "center": "center",
    "right": "right",
    "end": "right",
    "both": "justify",
    "distribute": "justify",
}
LIST_TYPES = {"lowerLetter": "a", "upperLetter": "A", "lowerRoman": "i", "upperRoman": "I"}
FONT_MAP = {
    "arial": "Arial",
    "helvetica": "Arial",
    "georgia": "Georgia",
    "cambria": "Georgia",
    "garamond": "Georgia",
    "book antiqua": "Georgia",
    "times new roman": "Times New Roman",
    "times": "Times New Roman",
    "courier new": "Courier New",
    "courier": "Courier New",
    "consolas": "Courier New",
    "lucida console": "Courier New",
}
FONT_SIZES_PX = sorted(int(size.removesuffix("px")) for size in ALLOWED_FONT_SIZES)
HIGHLIGHT_COLORS = {
    "yellow": "#ffff00",
    "green": "#00ff00",
    "cyan": "#00ffff",
    "magenta": "#ff00ff",
    "blue": "#0000ff",
    "red": "#ff0000",
    "darkBlue": "#000080",
    "darkCyan": "#008080",
    "darkGreen": "#008000",
    "darkMagenta": "#800080",
    "darkRed": "#800000",
    "darkYellow": "#808000",
    "darkGray": "#808080",
    "lightGray": "#c0c0c0",
    "black": "#000000",
}
IMAGE_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
HEX_RE = re.compile(r"^[0-9a-fA-F]{6}$")


def _val(element, default=None):
    return element.get(f"{W}val", default) if element is not None else default


def _on(element):
    """A WordprocessingML toggle such as <w:b/> or <w:b w:val="0"/>."""
    return _val(element, "true").lower() not in {"0", "false", "off", "none"}


def _hex(value):
    """A w:color/w:fill value as #rrggbb, or None for auto and pure black/white (which only fight
    Lunora's light and dark themes)."""
    if not value or not HEX_RE.match(value) or value.lower() in {"000000", "ffffff"}:
        return None
    return f"#{value.lower()}"


def _run_props(rpr):
    props = {}
    if rpr is None:
        return props
    for tag, key in (("b", "bold"), ("i", "italic"), ("strike", "strike"), ("dstrike", "strike")):
        if (element := rpr.find(W + tag)) is not None:
            props[key] = _on(element)
    if (element := rpr.find(W + "u")) is not None:
        props["underline"] = _val(element, "single") != "none"
    if (element := rpr.find(W + "vertAlign")) is not None:
        props["vert"] = _val(element)
    if (element := rpr.find(W + "color")) is not None:
        props["color"] = _hex(_val(element))
    if (element := rpr.find(W + "sz")) is not None and (_val(element) or "").isdigit():
        props["size"] = int(_val(element))
    if (element := rpr.find(W + "rFonts")) is not None:
        props["font"] = element.get(f"{W}ascii") or element.get(f"{W}hAnsi")
    if (element := rpr.find(W + "highlight")) is not None:
        props["highlight"] = HIGHLIGHT_COLORS.get(_val(element))
    elif (element := rpr.find(W + "shd")) is not None:
        props["highlight"] = _hex(element.get(f"{W}fill"))
    return props


def _font_size(half_points):
    px = half_points / 2 * 4 / 3
    return f"{min(FONT_SIZES_PX, key=lambda size: abs(size - px))}px"


class _Converter:
    def __init__(self, archive):
        self.archive = archive
        self.budget = NOTE_TOTAL_ATTACHMENT_BYTES
        self.images = []  # (file_id, name, content_type, bytes)
        self.image_ids = {}  # media path → file_id, so a repeated picture is stored once
        self.skipped_images = 0
        self.title = None
        self.counters = {}
        self.rels = self._relationships()
        self.styles, self.default_style = self._styles()
        self.numbering = self._numbering()
        defaults = self._xml("word/styles.xml")
        doc_rpr = defaults.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr") if defaults is not None else None
        self.base_props = _run_props(doc_rpr)
        for style in self._chain(self.default_style):
            self.base_props.update(style["rpr"])

    # ---- package parts ----

    def _read(self, name, limit):
        try:
            info = self.archive.getinfo(name)
        except KeyError:
            return None
        if info.file_size > limit or info.file_size > self.budget:
            raise ValidationError("Die Word-Datei enthält zu große Bestandteile.")
        with self.archive.open(info) as handle:
            data = handle.read(limit + 1)
        # The declared size can lie, so the real one is checked again.
        if len(data) > limit or len(data) > self.budget:
            raise ValidationError("Die Word-Datei enthält zu große Bestandteile.")
        self.budget -= len(data)
        return data

    def _xml(self, name):
        data = self._read(name, MAX_XML_PART_BYTES)
        if data is None:
            return None
        if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
            raise ValidationError("Die Word-Datei enthält nicht erlaubte XML-Deklarationen.")
        try:
            return ElementTree.fromstring(data)
        except ElementTree.ParseError as error:
            raise ValidationError("Die Word-Datei ist beschädigt.") from error

    def _relationships(self):
        root = self._xml("word/_rels/document.xml.rels")
        rels = {}
        for rel in root.iter(f"{PR}Relationship") if root is not None else ():
            rels[rel.get("Id")] = (rel.get("Target") or "", rel.get("TargetMode") == "External")
        return rels

    def _styles(self):
        root = self._xml("word/styles.xml")
        styles, default = {}, None
        for style in root.iter(f"{W}style") if root is not None else ():
            style_id = style.get(f"{W}styleId")
            ppr = style.find(W + "pPr")
            styles[style_id] = {
                "name": (_val(style.find(W + "name")) or "").lower(),
                "based_on": _val(style.find(W + "basedOn")),
                "rpr": _run_props(style.find(W + "rPr")),
                "ppr": ppr,
            }
            if style.get(f"{W}type") == "paragraph" and _on_attr(style.get(f"{W}default")):
                default = style_id
        return styles, default

    def _chain(self, style_id):
        """The style and its basedOn ancestors, base first."""
        chain = []
        while style_id in self.styles and len(chain) < 20:
            style = self.styles[style_id]
            if style in chain:
                break
            chain.insert(0, style)
            style_id = style["based_on"]
        return chain

    def _numbering(self):
        root = self._xml("word/numbering.xml")
        if root is None:
            return {}
        abstract = {}
        for item in root.iter(f"{W}abstractNum"):
            levels = {}
            for level in item.iter(f"{W}lvl"):
                start = _val(level.find(W + "start"), "1")
                levels[level.get(f"{W}ilvl")] = (
                    _val(level.find(W + "numFmt"), "decimal"),
                    int(start) if start.isdigit() else 1,
                )
            abstract[item.get(f"{W}abstractNumId")] = levels
        numbering = {}
        for num in root.iter(f"{W}num"):
            levels = dict(abstract.get(_val(num.find(W + "abstractNumId")), {}))
            for override in num.iter(f"{W}lvlOverride"):
                start = _val(override.find(W + "startOverride"))
                ilvl = override.get(f"{W}ilvl")
                if start and start.isdigit() and ilvl in levels:
                    levels[ilvl] = (levels[ilvl][0], int(start))
            numbering[num.get(f"{W}numId")] = levels
        return numbering

    # ---- blocks ----

    def blocks(self, container):
        blocks, list_stack = [], []
        for child in container:
            if child.tag == W + "p":
                self._paragraph(child, blocks, list_stack)
                continue
            list_stack.clear()
            if child.tag == W + "tbl":
                blocks.extend(self._table(child))
            elif child.tag == W + "sdt" and (content := child.find(W + "sdtContent")) is not None:
                blocks.extend(self.blocks(content))
            elif child.tag == W + "customXml":
                blocks.extend(self.blocks(child))
        return blocks

    def _paragraph(self, paragraph, blocks, list_stack):
        ppr = paragraph.find(W + "pPr")
        style_id = _val(ppr.find(W + "pStyle")) if ppr is not None else None
        chain = self._chain(style_id or self.default_style)
        names = [style["name"] for style in chain]
        pprs = [style["ppr"] for style in chain if style["ppr"] is not None] + (
            [ppr] if ppr is not None else []
        )

        level = None
        for name in names:
            if match := HEADING_STYLE_RE.match(name):
                level = int(match.group(1))
            elif name == "title":
                level = 0
        outline = _last(pprs, "outlineLvl")
        if (
            level is None
            and outline is not None
            and (_val(outline) or "").isdigit()
            and int(_val(outline)) < 9
        ):
            level = int(_val(outline)) + 1
        is_heading = level is not None

        if is_heading:
            props = {}
        else:
            props = dict(self.base_props)
            for style in chain:
                props.update(style["rpr"])
        inline, extra = self._inline(paragraph, props, is_heading, link=None)
        align = ALIGNMENTS.get(_val(_last(pprs, "jc")))

        if level == 0 and self.title is None and (text := _inline_text(inline).strip()):
            self.title = text[:200]
            list_stack.clear()
            blocks.extend(extra)
            return

        segments = _split_images(inline)
        first = segments[0] if segments and segments[0]["type"] != "noteImage" else None
        if first is not None:
            segments = segments[1:]
        if is_heading:
            node = {"type": "heading", "attrs": {"level": min(max(level, 1), 3)}}
        else:
            node = {"type": "paragraph"}
        if align:
            node.setdefault("attrs", {})["textAlign"] = align
        if first is not None and first.get("content"):
            node["content"] = first["content"]
        rest = [
            _with_attrs(segment, node) if segment["type"] == "paragraph" else segment for segment in segments
        ]

        num_pr = _last(pprs, "numPr")
        num_id = _val(num_pr.find(W + "numId")) if num_pr is not None else None
        if not is_heading and num_id and num_id != "0" and num_id in self.numbering:
            ilvl = _val(num_pr.find(W + "ilvl"), "0")
            self._list_item(blocks, list_stack, num_id, ilvl, [node, *rest, *extra])
            return

        list_stack.clear()
        quote = any(name in {"quote", "intense quote"} for name in names)
        produced = ([node] if first is not None or not rest else []) + rest + extra
        if quote and produced:
            if blocks and blocks[-1]["type"] == "blockquote":
                blocks[-1]["content"].extend(produced)
            else:
                blocks.append({"type": "blockquote", "content": produced})
        else:
            blocks.extend(produced)

    def _list_item(self, blocks, stack, num_id, ilvl, content):
        levels = self.numbering[num_id]
        number_format, start = levels.get(ilvl, ("decimal", 1))
        ordered = number_format not in {"bullet", "none"}
        level = int(ilvl) if ilvl.isdigit() else 0
        key = (num_id, ordered)
        while stack and stack[-1]["level"] > level:
            stack.pop()
        if stack and stack[-1]["level"] == level and stack[-1]["key"] != key:
            stack.pop()
        # Word numbers per list and level across interruptions, and restarts deeper levels.
        counters = self.counters.setdefault(num_id, {})
        for deeper in [item for item in counters if item > level]:
            del counters[deeper]
        number = counters.get(level, start - 1) + 1
        counters[level] = number
        if not stack or stack[-1]["level"] < level:
            node = {"type": "orderedList" if ordered else "bulletList", "content": []}
            if ordered:
                attrs = {}
                if number != 1:
                    attrs["start"] = number
                if number_format in LIST_TYPES:
                    attrs["type"] = LIST_TYPES[number_format]
                if attrs:
                    node["attrs"] = attrs
            if stack:
                stack[-1]["node"]["content"][-1]["content"].append(node)
            else:
                blocks.append(node)
            stack.append({"level": level, "key": key, "node": node})
        stack[-1]["node"]["content"].append({"type": "listItem", "content": content})

    def _table(self, table):
        grid = [
            min(max(int(width) // 15, 20), 2000)
            for column in table.findall(f"{W}tblGrid/{W}gridCol")
            if (width := column.get(f"{W}w", "")).isdigit()
        ]
        rows = []
        for row in table.findall(W + "tr"):
            header = row.find(f"{W}trPr/{W}tblHeader") is not None
            cells, column = [], 0
            for cell in row.findall(W + "tc"):
                tcpr = cell.find(W + "tcPr")
                span_value = _val(tcpr.find(W + "gridSpan")) if tcpr is not None else None
                span = min(max(int(span_value), 1), MAX_TABLE_SIZE) if (span_value or "").isdigit() else 1
                merge = tcpr.find(W + "vMerge") if tcpr is not None else None
                shading = tcpr.find(W + "shd") if tcpr is not None else None
                cells.append(
                    {
                        "cell": cell,
                        "column": column,
                        "span": span,
                        "header": header,
                        "merge": None if merge is None else _val(merge, "continue"),
                        "fill": _hex(shading.get(f"{W}fill")) if shading is not None else None,
                    }
                )
                column += span
            rows.append(cells)
        if not rows:
            return []
        if any(len(cells) > MAX_TABLE_SIZE for cells in rows):
            # Wider than the editor allows: one line per row instead of a broken table.
            return [
                {"type": "paragraph", "content": [{"type": "text", "text": text}]}
                for cells in rows
                if (
                    text := " | ".join(
                        document_plain_text({"type": "doc", "content": self.blocks(item["cell"])})
                        for item in cells
                    ).strip()
                )
            ]
        tables = []
        # Longer tables are split, since a note table holds at most 20 rows.
        for chunk_start in range(0, len(rows), MAX_TABLE_SIZE):
            open_merges, table_rows = {}, []
            for cells in rows[chunk_start : chunk_start + MAX_TABLE_SIZE]:
                row_nodes = []
                for item in cells:
                    if item["merge"] == "continue" and item["column"] in open_merges:
                        merged = open_merges[item["column"]]
                        merged["rowspan"] = merged.get("rowspan", 1) + 1
                        continue
                    attrs = {}
                    if item["span"] > 1:
                        attrs["colspan"] = item["span"]
                    widths = grid[item["column"] : item["column"] + item["span"]]
                    if len(widths) == item["span"]:
                        attrs["colwidth"] = widths
                    if item["fill"]:
                        attrs["backgroundColor"] = item["fill"]
                    if item["merge"] == "restart":
                        open_merges[item["column"]] = attrs
                    else:
                        open_merges.pop(item["column"], None)
                    row_nodes.append(
                        {
                            "type": "tableHeader" if item["header"] else "tableCell",
                            "attrs": attrs,
                            "content": self.blocks(item["cell"]) or [{"type": "paragraph"}],
                        }
                    )
                if row_nodes:
                    table_rows.append({"type": "tableRow", "content": row_nodes})
            if table_rows:
                tables.append({"type": "table", "content": table_rows})
        return tables

    # ---- inline content ----

    def _inline(self, element, props, is_heading, link):
        """Text nodes, hard breaks and image nodes of a paragraph, plus blocks (text boxes) to put
        after it."""
        nodes, extra = [], []
        for child in element:
            tag = child.tag
            if tag == W + "r":
                self._run(child, props, is_heading, link, nodes, extra)
            elif tag == W + "hyperlink":
                target, external = self.rels.get(child.get(f"{R}id"), ("", False))
                href = safe_link_href(target) if external else None
                child_nodes, child_extra = self._inline(child, props, is_heading, href or link)
                nodes += child_nodes
                extra += child_extra
            elif tag == W + "fldSimple":
                match = re.match(r'\s*HYPERLINK\s+"([^"]+)"', child.get(f"{W}instr", ""))
                href = safe_link_href(match.group(1)) if match else None
                child_nodes, child_extra = self._inline(child, props, is_heading, href or link)
                nodes += child_nodes
                extra += child_extra
            elif tag in {W + "del", W + "moveFrom", W + "pPr", W + "rPr"}:
                continue
            else:
                # Insertions, smart tags, content controls and the like just wrap runs.
                child_nodes, child_extra = self._inline(child, props, is_heading, link)
                nodes += child_nodes
                extra += child_extra
        return nodes, extra

    def _run(self, run, paragraph_props, is_heading, link, nodes, extra):
        props = dict(paragraph_props)
        rpr = run.find(W + "rPr")
        if rpr is not None and (style_id := _val(rpr.find(W + "rStyle"))):
            for style in self._chain(style_id):
                props.update(style["rpr"])
        props.update(_run_props(rpr))
        marks = self._marks(props, is_heading, link)
        for child in run:
            tag = child.tag
            if tag == W + "t":
                _add_text(nodes, child.text or "", marks)
            elif tag == W + "tab":
                _add_text(nodes, "\t", marks)
            elif tag in {W + "noBreakHyphen"}:
                _add_text(nodes, "-", marks)
            elif tag == W + "cr" or (tag == W + "br" and child.get(f"{W}type") in {None, "textWrapping"}):
                nodes.append({"type": "hardBreak"})
            elif tag in {W + "drawing", W + "pict"}:
                self._drawing(child, nodes, extra)
            elif tag == MC + "AlternateContent":
                # The Choice is the modern form; the Fallback repeats it for old readers.
                choice = child.find(MC + "Choice")
                if choice is not None:
                    for item in choice:
                        if item.tag in {W + "drawing", W + "pict"}:
                            self._drawing(item, nodes, extra)

    def _marks(self, props, is_heading, link):
        marks = [{"type": key} for key in ("bold", "italic", "underline", "strike") if props.get(key)]
        if props.get("vert") == "superscript":
            marks.append({"type": "superscript"})
        elif props.get("vert") == "subscript":
            marks.append({"type": "subscript"})
        style = {}
        if props.get("color"):
            style["color"] = props["color"]
        if (font := FONT_MAP.get((props.get("font") or "").lower())) is not None:
            style["fontFamily"] = font
        if not is_heading and props.get("size") and props["size"] != self.base_props.get("size"):
            style["fontSize"] = _font_size(props["size"])
        if style:
            marks.append({"type": "textStyle", "attrs": style})
        if props.get("highlight"):
            marks.append({"type": "highlight", "attrs": {"color": props["highlight"]}})
        if link:
            marks.append({"type": "link", "attrs": {"href": link}})
        return marks

    def _drawing(self, drawing, nodes, extra):
        for textbox in drawing.iter(f"{W}txbxContent"):
            extra.extend(self.blocks(textbox))
        blip = next(drawing.iter(f"{A}blip"), None)
        rel_id = blip.get(f"{R}embed") if blip is not None else None
        width = None
        if rel_id:
            extent = next(drawing.iter(f"{WP}extent"), None)
            if extent is not None and extent.get("cx", "").isdigit():
                width = int(extent.get("cx")) // 9525
            properties = next(drawing.iter(f"{WP}docPr"), None)
            alt = (properties.get("descr") or properties.get("title") or "") if properties is not None else ""
        else:
            imagedata = next(drawing.iter(f"{V}imagedata"), None)
            rel_id = imagedata.get(f"{R}id") if imagedata is not None else None
            alt = (imagedata.get("title") or "") if imagedata is not None else ""
            shape = next(drawing.iter(f"{V}shape"), None)
            if shape is not None and (match := re.search(r"width:\s*([\d.]+)pt", shape.get("style", ""))):
                width = round(float(match.group(1)) * 4 / 3)
        if not rel_id:
            return
        file_id = self._image(rel_id)
        if file_id is None:
            self.skipped_images += 1
            return
        attrs = {"attachmentId": file_id, "alt": alt[:300], "width": min(max(width or 720, 120), 1600)}
        nodes.append({"type": "noteImage", "attrs": attrs})

    def _image(self, rel_id):
        target, external = self.rels.get(rel_id, ("", True))
        if external or not target:
            return None
        path = posixpath.normpath(target[1:] if target.startswith("/") else posixpath.join("word", target))
        if path in self.image_ids:
            return self.image_ids[path]
        if len(self.images) >= MAX_IMAGES:
            return None
        try:
            data = self._read(path, MAX_IMAGE_BYTES)
        except ValidationError:
            return None
        converted = _note_image(posixpath.basename(path), data) if data else None
        if converted is None:
            return None
        name, content = converted
        file_id = str(uuid.uuid4())
        content_type = IMAGE_CONTENT_TYPES[PurePath(name).suffix.lower()]
        self.images.append((file_id, name, content_type, content))
        self.image_ids[path] = file_id
        return file_id


def _on_attr(value):
    return (value or "").lower() in {"1", "true", "on"}


def _last(pprs, tag):
    """The most specific paragraph property: direct formatting wins over the style chain."""
    for ppr in reversed(pprs):
        if (element := ppr.find(W + tag)) is not None:
            return element
    return None


def _add_text(nodes, text, marks):
    if not text:
        return
    if nodes and nodes[-1].get("type") == "text" and nodes[-1].get("marks", []) == marks:
        nodes[-1]["text"] += text
        return
    node = {"type": "text", "text": text}
    if marks:
        node["marks"] = marks
    nodes.append(node)


def _inline_text(nodes):
    return "".join(node.get("text", "") for node in nodes)


def _split_images(inline):
    """noteImage is a block node, so a paragraph with pictures splits around them."""
    segments, current = [], []
    for node in inline:
        if node["type"] == "noteImage":
            if current:
                segments.append({"type": "paragraph", "content": current})
            elif not segments:
                segments.append({"type": "paragraph"})
            segments.append(node)
            current = []
        else:
            current.append(node)
    if current or not segments:
        segments.append({"type": "paragraph", "content": current} if current else {"type": "paragraph"})
    # Drop the empty paragraph a picture-only line would leave behind.
    if len(segments) > 1 and segments[0]["type"] == "paragraph" and not segments[0].get("content"):
        segments = segments[1:]
    return segments


def _with_attrs(segment, node):
    if node.get("attrs", {}).get("textAlign") and segment["type"] == "paragraph":
        segment["attrs"] = {"textAlign": node["attrs"]["textAlign"]}
    return segment


def _note_image(name, data):
    """(file name, bytes) of an image note attachments accept, converting other formats (GIF, BMP,
    TIFF) and oversized pictures to PNG with Pillow; None when it can't be used."""
    try:
        validate_note_upload(ContentFile(data, name=name), kind="image")
        return name, data
    except ValidationError:
        pass
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_CONVERT_PIXELS:
                return None
            image.thumbnail((NOTE_IMAGE_MAX_WIDTH, NOTE_IMAGE_MAX_HEIGHT))
            if image.mode not in {"RGB", "RGBA", "L", "LA", "P"}:
                image = image.convert("RGBA")
            output = io.BytesIO()
            image.save(output, "PNG")
    except Exception:  # Untrusted image data; Pillow raises many different error types.
        return None
    name = f"{PurePath(name).stem}.png"
    try:
        validate_note_upload(ContentFile(output.getvalue(), name=name), kind="image")
    except ValidationError:
        return None
    return name, output.getvalue()


def docx_to_note(data, fallback_title):
    """Return (title, document, images, skipped image count) for a .docx file's bytes.

    images are (file_id, name, content_type, bytes) tuples the document's noteImage nodes refer to.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as error:
        raise ValidationError("Die Datei ist kein gültiges Word-Dokument (.docx).") from error
    with archive:
        if len(archive.infolist()) > MAX_DOCX_ENTRIES:
            raise ValidationError("Die Word-Datei enthält zu viele Bestandteile.")
        try:
            converter = _Converter(archive)
            root = converter._xml("word/document.xml")
            body = root.find(W + "body") if root is not None else None
            if body is None:
                raise ValidationError("Die Datei ist kein gültiges Word-Dokument (.docx).")
            content = converter.blocks(body)
        except RecursionError as error:
            raise ValidationError("Die Word-Datei ist zu tief verschachtelt.") from error
        except (zipfile.BadZipFile, OSError, EOFError, zlib.error, RuntimeError) as error:
            # RuntimeError: encrypted entries; zlib.error/EOFError: broken compressed data.
            raise ValidationError("Die Word-Datei ist beschädigt.") from error
    title = converter.title or fallback_title
    document = {"type": "doc", "content": content or [{"type": "paragraph"}]}
    return title[:200], document, converter.images, converter.skipped_images


def import_docx_note(user, filename, data):
    """Create a note from a .docx file; returns (note, number of images that couldn't be used)."""
    title, document, images, skipped = docx_to_note(
        data, PurePath(filename).stem.strip() or "Importierte Notiz"
    )
    validate_note_document(document)
    saved = []
    try:
        with transaction.atomic():
            note = create_note(user, title=title)
            for file_id, name, content_type, content in images:
                attachment = NoteAttachment(
                    note=note,
                    uploaded_by=user,
                    file_id=file_id,
                    kind=NoteAttachment.KIND_IMAGE,
                    original_name=name[:255],
                    content_type=content_type,
                    size=len(content),
                )
                attachment.file.save(name, ContentFile(content), save=False)
                saved.append(attachment.file)
                attachment.save()
            note.document = document
            note.plain_text = document_plain_text(document)
            note.save(update_fields=["document", "plain_text"])
    except Exception:
        for file in saved:
            file.delete(save=False)
        raise
    return note, skipped


def import_docx_upload(user, upload):
    """import_docx_note for an uploaded file, with the size checked before anything is read."""
    if upload.size > MAX_DOCX_BYTES:
        raise ValidationError(
            f"Word-Dateien dürfen höchstens {MAX_DOCX_BYTES // (1024 * 1024)} MB groß sein."
        )
    return import_docx_note(user, upload.name or "Word-Dokument.docx", upload.read(MAX_DOCX_BYTES + 1))
