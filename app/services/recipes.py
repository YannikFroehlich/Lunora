"""Recipes: reading the ingredient text and putting ingredients on a shopping list."""

import re
from collections import defaultdict
from datetime import timedelta
from fractions import Fraction

from app.models import MealPlanEntry
from app.services.shopping_products import add_shopping_item

MAX_RECIPES = 500
MAX_MEALS_PER_DAY = 6
WEEKDAY_NAMES = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")

_NUMBER = r"(?:\d+\s+\d+/\d+|\d+/\d+|\d+(?:[.,]\d+)?|[½⅓⅔¼¾⅛])"
_UNITS = (
    "g|gr|gramm|kg|kilo|mg|ml|cl|dl|l|liter|el|tl|esslöffel|teelöffel|msp|messerspitze|prise|prisen|"
    "pck|päckchen|packung|packungen|dose|dosen|bund|stück|stk|becher|tasse|tassen|glas|gläser|zehe|zehen|"
    "scheibe|scheiben|blatt|blätter|zweig|zweige|handvoll|würfel|kugel|kugeln|flasche|flaschen|beutel|"
    "tüte|tüten|schuss|spritzer|stange|stangen|knolle|knollen|kopf|köpfe|rolle|rollen"
)
# "200 g Mehl", "1 1/2 TL Zimt", "2-3 Zehen Knoblauch", "ca. 500ml Milch", "2 Eier".
_INGREDIENT_RE = re.compile(
    rf"^(?P<quantity>(?:ca\.\s*)?{_NUMBER}(?:\s*[-–]\s*{_NUMBER})?(?:\s*(?:{_UNITS})\.?(?=\s|$))?)\s+(?P<name>\S.*)$",
    re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^(?:[-*•·]|\d+[.)])\s+")
_NUMBER_RE = re.compile(_NUMBER)
_VULGAR_FRACTIONS = {"½": Fraction(1, 2), "⅓": Fraction(1, 3), "⅔": Fraction(2, 3), "¼": Fraction(1, 4)}
_VULGAR_FRACTIONS |= {"¾": Fraction(3, 4), "⅛": Fraction(1, 8)}
_NAME_MAX = 120
_QUANTITY_MAX = 40
# Units that convert into each other when quantities are added up; any other unit only adds to itself.
_UNIT_BASES = {"g": ("g", 1), "gr": ("g", 1), "gramm": ("g", 1), "kg": ("g", 1000), "kilo": ("g", 1000)}
_UNIT_BASES |= {
    "ml": ("ml", 1),
    "cl": ("ml", 10),
    "dl": ("ml", 100),
    "l": ("ml", 1000),
    "liter": ("ml", 1000),
}
_LARGE_UNITS = {"g": "kg", "ml": "l"}
_PLAIN_QUANTITY_RE = re.compile(rf"^(?P<number>{_NUMBER})\s*(?P<unit>[^\d\s\-–/][^\d]*)?$")


def parse_ingredient(line):
    """Splits one ingredient line into (quantity, name); quantity is "" when the line has none."""
    line = " ".join(_BULLET_RE.sub("", line.strip()).split())
    match = _INGREDIENT_RE.match(line)
    if match is None:
        return "", line
    return match["quantity"], match["name"]


def _number_value(text):
    """The value of one _NUMBER match: "1 1/2", "1/2", "1,5", "200" or "½"."""
    if text in _VULGAR_FRACTIONS:
        return _VULGAR_FRACTIONS[text]
    if "/" in text:
        whole, _space, fraction = text.rpartition(" ")
        numerator, denominator = fraction.split("/")
        return int(whole or 0) + Fraction(int(numerator), int(denominator) or 1)
    return Fraction(text.replace(",", "."))


def _number_label(value):
    """German number for a shopping list: whole numbers stay whole, from 10 up it's rounded."""
    if value >= 10 or value.denominator == 1:
        return str(int(value + Fraction(1, 2)))  # half up, not round()'s half to even
    return f"{float(value):.2f}".rstrip("0").rstrip(".").replace(".", ",")


def scale_quantity(quantity, factor):
    """Every number in `quantity` times `factor`: "200 g" × 1,5 → "300 g", "2-3 Zehen" × 2 → "4-6 Zehen"."""
    if factor == 1 or not quantity:
        return quantity
    factor = Fraction(factor)
    return _NUMBER_RE.sub(lambda match: _number_label(_number_value(match[0]) * factor), quantity)


def _amount(quantity):
    """(value, unit key, unit text) of a plain "<number> <unit>" quantity; None for ranges, "ca." etc."""
    match = _PLAIN_QUANTITY_RE.match(quantity.strip())
    if match is None:
        return None
    unit = (match["unit"] or "").strip().rstrip(".")
    base, scale = _UNIT_BASES.get(unit.lower(), (unit.lower(), 1))
    return _number_value(match["number"]) * scale, base, base if unit.lower() in _UNIT_BASES else unit


def add_quantities(first, second):
    """ "200 g" + "300 g" → "500 g", "800 ml" + "0,5 l" → "1,3 l", "2" + "1" → "3"; quantities that
    don't add up ("1 Zehe" + "2 Zehen", "2-3") are kept side by side as "1 Zehe + 2 Zehen"."""
    one, other = _amount(first), _amount(second)
    if one is None or other is None or one[1] != other[1]:
        joined = f"{first} + {second}"
        return joined if len(joined) <= _QUANTITY_MAX else second
    total, unit = one[0] + other[0], one[2]
    if unit in _LARGE_UNITS and total >= 1000:
        total, unit = total / 1000, _LARGE_UNITS[unit]
    return f"{_number_label(total)} {unit}".strip()


def ingredient_rows(text):
    """The recipe's ingredient lines in order, each a heading or an ingredient with its list name.

    The list name drops preparation notes ("Zwiebeln, gewürfelt" → "Zwiebeln"), so the shopping
    list sorts it into the right aisle and finds the row if it is already there.
    """
    rows = []
    for index, raw in enumerate(text.splitlines()):
        line = raw.strip()
        if not line:
            continue
        if line.endswith(":"):
            rows.append({"index": index, "heading": line[:-1].strip()})
            continue
        quantity, name = parse_ingredient(line)
        item_name = re.split(r"[,(]", name, maxsplit=1)[0].strip() or name
        rows.append(
            {
                "index": index,
                "text": line,
                "quantity": quantity[:_QUANTITY_MAX],
                "name": item_name[:_NAME_MAX],
                # The line without its quantity, preparation notes kept, for a rescaled display.
                "label": name,
            }
        )
    return rows


def add_ingredients_to_list(recipe, line_indexes, list_owner, factor=1):
    """Puts the chosen ingredient lines on `list_owner`'s list, quantities times `factor`;
    returns (added count, error or None)."""
    wanted = set(line_indexes)
    added = 0
    for row in ingredient_rows(recipe.ingredients):
        if "heading" in row or row["index"] not in wanted:
            continue
        error = add_shopping_item(
            list_owner,
            row["name"],
            scale_quantity(row["quantity"], factor)[:_QUANTITY_MAX],
            merge=add_quantities,
        )
        if error:
            return added, error
        added += 1
    return added, None


def meal_plan_week(owner, week_start):
    """The seven days from Monday `week_start`, each with its planned entries (recipe loaded)."""
    entries = MealPlanEntry.objects.filter(
        owner=owner, date__range=(week_start, week_start + timedelta(days=6))
    ).select_related("recipe")
    days = [
        {"date": week_start + timedelta(days=offset), "name": WEEKDAY_NAMES[offset], "entries": []}
        for offset in range(7)
    ]
    for entry in entries:
        days[(entry.date - week_start).days]["entries"].append(entry)
    return days


def plan_recipe(owner, recipe, day):
    """Puts `recipe` on `day` of the owner's plan; returns an error message or None."""
    if MealPlanEntry.objects.filter(owner=owner, date=day).count() >= MAX_MEALS_PER_DAY:
        return f"An einem Tag lassen sich höchstens {MAX_MEALS_PER_DAY} Rezepte planen."
    MealPlanEntry.objects.create(owner=owner, recipe=recipe, date=day)
    return None


def add_week_to_list(owner, week_start, list_owner):
    """Puts every ingredient of the week's planned recipes on `list_owner`'s list.

    Returns (recipe count, error or None). A recipe with a number of servings is scaled to the
    servings planned for it over the whole week; one without counts once however often it is
    planned. An ingredient several recipes share lands on the list once with the quantities added
    up (see add_quantities), so sending the same week twice doubles them.
    """
    recipes = {}
    planned_servings = defaultdict(int)
    for entry in MealPlanEntry.objects.filter(
        owner=owner, date__range=(week_start, week_start + timedelta(days=6))
    ).select_related("recipe"):
        recipes[entry.recipe_id] = entry.recipe
        planned_servings[entry.recipe_id] += entry.servings or entry.recipe.servings or 0
    for recipe in recipes.values():
        factor = Fraction(planned_servings[recipe.pk], recipe.servings) if recipe.servings else 1
        indexes = [row["index"] for row in ingredient_rows(recipe.ingredients)]
        _added, error = add_ingredients_to_list(recipe, indexes, list_owner, factor)
        if error:
            return len(recipes), error
    return len(recipes), None
