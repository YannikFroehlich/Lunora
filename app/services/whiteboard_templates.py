"""Ready-made starting boards for "Neues Whiteboard" (WHITEBOARD_TEMPLATES).

Every template is a plain list of elements that passes validate_whiteboard_document, so a new board
built from one is an ordinary board afterwards. The mind map binds its arrows to the bubbles, which
shows off that arrows follow their shapes.
"""

import math

from app.services.whiteboard_content import WHITEBOARD_DOCUMENT_VERSION, validate_whiteboard_document

# Space between a bound arrow end and its shape (CONNECTOR_GAP in whiteboard_format.js).
CONNECTOR_GAP = 6


def _text(element_id, x, y, text, size="m", color="default"):
    return {"id": element_id, "type": "text", "color": color, "x": x, "y": y, "size": size, "text": text}


def _rect(element_id, x, y, w, h, color="default", fill=False):
    return {
        "id": element_id,
        "type": "rect",
        "color": color,
        "width": 2,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "fill": fill,
    }


def _sticky(element_id, x, y, color="yellow", text=""):
    return {
        "id": element_id,
        "type": "sticky",
        "color": color,
        "x": x,
        "y": y,
        "w": 200,
        "h": 200,
        "text": text,
    }


def _ellipse_edge(x, y, w, h, toward_x, toward_y):
    """Where the line from the ellipse's center to (toward_x, toward_y) leaves it, plus the gap."""
    cx, cy, rx, ry = x + w / 2, y + h / 2, w / 2, h / 2
    dx, dy = toward_x - cx, toward_y - cy
    scale = 1 / math.hypot(dx / rx, dy / ry)
    length = math.hypot(dx, dy)
    return (
        round(cx + dx * scale + dx / length * CONNECTOR_GAP, 1),
        round(cy + dy * scale + dy / length * CONNECTOR_GAP, 1),
    )


def _columns(titles, colors, *, width=280, height=640, gap=30, stickies=True):
    elements = []
    for index, (title, color) in enumerate(zip(titles, colors, strict=True)):
        x = index * (width + gap)
        elements.append(_rect(f"col{index}", x, 0, width, height, color))
        elements.append(_text(f"head{index}", x + 20, 20, title, "l", color))
        if stickies:
            elements.append(_sticky(f"note{index}", x + 40, 110, "yellow" if color == "default" else color))
    return elements


def _retro():
    return _columns(["Gut gelaufen", "Verbessern", "Ideen"], ["green", "red", "blue"])


def _kanban():
    return _columns(["Offen", "In Arbeit", "Erledigt"], ["gray", "blue", "green"])


def _week():
    days = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    return _columns(days, ["default"] * 5 + ["violet"] * 2, width=220, height=520, gap=16, stickies=False)


def _swot():
    quadrants = [("Stärken", "green"), ("Schwächen", "red"), ("Chancen", "blue"), ("Risiken", "yellow")]
    elements = []
    for index, (title, color) in enumerate(quadrants):
        x, y = (index % 2) * 440, (index // 2) * 340
        elements.append(_rect(f"q{index}", x, y, 420, 320, color))
        elements.append(_text(f"t{index}", x + 20, y + 20, title, "l", color))
    return elements


def _mindmap():
    center = {
        "id": "center",
        "type": "ellipse",
        "color": "violet",
        "width": 4,
        "x": -110,
        "y": -50,
        "w": 220,
        "h": 100,
        "fill": True,
    }
    elements = [center, _text("center-label", -45, -16, "Thema", "l")]
    branches = [("Idee 1", -420, -260), ("Idee 2", 240, -260), ("Idee 3", -420, 180), ("Idee 4", 240, 180)]
    for index, (label, x, y) in enumerate(branches):
        bubble_id = f"branch{index}"
        elements.append(
            {
                "id": bubble_id,
                "type": "ellipse",
                "color": "blue",
                "width": 2,
                "x": x,
                "y": y,
                "w": 180,
                "h": 80,
                "fill": False,
            }
        )
        elements.append(_text(f"branch{index}-label", x + 50, y + 26, label))
        start_x, start_y = _ellipse_edge(-110, -50, 220, 100, x + 90, y + 40)
        end_x, end_y = _ellipse_edge(x, y, 180, 80, 0, 0)
        elements.append(
            {
                "id": f"link{index}",
                "type": "arrow",
                "color": "default",
                "width": 2,
                "x1": start_x,
                "y1": start_y,
                "x2": end_x,
                "y2": end_y,
                "from": "center",
                "to": bubble_id,
            }
        )
    return elements


WHITEBOARD_TEMPLATES = {
    "blank": ("Leer", list),
    "retro": ("Retrospektive", _retro),
    "kanban": ("Kanban", _kanban),
    "mindmap": ("Mindmap", _mindmap),
    "swot": ("SWOT-Analyse", _swot),
    "week": ("Wochenplan", _week),
}


def whiteboard_template_document(key):
    """A validated document for the template `key` (unknown keys give an empty board)."""
    _label, build = WHITEBOARD_TEMPLATES.get(key, WHITEBOARD_TEMPLATES["blank"])
    return validate_whiteboard_document({"version": WHITEBOARD_DOCUMENT_VERSION, "elements": build()})
