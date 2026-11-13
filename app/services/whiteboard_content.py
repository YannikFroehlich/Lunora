import json

from django.core.exceptions import ValidationError

from app.services.uml_content import ID_PATTERN, _bool, _check_keys, _coordinate, _list, _text

WHITEBOARD_DOCUMENT_VERSION = 1

COLORS = {"default", "blue", "green", "yellow", "red", "violet", "gray"}
STROKE_WIDTHS = {2, 4, 8}
TEXT_SIZES = {"s", "m", "l"}

MAX_DOCUMENT_BYTES = 1_000_000
MAX_ELEMENTS = 3000
MAX_STROKE_POINTS = 2000
MAX_TOTAL_POINTS = 150_000
MAX_TEXT_LENGTH = 500

DOCUMENT_KEYS = {"version", "elements"}
COMMON_KEYS = {"id", "type", "color"}
ELEMENT_KEYS = {
    "stroke": COMMON_KEYS | {"width", "points"},
    "line": COMMON_KEYS | {"width", "x1", "y1", "x2", "y2"},
    "arrow": COMMON_KEYS | {"width", "x1", "y1", "x2", "y2"},
    "rect": COMMON_KEYS | {"width", "x", "y", "w", "h", "fill"},
    "ellipse": COMMON_KEYS | {"width", "x", "y", "w", "h", "fill"},
    "text": COMMON_KEYS | {"x", "y", "size", "text"},
}


def empty_whiteboard_document():
    return {"version": WHITEBOARD_DOCUMENT_VERSION, "elements": []}


def validate_whiteboard_document(document):
    """Return a cleaned copy of a client-supplied whiteboard or raise ValidationError."""
    if not isinstance(document, dict):
        raise ValidationError("Das Whiteboard ist ungültig.")
    try:
        encoded_size = len(json.dumps(document, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError) as error:
        raise ValidationError("Das Whiteboard enthält ungültige Daten.") from error
    if encoded_size > MAX_DOCUMENT_BYTES:
        raise ValidationError("Das Whiteboard ist zu groß.")

    _check_keys(document, DOCUMENT_KEYS, "Das Whiteboard")
    if type(document["version"]) is not int or document["version"] != WHITEBOARD_DOCUMENT_VERSION:
        raise ValidationError("Die Whiteboard-Version wird nicht unterstützt.")

    elements = _list(document["elements"], "Elemente", MAX_ELEMENTS)
    seen_ids = set()
    budget = {"points": MAX_TOTAL_POINTS}
    try:
        cleaned = [_clean_element(item, seen_ids, budget) for item in elements]
    except (TypeError, OverflowError) as error:
        # A list where an allowlist expects a string, or an integer too large for a float.
        raise ValidationError("Das Whiteboard enthält ungültige Daten.") from error
    return {"version": WHITEBOARD_DOCUMENT_VERSION, "elements": cleaned}


def _clean_element(item, seen_ids, budget):
    if not isinstance(item, dict):
        raise ValidationError("Ein Element ist ungültig.")
    kind = item.get("type")
    if kind not in ELEMENT_KEYS:
        raise ValidationError("Die Elementart ist ungültig.")
    _check_keys(item, ELEMENT_KEYS[kind], "Ein Element")
    if item["color"] not in COLORS:
        raise ValidationError("Die Farbe ist ungültig.")
    element_id = _unique_id(item["id"], seen_ids)
    cleaned = {"id": element_id, "type": kind, "color": item["color"]}

    if kind == "text":
        if item["size"] not in TEXT_SIZES:
            raise ValidationError("Die Textgröße ist ungültig.")
        text = _text(item["text"], "Der Text", MAX_TEXT_LENGTH)
        if not text.strip():
            raise ValidationError("Ein Textelement darf nicht leer sein.")
        cleaned.update(x=_coordinate(item["x"]), y=_coordinate(item["y"]), size=item["size"], text=text)
        return cleaned

    width = item["width"]
    if type(width) is not int or width not in STROKE_WIDTHS:
        raise ValidationError("Die Strichstärke ist ungültig.")
    cleaned["width"] = width
    if kind == "stroke":
        cleaned["points"] = _points(item["points"], budget)
    elif kind in ("line", "arrow"):
        cleaned.update({key: _coordinate(item[key]) for key in ("x1", "y1", "x2", "y2")})
    else:
        cleaned.update(
            x=_coordinate(item["x"]),
            y=_coordinate(item["y"]),
            w=_size(item["w"]),
            h=_size(item["h"]),
            fill=_bool(item["fill"]),
        )
    return cleaned


def _unique_id(value, seen_ids):
    if not isinstance(value, str) or not ID_PATTERN.match(value) or value in seen_ids:
        raise ValidationError("Ein Element hat eine ungültige oder doppelte ID.")
    seen_ids.add(value)
    return value


def _points(value, budget):
    """A flat [x1, y1, x2, y2, ...] list; a single point is a dot."""
    if not isinstance(value, list) or not value or len(value) % 2:
        raise ValidationError("Ein Strich hat ungültige Punkte.")
    count = len(value) // 2
    if count > MAX_STROKE_POINTS:
        raise ValidationError(f"Ein Strich darf höchstens {MAX_STROKE_POINTS} Punkte haben.")
    budget["points"] -= count
    if budget["points"] < 0:
        raise ValidationError("Das Whiteboard enthält zu viele Punkte.")
    return [_coordinate(number) for number in value]


def _size(value):
    size = _coordinate(value)
    if size < 0:
        raise ValidationError("Eine Größe ist ungültig.")
    return size
