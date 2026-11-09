"""Barcode scanning for the shopping list: product lookup on Open Food Facts and list matching.

The lookup runs on the server, so Open Food Facts sees which barcodes Lunora asks for, but not
who scanned them. Only text and numbers are kept, no product photos.
"""

import json
import logging
import math
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.core.cache import cache
from django.db import IntegrityError, transaction

from app.models import ShoppingItem, ShoppingProduct
from app.services.throttle import count_hit

logger = logging.getLogger(__name__)

MAX_SHOPPING_ITEMS = 200
MAX_SHOPPING_PRODUCTS = 1000
LOOKUP_URL = "https://world.openfoodfacts.org/api/v2/product/{}.json?fields={}"
LOOKUP_FIELDS = "product_name_de,product_name,generic_name_de,brands,quantity,nutriscore_grade,nutriments"
LOOKUP_USER_AGENT = "Lunora/1.0 (self-hosted workspace)"
LOOKUP_TIMEOUT_SECONDS = 5
MAX_LOOKUP_BYTES = 512 * 1024
FOUND_CACHE_SECONDS = 7 * 24 * 3600
MISSING_CACHE_SECONDS = 24 * 3600
LOOKUPS_PER_WINDOW = 30
LOOKUP_WINDOW_SECONDS = 600

# Open Food Facts key, label, unit; values are per 100 g or 100 ml.
NUTRIENTS = (
    ("energy-kcal", "Energie", "kcal"),
    ("fat", "Fett", "g"),
    ("saturated-fat", "davon gesättigte Fettsäuren", "g"),
    ("carbohydrates", "Kohlenhydrate", "g"),
    ("sugars", "davon Zucker", "g"),
    ("proteins", "Eiweiß", "g"),
    ("salt", "Salz", "g"),
)
SCAN_MODES = ("shop", "add")


class ProductLookupError(Exception):
    """A lookup that failed for now (network, throttle, limit); the message is for the user."""


def normalize_barcode(raw):
    """The EAN-8/EAN-13/GTIN-14 digits of a scanned or typed code, or None when it isn't one.

    UPC-A (12 digits) becomes its EAN-13 form, so both spellings of a product meet.
    """
    digits = re.sub(r"[\s-]", "", raw or "")
    if not re.fullmatch(r"\d{8}|\d{12,14}", digits):
        return None
    if len(digits) > 8:
        digits = digits.lstrip("0").zfill(13)
    # GTIN check digit: weights 3 and 1 alternate from the right, starting next to it.
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(digit) * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(body)))
    return digits if (10 - total % 10) % 10 == check else None


def _text(value, limit):
    return " ".join(str(value or "").split())[:limit] if isinstance(value, str) else ""


def parse_open_food_facts(data):
    """Product fields from an Open Food Facts v2 response, or None when it has no usable product."""
    if not isinstance(data, dict) or data.get("status") != 1 or not isinstance(data.get("product"), dict):
        return None
    product = data["product"]
    name = next(
        (
            text
            for key in ("product_name_de", "product_name", "generic_name_de")
            if (text := _text(product.get(key), 120))
        ),
        "",
    )
    if not name:
        return None
    grade = _text(product.get("nutriscore_grade"), 1).lower()
    nutriments = product.get("nutriments") if isinstance(product.get("nutriments"), dict) else {}
    nutrition = {}
    for key, _label, _unit in NUTRIENTS:
        value = nutriments.get(f"{key}_100g")
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            continue
        try:
            number = float(value)
        except ValueError:
            continue
        if math.isfinite(number) and 0 <= number <= 10000:
            nutrition[key] = round(number, 1)
    return {
        "name": name,
        "brand": _text(product.get("brands"), 200).split(",")[0].strip()[:80],
        # Drops the EU estimated-quantity sign, which arrives as "℮" or a bare "e" ("400 g e").
        "package": re.sub(r"\s+(℮|e)$", "", _text(product.get("quantity"), 40)),
        "nutri_score": grade if grade in "abcde" else "",
        "nutrition": nutrition,
    }


def lookup_open_food_facts(barcode):
    """Product fields for a normalized barcode, or None when Open Food Facts doesn't know it.

    Answers are cached for everyone, since they don't depend on who asks.
    """
    cache_key = f"shopping:off:{barcode}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached or None
    request = Request(LOOKUP_URL.format(barcode, LOOKUP_FIELDS), headers={"User-Agent": LOOKUP_USER_AGENT})
    try:
        with urlopen(request, timeout=LOOKUP_TIMEOUT_SECONDS) as response:
            body = response.read(MAX_LOOKUP_BYTES + 1)
        if len(body) > MAX_LOOKUP_BYTES:
            raise ValueError("response too large")
        product = parse_open_food_facts(json.loads(body.decode("utf-8")))
    except HTTPError as error:
        if error.code != 404:
            logger.warning("Open Food Facts lookup failed with HTTP %s", error.code)
            raise ProductLookupError("Open Food Facts ist gerade nicht erreichbar.") from error
        product = None
    except (URLError, TimeoutError, OSError, ValueError) as error:
        logger.warning("Open Food Facts lookup failed: %s", type(error).__name__)
        raise ProductLookupError("Open Food Facts ist gerade nicht erreichbar.") from error
    cache.set(cache_key, product or {}, FOUND_CACHE_SECONDS if product else MISSING_CACHE_SECONDS)
    return product


def _store_product(user, barcode, fields):
    if ShoppingProduct.objects.filter(owner=user).count() >= MAX_SHOPPING_PRODUCTS:
        raise ProductLookupError(f"Es lassen sich höchstens {MAX_SHOPPING_PRODUCTS} Produkte speichern.")
    try:
        with transaction.atomic():
            return ShoppingProduct.objects.create(owner=user, barcode=barcode, **fields)
    except IntegrityError:  # A parallel scan of the same code stored it first.
        return ShoppingProduct.objects.get(owner=user, barcode=barcode)


def product_for_barcode(user, barcode, name=""):
    """The user's product for a normalized barcode: stored, looked up and stored, or None.

    A `name` stores a product Open Food Facts doesn't know (or renames a stored one).
    """
    product = ShoppingProduct.objects.filter(owner=user, barcode=barcode).first()
    name = _text(name, 120)
    if product is not None:
        if name and name != product.name:
            product.name = name
            product.save(update_fields=["name"])
        return product
    if name:
        return _store_product(user, barcode, {"name": name})
    if count_hit(f"shopping:lookups:{user.pk}", LOOKUP_WINDOW_SECONDS) > LOOKUPS_PER_WINDOW:
        raise ProductLookupError("Zu viele neue Produkte auf einmal. Versuch es gleich noch einmal.")
    fields = lookup_open_food_facts(barcode)
    return _store_product(user, barcode, fields) if fields else None


def _stem(word):
    """Drops a German plural ending like shopping.js's singular(): "bananen" -> "banan"."""
    if len(word) > 4 and re.search(r"[aeiou]s$", word):
        return word[:-1]
    match = re.fullmatch(r"(.{3,}?)(en|n|e)", word)
    return match.group(1) if match else word


def find_open_item(list_owner, product):
    """The open list item a scanned product stands for.

    The item scanned with this barcode before wins; otherwise one whose words all appear in the
    product name ("Milch" for "Frische Vollmilch 3,5 %"), the longest such name first.
    ponytail: word-stem matching, misses synonyms ("Möhren" for "Karotten").
    """
    items = list(ShoppingItem.objects.filter(owner=list_owner, is_done=False))
    for item in items:
        if item.barcode == product.barcode:
            return item
    text = f"{product.name} {product.brand}".casefold()
    matches = []
    for item in items:
        if item.barcode:
            continue
        stems = [_stem(word) for word in re.findall(r"\w{3,}", item.name.casefold())]
        if stems and all(stem in text for stem in stems):
            matches.append(item)
    return max(matches, key=lambda item: len(item.name), default=None)


def add_product_to_list(list_owner, product):
    """Puts a scanned product on the list (or reopens its row); returns the item."""
    item = (
        ShoppingItem.objects.filter(owner=list_owner, barcode=product.barcode).first()
        or ShoppingItem.objects.filter(owner=list_owner, name__iexact=product.name).first()
    )
    if item is not None:
        item.is_done = False
        item.barcode = product.barcode
        item.save(update_fields=["is_done", "barcode"])
        return item
    if ShoppingItem.objects.filter(owner=list_owner).count() >= MAX_SHOPPING_ITEMS:
        raise ProductLookupError(f"Es passen höchstens {MAX_SHOPPING_ITEMS} Artikel auf den Zettel.")
    return ShoppingItem.objects.create(
        owner=list_owner, name=product.name, quantity=product.package, barcode=product.barcode
    )


def product_summary(product):
    return {
        "name": product.name,
        "brand": product.brand,
        "package": product.package,
        "nutri_score": product.nutri_score.upper(),
    }


def scan_barcode(user, raw_barcode, mode, name="", list_owner=None):
    """Handles one scan on `list_owner`'s list (default: the user's own); returns (HTTP status, result).

    The product is stored for the user who scanned, the item changes on the list.

    "shop" ticks the matching open item off, "add" puts the product on the list.
    """
    list_owner = list_owner or user
    barcode = normalize_barcode(raw_barcode)
    if barcode is None:
        return 400, {"status": "invalid", "message": "Das ist kein gültiger Strichcode (EAN)."}
    if mode not in SCAN_MODES:
        return 400, {"status": "invalid", "message": "Unbekannter Scan-Modus."}
    try:
        product = product_for_barcode(user, barcode, name)
        if product is None:
            return 200, {
                "status": "unknown",
                "barcode": barcode,
                "message": "Dieses Produkt kennt Open Food Facts nicht. Wie heißt es?",
            }
        if mode == "add":
            item = add_product_to_list(list_owner, product)
            return 200, {
                "status": "added",
                "changed": True,
                "product": product_summary(product),
                "message": f"„{item.name}“ steht auf dem Zettel.",
            }
    except ProductLookupError as error:
        return 503, {"status": "error", "message": str(error)}

    result = {"product": product_summary(product)}
    item = find_open_item(list_owner, product)
    if item is not None:
        item.is_done = True
        item.barcode = product.barcode
        item.save(update_fields=["is_done", "barcode"])
        return 200, {**result, "status": "ticked", "changed": True, "message": f"„{item.name}“ abgehakt."}
    if ShoppingItem.objects.filter(owner=list_owner, barcode=product.barcode, is_done=True).exists():
        return 200, {**result, "status": "done", "message": f"„{product.name}“ ist schon im Wagen."}
    return 200, {**result, "status": "missing", "message": f"„{product.name}“ steht nicht auf dem Zettel."}


def _number(value, unit):
    return f"{value:.0f}" if unit == "kcal" else f"{value:.1f}".replace(".", ",")


def attach_products(users, items):
    """Sets `product` and its `nutrition_rows` on every item that was scanned before.

    Products come from `users` (everyone on a shared list); when several scanned the same barcode,
    the earliest in `users` wins.
    """
    barcodes = {item.barcode for item in items if item.barcode}
    rank = {user.pk: index for index, user in enumerate(users)}
    products = {}
    for product in sorted(
        ShoppingProduct.objects.filter(owner__in=users, barcode__in=barcodes),
        key=lambda product: rank[product.owner_id],
        reverse=True,
    ):
        products[product.barcode] = product
    for item in items:
        item.product = products.get(item.barcode)
        if item.product is not None:
            item.nutrition_rows = [
                (label, f"{_number(item.product.nutrition[key], unit)} {unit}")
                for key, label, unit in NUTRIENTS
                if isinstance(item.product.nutrition.get(key), (int, float))
            ]
