from __future__ import annotations

import re

from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

ESCAPE_RE = re.compile(r"([\\`*_\[\]<>|])")


def note_markdown_filename(note):
    safe_title = slugify(note.title, allow_unicode=True).strip("-_")[:100] or "notiz"
    return f"{safe_title}.md"


def render_note_markdown(note):
    renderer = NoteMarkdownRenderer(note)
    return renderer.render()


class NoteMarkdownRenderer:
    def __init__(self, note):
        self.note = note
        self.attachments = {str(item.file_id).lower(): item for item in note.attachments.all()}
        editor = note.last_edited_by or note.owner
        profile = getattr(editor, "profile", None)
        self.editor_name = (
            getattr(profile, "display_name", "").strip()
            or editor.get_full_name().strip()
            or editor.get_username()
        )

    def render(self):
        lines = [f"# {self.note.title}"]
        updated = timezone.localtime(self.note.updated_at).strftime("%d.%m.%Y, %H:%M")
        lines.append(f"*Zuletzt bearbeitet am {updated} von {self.editor_name}*")
        body = self.blocks(self.note.document.get("content") or [])
        lines.append(body if body else "*Noch kein Inhalt*")
        return "\n\n".join(lines).rstrip() + "\n"

    def blocks(self, nodes, *, list_depth=0):
        parts = [rendered for node in nodes if (rendered := self.block(node, list_depth=list_depth))]
        return "\n\n".join(parts)

    def block(self, node, *, list_depth=0):
        node_type = node.get("type")
        if node_type == "paragraph":
            return self.inline(node.get("content") or [])
        if node_type == "heading":
            level = (node.get("attrs") or {}).get("level", 1)
            return f"{'#' * level} {self.inline(node.get('content') or [])}"
        if node_type == "bulletList":
            return self.list_block(node, ordered=False, list_depth=list_depth)
        if node_type == "orderedList":
            return self.list_block(node, ordered=True, list_depth=list_depth)
        if node_type == "taskList":
            return self.task_list_block(node, list_depth=list_depth)
        if node_type == "blockquote":
            inner = self.blocks(node.get("content") or [], list_depth=list_depth)
            return "\n".join(f"> {line}" if line else ">" for line in inner.split("\n"))
        if node_type == "codeBlock":
            return self.code_block(node)
        if node_type == "horizontalRule":
            return "---"
        if node_type == "table":
            return self.table_block(node)
        if node_type == "noteImage":
            return self.image_block(node)
        if node_type == "noteAttachment":
            return self.attachment_block(node)
        if node_type == "mathBlock":
            return f"$${(node.get('attrs') or {}).get('latex', '')}$$"
        return ""

    def list_block(self, node, *, ordered, list_depth):
        start = (node.get("attrs") or {}).get("start") or 1
        lines = []
        for index, item in enumerate(node.get("content") or []):
            marker = f"{start + index}." if ordered else "-"
            indent = " " * (len(marker) + 1)
            item_lines = self._wrapped_item_lines(item, list_depth=list_depth)
            first, *rest = item_lines or [""]
            lines.append(f"{marker} {first}".rstrip())
            for line in rest:
                lines.append(f"{indent}{line}" if line else "")
        return "\n".join(lines)

    def task_list_block(self, node, *, list_depth):
        lines = []
        for item in node.get("content") or []:
            checked = bool((item.get("attrs") or {}).get("checked"))
            marker = "- [x]" if checked else "- [ ]"
            item_lines = self._wrapped_item_lines(item, list_depth=list_depth)
            first, *rest = item_lines or [""]
            lines.append(f"{marker} {first}".rstrip())
            for line in rest:
                lines.append(f"      {line}" if line else "")
        return "\n".join(lines)

    def _wrapped_item_lines(self, item, *, list_depth):
        rendered = self.blocks(item.get("content") or [], list_depth=list_depth + 1)
        return rendered.split("\n") if rendered else []

    def code_block(self, node):
        language = (node.get("attrs") or {}).get("language") or ""
        if language == "plaintext":
            language = ""
        text = "".join(
            child.get("text", "") for child in node.get("content") or [] if child.get("type") == "text"
        )
        fence = self._code_fence(text)
        return f"{fence}{language}\n{text}\n{fence}"

    def table_block(self, node):
        rows = node.get("content") or []
        if not rows:
            return ""
        grid = []
        for row in rows:
            cells = []
            for cell in row.get("content") or []:
                text = self.blocks(cell.get("content") or []).replace("\n", "<br>")
                cells.append(text or " ")
            grid.append(cells)
        max_columns = max(len(row) for row in grid)
        for row in grid:
            row.extend([" "] * (max_columns - len(row)))
        header, *rest = grid
        lines = [
            "| " + " | ".join(header) + " |",
            "| " + " | ".join(["---"] * max_columns) + " |",
        ]
        lines.extend("| " + " | ".join(row) + " |" for row in rest)
        return "\n".join(lines)

    def image_block(self, node):
        attrs = node.get("attrs") or {}
        attachment = self.attachments.get(str(attrs.get("attachmentId", "")).lower())
        alt = self._escape(str(attrs.get("alt") or attrs.get("title") or ""))
        if not attachment:
            return f"*[Bild nicht verfügbar: {alt or 'ohne Titel'}]*"
        url = reverse("note_attachment_download", args=[attachment.file_id, "inline"])
        return f"![{alt}]({url})"

    def attachment_block(self, node):
        attrs = node.get("attrs") or {}
        attachment = self.attachments.get(str(attrs.get("attachmentId", "")).lower())
        if not attachment:
            name = self._escape(str(attrs.get("name") or "Datei"))
            return f"*Anhang nicht verfügbar: {name}*"
        url = reverse("note_attachment_download", args=[attachment.file_id, "download"])
        name = self._escape(attachment.original_name)
        return f"[📎 {name}]({url}) ({self._file_size(attachment.size)})"

    def inline(self, content):
        parts = []
        for child in content:
            child_type = child.get("type")
            if child_type == "hardBreak":
                parts.append("\\\n")
            elif child_type == "mention":
                label = self._escape((child.get("attrs") or {}).get("label", ""))
                parts.append(f"**@{label}**")
            elif child_type == "noteLink":
                attrs = child.get("attrs") or {}
                label = self._escape(attrs.get("label", ""))
                href = reverse("note_detail", args=[attrs.get("noteId")])
                parts.append(f"[{label}]({href})")
            elif child_type == "mathInline":
                parts.append(f"${(child.get('attrs') or {}).get('latex', '')}$")
            elif child_type == "text":
                parts.append(self._styled_text(child))
        return "".join(parts)

    def _styled_text(self, node):
        text = node.get("text", "")
        marks = {mark.get("type"): mark for mark in node.get("marks") or []}
        if "code" in marks:
            return self._inline_code(text)
        rendered = self._escape(text)
        if "italic" in marks:
            rendered = f"_{rendered}_"
        if "bold" in marks:
            rendered = f"**{rendered}**"
        if "underline" in marks:
            rendered = f"<u>{rendered}</u>"
        if "strike" in marks:
            rendered = f"~~{rendered}~~"
        if "superscript" in marks:
            rendered = f"<sup>{rendered}</sup>"
        if "subscript" in marks:
            rendered = f"<sub>{rendered}</sub>"
        if "highlight" in marks:
            rendered = f"<mark>{rendered}</mark>"
        if "link" in marks:
            href = (marks["link"].get("attrs") or {}).get("href", "")
            rendered = f"[{rendered}]({href})"
        return rendered

    @staticmethod
    def _inline_code(text):
        fence = "`"
        while fence in text:
            fence += "`"
        padding = " " if fence != "`" else ""
        return f"{fence}{padding}{text}{padding}{fence}"

    @staticmethod
    def _code_fence(text):
        longest = current = 0
        for char in text:
            if char == "`":
                current += 1
                longest = max(longest, current)
            else:
                current = 0
        return "`" * max(3, longest + 1)

    @classmethod
    def _escape(cls, text):
        return ESCAPE_RE.sub(r"\\\1", text)

    @staticmethod
    def _file_size(size):
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{round(size / 1024)} KB"
        return f"{size / (1024 * 1024):.1f} MB"
