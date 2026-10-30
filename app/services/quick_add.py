"""German natural-language quick add: pull a due date, time, priority and labels out of a typed title.

"Bericht abgeben Freitag 14 Uhr !hoch #arbeit" becomes the title "Bericht abgeben", due next Friday at
14:00, high priority, label "arbeit". Only phrases standing on their own count (whitespace or the text
edge around them), so "Montag-Meeting" or "Morgenlauf" stay part of the title. Each kind is taken at
most once, the first match wins, and anything not understood simply stays in the title.
"""

import re
from dataclasses import dataclass, field
from datetime import date, time, timedelta

from app.services.calendar_service import _add_months

WEEKDAYS = {
    "montag": 0,
    "dienstag": 1,
    "mittwoch": 2,
    "donnerstag": 3,
    "freitag": 4,
    "samstag": 5,
    "sonntag": 6,
}
MONTHS = {
    "januar": 1,
    "jan": 1,
    "februar": 2,
    "feb": 2,
    "märz": 3,
    "maerz": 3,
    "mär": 3,
    "april": 4,
    "apr": 4,
    "mai": 5,
    "juni": 6,
    "jun": 6,
    "juli": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "oktober": 10,
    "okt": 10,
    "november": 11,
    "nov": 11,
    "dezember": 12,
    "dez": 12,
}
NUMBER_WORDS = {
    "ein": 1,
    "eine": 1,
    "einen": 1,
    "einem": 1,
    "einer": 1,
    "zwei": 2,
    "drei": 3,
    "vier": 4,
    "fünf": 5,
    "sechs": 6,
    "sieben": 7,
    "acht": 8,
    "neun": 9,
    "zehn": 10,
}
PRIORITIES = {"hoch": "high", "1": "high", "mittel": "medium", "2": "medium", "niedrig": "low", "3": "low"}

# A phrase must stand on its own: preceded by whitespace or the start, followed by whitespace,
# punctuation that ends a clause, or the end.
_START = r"(?<!\S)"
_END = r"(?=[\s.,;!?]|$)"
_PREPOSITION = r"(?:(?:am|bis|ab|für|zum|fällig)\s+)?"
_NUMBER = r"\d{1,2}|" + "|".join(NUMBER_WORDS)
_MONTH_NAMES = "|".join(sorted(MONTHS, key=len, reverse=True))

RELATIVE_DAY_RE = re.compile(rf"{_START}{_PREPOSITION}(übermorgen|morgen|heute){_END}", re.IGNORECASE)
IN_PERIOD_RE = re.compile(
    rf"{_START}in\s+({_NUMBER})\s+(tagen|tag|wochen|woche|monaten|monat){_END}", re.IGNORECASE
)
WEEKDAY_RE = re.compile(
    rf"{_START}{_PREPOSITION}(?:(nächsten|nächste|kommenden|kommende)\s+)?({'|'.join(WEEKDAYS)}){_END}",
    re.IGNORECASE,
)
NUMERIC_DATE_RE = re.compile(
    rf"{_START}{_PREPOSITION}(\d{{1,2}})\.(\d{{1,2}})\.(\d{{4}}|\d{{2}})?{_END}", re.IGNORECASE
)
ISO_DATE_RE = re.compile(rf"{_START}{_PREPOSITION}(\d{{4}})-(\d{{2}})-(\d{{2}}){_END}", re.IGNORECASE)
NAMED_DATE_RE = re.compile(
    rf"{_START}{_PREPOSITION}(\d{{1,2}})\.?\s+({_MONTH_NAMES})\.?(?:\s+(\d{{4}}))?{_END}", re.IGNORECASE
)
TIME_WITH_UHR_RE = re.compile(
    rf"{_START}(?:(?:um|ab|bis)\s+)?(\d{{1,2}})(?:[:.](\d{{2}}))?\s*uhr{_END}", re.IGNORECASE
)
TIME_WITH_UM_RE = re.compile(rf"{_START}um\s+(\d{{1,2}})(?:[:.](\d{{2}}))?{_END}", re.IGNORECASE)
CLOCK_RE = re.compile(rf"{_START}(\d{{1,2}}):(\d{{2}}){_END}")
PRIORITY_RE = re.compile(rf"{_START}!({'|'.join(PRIORITIES)}){_END}", re.IGNORECASE)
LABEL_RE = re.compile(rf"{_START}#([\w-]+){_END}")


@dataclass
class QuickAddResult:
    title: str
    due_date: date | None = None
    due_time: time | None = None
    priority: str | None = None
    labels: list = field(default_factory=list)

    @property
    def found_anything(self):
        return bool(self.due_date or self.due_time or self.priority or self.labels)


def parse_quick_add(text, *, today, now_time=None, labels=None):
    """Split `text` into a title and the date/time/priority/labels it names.

    `today` and `now_time` are the user's local date and time; a bare time that already passed today
    means tomorrow. `labels` maps lowercase label names to the objects returned for "#name"; unknown
    labels stay in the title. If nothing but those phrases was typed, the text is kept as the title.
    """
    remaining = text
    result = QuickAddResult(title=text)

    def take(pattern, convert):
        nonlocal remaining
        for match in pattern.finditer(remaining):
            value = convert(match)
            if value is not None:
                remaining = f"{remaining[: match.start()]} {remaining[match.end() :]}"
                return value
        return None

    for pattern, convert in (
        (RELATIVE_DAY_RE, lambda match: _relative_day(match, today)),
        (IN_PERIOD_RE, lambda match: _in_period(match, today)),
        (WEEKDAY_RE, lambda match: _weekday(match, today)),
        (ISO_DATE_RE, _iso_date),
        (NUMERIC_DATE_RE, lambda match: _numeric_date(match, today)),
        (NAMED_DATE_RE, lambda match: _named_date(match, today)),
    ):
        result.due_date = take(pattern, convert)
        if result.due_date:
            break

    for pattern in (TIME_WITH_UHR_RE, TIME_WITH_UM_RE, CLOCK_RE):
        result.due_time = take(pattern, _clock)
        if result.due_time:
            break

    result.priority = take(PRIORITY_RE, lambda match: PRIORITIES[match.group(1).lower()])

    label_map = labels or {}
    while label := take(LABEL_RE, lambda match: label_map.get(match.group(1).lower())):
        if label not in result.labels:
            result.labels.append(label)

    if result.due_time and not result.due_date:
        result.due_date = (
            today if now_time is None or result.due_time > now_time else today + timedelta(days=1)
        )

    title = re.sub(r"\s+", " ", remaining).strip(" ,;-–")
    if not title:
        return QuickAddResult(title=text.strip())
    result.title = title
    return result


def _relative_day(match, today):
    return today + timedelta(days={"heute": 0, "morgen": 1, "übermorgen": 2}[match.group(1).lower()])


def _in_period(match, today):
    amount = match.group(1).lower()
    count = int(amount) if amount.isdigit() else NUMBER_WORDS[amount]
    unit = match.group(2).lower()
    if unit.startswith("tag"):
        return today + timedelta(days=count)
    if unit.startswith("woche"):
        return today + timedelta(weeks=count)
    return _add_months(today, count)


def _weekday(match, today):
    days_ahead = (WEEKDAYS[match.group(2).lower()] - today.weekday()) % 7
    # "Freitag" on a Friday means today, "nächsten Freitag" the one a week later.
    if match.group(1) and days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


def _iso_date(match):
    return _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _numeric_date(match, today):
    year = match.group(3)
    if year:
        year = int(year) + (2000 if len(year) == 2 else 0)
        return _safe_date(year, int(match.group(2)), int(match.group(1)))
    return _next_occurrence(int(match.group(1)), int(match.group(2)), today)


def _named_date(match, today):
    month = MONTHS[match.group(2).lower()]
    if match.group(3):
        return _safe_date(int(match.group(3)), month, int(match.group(1)))
    return _next_occurrence(int(match.group(1)), month, today)


def _next_occurrence(day, month, today):
    """A day and month without a year: this year, or next year once that date has passed."""
    candidate = _safe_date(today.year, month, day)
    if candidate and candidate < today:
        candidate = _safe_date(today.year + 1, month, day)
    return candidate


def _clock(match):
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _safe_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None
