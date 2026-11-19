import hashlib
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
    # A highlighter stroke: same data, drawn wide and translucent.
    "highlight": COMMON_KEYS | {"width", "points"},
    "line": COMMON_KEYS | {"width", "x1", "y1", "x2", "y2"},
    "arrow": COMMON_KEYS | {"width", "x1", "y1", "x2", "y2"},
    "rect": COMMON_KEYS | {"width", "x", "y", "w", "h", "fill"},
    "ellipse": COMMON_KEYS | {"width", "x", "y", "w", "h", "fill"},
    "text": COMMON_KEYS | {"x", "y", "size", "text"},
    # A sticky note: a tinted box with wrapped text inside; the text may still be empty.
    "sticky": COMMON_KEYS | {"x", "y", "w", "h", "text"},
}
TEXT_TYPES = ("text", "sticky")


def empty_whiteboard_document():
    return {"version": WHITEBOARD_DOCUMENT_VERSION, "elements": []}


def whiteboard_plain_text(document):
    """The board's text elements joined by newlines; tolerates broken documents."""
    elements = document.get("elements") if isinstance(document, dict) else None
    if not isinstance(elements, list):
        return ""
    return "\n".join(
        item["text"]
        for item in elements
        if isinstance(item, dict) and item.get("type") in TEXT_TYPES and isinstance(item.get("text"), str)
    )


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
    if kind == "sticky":
        cleaned.update(
            x=_coordinate(item["x"]),
            y=_coordinate(item["y"]),
            w=_size(item["w"]),
            h=_size(item["h"]),
            text=_text(item["text"], "Der Text", MAX_TEXT_LENGTH),
        )
        return cleaned

    width = item["width"]
    if type(width) is not int or width not in STROKE_WIDTHS:
        raise ValidationError("Die Strichstärke ist ungültig.")
    cleaned["width"] = width
    if kind in ("stroke", "highlight"):
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


# ---- Live editing -------------------------------------------------------------------------------
# A change is one operation on the element list. The server applies operations in the order it
# received them and every editor applies the same ones in the same order with the same rules
# (applyOps in app/static/js/whiteboard_format.js mirrors apply_whiteboard_ops), using the elements
# exactly as the server cleaned them; whiteboard_hash lets editors prove they ended up identical.

MAX_OPS_PER_PUSH = 2 * MAX_ELEMENTS + 1
OP_KEYS = {
    # "after" places the element right above that one (None: at the bottom, unknown: on top);
    # a put without "after" keeps an existing element where it is.
    "put": ({"op", "seq", "element"}, {"after"}),
    "move": ({"op", "seq", "id", "after"}, set()),
    "delete": ({"op", "seq", "id"}, set()),
    "title": ({"op", "seq", "title"}, set()),
}


def clean_whiteboard_op(op):
    """A checked copy of one client operation (elements cleaned like in a document)."""
    if not isinstance(op, dict) or op.get("op") not in OP_KEYS:
        raise ValidationError("Eine Änderung ist ungültig.")
    required, optional = OP_KEYS[op["op"]]
    if not required <= set(op) or set(op) - required - optional:
        raise ValidationError("Eine Änderung enthält unbekannte oder fehlende Felder.")
    if type(op["seq"]) is not int or op["seq"] <= 0:
        raise ValidationError("Eine Änderung ist ungültig.")
    cleaned = {"op": op["op"], "seq": op["seq"]}
    if op["op"] == "title":
        if not isinstance(op["title"], str):
            raise ValidationError("Der Titel ist ungültig.")
        cleaned["title"] = op["title"]
        return cleaned
    if op["op"] == "put":
        cleaned["element"] = validate_whiteboard_document(
            {"version": WHITEBOARD_DOCUMENT_VERSION, "elements": [op["element"]]}
        )["elements"][0]
        target = cleaned["element"]["id"]
    else:
        target = _unique_id(op["id"], set())
        cleaned["id"] = target
    if "after" in op:
        after = op["after"]
        if after is not None and (
            not isinstance(after, str) or not ID_PATTERN.match(after) or after == target
        ):
            raise ValidationError("Eine Änderung verweist auf ein ungültiges Element.")
        cleaned["after"] = after
    return cleaned


def _index_of(elements, element_id):
    return next((index for index, item in enumerate(elements) if item["id"] == element_id), None)


def apply_whiteboard_ops(elements, ops):
    """The element list after the operations; never fails, so every editor gets the same result."""
    # ponytail: linear id lookups, O(ops × elements); fine up to an import of a full board.
    result = list(elements)
    for op in ops:
        kind = op["op"]
        if kind == "title":
            continue
        target = op["element"]["id"] if kind == "put" else op["id"]
        current = _index_of(result, target)
        if current is None and kind != "put":
            continue
        previous = result.pop(current) if current is not None else None
        if kind == "delete":
            continue
        element = op["element"] if kind == "put" else previous
        if "after" not in op:
            index = len(result) if current is None else current
        elif op["after"] is None:
            index = 0
        else:
            anchor = _index_of(result, op["after"])
            index = len(result) if anchor is None else anchor + 1
        result.insert(index, element)
    return result


def _canonical(value):
    # JavaScript writes 5.0 as 5; everything else already serializes the same in both languages.
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    return value


def whiteboard_hash(elements):
    """SHA-256 of the elements as sorted-key, ASCII-escaped JSON (boardHash in whiteboard_format.js)."""
    text = json.dumps(_canonical(elements), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()
