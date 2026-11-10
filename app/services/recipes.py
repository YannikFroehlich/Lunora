"""Recipes: reading the ingredient text and putting ingredients on a shopping list."""

import re
from datetime import timedelta

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
_NAME_MAX = 120
_QUANTITY_MAX = 40


def parse_ingredient(line):
    """Splits one ingredient line into (quantity, name); quantity is "" when the line has none."""
    line = " ".join(_BULLET_RE.sub("", line.strip()).split())
    match = _INGREDIENT_RE.match(line)
    if match is None:
        return "", line
    return match["quantity"], match["name"]


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
            }
        )
    return rows


def add_ingredients_to_list(recipe, line_indexes, list_owner):
    """Puts the chosen ingredient lines on `list_owner`'s list; returns (added count, error or None)."""
    wanted = set(line_indexes)
    added = 0
    for row in ingredient_rows(recipe.ingredients):
        if "heading" in row or row["index"] not in wanted:
            continue
        error = add_shopping_item(list_owner, row["name"], row["quantity"])
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

    Returns (recipe count, error or None). A recipe planned twice counts once, and an ingredient
    several recipes share lands on the list once, because adding an item already there brings that
    row back instead of writing a second one (quantities are not added up).
    """
    recipes = {
        entry.recipe_id: entry.recipe
        for entry in MealPlanEntry.objects.filter(
            owner=owner, date__range=(week_start, week_start + timedelta(days=6))
        ).select_related("recipe")
    }
    for recipe in recipes.values():
        indexes = [row["index"] for row in ingredient_rows(recipe.ingredients)]
        _added, error = add_ingredients_to_list(recipe, indexes, list_owner)
        if error:
            return len(recipes), error
    return len(recipes), None
