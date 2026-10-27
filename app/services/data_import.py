"""Import counterpart to the data export: Markdown files become notes, .ics files become events.

Both parsers are hand-written on the stdlib, like the rest of Lunora's file formats. Imported notes
still pass validate_note_document, so a parser bug can only fail an import, never store bad JSON.
"""

import re
import uuid
from datetime import UTC, datetime, time, timedelta
from pathlib import PurePath
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError
from django.db import transaction

from app.models import CalendarEvent
from app.services.calendar_service import MANUAL_RECURRENCE_MAX_INSTANCES, expand_manual_recurrence
from app.services.note_content import ALLOWED_CODE_LANGUAGES, document_plain_text, validate_note_document
from app.services.notes import create_note
from app.services.user_preferences import get_user_zoneinfo

MAX_IMPORT_FILES = 20
MAX_IMPORT_FILE_BYTES = 2 * 1024 * 1024
MARKDOWN_EXTENSIONS = {".md", ".markdown", ".txt"}
ICS_EXTENSIONS = {".ics"}
# Unbounded series ("every year") are expanded this far ahead, like the event form's own limit.
RECURRENCE_HORIZON_DAYS = 1825

# ---- Markdown → Tiptap document ----------------------------------------------------------------

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
RULE_RE = re.compile(r"^ {0,3}([-*_])(?:\s*\1){2,}\s*$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*([\w+-]*)")
LIST_RE = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(?:\[([ xX])\]\s+)?(.*)$")
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
MATH_BLOCK_RE = re.compile(r"^\$\$(.+)\$\$$")
# The export writes this byline under the title; it is not part of the note's content.
EXPORT_BYLINE_RE = re.compile(r"^\*Zuletzt bearbeitet am .+ von .+\*$")
INLINE_RE = re.compile(
    r"""
    \\(?P<esc>[\\`*_{}\[\]()#+\-.!|~<>$])
    | (?P<ticks>`+)(?P<code>.+?)(?P=ticks)
    | !?\[(?P<ltext>[^\]]*)\]\((?P<href>[^)\s]+)(?:\s+"[^"]*")?\)
    | <(?P<auto>https?://[^>\s]+)>
    | \*\*(?P<b1>.+?)\*\*
    | __(?P<b2>.+?)__
    | ~~(?P<strike>.+?)~~
    | \*(?P<i1>[^\s*](?:.*?[^\s])?)\*
    | (?<!\w)_(?P<i2>[^\s_](?:.*?[^\s])?)_(?!\w)
    | <(?P<tag>u|mark|sup|sub)>(?P<tagbody>.+?)</(?P=tag)>
    """,
    re.VERBOSE,
)
TAG_MARKS = {"u": "underline", "mark": "highlight", "sup": "superscript", "sub": "subscript"}


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _safe_href(href):
    parsed = urlparse(href)
    scheme = parsed.scheme.lower()
    if scheme in {"http", "https"} and parsed.hostname:
        return href
    if scheme == "mailto" and "@" in parsed.path:
        return href
    return None


def _text_node(text, marks):
    node = {"type": "text", "text": text}
    if marks:
        node["marks"] = [dict(mark) for mark in marks]
    return node


def parse_inline(text, marks=()):
    """Inline Markdown (emphasis, code, links, the export's HTML tags) → text nodes with marks."""
    nodes = []
    position = 0

    def add(value, node_marks):
        if not value:
            return
        if nodes and nodes[-1].get("marks", []) == [dict(mark) for mark in node_marks]:
            nodes[-1]["text"] += value
        else:
            nodes.append(_text_node(value, node_marks))

    for match in INLINE_RE.finditer(text):
        add(text[position : match.start()], marks)
        position = match.end()
        groups = match.groupdict()
        if groups["esc"] is not None:
            add(groups["esc"], marks)
        elif groups["code"] is not None:
            # Tiptap's code mark excludes every other mark.
            add(groups["code"].strip() if groups["ticks"] != "`" else groups["code"], ({"type": "code"},))
        elif groups["href"] is not None:
            href = _safe_href(groups["href"])
            link_marks = (*marks, {"type": "link", "attrs": {"href": href}}) if href else marks
            for child in parse_inline(groups["ltext"] or groups["href"], link_marks):
                add(child["text"], tuple(child.get("marks", ())))
        elif groups["auto"] is not None:
            add(groups["auto"], (*marks, {"type": "link", "attrs": {"href": groups["auto"]}}))
        else:
            if groups["b1"] is not None or groups["b2"] is not None:
                inner, mark = groups["b1"] or groups["b2"], "bold"
            elif groups["strike"] is not None:
                inner, mark = groups["strike"], "strike"
            elif groups["i1"] is not None or groups["i2"] is not None:
                inner, mark = groups["i1"] or groups["i2"], "italic"
            else:
                inner, mark = groups["tagbody"], TAG_MARKS[groups["tag"]]
            for child in parse_inline(inner, (*marks, {"type": mark})):
                add(child["text"], tuple(child.get("marks", ())))
    add(text[position:], marks)
    return nodes


def _paragraph(lines):
    content = []
    for index, line in enumerate(lines):
        hard_break = line.endswith("\\") or line.endswith("  ")
        stripped = line.rstrip()
        if hard_break and stripped.endswith("\\"):
            stripped = stripped[:-1]
        content.extend(parse_inline(stripped.strip()))
        if index < len(lines) - 1:
            content.append({"type": "hardBreak"} if hard_break else _text_node(" ", ()))
    # Merge the joining spaces into neighbouring unformatted text.
    merged = []
    for node in content:
        if (
            merged
            and node.get("type") == "text"
            and merged[-1].get("type") == "text"
            and (merged[-1].get("marks") == node.get("marks"))
        ):
            merged[-1]["text"] += node["text"]
        else:
            merged.append(node)
    paragraph = {"type": "paragraph"}
    if merged:
        paragraph["content"] = merged
    return paragraph


def _table_cells(line):
    cells = line.strip()
    if cells.startswith("|"):
        cells = cells[1:]
    if cells.endswith("|") and not cells.endswith("\\|"):
        cells = cells[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", cells)]


def _table(lines):
    rows = []
    for row_index, line in enumerate(lines):
        cell_type = "tableHeader" if row_index == 0 else "tableCell"
        cells = []
        for cell in _table_cells(line):
            parts = [part for part in re.split(r"<br\s*/?>", cell) if part.strip()] or [""]
            cells.append({"type": cell_type, "content": [_paragraph([part]) for part in parts]})
        rows.append({"type": "tableRow", "content": cells})
    return {"type": "table", "content": rows}


def _parse_list(lines, start):
    first = LIST_RE.match(lines[start])
    base_indent = len(first.group(1))
    ordered = first.group(2)[0].isdigit()
    is_task = first.group(3) is not None
    items = []
    index = start
    while index < len(lines):
        match = LIST_RE.match(lines[index])
        if (
            not match
            or len(match.group(1)) != base_indent
            or match.group(2)[0].isdigit() != ordered
            or (match.group(3) is not None) != is_task
        ):
            break
        content_indent = base_indent + len(match.group(2)) + 1
        item_lines = [match.group(4)]
        index += 1
        while index < len(lines):
            line = lines[index]
            if not line.strip():
                following = next((j for j in range(index + 1, len(lines)) if lines[j].strip()), None)
                if following is not None and _indent(lines[following]) > base_indent:
                    item_lines.append("")
                    index += 1
                    continue
                break
            if _indent(line) <= base_indent:
                break
            item_lines.append(line[min(_indent(line), content_indent) :])
            index += 1
        children = parse_blocks(item_lines)
        if not children or children[0]["type"] != "paragraph":
            children.insert(0, {"type": "paragraph"})
        item = {"type": "taskItem" if is_task else "listItem", "content": children}
        if is_task:
            item["attrs"] = {"checked": match.group(3).lower() == "x"}
        items.append(item)
    if is_task:
        block = {"type": "taskList", "content": items}
    elif ordered:
        block = {"type": "orderedList", "content": items}
        number = int(first.group(2)[:-1])
        if number != 1:
            block["attrs"] = {"start": number}
    else:
        block = {"type": "bulletList", "content": items}
    return block, index


def _starts_block(lines, index):
    line = lines[index]
    return bool(
        HEADING_RE.match(line)
        or RULE_RE.match(line)
        or FENCE_RE.match(line)
        or LIST_RE.match(line)
        or line.lstrip().startswith(">")
        or MATH_BLOCK_RE.match(line.strip())
        or (
            line.lstrip().startswith("|")
            and index + 1 < len(lines)
            and TABLE_SEPARATOR_RE.match(lines[index + 1])
        )
    )


def parse_blocks(lines):
    blocks = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if fence := FENCE_RE.match(line):
            marker = fence.group(1)
            code_lines = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith(marker):
                code_lines.append(lines[index])
                index += 1
            index += 1
            language = fence.group(2).lower()
            block = {"type": "codeBlock"}
            if language in ALLOWED_CODE_LANGUAGES:
                block["attrs"] = {"language": language}
            if code_lines:
                block["content"] = [_text_node("\n".join(code_lines), ())]
            blocks.append(block)
        elif heading := HEADING_RE.match(line):
            block = {"type": "heading", "attrs": {"level": min(len(heading.group(1)), 3)}}
            if content := parse_inline(heading.group(2)):
                block["content"] = content
            blocks.append(block)
            index += 1
        elif RULE_RE.match(line):
            blocks.append({"type": "horizontalRule"})
            index += 1
        elif math := MATH_BLOCK_RE.match(line.strip()):
            blocks.append({"type": "mathBlock", "attrs": {"latex": math.group(1).strip()}})
            index += 1
        elif line.lstrip().startswith(">"):
            quoted = []
            while index < len(lines) and lines[index].lstrip().startswith(">"):
                quoted.append(re.sub(r"^\s*>\s?", "", lines[index]))
                index += 1
            blocks.append({"type": "blockquote", "content": parse_blocks(quoted) or [{"type": "paragraph"}]})
        elif LIST_RE.match(line):
            block, index = _parse_list(lines, index)
            blocks.append(block)
        elif (
            line.lstrip().startswith("|")
            and index + 1 < len(lines)
            and TABLE_SEPARATOR_RE.match(lines[index + 1])
        ):
            table_lines = [line]
            index += 2
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                table_lines.append(lines[index])
                index += 1
            blocks.append(_table(table_lines))
        else:
            paragraph_lines = [line]
            index += 1
            while index < len(lines) and lines[index].strip() and not _starts_block(lines, index):
                paragraph_lines.append(lines[index])
                index += 1
            blocks.append(_paragraph(paragraph_lines))
    return blocks


def markdown_to_note(text, fallback_title):
    """Return (title, document); a leading "# Heading" becomes the title, as in the export."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    title = fallback_title
    if lines and (heading := HEADING_RE.match(lines[0])) and len(heading.group(1)) == 1:
        title = heading.group(2).strip() or fallback_title
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
        if lines and EXPORT_BYLINE_RE.match(lines[0].strip()):
            lines.pop(0)
    content = parse_blocks(lines) or [{"type": "paragraph"}]
    return title[:200], {"type": "doc", "content": content}


def import_markdown_note(user, filename, text):
    title, document = markdown_to_note(text, PurePath(filename).stem.strip() or "Importierte Notiz")
    validate_note_document(document)
    with transaction.atomic():
        note = create_note(user, title=title)
        note.document = document
        note.plain_text = document_plain_text(document)
        note.save(update_fields=["document", "plain_text"])
    return note


# ---- iCalendar → events --------------------------------------------------------------------------

ICS_DURATION_RE = re.compile(
    r"^(?P<sign>[+-])?P(?:(?P<weeks>\d+)W)?(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)
SUPPORTED_FREQUENCIES = {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}


def _ics_unescape(value):
    return re.sub(r"\\([\\;,nN])", lambda match: "\n" if match.group(1) in "nN" else match.group(1), value)


def _ics_properties(block_lines):
    properties = {}
    for line in block_lines:
        if ":" not in line:
            continue
        head, value = line.split(":", 1)
        name, *raw_params = head.split(";")
        params = {}
        for param in raw_params:
            key, _sep, param_value = param.partition("=")
            params[key.upper()] = param_value.strip('"')
        # Keep the first occurrence; repeated properties (e.g. several EXDATEs) are not needed.
        properties.setdefault(name.upper(), (value, params))
    return properties


def _ics_datetime(value, params, default_zone):
    """Return (aware datetime, is_all_day) or None for values this importer can't read."""
    value = value.strip()
    try:
        if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
            day = datetime.strptime(value[:8], "%Y%m%d").date()
            return datetime.combine(day, time.min, tzinfo=default_zone), True
        parsed = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    if value.endswith("Z"):
        return parsed.replace(tzinfo=UTC), False
    zone = default_zone
    if params.get("TZID"):
        try:
            zone = ZoneInfo(params["TZID"])
        except (ZoneInfoNotFoundError, ValueError):
            # Outlook uses Windows zone names ("W. Europe Standard Time"); fall back to the user's.
            zone = default_zone
    return parsed.replace(tzinfo=zone), False


def _ics_duration(value):
    match = ICS_DURATION_RE.match(value.strip())
    if not match:
        return None
    parts = {key: int(number or 0) for key, number in match.groupdict().items() if key != "sign"}
    duration = timedelta(
        weeks=parts["weeks"],
        days=parts["days"],
        hours=parts["hours"],
        minutes=parts["minutes"],
        seconds=parts["seconds"],
    )
    return -duration if match.group("sign") == "-" else duration


def _ics_recurrence(rule, start_at):
    """Map a simple RRULE onto Lunora's own series: (frequency, until, count), or None if too complex."""
    parts = dict(part.split("=", 1) for part in rule.upper().split(";") if "=" in part)
    if parts.get("FREQ") not in SUPPORTED_FREQUENCIES or parts.get("INTERVAL", "1") != "1":
        return None
    if set(parts) - {"FREQ", "INTERVAL", "UNTIL", "COUNT", "WKST"}:
        return None
    until = start_at + timedelta(days=RECURRENCE_HORIZON_DAYS)
    if "UNTIL" in parts:
        parsed = _ics_datetime(parts["UNTIL"], {}, start_at.tzinfo)
        if parsed:
            until_value = parsed[0]
            if parsed[1]:
                until_value = datetime.combine(until_value.date(), time.max, tzinfo=until_value.tzinfo)
            until = min(until, until_value)
    count = int(parts["COUNT"]) if parts.get("COUNT", "").isdigit() else None
    return parts["FREQ"], until, count


def parse_ics_events(text, default_zone):
    """Yield one dict per VEVENT; skipped (cancelled or unreadable) events yield None."""
    unfolded = re.sub(r"\r?\n[ \t]", "", text.replace("\r\n", "\n"))
    block = None
    for line in unfolded.split("\n"):
        upper = line.strip().upper()
        if upper == "BEGIN:VEVENT":
            block = []
        elif upper == "END:VEVENT" and block is not None:
            yield _ics_event(_ics_properties(block), default_zone)
            block = None
        elif block is not None:
            block.append(line.rstrip())


def _ics_event(properties, default_zone):
    if "DTSTART" not in properties:
        return None
    if properties.get("STATUS", ("",))[0].strip().upper() == "CANCELLED":
        return None
    start = _ics_datetime(*properties["DTSTART"], default_zone)
    if start is None:
        return None
    start_at, is_all_day = start
    end_at = None
    if "DTEND" in properties:
        end = _ics_datetime(*properties["DTEND"], default_zone)
        end_at = end[0] if end else None
    elif "DURATION" in properties:
        duration = _ics_duration(properties["DURATION"][0])
        end_at = start_at + duration if duration is not None else None
    if end_at is None or end_at < start_at:
        end_at = start_at + (timedelta(days=1) if is_all_day else timedelta(0))
    return {
        "uid": properties.get("UID", ("",))[0].strip()[:255],
        "title": _ics_unescape(properties.get("SUMMARY", ("",))[0]).strip()[:255] or "Ohne Titel",
        "location": _ics_unescape(properties.get("LOCATION", ("",))[0]).strip()[:255],
        "description": _ics_unescape(properties.get("DESCRIPTION", ("",))[0]).strip(),
        "start_at": start_at,
        "end_at": end_at,
        "is_all_day": is_all_day,
        "rrule": properties.get("RRULE", ("",))[0].strip(),
    }


def import_ics_events(user, text):
    """Create the file's events for user. Returns counts: created, duplicates, skipped, simplified."""
    default_zone = get_user_zoneinfo(user)
    known_uids = set(
        CalendarEvent.objects.filter(user=user).exclude(import_uid="").values_list("import_uid", flat=True)
    )
    counts = {"created": 0, "duplicates": 0, "skipped": 0, "simplified": 0}
    rows = []
    for event in parse_ics_events(text, default_zone):
        if event is None:
            counts["skipped"] += 1
            continue
        if event["uid"] and event["uid"] in known_uids:
            counts["duplicates"] += 1
            continue
        known_uids.add(event["uid"])
        starts = [event["start_at"]]
        recurrence_id = None
        frequency = ""
        if event["rrule"]:
            recurrence = _ics_recurrence(event["rrule"], event["start_at"])
            if recurrence is None:
                # ponytail: BYDAY/INTERVAL/EXDATE series import as their first date only; extend
                # _ics_recurrence if real calendars need them.
                counts["simplified"] += 1
            else:
                frequency, until, count = recurrence
                starts = expand_manual_recurrence(event["start_at"], frequency, until)[
                    : count or MANUAL_RECURRENCE_MAX_INSTANCES
                ]
                recurrence_id = uuid.uuid4() if len(starts) > 1 else None
        duration = event["end_at"] - event["start_at"]
        rows.extend(
            CalendarEvent(
                user=user,
                title=event["title"],
                category=CalendarEvent.CATEGORY_APPOINTMENT,
                description=event["description"],
                location=event["location"],
                start_at=start,
                end_at=start + duration,
                is_all_day=event["is_all_day"],
                import_uid=event["uid"],
                recurrence_id=recurrence_id,
                recurrence_rule=frequency if recurrence_id else "",
            )
            for start in starts
        )
        counts["created"] += 1
    CalendarEvent.objects.bulk_create(rows)
    return counts


# ---- Upload dispatch -------------------------------------------------------------------------------


def import_files(user, files, *, notes_enabled, calendar_enabled):
    """Import uploaded files by extension; returns (success messages, error messages) in German."""
    successes, errors = [], []
    if len(files) > MAX_IMPORT_FILES:
        return [], [f"Bitte wähle höchstens {MAX_IMPORT_FILES} Dateien auf einmal."]
    note_count = 0
    for upload in files:
        name = upload.name or "Datei"
        extension = PurePath(name).suffix.lower()
        if extension not in MARKDOWN_EXTENSIONS | ICS_EXTENSIONS:
            errors.append(f"„{name}“: Nur .md-, .txt- und .ics-Dateien können importiert werden.")
            continue
        if upload.size > MAX_IMPORT_FILE_BYTES:
            errors.append(f"„{name}“ ist größer als 2 MB.")
            continue
        try:
            text = upload.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            errors.append(f"„{name}“ ist keine UTF-8-Textdatei.")
            continue
        if extension in ICS_EXTENSIONS:
            if not calendar_enabled:
                errors.append(f"„{name}“: Termine anlegen ist gerade deaktiviert.")
                continue
            counts = import_ics_events(user, text)
            successes.append(_ics_summary(name, counts))
        else:
            if not notes_enabled:
                errors.append(f"„{name}“: Notizen sind gerade deaktiviert.")
                continue
            try:
                import_markdown_note(user, name, text)
            except ValidationError as error:
                errors.append(f"„{name}“: {' '.join(error.messages)}")
                continue
            note_count += 1
    if note_count:
        successes.insert(0, f"{note_count} Notiz(en) importiert.")
    return successes, errors


def _ics_summary(name, counts):
    parts = [f"„{name}“: {counts['created']} Termin(e) importiert"]
    if counts["duplicates"]:
        parts.append(f"{counts['duplicates']} schon vorhanden")
    if counts["skipped"]:
        parts.append(f"{counts['skipped']} übersprungen")
    if counts["simplified"]:
        parts.append(f"{counts['simplified']} Serie(n) nur mit dem ersten Termin")
    return ", ".join(parts) + "."
