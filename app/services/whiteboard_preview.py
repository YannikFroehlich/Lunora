"""A small server-rendered SVG of a whiteboard for the overview page.

The stored document is validated on save, but this is still a second trust boundary into HTML:
numbers go through float(), colors through a fixed table and text through format_html's escaping.
"""

from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

# Mirrors INK and TEXT_SIZES in app/static/js/whiteboard.js / whiteboard_format.js.
INK = {
    "default": "#2b2520",
    "blue": "#2f6fb5",
    "green": "#3a8a4e",
    "yellow": "#d1a013",
    "red": "#c0453f",
    "violet": "#7a55b0",
    "gray": "#8c8c8c",
}
TEXT_SIZES = {"s": 16, "m": 24, "l": 40}
TEXT_LINE_HEIGHT = 1.25
# A board may hold 150,000 points; the thumbnail keeps about this many so the list page stays small.
MAX_PREVIEW_POINTS = 1200
MARGIN = 16


def whiteboard_preview_svg(document):
    """The thumbnail markup for a board, or "" when it is empty or not drawable."""
    try:
        elements = document["elements"]
        if not elements:
            return ""
        total = sum(len(item["points"]) // 2 for item in elements if "points" in item)
        step = max(1, -(-total // MAX_PREVIEW_POINTS))
        shapes = [_shape(item, step) for item in elements]
        boxes = [box for _markup, box in shapes]
    except (KeyError, TypeError, ValueError, IndexError):
        return ""
    x1 = min(box[0] for box in boxes) - MARGIN
    y1 = min(box[1] for box in boxes) - MARGIN
    width = max(box[2] for box in boxes) + MARGIN - x1
    height = max(box[3] for box in boxes) + MARGIN - y1
    return format_html(
        '<svg viewBox="{} {} {} {}" focusable="false">{}</svg>',
        _number(x1),
        _number(y1),
        _number(max(width, 1)),
        _number(max(height, 1)),
        mark_safe("".join(markup for markup, _box in shapes)),  # noqa: S308 - built by format_html below
    )


def _number(value):
    return f"{float(value):.1f}".rstrip("0").rstrip(".")


def _line_attrs(item):
    # Thumbnails are tiny, so every line gets the same visible width regardless of the drawing's size.
    return format_html(
        'fill="none" stroke="{}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" '
        'vector-effect="non-scaling-stroke"',
        INK.get(item["color"], INK["default"]),
    )


def _shape(item, step):
    kind = item["type"]
    color = INK.get(item["color"], INK["default"])
    if kind in ("stroke", "highlight"):
        values = [float(value) for value in item["points"]]
        pairs = list(zip(values[0::2], values[1::2], strict=True))
        kept = pairs[::step]
        if kept[-1] != pairs[-1]:
            kept.append(pairs[-1])
        if len(kept) == 1:
            kept.append(kept[0])
        markup = format_html(
            '<polyline points="{}" {}{}/>',
            " ".join(f"{_number(x)},{_number(y)}" for x, y in kept),
            _line_attrs(item),
            mark_safe(' stroke-opacity="0.4" stroke-width="5"') if kind == "highlight" else "",
        )
        xs, ys = values[0::2], values[1::2]
        return markup, (min(xs), min(ys), max(xs), max(ys))
    if kind in ("line", "arrow"):
        x1, y1, x2, y2 = (float(item[key]) for key in ("x1", "y1", "x2", "y2"))
        markup = format_html(
            '<line x1="{}" y1="{}" x2="{}" y2="{}" {}/>',
            _number(x1),
            _number(y1),
            _number(x2),
            _number(y2),
            _line_attrs(item),
        )
        return markup, (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    x, y = float(item["x"]), float(item["y"])
    if kind == "text":
        size = TEXT_SIZES[item["size"]]
        lines = item["text"].split("\n")
        markup = format_html(
            '<text font-size="{}" fill="{}" font-family="Inter, sans-serif">{}</text>',
            size,
            color,
            format_html_join(
                "",
                '<tspan x="{}" y="{}">{}</tspan>',
                (
                    (_number(x), _number(y + size * (1.05 + index * TEXT_LINE_HEIGHT)), line)
                    for index, line in enumerate(lines)
                ),
            ),
        )
        width = max(len(line) for line in lines) * size * 0.55
        return markup, (x, y, x + width, y + len(lines) * size * TEXT_LINE_HEIGHT)
    width, height = float(item["w"]), float(item["h"])
    fill = format_html(' style="fill:{};fill-opacity:0.3"', color) if item["fill"] else ""
    if kind == "rect":
        markup = format_html(
            '<rect x="{}" y="{}" width="{}" height="{}" {}{}/>',
            _number(x),
            _number(y),
            _number(width),
            _number(height),
            _line_attrs(item),
            fill,
        )
    elif kind == "ellipse":
        markup = format_html(
            '<ellipse cx="{}" cy="{}" rx="{}" ry="{}" {}{}/>',
            _number(x + width / 2),
            _number(y + height / 2),
            _number(width / 2),
            _number(height / 2),
            _line_attrs(item),
            fill,
        )
    else:
        raise ValueError(kind)
    return markup, (x, y, x + width, y + height)
