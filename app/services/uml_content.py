import json
import math
import re

from django.core.exceptions import ValidationError

UML_DOCUMENT_VERSION = 1

CLASS_KINDS = {"class", "abstract", "interface", "enum"}
RELATION_KINDS = {
    "association",
    "directed_association",
    "aggregation",
    "composition",
    "inheritance",
    "realization",
    "dependency",
    "note_link",
}
# Generalizations cannot point at their own source; every other kind may be reflexive.
NON_REFLEXIVE_RELATION_KINDS = {"inheritance", "realization"}
CLASS_COLORS = {"default", "blue", "green", "yellow", "red", "violet", "gray"}
VISIBILITIES = {"", "+", "-", "#", "~"}

MAX_DOCUMENT_BYTES = 1_000_000
MAX_CLASSES = 300
MAX_RELATIONS = 600
MAX_NOTES = 200
MAX_MEMBERS = 80
MAX_COORDINATE = 100_000

ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

DOCUMENT_KEYS = {"version", "classes", "relations", "notes"}
CLASS_KEYS = {"id", "kind", "name", "stereotype", "x", "y", "color", "attributes", "methods", "literals"}
ATTRIBUTE_KEYS = {"visibility", "name", "type", "default", "is_static"}
METHOD_KEYS = {"visibility", "name", "params", "return_type", "is_static", "is_abstract"}
RELATION_KEYS = {"id", "kind", "source", "target", "label", "source_multiplicity", "target_multiplicity"}
NOTE_KEYS = {"id", "text", "x", "y"}


def empty_uml_document():
    return {"version": UML_DOCUMENT_VERSION, "classes": [], "relations": [], "notes": []}


def validate_uml_document(document):
    """Return a cleaned copy of a client-supplied UML class diagram or raise ValidationError."""
    if not isinstance(document, dict):
        raise ValidationError("Das Diagramm ist ungültig.")
    try:
        encoded_size = len(json.dumps(document, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError) as error:
        raise ValidationError("Das Diagramm enthält ungültige Daten.") from error
    if encoded_size > MAX_DOCUMENT_BYTES:
        raise ValidationError("Das Diagramm ist zu groß.")

    _check_keys(document, DOCUMENT_KEYS, "Das Diagramm")
    if type(document["version"]) is not int or document["version"] != UML_DOCUMENT_VERSION:
        raise ValidationError("Die Diagrammversion wird nicht unterstützt.")

    classes = _list(document["classes"], "Klassen", MAX_CLASSES)
    notes = _list(document["notes"], "Notizen", MAX_NOTES)
    relations = _list(document["relations"], "Beziehungen", MAX_RELATIONS)

    element_kinds = {}
    cleaned_classes = [_clean_class(item, element_kinds) for item in classes]
    cleaned_notes = [_clean_note(item, element_kinds) for item in notes]
    relation_ids = set()
    cleaned_relations = [_clean_relation(item, element_kinds, relation_ids) for item in relations]

    return {
        "version": UML_DOCUMENT_VERSION,
        "classes": cleaned_classes,
        "relations": cleaned_relations,
        "notes": cleaned_notes,
    }


def _clean_class(item, element_kinds):
    _check_keys(item, CLASS_KEYS, "Eine Klasse")
    element_id = _element_id(item["id"], element_kinds)
    kind = item["kind"]
    if kind not in CLASS_KINDS:
        raise ValidationError("Die Klassenart ist ungültig.")
    color = item["color"]
    if color not in CLASS_COLORS:
        raise ValidationError("Die Klassenfarbe ist ungültig.")
    name = _text(item["name"], "Der Klassenname", 120)
    if not name.strip():
        raise ValidationError("Jede Klasse braucht einen Namen.")
    element_kinds[element_id] = "class"
    return {
        "id": element_id,
        "kind": kind,
        "name": name,
        "stereotype": _text(item["stereotype"], "Der Stereotyp", 60),
        "x": _coordinate(item["x"]),
        "y": _coordinate(item["y"]),
        "color": color,
        "attributes": [
            _clean_attribute(entry) for entry in _list(item["attributes"], "Attribute", MAX_MEMBERS)
        ],
        "methods": [_clean_method(entry) for entry in _list(item["methods"], "Methoden", MAX_MEMBERS)],
        "literals": [
            _text(entry, "Ein Enum-Wert", 120) for entry in _list(item["literals"], "Enum-Werte", MAX_MEMBERS)
        ],
    }


def _clean_attribute(item):
    _check_keys(item, ATTRIBUTE_KEYS, "Ein Attribut")
    return {
        "visibility": _visibility(item["visibility"]),
        "name": _member_name(item["name"], "Der Attributname"),
        "type": _text(item["type"], "Der Attributtyp", 120),
        "default": _text(item["default"], "Der Standardwert", 200),
        "is_static": _bool(item["is_static"]),
    }


def _clean_method(item):
    _check_keys(item, METHOD_KEYS, "Eine Methode")
    return {
        "visibility": _visibility(item["visibility"]),
        "name": _member_name(item["name"], "Der Methodenname"),
        "params": _text(item["params"], "Die Parameterliste", 400),
        "return_type": _text(item["return_type"], "Der Rückgabetyp", 120),
        "is_static": _bool(item["is_static"]),
        "is_abstract": _bool(item["is_abstract"]),
    }


def _clean_note(item, element_kinds):
    _check_keys(item, NOTE_KEYS, "Eine Notiz")
    element_id = _element_id(item["id"], element_kinds)
    element_kinds[element_id] = "note"
    return {
        "id": element_id,
        "text": _text(item["text"], "Der Notiztext", 2000),
        "x": _coordinate(item["x"]),
        "y": _coordinate(item["y"]),
    }


def _clean_relation(item, element_kinds, relation_ids):
    _check_keys(item, RELATION_KEYS, "Eine Beziehung")
    relation_id = item["id"]
    if not isinstance(relation_id, str) or not ID_PATTERN.match(relation_id) or relation_id in relation_ids:
        raise ValidationError("Eine Beziehung hat eine ungültige oder doppelte ID.")
    relation_ids.add(relation_id)

    kind = item["kind"]
    if kind not in RELATION_KINDS:
        raise ValidationError("Die Beziehungsart ist ungültig.")
    source, target = item["source"], item["target"]
    if source not in element_kinds or target not in element_kinds:
        raise ValidationError("Eine Beziehung verweist auf ein unbekanntes Element.")
    endpoint_kinds = {element_kinds[source], element_kinds[target]}
    if kind == "note_link":
        if "note" not in endpoint_kinds or source == target:
            raise ValidationError("Eine Notizverbindung muss eine Notiz mit einem anderen Element verbinden.")
    elif endpoint_kinds != {"class"}:
        raise ValidationError("Notizen können nur über Notizverbindungen verknüpft werden.")
    if kind in NON_REFLEXIVE_RELATION_KINDS and source == target:
        raise ValidationError("Eine Klasse kann nicht von sich selbst erben.")

    return {
        "id": relation_id,
        "kind": kind,
        "source": source,
        "target": target,
        "label": _text(item["label"], "Die Beschriftung", 120),
        "source_multiplicity": _text(item["source_multiplicity"], "Die Multiplizität", 20),
        "target_multiplicity": _text(item["target_multiplicity"], "Die Multiplizität", 20),
    }


def _check_keys(item, expected_keys, label):
    if not isinstance(item, dict) or set(item) != expected_keys:
        raise ValidationError(f"{label} enthält unbekannte oder fehlende Felder.")


def _list(value, label, limit):
    if not isinstance(value, list):
        raise ValidationError(f"{label} müssen eine Liste sein.")
    if len(value) > limit:
        raise ValidationError(f"Es sind höchstens {limit} {label} erlaubt.")
    return value


def _element_id(value, element_kinds):
    if not isinstance(value, str) or not ID_PATTERN.match(value) or value in element_kinds:
        raise ValidationError("Ein Element hat eine ungültige oder doppelte ID.")
    return value


def _text(value, label, max_length):
    if not isinstance(value, str):
        raise ValidationError(f"{label} muss Text sein.")
    if len(value) > max_length:
        raise ValidationError(f"{label} darf höchstens {max_length} Zeichen lang sein.")
    return value


def _member_name(value, label):
    name = _text(value, label, 120)
    if not name.strip():
        raise ValidationError(f"{label} darf nicht leer sein.")
    return name


def _visibility(value):
    if value not in VISIBILITIES:
        raise ValidationError("Die Sichtbarkeit ist ungültig.")
    return value


def _bool(value):
    if not isinstance(value, bool):
        raise ValidationError("Ein Schalterwert ist ungültig.")
    return value


def _coordinate(value):
    # bool is an int subclass, so reject it explicitly before the numeric check.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValidationError("Eine Position ist ungültig.")
    if abs(value) > MAX_COORDINATE:
        raise ValidationError("Eine Position liegt außerhalb der Zeichenfläche.")
    return round(float(value), 1)
