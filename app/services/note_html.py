"""Read-only HTML rendering of a note document for its public link.

Works like note_markdown.py on the same (already validated) Tiptap JSON, but is a trust boundary of
its own: every text and attribute is escaped, and styles, colors, alignments, languages and link
schemes are re-checked against note_content's allowlists, so even a document that somehow bypassed
validation can't inject markup. Mentions and note links render as plain labels (their targets are
private), comment marks are dropped, and formulas become [data-latex] elements that note_public.js
renders with KaTeX.
"""

from urllib.parse import urlparse

from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

from app.services.note_content import (
    ALLOWED_ALIGNMENTS,
    ALLOWED_CODE_LANGUAGES,
    ALLOWED_FONT_SIZES,
    ALLOWED_FONTS,
    ALLOWED_LINE_HEIGHTS,
    HEX_COLOR_RE,
)
from app.services.note_markdown import NoteMarkdownRenderer

INLINE_MARK_TAGS = {
    "bold": "strong",
    "italic": "em",
    "underline": "u",
    "strike": "s",
    "superscript": "sup",
    "subscript": "sub",
    "code": "code",
}


def render_note_html(note, *, attachment_url):
    """The note body as safe HTML. `attachment_url(attachment, disposition)` builds file links."""
    return NoteHtmlRenderer(note, attachment_url=attachment_url).render()


class NoteHtmlRenderer:
    def __init__(self, note, *, attachment_url):
        self.attachment_url = attachment_url
        self.attachments = {str(item.file_id).lower(): item for item in note.attachments.all()}
        self.document = note.document if isinstance(note.document, dict) else {}

    def render(self):
        return mark_safe(self.blocks(self.document.get("content") or []))  # every part is escaped above

    def blocks(self, nodes):
        return "".join(self.block(node) for node in nodes if isinstance(node, dict))

    def block(self, node):
        node_type = node.get("type")
        attrs = node.get("attrs") or {}
        content = node.get("content") or []
        if node_type == "paragraph":
            return f"<p{self._align(attrs)}>{self.inline(content)}</p>"
        if node_type == "heading":
            # The note title is the page's h1, so note headings start one level lower.
            level = attrs.get("level") if attrs.get("level") in {1, 2, 3} else 1
            return f"<h{level + 1}{self._align(attrs)}>{self.inline(content)}</h{level + 1}>"
        if node_type == "bulletList":
            return f"<ul>{self.blocks(content)}</ul>"
        if node_type == "orderedList":
            start = attrs.get("start")
            start_attr = f' start="{start}"' if isinstance(start, int) and start > 1 else ""
            return f"<ol{start_attr}>{self.blocks(content)}</ol>"
        if node_type == "listItem":
            return f"<li>{self.blocks(content)}</li>"
        if node_type == "taskList":
            return f'<ul class="task-list">{self.blocks(content)}</ul>'
        if node_type == "taskItem":
            checked = attrs.get("checked") is True
            label = "erledigt" if checked else "offen"
            return (
                f'<li class="task-item{" is-checked" if checked else ""}">'
                f'<input type="checkbox" disabled{" checked" if checked else ""} aria-label="{label}">'
                f"<div>{self.blocks(content)}</div></li>"
            )
        if node_type == "blockquote":
            return f"<blockquote>{self.blocks(content)}</blockquote>"
        if node_type == "codeBlock":
            language = attrs.get("language")
            language_attr = (
                f' class="language-{language}"' if language in ALLOWED_CODE_LANGUAGES - {"plaintext"} else ""
            )
            text = "".join(
                child.get("text", "")
                for child in content
                if isinstance(child, dict) and child.get("type") == "text"
            )
            return f"<pre><code{language_attr}>{escape(text)}</code></pre>"
        if node_type == "horizontalRule":
            return "<hr>"
        if node_type == "table":
            return f'<div class="table-scroll"><table><tbody>{self.blocks(content)}</tbody></table></div>'
        if node_type == "tableRow":
            return f"<tr>{self.blocks(content)}</tr>"
        if node_type in {"tableHeader", "tableCell"}:
            return self.table_cell(node_type, attrs, content)
        if node_type == "noteImage":
            return self.image(attrs)
        if node_type == "noteAttachment":
            return self.attachment(attrs)
        if node_type == "mathBlock":
            return format_html(
                '<div class="math-block" data-latex="{}" data-display="block">{}</div>',
                attrs.get("latex", ""),
                attrs.get("latex", ""),
            )
        return ""

    def table_cell(self, node_type, attrs, content):
        tag = "th" if node_type == "tableHeader" else "td"
        parts = []
        for key in ("colspan", "rowspan"):
            value = attrs.get(key)
            if isinstance(value, int) and 1 < value <= 20:
                parts.append(f' {key}="{value}"')
        styles = []
        if HEX_COLOR_RE.fullmatch(str(attrs.get("backgroundColor") or "")):
            styles.append(f"background-color: {attrs['backgroundColor']}")
        if attrs.get("align") in {"left", "center", "right"}:
            styles.append(f"text-align: {attrs['align']}")
        if styles:
            parts.append(f' style="{"; ".join(styles)}"')
        return f"<{tag}{''.join(parts)}>{self.blocks(content)}</{tag}>"

    def image(self, attrs):
        attachment = self.attachments.get(str(attrs.get("attachmentId", "")).lower())
        alt = str(attrs.get("alt") or attrs.get("title") or "")
        if attachment is None or attachment.kind != "image":
            return format_html(
                '<p class="missing-file">Bild nicht verfügbar{}</p>', f": {alt}" if alt else ""
            )
        width = attrs.get("width")
        style = f' style="width: {width}px"' if isinstance(width, int) and 120 <= width <= 1600 else ""
        return format_html(
            '<figure><img src="{}" alt="{}" loading="lazy"{}></figure>',
            self.attachment_url(attachment, "inline"),
            alt,
            mark_safe(style),  # built from a range-checked integer
        )

    def attachment(self, attrs):
        attachment = self.attachments.get(str(attrs.get("attachmentId", "")).lower())
        if attachment is None:
            return format_html(
                '<p class="missing-file">Anhang nicht verfügbar: {}</p>', attrs.get("name") or "Datei"
            )
        return format_html(
            '<p class="note-file"><a href="{}" download><i class="fa-solid fa-paperclip" aria-hidden="true"></i> '
            "{}</a> <small>{}</small></p>",
            self.attachment_url(attachment, "download"),
            attachment.original_name,
            NoteMarkdownRenderer._file_size(attachment.size),
        )

    def inline(self, content):
        parts = []
        for child in content:
            if not isinstance(child, dict):
                continue
            child_type = child.get("type")
            attrs = child.get("attrs") or {}
            if child_type == "text":
                parts.append(self.text(child))
            elif child_type == "hardBreak":
                parts.append("<br>")
            elif child_type == "mention":
                parts.append(format_html('<span class="mention">@{}</span>', attrs.get("label", "")))
            elif child_type == "noteLink":
                parts.append(format_html('<span class="note-link">{}</span>', attrs.get("label", "")))
            elif child_type == "mathInline":
                parts.append(
                    format_html(
                        '<span class="math-inline" data-latex="{}">{}</span>',
                        attrs.get("latex", ""),
                        attrs.get("latex", ""),
                    )
                )
        return "".join(parts)

    def text(self, node):
        rendered = escape(node.get("text", ""))
        marks = [mark for mark in node.get("marks") or [] if isinstance(mark, dict)]
        for mark in marks:
            mark_type = mark.get("type")
            attrs = mark.get("attrs") or {}
            if mark_type in INLINE_MARK_TAGS:
                tag = INLINE_MARK_TAGS[mark_type]
                rendered = f"<{tag}>{rendered}</{tag}>"
            elif mark_type == "highlight":
                color = attrs.get("color")
                style = (
                    f' style="background-color: {color}"' if HEX_COLOR_RE.fullmatch(str(color or "")) else ""
                )
                rendered = f"<mark{style}>{rendered}</mark>"
            elif mark_type == "textStyle":
                style = _text_style(attrs)
                if style:
                    rendered = f'<span style="{style}">{rendered}</span>'
        # The link wraps everything else, so a link over bold text stays one link.
        link = next((mark for mark in marks if mark.get("type") == "link"), None)
        if link:
            href = _safe_href((link.get("attrs") or {}).get("href"))
            if href:
                rendered = format_html(
                    '<a href="{}" target="_blank" rel="noopener noreferrer nofollow">{}</a>',
                    href,
                    mark_safe(rendered),  # already escaped above
                )
        return rendered

    @staticmethod
    def _align(attrs):
        align = attrs.get("textAlign")
        if align in ALLOWED_ALIGNMENTS and align not in {None, "left"}:
            return f' style="text-align: {align}"'
        return ""


def _text_style(attrs):
    styles = []
    if HEX_COLOR_RE.fullmatch(str(attrs.get("color") or "")):
        styles.append(f"color: {attrs['color']}")
    if HEX_COLOR_RE.fullmatch(str(attrs.get("backgroundColor") or "")):
        styles.append(f"background-color: {attrs['backgroundColor']}")
    if attrs.get("fontFamily") in ALLOWED_FONTS:
        styles.append(f"font-family: '{attrs['fontFamily']}'")
    if attrs.get("fontSize") in ALLOWED_FONT_SIZES:
        styles.append(f"font-size: {attrs['fontSize']}")
    if attrs.get("lineHeight") in ALLOWED_LINE_HEIGHTS:
        styles.append(f"line-height: {attrs['lineHeight']}")
    return "; ".join(styles)


def _safe_href(href):
    href = str(href or "").strip()
    parsed = urlparse(href)
    if parsed.scheme.lower() in {"http", "https"} and parsed.hostname:
        return href
    if parsed.scheme.lower() == "mailto" and "@" in parsed.path:
        return href
    return None
