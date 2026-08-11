import json
import re
from urllib.parse import urlparse

from django.core.exceptions import ValidationError


MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARACTERS = 500_000
MAX_DOCUMENT_DEPTH = 40
MAX_DOCUMENT_NODES = 50_000
MAX_TAGS = 20
MAX_TAG_LENGTH = 30

ALLOWED_NODE_TYPES = {
    "doc",
    "paragraph",
    "text",
    "heading",
    "bulletList",
    "orderedList",
    "listItem",
    "taskList",
    "taskItem",
    "blockquote",
    "codeBlock",
    "hardBreak",
    "horizontalRule",
    "table",
    "tableRow",
    "tableHeader",
    "tableCell",
    "noteImage",
    "noteAttachment",
    "mention",
}
ALLOWED_MARK_TYPES = {"bold", "italic", "underline", "strike", "code", "link", "textStyle", "highlight", "commentThread"}
THREAD_ID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
ALLOWED_CODE_LANGUAGES = {
    "plaintext",
    "javascript",
    "typescript",
    "python",
    "bash",
    "json",
    "css",
    "xml",
    "sql",
    "java",
    "csharp",
    "cpp",
    "c",
    "go",
    "rust",
    "ruby",
    "php",
    "yaml",
    "markdown",
    "diff",
}
ALLOWED_FONTS = {"Inter", "Arial", "Georgia", "Times New Roman", "Courier New"}
ALLOWED_FONT_SIZES = {"10px", "12px", "14px", "16px", "18px", "24px", "32px", "48px"}
ALLOWED_LINE_HEIGHTS = {"1", "1.15", "1.5", "2"}
ALLOWED_ALIGNMENTS = {"left", "center", "right", "justify", None}
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
ATTACHMENT_ID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
TAG_RE = re.compile(r"^[\w-]+$", re.UNICODE)


EMPTY_DOCUMENT = {"type": "doc", "content": [{"type": "paragraph"}]}


def empty_note_document():
    return {"type": "doc", "content": [{"type": "paragraph"}]}


def validate_note_document(document):
    if not isinstance(document, dict) or document.get("type") != "doc":
        raise ValidationError("Das Notizdokument ist ungültig.")
    try:
        encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValidationError("Das Notizdokument enthält ungültige Daten.") from error
    if len(encoded) > MAX_DOCUMENT_BYTES:
        raise ValidationError("Die Notiz ist zu groß.")

    state = {"nodes": 0, "characters": 0, "attachments": set(), "mentions": set(), "comment_threads": set()}
    _validate_node(document, depth=0, state=state)
    if state["characters"] > MAX_TEXT_CHARACTERS:
        raise ValidationError("Die Notiz enthält zu viel Text.")
    return {
        "attachments": state["attachments"],
        "mentions": state["mentions"],
        "comment_threads": state["comment_threads"],
    }


def _validate_node(node, *, depth, state):
    if not isinstance(node, dict) or set(node) - {"type", "attrs", "content", "marks", "text"}:
        raise ValidationError("Das Notizdokument enthält unbekannte Felder.")
    if depth > MAX_DOCUMENT_DEPTH:
        raise ValidationError("Das Notizdokument ist zu tief verschachtelt.")
    state["nodes"] += 1
    if state["nodes"] > MAX_DOCUMENT_NODES:
        raise ValidationError("Das Notizdokument enthält zu viele Elemente.")

    node_type = node.get("type")
    if node_type not in ALLOWED_NODE_TYPES:
        raise ValidationError(f"Das Element „{node_type}“ ist nicht erlaubt.")

    attrs = node.get("attrs") or {}
    if not isinstance(attrs, dict):
        raise ValidationError("Elementattribute sind ungültig.")
    _validate_node_attrs(node_type, attrs, state)

    if node_type == "text":
        text = node.get("text")
        if not isinstance(text, str):
            raise ValidationError("Ein Textelement enthält keinen gültigen Text.")
        state["characters"] += len(text)
        marks = node.get("marks") or []
        if not isinstance(marks, list):
            raise ValidationError("Textformatierungen sind ungültig.")
        for mark in marks:
            _validate_mark(mark, state)
    elif "text" in node or "marks" in node:
        raise ValidationError("Nur Textelemente dürfen Textformatierungen enthalten.")

    content = node.get("content") or []
    if not isinstance(content, list):
        raise ValidationError("Elementinhalt ist ungültig.")
    for child in content:
        _validate_node(child, depth=depth + 1, state=state)

    if node_type == "table":
        rows = [child for child in content if child.get("type") == "tableRow"]
        if len(rows) > 20 or any(len(row.get("content") or []) > 20 for row in rows):
            raise ValidationError("Tabellen dürfen maximal 20×20 Zellen groß sein.")


def _validate_node_attrs(node_type, attrs, state):
    allowed = {
        "paragraph": {"textAlign"},
        "heading": {"level", "textAlign"},
        "orderedList": {"start", "type"},
        "codeBlock": {"language"},
        "taskItem": {"checked"},
        "tableCell": {"colspan", "rowspan", "colwidth", "backgroundColor", "align"},
        "tableHeader": {"colspan", "rowspan", "colwidth", "backgroundColor", "align"},
        "noteImage": {"attachmentId", "alt", "title", "width"},
        "noteAttachment": {"attachmentId", "name", "size"},
        "mention": {"userId", "label"},
    }.get(node_type, set())
    if set(attrs) - allowed:
        raise ValidationError(f"Das Element „{node_type}“ enthält unerlaubte Attribute.")
    if "textAlign" in attrs and attrs["textAlign"] not in ALLOWED_ALIGNMENTS:
        raise ValidationError("Die Textausrichtung ist ungültig.")
    if node_type == "heading" and attrs.get("level") not in {1, 2, 3}:
        raise ValidationError("Es sind nur Überschriften der Ebenen 1 bis 3 erlaubt.")
    if node_type == "codeBlock" and attrs.get("language") not in {None, ""} and attrs.get("language") not in ALLOWED_CODE_LANGUAGES:
        raise ValidationError("Diese Programmiersprache ist nicht erlaubt.")
    if node_type == "taskItem" and not isinstance(attrs.get("checked", False), bool):
        raise ValidationError("Der Checklistenstatus ist ungültig.")
    if node_type in {"tableCell", "tableHeader"}:
        for key in ("colspan", "rowspan"):
            if key in attrs and (not isinstance(attrs[key], int) or not 1 <= attrs[key] <= 20):
                raise ValidationError("Die Tabellenzellengröße ist ungültig.")
        if attrs.get("backgroundColor") and not HEX_COLOR_RE.fullmatch(attrs["backgroundColor"]):
            raise ValidationError("Die Zellenfarbe ist ungültig.")
        if attrs.get("align") not in {None, "left", "center", "right"}:
            raise ValidationError("Die Zellenausrichtung ist ungültig.")
        colwidth = attrs.get("colwidth")
        if colwidth is not None and (
            not isinstance(colwidth, list)
            or len(colwidth) > 20
            or any(not isinstance(width, int) or width < 20 or width > 2000 for width in colwidth)
        ):
            raise ValidationError("Die Spaltenbreite ist ungültig.")
    if node_type in {"noteImage", "noteAttachment"}:
        attachment_id = str(attrs.get("attachmentId", ""))
        if not ATTACHMENT_ID_RE.fullmatch(attachment_id):
            raise ValidationError("Die Dateireferenz ist ungültig.")
        state["attachments"].add(attachment_id.lower())
        if len(str(attrs.get("name", ""))) > 255 or len(str(attrs.get("alt", ""))) > 300:
            raise ValidationError("Dateiname oder Alternativtext ist zu lang.")
        if "width" in attrs and (not isinstance(attrs["width"], int) or not 120 <= attrs["width"] <= 1600):
            raise ValidationError("Die Bildbreite ist ungültig.")
    if node_type == "mention":
        user_id = attrs.get("userId")
        if not isinstance(user_id, int) or user_id <= 0:
            raise ValidationError("Die Erwähnung verweist auf keinen gültigen Nutzer.")
        if not isinstance(attrs.get("label"), str) or not attrs["label"] or len(attrs["label"]) > 100:
            raise ValidationError("Der Erwähnungstext ist ungültig.")
        state["mentions"].add(user_id)


def _validate_mark(mark, state):
    if not isinstance(mark, dict) or set(mark) - {"type", "attrs"}:
        raise ValidationError("Eine Textformatierung ist ungültig.")
    mark_type = mark.get("type")
    if mark_type not in ALLOWED_MARK_TYPES:
        raise ValidationError(f"Die Textformatierung „{mark_type}“ ist nicht erlaubt.")
    attrs = mark.get("attrs") or {}
    if not isinstance(attrs, dict):
        raise ValidationError("Formatierungsattribute sind ungültig.")
    if mark_type == "link":
        if set(attrs) - {"href", "target", "rel", "class", "title"}:
            raise ValidationError("Der Link enthält unerlaubte Attribute.")
        href = str(attrs.get("href", "")).strip()
        parsed = urlparse(href)
        if not parsed.scheme and href and not re.search(r"[\s\\]", href):
            candidate = urlparse(f"https://{href}")
            if candidate.hostname and "." in candidate.hostname:
                href = f"https://{href}"
                attrs["href"] = href
                parsed = candidate
        if parsed.scheme.lower() not in {"http", "https", "mailto"}:
            raise ValidationError("Es sind nur HTTP-, HTTPS- und E-Mail-Links erlaubt.")
        if parsed.scheme.lower() in {"http", "https"} and not parsed.hostname:
            raise ValidationError("Die Link-Adresse ist unvollständig.")
        if parsed.scheme.lower() == "mailto" and "@" not in parsed.path:
            raise ValidationError("Die E-Mail-Link-Adresse ist unvollständig.")
        if attrs.get("target") not in {None, "_blank", "_self"}:
            raise ValidationError("Das Linkziel ist nicht erlaubt.")
        rel_tokens = set(str(attrs.get("rel") or "").split())
        if rel_tokens - {"noopener", "noreferrer", "nofollow"}:
            raise ValidationError("Die Link-Sicherheitsattribute sind nicht erlaubt.")
        if attrs.get("class") not in {None, ""}:
            raise ValidationError("Link-CSS-Klassen sind nicht erlaubt.")
        if len(str(attrs.get("title") or "")) > 500:
            raise ValidationError("Der Linktitel ist zu lang.")
    elif mark_type == "textStyle":
        if set(attrs) - {"color", "backgroundColor", "fontFamily", "fontSize", "lineHeight"}:
            raise ValidationError("Der Textstil enthält unerlaubte Attribute.")
        for key in ("color", "backgroundColor"):
            if attrs.get(key) and not HEX_COLOR_RE.fullmatch(attrs[key]):
                raise ValidationError("Eine Textfarbe ist ungültig.")
        if attrs.get("fontFamily") and attrs["fontFamily"] not in ALLOWED_FONTS:
            raise ValidationError("Die Schriftart ist nicht erlaubt.")
        if attrs.get("fontSize") and attrs["fontSize"] not in ALLOWED_FONT_SIZES:
            raise ValidationError("Die Schriftgröße ist nicht erlaubt.")
        if attrs.get("lineHeight") and attrs["lineHeight"] not in ALLOWED_LINE_HEIGHTS:
            raise ValidationError("Der Zeilenabstand ist nicht erlaubt.")
    elif mark_type == "highlight":
        if set(attrs) - {"color"} or (attrs.get("color") and not HEX_COLOR_RE.fullmatch(attrs["color"])):
            raise ValidationError("Die Markierfarbe ist ungültig.")
    elif mark_type == "commentThread":
        thread_id = str(attrs.get("threadId", ""))
        if set(attrs) - {"threadId"} or not THREAD_ID_RE.fullmatch(thread_id):
            raise ValidationError("Der Kommentarbezug ist ungültig.")
        state["comment_threads"].add(thread_id.lower())
    elif attrs:
        raise ValidationError(f"Die Textformatierung „{mark_type}“ darf keine Attribute enthalten.")


def document_plain_text(document):
    parts = []

    def visit(node):
        if node.get("type") == "text":
            parts.append(node.get("text", ""))
        elif node.get("type") == "mention":
            parts.append(f"@{(node.get('attrs') or {}).get('label', '')}")
        elif node.get("type") in {"hardBreak", "paragraph", "heading", "listItem", "taskItem", "tableRow"}:
            if parts and not parts[-1].endswith("\n"):
                parts.append("\n")
        for child in node.get("content") or []:
            visit(child)

    visit(document)
    return re.sub(r"\n{3,}", "\n\n", "".join(parts)).strip()


def extract_mention_user_ids(document):
    """Collect mention userIds from an already-trusted (previously validated) document tree."""
    user_ids = set()

    def visit(node):
        if not isinstance(node, dict):
            return
        if node.get("type") == "mention":
            user_id = (node.get("attrs") or {}).get("userId")
            if isinstance(user_id, int):
                user_ids.add(user_id)
        for child in node.get("content") or []:
            visit(child)

    visit(document)
    return user_ids


def normalize_tags(tags):
    if not isinstance(tags, list):
        raise ValidationError("Hashtags müssen als Liste gesendet werden.")
    normalized = []
    seen = set()
    for raw_tag in tags:
        display = str(raw_tag).strip().lstrip("#")
        key = display.casefold()
        if not display or len(display) > MAX_TAG_LENGTH or len(key) > MAX_TAG_LENGTH or not TAG_RE.fullmatch(display):
            raise ValidationError("Hashtags dürfen nur Buchstaben, Zahlen, Unterstriche und Bindestriche enthalten.")
        if key not in seen:
            seen.add(key)
            normalized.append((key, display))
    if len(normalized) > MAX_TAGS:
        raise ValidationError(f"Eine Notiz darf maximal {MAX_TAGS} Hashtags haben.")
    return normalized
