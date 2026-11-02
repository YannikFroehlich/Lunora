"""Import counterpart to the data export: Markdown files become notes, .ics files become events.

Both parsers are hand-written on the stdlib, like the rest of Lunora's file formats. Imported notes
still pass validate_note_document, so a parser bug can only fail an import, never store bad JSON.
"""

import calendar
import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import PurePath
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError
from django.db import transaction

from app.models import CalendarEvent
from app.services.calendar_service import MANUAL_RECURRENCE_MAX_INSTANCES
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
# RRULE parts _ics_occurrences understands; a rule with any other part (BYSETPOS, BYWEEKNO, BYHOUR,
# ...) imports as its first date only.
SUPPORTED_RULE_PARTS = {"FREQ", "INTERVAL", "UNTIL", "COUNT", "WKST", "BYDAY", "BYMONTHDAY", "BYMONTH"}
ICS_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
BYDAY_RE = re.compile(r"^([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)$")
# Periods walked at most, so a rule that rarely matches (FEB 30th never does) can't loop for long.
MAX_RECURRENCE_PERIODS = 5000


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
        name = name.upper()
        if name == "EXDATE":
            # Calendars write several EXDATE lines, each possibly a comma-separated list.
            properties.setdefault(name, []).append((value, params))
            continue
        # Keep the first occurrence of every other property.
        properties.setdefault(name, (value, params))
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


def _ics_occurrences(rule, start_at):
    """Expand an RRULE from start_at into the series' start datetimes, or None for a rule this
    importer can't read (then only the first date is imported).

    Handles FREQ, INTERVAL, COUNT, UNTIL, BYDAY (with ordinals such as 2MO or -1FR in monthly and
    yearly rules), BYMONTHDAY and BYMONTH. DTSTART is always the first occurrence, and every
    occurrence keeps start_at's wall-clock time, so a series stays at 9:00 across DST changes.
    """
    parts = dict(part.split("=", 1) for part in rule.upper().split(";") if "=" in part)
    frequency = parts.get("FREQ")
    if frequency not in SUPPORTED_FREQUENCIES or set(parts) - SUPPORTED_RULE_PARTS:
        return None
    try:
        interval = int(parts.get("INTERVAL", "1"))
        count = int(parts["COUNT"]) if "COUNT" in parts else MANUAL_RECURRENCE_MAX_INSTANCES
        months = {int(value) for value in parts["BYMONTH"].split(",")} if "BYMONTH" in parts else set()
        month_days = [int(value) for value in parts["BYMONTHDAY"].split(",")] if "BYMONTHDAY" in parts else []
    except ValueError:
        return None
    by_day = []
    for token in parts["BYDAY"].split(",") if "BYDAY" in parts else []:
        match = BYDAY_RE.match(token.strip())
        if not match:
            return None
        by_day.append((int(match.group(1)) if match.group(1) else None, ICS_WEEKDAYS[match.group(2)]))
    # "The 20th Monday of the year" and ordinals in daily/weekly rules are left to the fallback.
    has_ordinal = any(ordinal for ordinal, _weekday in by_day)
    if interval < 1 or count < 1 or (has_ordinal and frequency in {"DAILY", "WEEKLY"}):
        return None
    if frequency == "YEARLY" and by_day and not months:
        return None

    until = start_at + timedelta(days=RECURRENCE_HORIZON_DAYS)
    if "UNTIL" in parts:
        parsed = _ics_datetime(parts["UNTIL"], {}, start_at.tzinfo)
        if parsed:
            until_value = parsed[0]
            if parsed[1]:
                until_value = datetime.combine(until_value.date(), time.max, tzinfo=until_value.tzinfo)
            until = min(until, until_value)

    limit = min(count, MANUAL_RECURRENCE_MAX_INSTANCES)
    start_date = start_at.date()
    occurrences = [start_at]
    for period in range(MAX_RECURRENCE_PERIODS):
        period_start, days = _rule_period_days(
            frequency, start_date, period * interval, by_day, month_days, months
        )
        if datetime.combine(period_start, time.min, tzinfo=start_at.tzinfo) > until:
            break
        for day in sorted(set(days)):
            if day <= start_date:
                continue
            occurrence = datetime.combine(day, start_at.time(), tzinfo=start_at.tzinfo)
            if occurrence > until or len(occurrences) >= limit:
                return occurrences
            occurrences.append(occurrence)
    return occurrences[:limit]


def _rule_period_days(frequency, start_date, step, by_day, month_days, months):
    """The first day of the step-th period (day, week, month or year) after start_date's own, and the
    days of that period the rule picks."""
    weekdays = {weekday for _ordinal, weekday in by_day}
    if frequency == "DAILY":
        day = start_date + timedelta(days=step)
        matches = (
            (not months or day.month in months)
            and (not weekdays or day.weekday() in weekdays)
            and (not month_days or day.day in _resolve_month_days(day.year, day.month, month_days))
        )
        return day, [day] if matches else []
    if frequency == "WEEKLY":
        # ponytail: weeks start on Monday (WKST is ignored); only an INTERVAL>1 rule with WKST=SU
        # and a Sunday in BYDAY would come out a week off.
        week_start = start_date - timedelta(days=start_date.weekday()) + timedelta(weeks=step)
        days = [week_start + timedelta(days=weekday) for weekday in weekdays or {start_date.weekday()}]
        return week_start, [day for day in days if not months or day.month in months]
    if frequency == "MONTHLY":
        month_index = start_date.month - 1 + step
        first = date(start_date.year + month_index // 12, month_index % 12 + 1, 1)
        if months and first.month not in months:
            return first, []
        return first, _rule_month_days(first.year, first.month, by_day, month_days, start_date.day)
    year = start_date.year + step
    days = []
    for month in sorted(months or {start_date.month}):
        days += _rule_month_days(year, month, by_day, month_days, start_date.day)
    return date(year, 1, 1), days


def _resolve_month_days(year, month, month_days):
    """BYMONTHDAY values as real days of that month; -1 is the last day, impossible days drop out."""
    last = calendar.monthrange(year, month)[1]
    resolved = (value if value > 0 else last + value + 1 for value in month_days)
    return {value for value in resolved if 1 <= value <= last}


def _rule_month_days(year, month, by_day, month_days, default_day):
    last = calendar.monthrange(year, month)[1]
    if month_days:
        days = [date(year, month, value) for value in _resolve_month_days(year, month, month_days)]
        weekdays = {weekday for _ordinal, weekday in by_day}
        return [day for day in days if not weekdays or day.weekday() in weekdays]
    if by_day:
        days = []
        for ordinal, weekday in by_day:
            matching = [date(year, month, value) for value in range(1, last + 1)]
            matching = [day for day in matching if day.weekday() == weekday]
            if ordinal is None:
                days += matching
            elif 1 <= abs(ordinal) <= len(matching):
                days.append(matching[ordinal - 1 if ordinal > 0 else ordinal])
        return days
    # A monthly series on the 31st skips shorter months, as RFC 5545 asks.
    return [date(year, month, default_day)] if default_day <= last else []


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
    # A VEVENT with RECURRENCE-ID replaces one occurrence of the series with the same UID; a
    # cancelled one removes that occurrence, so it is kept instead of skipped.
    recurrence_of = None
    if "RECURRENCE-ID" in properties:
        parsed = _ics_datetime(*properties["RECURRENCE-ID"], default_zone)
        recurrence_of = parsed[0] if parsed else None
    cancelled = properties.get("STATUS", ("",))[0].strip().upper() == "CANCELLED"
    if cancelled and recurrence_of is None:
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
        "exdates": _ics_exdates(properties.get("EXDATE", []), default_zone),
        "recurrence_of": recurrence_of,
        "cancelled": cancelled,
    }


def _ics_exdates(values, default_zone):
    exdates = set()
    for value, params in values:
        for part in value.split(","):
            parsed = _ics_datetime(part, params, default_zone)
            if parsed:
                exdates.add(parsed[0])
    return exdates


def _ics_event_row(user, event, start, recurrence_id, frequency):
    return CalendarEvent(
        user=user,
        title=event["title"],
        category=CalendarEvent.CATEGORY_APPOINTMENT,
        description=event["description"],
        location=event["location"],
        start_at=start,
        end_at=start + (event["end_at"] - event["start_at"]),
        is_all_day=event["is_all_day"],
        import_uid=event["uid"],
        recurrence_id=recurrence_id,
        recurrence_rule=frequency if recurrence_id else "",
    )


def import_ics_events(user, text):
    """Create the file's events for user. Returns counts: created, duplicates, skipped, simplified."""
    default_zone = get_user_zoneinfo(user)
    known_uids = set(
        CalendarEvent.objects.filter(user=user).exclude(import_uid="").values_list("import_uid", flat=True)
    )
    counts = {"created": 0, "duplicates": 0, "skipped": 0, "simplified": 0}
    events = []
    overrides = {}
    for event in parse_ics_events(text, default_zone):
        if event is None:
            counts["skipped"] += 1
        elif event["recurrence_of"] is not None and event["uid"]:
            overrides.setdefault(event["uid"], []).append(event)
        else:
            events.append(event)

    rows = []
    for event in events:
        series_overrides = overrides.pop(event["uid"], []) if event["uid"] else []
        if event["uid"] and event["uid"] in known_uids:
            counts["duplicates"] += 1
            continue
        known_uids.add(event["uid"])
        starts = [event["start_at"]]
        frequency = ""
        if event["rrule"]:
            occurrences = _ics_occurrences(event["rrule"], event["start_at"])
            if occurrences is None:
                counts["simplified"] += 1
            else:
                starts = occurrences
                frequency = re.search(r"FREQ=(\w+)", event["rrule"].upper()).group(1)
        replaced = event["exdates"] | {override["recurrence_of"] for override in series_overrides}
        starts = [start for start in starts if start not in replaced]
        moved = [override for override in series_overrides if not override["cancelled"]]
        if not starts and not moved:
            counts["skipped"] += 1
            continue
        recurrence_id = uuid.uuid4() if len(starts) + len(moved) > 1 else None
        rows.extend(_ics_event_row(user, event, start, recurrence_id, frequency) for start in starts)
        rows.extend(
            _ics_event_row(user, moved_event, moved_event["start_at"], recurrence_id, frequency)
            for moved_event in moved
        )
        counts["created"] += 1
    # Changed occurrences whose series isn't in the file have nothing to replace.
    counts["skipped"] += sum(len(series_overrides) for series_overrides in overrides.values())
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
