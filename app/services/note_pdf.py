from __future__ import annotations

from html import escape
from io import BytesIO

from django.utils import timezone
from django.utils.text import slugify
from PIL import Image as PillowImage
from PIL import UnidentifiedImageError
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Image,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.xpreformatted import XPreformatted


PAGE_WIDTH, PAGE_HEIGHT = A4
PAGE_MARGIN = 19 * mm
CONTENT_WIDTH = PAGE_WIDTH - (2 * PAGE_MARGIN)
TEXT_COLOR = colors.HexColor("#332d27")
MUTED_COLOR = colors.HexColor("#786f67")
ACCENT_COLOR = colors.HexColor("#a67c52")
LIGHT_BORDER = colors.HexColor("#e4ddd4")
SOFT_BACKGROUND = colors.HexColor("#f7f3ed")

FONT_MAP = {
    "Inter": "Helvetica",
    "Arial": "Helvetica",
    "Georgia": "Times-Roman",
    "Times New Roman": "Times-Roman",
    "Courier New": "Courier",
}
ALIGNMENT_MAP = {
    "left": TA_LEFT,
    "center": TA_CENTER,
    "right": TA_RIGHT,
    "justify": TA_JUSTIFY,
}


def note_pdf_filename(note):
    safe_title = slugify(note.title, allow_unicode=True).strip("-_")[:100] or "notiz"
    return f"{safe_title}.pdf"


def render_note_pdf(note):
    stream = BytesIO()
    renderer = NotePdfRenderer(note)
    document = SimpleDocTemplate(
        stream,
        pagesize=A4,
        rightMargin=PAGE_MARGIN,
        leftMargin=PAGE_MARGIN,
        topMargin=18 * mm,
        bottomMargin=19 * mm,
        title=note.title,
        author=renderer.editor_name,
        subject="Lunora Notiz",
        creator="Lunora Notes",
        allowSplitting=True,
    )
    document.build(renderer.story(), onFirstPage=renderer.draw_page, onLaterPages=renderer.draw_page)
    stream.seek(0)
    return stream


class NotePdfRenderer:
    def __init__(self, note):
        self.note = note
        self.attachments = {str(item.file_id).lower(): item for item in note.attachments.all()}
        self._style_counter = 0
        self._image_streams = []
        editor = note.last_edited_by or note.owner
        profile = getattr(editor, "profile", None)
        self.editor_name = (
            getattr(profile, "display_name", "").strip()
            or editor.get_full_name().strip()
            or editor.get_username()
        )
        self.body_style = ParagraphStyle(
            "NoteBody",
            fontName="Helvetica",
            fontSize=12,
            leading=18,
            textColor=TEXT_COLOR,
            spaceAfter=7,
            splitLongWords=True,
            allowWidows=0,
            allowOrphans=0,
        )

    def story(self):
        title_style = ParagraphStyle(
            "NoteTitle",
            parent=self.body_style,
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=27,
            textColor=TEXT_COLOR,
            spaceAfter=6,
            keepWithNext=True,
        )
        meta_style = ParagraphStyle(
            "NoteMeta",
            parent=self.body_style,
            fontSize=8.5,
            leading=12,
            textColor=MUTED_COLOR,
            spaceAfter=4,
            keepWithNext=True,
        )
        story = [Paragraph(escape(self.note.title), title_style)]
        updated = timezone.localtime(self.note.updated_at).strftime("%d.%m.%Y, %H:%M")
        story.append(Paragraph(f"Zuletzt bearbeitet am {updated} von {escape(self.editor_name)}", meta_style))
        tags = [tag.display_name for tag in self.note.tags.all()]
        if tags:
            tag_text = " &nbsp; ".join(f'<font color="#9a6f47">#{escape(tag)}</font>' for tag in tags)
            story.append(Paragraph(tag_text, meta_style))
        story.extend(
            [
                Spacer(1, 5),
                HRFlowable(width="100%", thickness=0.6, color=LIGHT_BORDER, spaceBefore=0, spaceAfter=15),
            ]
        )
        content_flowables = self.nodes(self.note.document.get("content") or [])
        story.extend(content_flowables)
        if not content_flowables:
            story.append(Paragraph("Noch kein Inhalt", self._style(textColor=MUTED_COLOR)))
        return story

    def draw_page(self, canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(LIGHT_BORDER)
        canvas.setLineWidth(0.5)
        canvas.line(PAGE_MARGIN, 13 * mm, PAGE_WIDTH - PAGE_MARGIN, 13 * mm)
        canvas.setFillColor(MUTED_COLOR)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(PAGE_MARGIN, 8.5 * mm, "Lunora Notes")
        canvas.drawRightString(PAGE_WIDTH - PAGE_MARGIN, 8.5 * mm, f"Seite {document.page}")
        canvas.restoreState()

    def nodes(self, nodes, *, compact=False, force_bold=False):
        flowables = []
        for node in nodes:
            node_type = node.get("type")
            if node_type in {"paragraph", "heading"}:
                flowables.append(self.paragraph(node, compact=compact, force_bold=force_bold))
            elif node_type in {"bulletList", "orderedList"}:
                flowables.append(self.list_flowable(node, compact=compact))
            elif node_type == "taskList":
                flowables.extend(self.task_list(node, compact=compact))
            elif node_type == "blockquote":
                flowables.append(self.blockquote(node))
            elif node_type == "codeBlock":
                flowables.append(self.code_block(node))
            elif node_type == "horizontalRule":
                rule = HRFlowable(width="100%", thickness=0.7, color=LIGHT_BORDER, spaceBefore=8, spaceAfter=12)
                rule.keepWithNext = True
                flowables.append(rule)
            elif node_type == "table":
                flowables.append(self.table(node))
            elif node_type == "noteImage":
                flowables.extend(self.image(node))
            elif node_type == "noteAttachment":
                flowables.append(self.attachment(node))
        return flowables

    def paragraph(self, node, *, compact=False, prefix="", force_bold=False):
        node_type = node.get("type")
        attrs = node.get("attrs") or {}
        font_size = 10 if compact else 12
        font_name = "Helvetica"
        leading_factor = 1.5
        if node_type == "heading":
            level = attrs.get("level", 1)
            font_size = {1: 23, 2: 18, 3: 14.5}.get(level, 14.5)
            font_name = "Helvetica-Bold"
            leading_factor = 1.22
        text_style = self._first_text_style(node)
        if node_type != "heading":
            font_name = FONT_MAP.get(text_style.get("fontFamily"), font_name)
            if text_style.get("fontSize"):
                font_size = self._css_size(text_style["fontSize"], font_size)
            if text_style.get("lineHeight"):
                leading_factor = float(text_style["lineHeight"])
        alignment = ALIGNMENT_MAP.get(attrs.get("textAlign"), TA_LEFT)
        style = self._style(
            fontName=font_name,
            fontSize=font_size,
            leading=max(font_size * leading_factor, font_size + 2),
            alignment=alignment,
            spaceBefore=(8 if node_type == "heading" else 0),
            spaceAfter=(6 if compact else 8),
            keepWithNext=(node_type == "heading"),
        )
        markup = prefix + self.inline_markup(node.get("content") or [])
        if force_bold and markup:
            markup = f"<b>{markup}</b>"
        return Paragraph(markup or "&#160;", style)

    def inline_markup(self, content):
        parts = []
        for child in content:
            if child.get("type") == "hardBreak":
                parts.append("<br/>")
                continue
            if child.get("type") != "text":
                continue
            markup = escape(child.get("text", ""))
            for mark in child.get("marks") or []:
                mark_type = mark.get("type")
                attrs = mark.get("attrs") or {}
                if mark_type == "bold":
                    markup = f"<b>{markup}</b>"
                elif mark_type == "italic":
                    markup = f"<i>{markup}</i>"
                elif mark_type == "underline":
                    markup = f"<u>{markup}</u>"
                elif mark_type == "strike":
                    markup = f"<strike>{markup}</strike>"
                elif mark_type == "code":
                    markup = f'<font name="Courier" backColor="#f1ede7">{markup}</font>'
                elif mark_type == "highlight" and attrs.get("color"):
                    markup = f'<font backColor="{escape(attrs["color"], quote=True)}">{markup}</font>'
                elif mark_type == "textStyle":
                    markup = self._text_style_markup(markup, attrs)
                elif mark_type == "link" and attrs.get("href"):
                    href = escape(attrs["href"], quote=True)
                    markup = f'<link href="{href}" color="#8a633e"><u>{markup}</u></link>'
            parts.append(markup)
        return "".join(parts)

    def list_flowable(self, node, *, compact=False):
        ordered = node.get("type") == "orderedList"
        items = []
        for child in node.get("content") or []:
            item_flowables = self.nodes(child.get("content") or [], compact=compact)
            if not item_flowables:
                item_flowables = [Paragraph("&#160;", self._style(fontSize=10 if compact else 12))]
            items.append(ListItem(item_flowables, leftIndent=12))
        list_options = {
            "bulletType": "1" if ordered else "bullet",
            "bulletChar": "•",
        }
        if ordered:
            list_options["start"] = (node.get("attrs") or {}).get("start", 1)
        return ListFlowable(
            items,
            leftIndent=18 if compact else 23,
            bulletFontName="Helvetica",
            bulletFontSize=9 if compact else 11,
            spaceAfter=7,
            **list_options,
        )

    def task_list(self, node, *, compact=False):
        flowables = []
        for item in node.get("content") or []:
            children = item.get("content") or []
            marker = "[x] " if (item.get("attrs") or {}).get("checked") else "[ ] "
            if children and children[0].get("type") == "paragraph":
                flowables.append(self.paragraph(children[0], compact=compact, prefix=f"<b>{marker}</b>"))
                flowables.extend(self.nodes(children[1:], compact=compact))
            else:
                flowables.append(Paragraph(f"<b>{marker}</b>", self._style()))
        return flowables

    def blockquote(self, node):
        content = self.nodes(node.get("content") or []) or [Paragraph("&#160;", self.body_style)]
        table = Table([[content]], colWidths=[CONTENT_WIDTH], hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("LINEBEFORE", (0, 0), (0, -1), 3, ACCENT_COLOR),
                    ("BACKGROUND", (0, 0), (-1, -1), SOFT_BACKGROUND),
                    ("LEFTPADDING", (0, 0), (-1, -1), 14),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                    ("TOPPADDING", (0, 0), (-1, -1), 9),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        return table

    def code_block(self, node):
        text = "".join(child.get("text", "") for child in node.get("content") or [] if child.get("type") == "text")
        code_style = self._style(
            fontName="Courier",
            fontSize=8.5,
            leading=12,
            textColor=TEXT_COLOR,
            spaceAfter=0,
        )
        content = XPreformatted(escape(text) or " ", code_style)
        table = Table([[content]], colWidths=[CONTENT_WIDTH], hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), SOFT_BACKGROUND),
                    ("BOX", (0, 0), (-1, -1), 0.5, LIGHT_BORDER),
                    ("LEFTPADDING", (0, 0), (-1, -1), 11),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 11),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        return table

    def table(self, node):
        rows = node.get("content") or []
        data = [[] for _row in rows]
        occupied = set()
        commands = [
            ("GRID", (0, 0), (-1, -1), 0.55, colors.HexColor("#cfc4b7")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
        max_columns = 0
        has_header_row = bool(rows) and all(
            cell.get("type") == "tableHeader" for cell in (rows[0].get("content") or [])
        )
        for row_index, row in enumerate(rows):
            column = 0
            for cell in row.get("content") or []:
                while (row_index, column) in occupied:
                    column += 1
                attrs = cell.get("attrs") or {}
                colspan = max(1, int(attrs.get("colspan") or 1))
                rowspan = max(1, int(attrs.get("rowspan") or 1))
                while len(data[row_index]) <= column:
                    data[row_index].append("")
                cell_content = self.nodes(
                    cell.get("content") or [],
                    compact=True,
                    force_bold=cell.get("type") == "tableHeader",
                )
                data[row_index][column] = cell_content
                if colspan > 1 or rowspan > 1:
                    final_row = min(len(rows) - 1, row_index + rowspan - 1)
                    commands.append(
                        ("SPAN", (column, row_index), (column + colspan - 1, final_row))
                    )
                for target_row in range(row_index, min(len(rows), row_index + rowspan)):
                    for target_column in range(column, column + colspan):
                        if target_row != row_index or target_column != column:
                            occupied.add((target_row, target_column))
                if cell.get("type") == "tableHeader":
                    commands.extend(
                        [
                            ("BACKGROUND", (column, row_index), (column + colspan - 1, row_index), colors.HexColor("#eee5d9")),
                            ("FONTNAME", (column, row_index), (column + colspan - 1, row_index), "Helvetica-Bold"),
                        ]
                    )
                if attrs.get("backgroundColor"):
                    commands.append(
                        ("BACKGROUND", (column, row_index), (column + colspan - 1, row_index), colors.HexColor(attrs["backgroundColor"]))
                    )
                if attrs.get("align"):
                    commands.append(("ALIGN", (column, row_index), (column + colspan - 1, row_index), attrs["align"].upper()))
                column += colspan
                max_columns = max(max_columns, column)
        max_columns = max(1, max_columns)
        for row in data:
            row.extend([""] * (max_columns - len(row)))
        column_widths = [CONTENT_WIDTH / max_columns] * max_columns
        table = Table(data or [[""]], colWidths=column_widths, repeatRows=1 if has_header_row else 0, hAlign="LEFT")
        table.setStyle(TableStyle(commands))
        table.spaceBefore = 8
        table.spaceAfter = 12
        return table

    def image(self, node):
        attrs = node.get("attrs") or {}
        attachment = self.attachments.get(str(attrs.get("attachmentId", "")).lower())
        alt = str(attrs.get("alt") or "").strip()
        if not attachment:
            return [self._missing_media(alt or "Bild nicht verfügbar")]
        try:
            with attachment.file.open("rb") as source:
                image_bytes = source.read()
            image_stream = BytesIO(image_bytes)
            with PillowImage.open(BytesIO(image_bytes)) as source_image:
                pixel_width, pixel_height = source_image.size
            desired_width = min(float(attrs.get("width") or 720) * 0.75, CONTENT_WIDTH * 0.93)
            desired_height = desired_width * pixel_height / pixel_width
            max_height = PAGE_HEIGHT - 65 * mm
            if desired_height > max_height:
                scale = max_height / desired_height
                desired_width *= scale
                desired_height *= scale
            self._image_streams.append(image_stream)
            exported_image = Image(image_stream, width=desired_width, height=desired_height)
            exported_image.hAlign = "CENTER"
            exported_image.spaceBefore = 8
            exported_image.spaceAfter = 5 if alt else 12
            result = [exported_image]
            if alt:
                result.append(
                    Paragraph(
                        escape(alt),
                        self._style(fontSize=8.5, leading=11, textColor=MUTED_COLOR, alignment=TA_CENTER, spaceAfter=10),
                    )
                )
            return result
        except (OSError, UnidentifiedImageError, ValueError):
            return [self._missing_media(alt or attachment.original_name)]

    def attachment(self, node):
        attrs = node.get("attrs") or {}
        attachment = self.attachments.get(str(attrs.get("attachmentId", "")).lower())
        name = attachment.original_name if attachment else str(attrs.get("name") or "Datei")
        size = attachment.size if attachment else int(attrs.get("size") or 0)
        label = f"Anhang: {escape(name)}"
        if size:
            label += f" ({self._file_size(size)})"
        paragraph = Paragraph(
            f'<font color="#8a633e"><b>{label}</b></font>',
            self._style(fontSize=9.5, leading=13, spaceAfter=0),
        )
        table = Table([[paragraph]], colWidths=[CONTENT_WIDTH], hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), SOFT_BACKGROUND),
                    ("BOX", (0, 0), (-1, -1), 0.5, LIGHT_BORDER),
                    ("LEFTPADDING", (0, 0), (-1, -1), 11),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 11),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        table.spaceBefore = 5
        table.spaceAfter = 8
        return table

    def _missing_media(self, label):
        return Paragraph(
            f'<font color="#8f4d42">[Bild nicht verfügbar: {escape(label)}]</font>',
            self._style(fontSize=9, leading=12, spaceAfter=8),
        )

    def _text_style_markup(self, markup, attrs):
        font_attrs = []
        if attrs.get("color"):
            font_attrs.append(f'color="{escape(attrs["color"], quote=True)}"')
        if attrs.get("backgroundColor"):
            font_attrs.append(f'backColor="{escape(attrs["backgroundColor"], quote=True)}"')
        if attrs.get("fontFamily"):
            font_attrs.append(f'name="{FONT_MAP.get(attrs["fontFamily"], "Helvetica")}"')
        if attrs.get("fontSize"):
            font_attrs.append(f'size="{self._css_size(attrs["fontSize"], 12):g}"')
        return f'<font {" ".join(font_attrs)}>{markup}</font>' if font_attrs else markup

    def _first_text_style(self, node):
        for child in node.get("content") or []:
            if child.get("type") != "text":
                continue
            for mark in child.get("marks") or []:
                if mark.get("type") == "textStyle":
                    return mark.get("attrs") or {}
        return {}

    def _style(self, **overrides):
        self._style_counter += 1
        style = ParagraphStyle(f"NoteStyle{self._style_counter}", parent=self.body_style)
        for key, value in overrides.items():
            setattr(style, key, value)
        return style

    @staticmethod
    def _css_size(value, fallback):
        try:
            return float(str(value).removesuffix("px")) * 0.75
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _file_size(size):
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{round(size / 1024)} KB"
        return f"{size / (1024 * 1024):.1f} MB"
