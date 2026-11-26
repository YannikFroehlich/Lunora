import json
import re
from datetime import date
from urllib.parse import urlparse

from django.core.exceptions import ValidationError

MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARACTERS = 500_000
MAX_DOCUMENT_DEPTH = 40
MAX_DOCUMENT_NODES = 50_000

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
    "noteLink",
    "mathInline",
    "mathBlock",
    "formField",
    "callout",
    "details",
}
ALLOWED_MARK_TYPES = {
    "bold",
    "italic",
    "underline",
    "strike",
    "superscript",
    "subscript",
    "code",
    "link",
    "textStyle",
    "highlight",
    "commentThread",
    # Who wrote the text, set by the editor on shared notes; informational, not a proof.
    "author",
}
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
# Paragraph/heading shading and rules (e.g. from Word): each a #rrggbb color.
BLOCK_BOX_ATTRIBUTES = ("backgroundColor", "borderLeft", "borderBottom")
MAX_LATEX_LENGTH = 4000
# A fill-in field (Word's content controls): plain text typed into it plus the grey hint shown while
# it is empty. Exports show an empty one as FORM_FIELD_BLANK, as on a printed worksheet.
MAX_FORM_FIELD_VALUE = 2000
MAX_FORM_FIELD_PLACEHOLDER = 200
FORM_FIELD_BLANK = "__________"
# Hint boxes ("callout", a colored box around blocks) and collapsible sections ("details", blocks
# under a one-line summary that the reader opens and closes).
CALLOUT_LABELS = {"info": "Info", "tip": "Tipp", "warning": "Warnung"}
MAX_DETAILS_SUMMARY = 200
DETAILS_FALLBACK = "Details"
# Voice notes are ordinary file attachments; these extensions get a player instead of a file chip.
AUDIO_NAME_RE = re.compile(r"\.(?:webm|ogg|oga|m4a|mp3)$", re.IGNORECASE)


def is_audio_name(name):
    return bool(AUDIO_NAME_RE.search(str(name or "")))


EMPTY_DOCUMENT = {"type": "doc", "content": [{"type": "paragraph"}]}


def empty_note_document():
    return {"type": "doc", "content": [{"type": "paragraph"}]}


def _heading(text, level=2):
    return {"type": "heading", "attrs": {"level": level}, "content": [{"type": "text", "text": text}]}


def _bullet_item(text=""):
    content = [{"type": "text", "text": text}] if text else []
    return {"type": "listItem", "content": [{"type": "paragraph", "content": content}]}


def _task_item(text=""):
    content = [{"type": "text", "text": text}] if text else []
    return {
        "type": "taskItem",
        "attrs": {"checked": False},
        "content": [{"type": "paragraph", "content": content}],
    }


def _meeting_note_document():
    return {
        "type": "doc",
        "content": [
            _heading("Teilnehmer"),
            {"type": "paragraph"},
            _heading("Agenda"),
            {"type": "bulletList", "content": [_bullet_item()]},
            _heading("Notizen"),
            {"type": "paragraph"},
            _heading("Aufgaben"),
            {"type": "taskList", "content": [_task_item()]},
        ],
    }


def _checklist_document():
    return {
        "type": "doc",
        "content": [{"type": "taskList", "content": [_task_item(), _task_item(), _task_item()]}],
    }


NOTE_TEMPLATES = {
    "blank": empty_note_document,
    "meeting": _meeting_note_document,
    "checklist": _checklist_document,
}

NOTE_TEMPLATE_LABELS = {
    "blank": "Leer",
    "meeting": "Meeting-Notiz",
    "checklist": "Checkliste",
}


def validate_note_document(document):
    if not isinstance(document, dict) or document.get("type") != "doc":
        raise ValidationError("Das Notizdokument ist ungültig.")
    try:
        encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValidationError("Das Notizdokument enthält ungültige Daten.") from error
    if len(encoded) > MAX_DOCUMENT_BYTES:
        raise ValidationError("Die Notiz ist zu groß.")

    state = {
        "nodes": 0,
        "characters": 0,
        "attachments": set(),
        "mentions": set(),
        "comment_threads": set(),
        "note_links": set(),
    }
    _validate_node(document, depth=0, state=state)
    if state["characters"] > MAX_TEXT_CHARACTERS:
        raise ValidationError("Die Notiz enthält zu viel Text.")
    return {
        "attachments": state["attachments"],
        "mentions": state["mentions"],
        "comment_threads": state["comment_threads"],
        "note_links": state["note_links"],
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


NODE_ATTRIBUTES = {
    "paragraph": {"textAlign", *BLOCK_BOX_ATTRIBUTES},
    "heading": {"level", "textAlign", *BLOCK_BOX_ATTRIBUTES},
    "orderedList": {"start", "type"},
    "codeBlock": {"language"},
    "taskItem": {"checked", "dueDate"},
    "tableCell": {
        "colspan",
        "rowspan",
        "colwidth",
        "backgroundColor",
        "align",
        "borderColor",
        "borderLeft",
    },
    "tableHeader": {
        "colspan",
        "rowspan",
        "colwidth",
        "backgroundColor",
        "align",
        "borderColor",
        "borderLeft",
    },
    "noteImage": {"attachmentId", "alt", "title", "width", "float", "floatX", "floatY"},
    "noteAttachment": {"attachmentId", "name", "size"},
    "mention": {"userId", "label"},
    "noteLink": {"noteId", "label"},
    "mathInline": {"latex"},
    "mathBlock": {"latex"},
    "formField": {"placeholder", "value"},
    "callout": {"tone"},
    "details": {"summary", "open"},
}


def _is_iso_date(value):
    try:
        return isinstance(value, str) and len(value) == 10 and bool(date.fromisoformat(value))
    except ValueError:
        return False


def task_due_label(value):
    """A checklist item's due date for exports: "2026-11-20" → "fällig 20.11.2026"."""
    return f"fällig {date.fromisoformat(value):%d.%m.%Y}" if _is_iso_date(value) else ""


def _validate_node_attrs(node_type, attrs, state):
    allowed = NODE_ATTRIBUTES.get(node_type, set())
    if set(attrs) - allowed:
        raise ValidationError(f"Das Element „{node_type}“ enthält unerlaubte Attribute.")
    if "textAlign" in attrs and attrs["textAlign"] not in ALLOWED_ALIGNMENTS:
        raise ValidationError("Die Textausrichtung ist ungültig.")
    for key in BLOCK_BOX_ATTRIBUTES:
        if attrs.get(key) and not HEX_COLOR_RE.fullmatch(str(attrs[key])):
            raise ValidationError("Eine Absatzfarbe ist ungültig.")
    if node_type == "heading" and attrs.get("level") not in {1, 2, 3}:
        raise ValidationError("Es sind nur Überschriften der Ebenen 1 bis 3 erlaubt.")
    if (
        node_type == "codeBlock"
        and attrs.get("language") not in {None, ""}
        and attrs.get("language") not in ALLOWED_CODE_LANGUAGES
    ):
        raise ValidationError("Diese Programmiersprache ist nicht erlaubt.")
    if node_type == "taskItem" and not isinstance(attrs.get("checked", False), bool):
        raise ValidationError("Der Checklistenstatus ist ungültig.")
    if node_type == "taskItem" and attrs.get("dueDate") is not None and not _is_iso_date(attrs["dueDate"]):
        raise ValidationError("Das Fälligkeitsdatum ist ungültig.")
    if node_type in {"tableCell", "tableHeader"}:
        for key in ("colspan", "rowspan"):
            if key in attrs and (not isinstance(attrs[key], int) or not 1 <= attrs[key] <= 20):
                raise ValidationError("Die Tabellenzellengröße ist ungültig.")
        if attrs.get("backgroundColor") and not HEX_COLOR_RE.fullmatch(attrs["backgroundColor"]):
            raise ValidationError("Die Zellenfarbe ist ungültig.")
        if attrs.get("borderLeft") and not HEX_COLOR_RE.fullmatch(str(attrs["borderLeft"])):
            raise ValidationError("Die Rahmenfarbe ist ungültig.")
        border_color = attrs.get("borderColor")
        if border_color and border_color != "none" and not HEX_COLOR_RE.fullmatch(str(border_color)):
            raise ValidationError("Die Rahmenfarbe ist ungültig.")
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
        if "float" in attrs and not isinstance(attrs["float"], bool):
            raise ValidationError("Die Bildplatzierung ist ungültig.")
        for key in ("floatX", "floatY"):
            if key in attrs and (not isinstance(attrs[key], int) or not -2000 <= attrs[key] <= 4000):
                raise ValidationError("Die Bildposition ist ungültig.")
    if node_type == "mention":
        user_id = attrs.get("userId")
        if not isinstance(user_id, int) or user_id <= 0:
            raise ValidationError("Die Erwähnung verweist auf keinen gültigen Nutzer.")
        if not isinstance(attrs.get("label"), str) or not attrs["label"] or len(attrs["label"]) > 100:
            raise ValidationError("Der Erwähnungstext ist ungültig.")
        state["mentions"].add(user_id)
    if node_type == "noteLink":
        note_id = attrs.get("noteId")
        if not isinstance(note_id, int) or note_id <= 0:
            raise ValidationError("Der Notizverweis ist ungültig.")
        if not isinstance(attrs.get("label"), str) or not attrs["label"] or len(attrs["label"]) > 200:
            raise ValidationError("Der Notizverweis-Text ist ungültig.")
        state["note_links"].add(note_id)
    if node_type in {"mathInline", "mathBlock"}:
        latex = attrs.get("latex")
        if not isinstance(latex, str) or not latex.strip() or len(latex) > MAX_LATEX_LENGTH:
            raise ValidationError("Die Formel ist ungültig.")
    if node_type == "formField":
        value, placeholder = attrs.get("value", ""), attrs.get("placeholder", "")
        if not isinstance(value, str) or len(value) > MAX_FORM_FIELD_VALUE:
            raise ValidationError("Der Inhalt eines Eingabefelds ist ungültig.")
        if not isinstance(placeholder, str) or len(placeholder) > MAX_FORM_FIELD_PLACEHOLDER:
            raise ValidationError("Der Hinweistext eines Eingabefelds ist ungültig.")
        state["characters"] += len(value)
    if node_type == "callout" and attrs.get("tone", "info") not in CALLOUT_LABELS:
        raise ValidationError("Die Art des Hinweises ist ungültig.")
    if node_type == "details":
        summary = attrs.get("summary", "")
        if not isinstance(summary, str) or len(summary) > MAX_DETAILS_SUMMARY:
            raise ValidationError("Die Überschrift eines Aufklapp-Abschnitts ist ungültig.")
        if not isinstance(attrs.get("open", True), bool):
            raise ValidationError("Der Zustand eines Aufklapp-Abschnitts ist ungültig.")
        state["characters"] += len(summary)


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
    elif mark_type == "author":
        user_id = attrs.get("userId")
        if set(attrs) != {"userId"} or type(user_id) is not int or user_id <= 0:
            raise ValidationError("Die Autorenangabe ist ungültig.")
    elif attrs:
        raise ValidationError(f"Die Textformatierung „{mark_type}“ darf keine Attribute enthalten.")


MAX_STEPS_PER_PUSH = 500
MAX_STEP_POSITION = MAX_TEXT_CHARACTERS + 2 * MAX_DOCUMENT_NODES
# Required keys and optional keys of the ProseMirror step JSON the editor produces.
STEP_FIELDS = {
    "replace": ({"from", "to"}, {"slice", "structure"}),
    "replaceAround": ({"from", "to", "gapFrom", "gapTo", "insert"}, {"slice", "structure"}),
    "addMark": ({"from", "to", "mark"}, set()),
    "removeMark": ({"from", "to", "mark"}, set()),
    "attr": ({"pos", "attr"}, {"value"}),
}
STEP_POSITION_FIELDS = ("from", "to", "gapFrom", "gapTo", "insert", "pos")
# Attribute steps never change what a node points at, so these stay out of them; the
# fillers make the other checks of a node type pass when only one attribute is looked at.
STEP_REFERENCE_ATTRIBUTES = {"attachmentId", "userId", "noteId"}
_STEP_ATTR_FILLERS = {
    "noteImage": {"attachmentId": "00000000-0000-0000-0000-000000000000"},
    "noteAttachment": {"attachmentId": "00000000-0000-0000-0000-000000000000"},
    "mention": {"userId": 1, "label": "x"},
    "noteLink": {"noteId": 1, "label": "x"},
    "mathInline": {"latex": "x"},
    "mathBlock": {"latex": "x"},
    "heading": {"level": 1},
}


def validate_note_steps(steps):
    """Check live-editing steps against the same allowlists as a whole document.

    The server never applies steps, so it can't see the document they land in; it checks
    everything a step adds (inserted content, marks, attribute values). Returns the
    references the steps add, for the same ownership checks as a document save.
    """
    if not isinstance(steps, list) or not 0 < len(steps) <= MAX_STEPS_PER_PUSH:
        raise ValidationError("Die Änderungen sind ungültig.")
    try:
        encoded = json.dumps(steps, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValidationError("Die Änderungen enthalten ungültige Daten.") from error
    if len(encoded) > MAX_DOCUMENT_BYTES:
        raise ValidationError("Die Änderungen sind zu groß.")
    state = _empty_state()
    for step in steps:
        _validate_step(step, state)
    if state["characters"] > MAX_TEXT_CHARACTERS:
        raise ValidationError("Die Änderungen enthalten zu viel Text.")
    return {key: state[key] for key in ("attachments", "mentions", "comment_threads", "note_links")}


def _empty_state():
    return {
        "nodes": 0,
        "characters": 0,
        "attachments": set(),
        "mentions": set(),
        "comment_threads": set(),
        "note_links": set(),
    }


def _validate_step(step, state):
    if not isinstance(step, dict) or step.get("stepType") not in STEP_FIELDS:
        raise ValidationError("Eine Änderung hat einen unbekannten Typ.")
    required, optional = STEP_FIELDS[step["stepType"]]
    keys = set(step) - {"stepType"}
    if not required <= keys or keys - required - optional:
        raise ValidationError("Eine Änderung enthält unbekannte Felder.")
    for key in STEP_POSITION_FIELDS:
        if key in step and (
            not isinstance(step[key], int)
            or isinstance(step[key], bool)
            or not 0 <= step[key] <= MAX_STEP_POSITION
        ):
            raise ValidationError("Eine Änderung verweist auf eine ungültige Stelle.")
    if "from" in step and step["to"] < step["from"]:
        raise ValidationError("Eine Änderung verweist auf eine ungültige Stelle.")
    if "structure" in step and not isinstance(step["structure"], bool):
        raise ValidationError("Eine Änderung ist ungültig.")

    if "slice" in step:
        piece = step["slice"]
        if not isinstance(piece, dict) or set(piece) - {"content", "openStart", "openEnd"}:
            raise ValidationError("Eingefügter Inhalt ist ungültig.")
        for key in ("openStart", "openEnd"):
            value = piece.get(key, 0)
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= MAX_DOCUMENT_DEPTH:
                raise ValidationError("Eingefügter Inhalt ist ungültig.")
        content = piece.get("content") or []
        if not isinstance(content, list):
            raise ValidationError("Eingefügter Inhalt ist ungültig.")
        for node in content:
            if isinstance(node, dict) and node.get("type") == "doc":
                raise ValidationError("Eingefügter Inhalt ist ungültig.")
            _validate_node(node, depth=0, state=state)
    elif step["stepType"] == "addMark":
        _validate_mark(step["mark"], state)
    elif step["stepType"] == "removeMark":
        # Removing can't add anything, so the reference it names needs no check.
        _validate_mark(step["mark"], _empty_state())
    elif step["stepType"] == "attr":
        _validate_step_attribute(step["attr"], step.get("value"))


def _validate_step_attribute(name, value):
    if not isinstance(name, str) or name in STEP_REFERENCE_ATTRIBUTES:
        raise ValidationError("Diese Attributänderung ist nicht erlaubt.")
    node_types = [node_type for node_type, allowed in NODE_ATTRIBUTES.items() if name in allowed]
    for node_type in node_types:
        try:
            _validate_node_attrs(
                node_type, {**_STEP_ATTR_FILLERS.get(node_type, {}), name: value}, _empty_state()
            )
        except ValidationError:
            continue
        return
    raise ValidationError("Diese Attributänderung ist nicht erlaubt.")


def safe_link_href(href):
    """href when it is a link the note validator accepts (http/https with a host, mailto), else None."""
    parsed = urlparse(href)
    scheme = parsed.scheme.lower()
    if scheme in {"http", "https"} and parsed.hostname:
        return href
    if scheme == "mailto" and "@" in parsed.path:
        return href
    return None


def document_plain_text(document):
    parts = []

    def visit(node):
        if node.get("type") == "text":
            parts.append(node.get("text", ""))
        elif node.get("type") == "mention":
            parts.append(f"@{(node.get('attrs') or {}).get('label', '')}")
        elif node.get("type") == "noteLink":
            parts.append(f"[[{(node.get('attrs') or {}).get('label', '')}]]")
        elif node.get("type") == "formField":
            parts.append((node.get("attrs") or {}).get("value", ""))
        elif node.get("type") == "details":
            if parts and not parts[-1].endswith("\n"):
                parts.append("\n")
            parts.append(f"{(node.get('attrs') or {}).get('summary', '')}\n")
        elif node.get("type") in {"hardBreak", "paragraph", "heading", "listItem", "taskItem", "tableRow"}:
            if parts and not parts[-1].endswith("\n"):
                parts.append("\n")
        for child in node.get("content") or []:
            visit(child)

    visit(document)
    return re.sub(r"\n{3,}", "\n\n", "".join(parts)).strip()


def document_checklist_counts(document):
    """Return ``(done, total)`` over every ``taskItem`` in the document (nested ones included)."""
    done = total = 0
    stack = [document] if isinstance(document, dict) else []
    while stack:
        node = stack.pop()
        if node.get("type") == "taskItem":
            total += 1
            if (node.get("attrs") or {}).get("checked") is True:
                done += 1
        stack.extend(child for child in node.get("content") or [] if isinstance(child, dict))
    return done, total


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


def extract_note_link_ids(document):
    """Collect noteLink noteIds from an already-trusted (previously validated) document tree."""
    note_ids = set()

    def visit(node):
        if not isinstance(node, dict):
            return
        if node.get("type") == "noteLink":
            note_id = (node.get("attrs") or {}).get("noteId")
            if isinstance(note_id, int):
                note_ids.add(note_id)
        for child in node.get("content") or []:
            visit(child)

    visit(document)
    return note_ids
