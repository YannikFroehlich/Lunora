"""Reading a recipe from a web page through the schema.org/Recipe data (JSON-LD) most recipe sites
embed for search engines. The page is fetched through the SSRF-safe fetcher; nothing else of it is
kept, and the result still goes through RecipeForm like a typed-in recipe."""

import html
import json
import re
from html.parser import HTMLParser

from app.services import safe_fetch
from app.services.throttle import count_hit

MAX_PAGE_BYTES = 3_000_000
USER_AGENT = "Lunora-Rezepte/1.0"
TITLE_MAX = 120
INGREDIENTS_MAX = 5000
INSTRUCTIONS_MAX = 20000
_MAX_DEPTH = 8

_TAG_RE = re.compile(r"<[^>]*>")
_BREAK_RE = re.compile(r"<\s*(?:br|/p|/li|/div)\s*/?\s*>", re.IGNORECASE)


class _JsonLdScripts(HTMLParser):
    """Collects the text of every <script type="application/ld+json"> on a page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        if tag == "script" and (dict(attrs).get("type") or "").strip().lower() == "application/ld+json":
            self._current = []

    def handle_data(self, data):
        if self._current is not None:
            self._current.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._current is not None:
            self.blocks.append("".join(self._current))
            self._current = None


def _is_recipe(value):
    kind = value.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return any(isinstance(item, str) and item.rsplit("/", 1)[-1] == "Recipe" for item in kinds)


def _recipe_objects(value, depth=0):
    """Every Recipe object in a JSON-LD value: top level, lists, @graph or mainEntity."""
    if depth > _MAX_DEPTH:
        return
    if isinstance(value, list):
        for item in value:
            yield from _recipe_objects(item, depth + 1)
    elif isinstance(value, dict):
        if _is_recipe(value):
            yield value
        for key in ("@graph", "mainEntity", "mainEntityOfPage"):
            if key in value:
                yield from _recipe_objects(value[key], depth + 1)


def _text(value):
    """Plain text from a JSON-LD string that may carry HTML tags and entities."""
    if not isinstance(value, (str, int, float)):
        return ""
    text = _BREAK_RE.sub("\n", str(value))
    text = html.unescape(_TAG_RE.sub("", text))
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _instruction_steps(value, depth=0):
    """The instruction paragraphs: a text, HowToStep objects, or HowToSection groups of them."""
    if depth > _MAX_DEPTH:
        return []
    if isinstance(value, list):
        return [step for item in value for step in _instruction_steps(item, depth + 1)]
    if isinstance(value, dict):
        if "itemListElement" in value:
            name = _text(value.get("name"))
            return ([f"{name}:"] if name else []) + _instruction_steps(value["itemListElement"], depth + 1)
        return _instruction_steps(value.get("text") or value.get("name"), depth + 1)
    text = _text(value)
    return text.split("\n") if text else []


def _servings(value):
    if isinstance(value, list):
        value = value[0] if value else None
    match = re.search(r"\d+", str(value)) if isinstance(value, (str, int)) else None
    servings = int(match.group()) if match else None
    return servings if servings and 1 <= servings <= 99 else None


def _capped_lines(lines, limit):
    """`lines` joined by newlines, dropping whole lines from the end to stay within `limit`."""
    kept = []
    size = 0
    for line in lines:
        size += len(line) + 1
        if size > limit:
            break
        kept.append(line)
    return "\n".join(kept)


def parse_recipe_page(page):
    """{title, servings, ingredients, instructions} from the first Recipe on the page, or None."""
    parser = _JsonLdScripts()
    parser.feed(page)
    parser.close()
    for block in parser.blocks:
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for recipe in _recipe_objects(data):
            title = _text(recipe.get("name")).replace("\n", " ")[:TITLE_MAX].strip()
            if not title:
                continue
            ingredients = recipe.get("recipeIngredient") or recipe.get("ingredients") or []
            if isinstance(ingredients, str):
                ingredients = [ingredients]
            ingredient_lines = [
                _text(item).replace("\n", " ") for item in ingredients if isinstance(item, str)
            ]
            ingredient_lines = [line for line in ingredient_lines if line]
            steps = _instruction_steps(recipe.get("recipeInstructions"))
            return {
                "title": title,
                "servings": _servings(recipe.get("recipeYield")),
                "ingredients": _capped_lines(ingredient_lines, INGREDIENTS_MAX),
                # Blank lines between steps, so each one becomes its own paragraph on the recipe page.
                "instructions": _capped_lines(steps, INSTRUCTIONS_MAX // 2).replace("\n", "\n\n"),
            }
    return None


def _read_page(response):
    body = safe_fetch.read_capped(response, MAX_PAGE_BYTES, "Die Seite ist größer als 3 MB.")
    charset = (
        response.headers.get_content_charset() if hasattr(response.headers, "get_content_charset") else None
    )
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def fetch_recipe(url):
    """The recipe on the page at `url` (see parse_recipe_page); raises safe_fetch.FetchError."""
    page = safe_fetch.fetch(
        url,
        _read_page,
        user_agent=USER_AGENT,
        accept="text/html, application/xhtml+xml",
        server="Die Rezeptseite",
    )
    recipe = parse_recipe_page(page)
    if recipe is None:
        raise safe_fetch.FetchError(
            "Auf der Seite wurde kein Rezept gefunden. Sie muss es als schema.org-Rezept beschreiben, "
            "wie es die meisten Rezeptseiten tun."
        )
    return recipe


IMPORTS_PER_WINDOW = 10
IMPORT_WINDOW_SECONDS = 600


def fetch_recipe_for(user, raw_url):
    """(checked URL, recipe fields) for `user`'s import; raises safe_fetch.FetchError.

    Throttled per user, because every import makes the server fetch a page of the user's choosing.
    """
    url = safe_fetch.normalize_url(raw_url)
    if count_hit(f"recipes:imports:{user.pk}", IMPORT_WINDOW_SECONDS) > IMPORTS_PER_WINDOW:
        raise safe_fetch.FetchError(
            "Zu viele Importe auf einmal. Versuch es in ein paar Minuten noch einmal."
        )
    return url, fetch_recipe(url)
