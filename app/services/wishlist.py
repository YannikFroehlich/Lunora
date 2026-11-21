"""Wish lists of products from online shops, with details read from the public product page.

Shops don't offer a free product API, so the page is fetched like a browser would, through the
SSRF-safe fetcher with an honest User-Agent. Amazon gets its own reader for a few well-known parts of
its markup; every other shop is read from the schema.org/Product data (JSON-LD) or OpenGraph tags
shops embed for search engines. Some shops answer automated requests with a block page; the item is
kept anyway, named after its link, and title and price can be filled in by hand.

Product pictures are never loaded from the shop by the browser: `thumbnail` fetches them through the
same fetcher and re-encodes them as small JPEGs, which wish_item_image serves from Lunora itself.

A list can be shared read-only with other users (`share_wish_list`), who reserve the items they want
to give. Reservations are never shown to the owner, so every owner-facing path leaves them out. Items
with a target price are re-checked by `refresh_due_prices` (run_automations), and a price point that
reaches the target raises a price alert in the owner's inbox.
"""

import hashlib
import html
import io
import json
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone
from PIL import Image

from app.models import (
    UserNotification,
    WishList,
    WishListItem,
    WishListItemPricePoint,
    WishListReservation,
    WishListShare,
)
from app.services import safe_fetch
from app.services.expenses import euro
from app.services.notifications import materialize_wish_list_share_notification, materialize_wish_price_alert
from app.services.recipe_import import JsonLdScripts, json_ld_objects
from app.services.throttle import count_hit
from app.services.vocabulary import resolve_share_recipient

MAX_LISTS = 50
MAX_ITEMS = 200
MAX_PAGE_BYTES = 6_000_000
USER_AGENT = "Mozilla/5.0 (compatible; Lunora-Wunschliste/1.0)"
FETCHES_PER_WINDOW = 30
FETCH_WINDOW_SECONDS = 600

MAX_IMAGE_BYTES = 8_000_000
MAX_IMAGE_PIXELS = 50_000_000
THUMBNAIL_SIZE = 400
THUMBNAIL_CACHE_SECONDS = 7 * 24 * 3600
FAILED_THUMBNAIL_CACHE_SECONDS = 3600
IMAGE_FETCHES_PER_WINDOW = 120
IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "AVIF"}

MAX_WISH_SHARES = 20
# Automatic price checks only for items with a target price, each at most this often.
PRICE_CHECK_INTERVAL = timedelta(hours=12)
MAX_PRICE_CHECKS_PER_RUN = 10
PRIORITY_ORDER = {
    WishListItem.PRIORITY_HIGH: 0,
    WishListItem.PRIORITY_NORMAL: 1,
    WishListItem.PRIORITY_LOW: 2,
}
SPARKLINE_WIDTH = 80
SPARKLINE_HEIGHT = 24

AMAZON_DOMAINS = (
    "amazon.de",
    "amazon.at",
    "amazon.com",
    "amazon.co.uk",
    "amazon.fr",
    "amazon.it",
    "amazon.es",
    "amazon.nl",
    "amazon.com.be",
    "amazon.pl",
    "amazon.se",
    "amazon.ca",
    "amazon.com.tr",
    "amazon.co.jp",
    "amazon.com.au",
    "amazon.in",
    "amazon.com.mx",
    "amazon.com.br",
    "amazon.sg",
    "amazon.ae",
    "amazon.sa",
    "amazon.eg",
)
AMAZON_SHORT_LINK_HOSTS = ("amzn.to", "amzn.eu", "a.co")
TRACKING_PARAMETERS = {"gclid", "gbraid", "wbraid", "fbclid", "msclkid", "srsltid", "mc_cid", "mc_eid"}
CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥"}
AVAILABILITY_LABELS = {
    "InStock": "Auf Lager",
    "OnlineOnly": "Auf Lager",
    "LimitedAvailability": "Nur noch wenige",
    "PreOrder": "Vorbestellbar",
    "PreSale": "Vorbestellbar",
    "BackOrder": "Nachbestellt",
    "InStoreOnly": "Nur im Laden",
    "OutOfStock": "Nicht verfügbar",
    "SoldOut": "Ausverkauft",
    "Discontinued": "Nicht mehr erhältlich",
}

_ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d|exec/obidos/asin)/([A-Z0-9]{10})(?=[/?#]|$)", re.IGNORECASE)
_IMAGE_SIZE_RE = re.compile(r"\._[A-Za-z0-9_,]+_\.(jpe?g|png|webp)$")
_NUMBER_RE = re.compile(r"\d[\d.,]*")


def _is_amazon_host(host):
    return any(host == domain or host.endswith("." + domain) for domain in AMAZON_DOMAINS)


def _without_tracking(url):
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMETERS
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def product_url(raw_url):
    """(address to store, ASIN or "") for a product link; raises safe_fetch.FetchError.

    Amazon links with a known ASIN are cut down to /dp/<ASIN>, so tracking and partner parameters go;
    Amazon short links (amzn.to …) are resolved when the page is fetched. Other shops keep their
    address minus common tracking parameters (utm_*, gclid …).
    """
    url = safe_fetch.normalize_url(raw_url)
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in AMAZON_SHORT_LINK_HOSTS:
        return url, ""
    if not _is_amazon_host(host):
        return _without_tracking(url)[:500], ""
    match = _ASIN_RE.search(parts.path)
    if not match:
        raise safe_fetch.FetchError("Der Link zeigt auf keinen Amazon-Artikel.")
    return f"https://{host}/dp/{match.group(1).upper()}", match.group(1).upper()


def title_from_url(url):
    """A stand-in title from the words in a product link, for when the page can't be read."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = unquote(parts.path)
    if _is_amazon_host(host):
        match = re.search(r"/([^/]+)/(?:dp|gp/product)/", path)
        words = match.group(1) if match else ""
    else:
        segments = [segment for segment in path.split("/") if segment]
        words = re.sub(r"\.\w{2,5}$", "", segments[-1]) if segments else ""
    words = " ".join(words.replace("-", " ").replace("_", " ").split())
    if len(words) >= 3 and re.search(r"[^\W\d]", words) and words.lower() != "dp":
        return words[:300]
    if _is_amazon_host(host) or host in AMAZON_SHORT_LINK_HOSTS:
        return "Amazon-Artikel"
    return f"Artikel von {host.removeprefix('www.')}"[:300]


def _text(value):
    return " ".join(html.unescape(str(value)).split())


def _search(pattern, page):
    match = re.search(pattern, page)
    return _text(match.group(1)) if match else ""


def _amount(text):
    """Decimal from "1.299,99", "1,299.99", "39.99" or "19,99 €"; None when it isn't a price."""
    match = _NUMBER_RE.search(str(text))
    if not match:
        return None
    text = match.group().rstrip(".,")
    if "," in text and "." in text:
        decimal_mark = "," if text.rfind(",") > text.rfind(".") else "."
    elif "," in text:
        decimal_mark = "," if len(text) - text.rfind(",") <= 3 else None
    else:
        decimal_mark = "." if len(text) - text.rfind(".") <= 3 else None
    for mark in {",", "."} - {decimal_mark}:
        text = text.replace(mark, "")
    try:
        value = Decimal(text.replace(",", ".")).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return value if Decimal(0) <= value < Decimal("100000000") else None


def _rating(value, best=None):
    """A rating on a 0–5 scale, rescaled when the shop rates out of `best` (e.g. 10)."""
    try:
        rating = Decimal(str(value).replace(",", "."))
        scale = Decimal(str(best).replace(",", ".")) if best not in (None, "") else Decimal(5)
        rating = (rating * 5 / scale).quantize(Decimal("0.1")) if scale > 0 else None
    except (InvalidOperation, ValueError):
        return None
    return rating if rating is not None and Decimal(0) <= rating <= Decimal(5) else None


def _count(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return int(digits) if digits and len(digits) <= 9 else None


def _currency(code):
    code = _text(code or "").upper()
    return CURRENCY_SYMBOLS.get(code, code[:3] or "€")


def _image_link(value, base_url):
    """The http(s) address of a JSON-LD image: a string, an ImageObject or a list of either."""
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl") or ""
    if not isinstance(value, str) or not value.strip():
        return ""
    url = urljoin(base_url, value.strip())
    return url if urlsplit(url).scheme in ("http", "https") and len(url) <= 500 else ""


def _amazon_product(page):
    title = _search(r'id="productTitle"[^>]*>([^<]+)<', page)
    price = None
    if match := re.search(r'"priceAmount":\s*(\d+(?:\.\d+)?)', page):
        price = _amount(match.group(1))
    elif label := _search(r'id="apex-pricetopay-accessibility-label"[^>]*>\s*([\d.,]+)', page):
        price = _amount(label)
    image = _search(r'data-old-hires="(https://[^"]+)"', page) or _search(
        r'id="landingImage"[^>]*data-a-dynamic-image="\{&quot;(https://[^&]+)&quot;', page
    )
    return {
        "title": title[:300],
        "price": price,
        "currency": _search(r'"currencySymbol":"([^"]{1,3})"', page) or "€",
        # A 400 px version instead of the 1500 px original.
        "image_url": _IMAGE_SIZE_RE.sub(r"._AC_SL400_.\1", image)[:500],
        "rating": _rating(_search(r'id="acrPopover"[^>]*title="([\d.,]+)', page)),
        "review_count": _count(_search(r'id="acrCustomerReviewText"[^>]*>([^<]*)<', page)),
        "availability": (
            _search(r'primary-availability-message[^"]*"[^>]*>([^<]+)<', page)
            or _search(r'id="availability"[^>]*>\s*<span[^>]*>([^<]+)<', page)
        )[:120],
        "canonical": _search(r'<link rel="canonical" href="([^"]+)"', page),
    }


class _ProductPage(JsonLdScripts):
    """JSON-LD blocks plus the first value of every meta tag (property, name or itemprop → content)."""

    def __init__(self):
        super().__init__()
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        attributes = dict(attrs)
        key = attributes.get("property") or attributes.get("name") or attributes.get("itemprop")
        if key and attributes.get("content"):
            self.meta.setdefault(key.lower(), attributes["content"])


def _json_ld_product(blocks):
    for block in blocks:
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for product in json_ld_objects(data, "Product"):
            if isinstance(product.get("name"), str) and product["name"].strip():
                return product
    return {}


def _first_offer(offers):
    if isinstance(offers, list):
        offers = next((offer for offer in offers if isinstance(offer, dict)), {})
    return offers if isinstance(offers, dict) else {}


def _generic_product(page, base_url):
    parser = _ProductPage()
    parser.feed(page)
    parser.close()
    meta = parser.meta
    product = _json_ld_product(parser.blocks)
    offer = _first_offer(product.get("offers"))
    specification = _first_offer(offer.get("priceSpecification"))
    rating = product.get("aggregateRating") if isinstance(product.get("aggregateRating"), dict) else {}
    title = _text(product.get("name") or meta.get("og:title") or "")
    if not title:
        return None
    price_text = next(
        (
            value
            for value in (
                offer.get("price"),
                offer.get("lowPrice"),
                specification.get("price"),
                meta.get("product:price:amount"),
                meta.get("og:price:amount"),
                meta.get("price"),
            )
            if value not in (None, "")
        ),
        None,
    )
    currency = (
        offer.get("priceCurrency")
        or specification.get("priceCurrency")
        or meta.get("product:price:currency")
        or meta.get("og:price:currency")
        or meta.get("pricecurrency")
    )
    availability = str(offer.get("availability") or meta.get("product:availability") or "")
    return {
        "title": title[:300],
        "price": _amount(price_text) if price_text is not None else None,
        "currency": _currency(currency),
        "image_url": _image_link(product.get("image"), base_url)
        or _image_link(meta.get("og:image"), base_url),
        "rating": _rating(rating.get("ratingValue"), rating.get("bestRating")) if rating else None,
        "review_count": _count(rating.get("reviewCount") or rating.get("ratingCount")),
        "availability": AVAILABILITY_LABELS.get(availability.rsplit("/", 1)[-1], ""),
        "canonical": "",
    }


def parse_product_page(page, base_url=""):
    """{title, price, currency, image_url, rating, review_count, availability, canonical} from a
    product page, or None when it shows no product."""
    if 'id="productTitle"' in page:
        product = _amazon_product(page)
        return product if product["title"] else None
    return _generic_product(page, base_url)


def _read_page(response):
    body = safe_fetch.read_capped(response, MAX_PAGE_BYTES, "Die Produktseite ist zu groß.")
    charset = (
        response.headers.get_content_charset() if hasattr(response.headers, "get_content_charset") else None
    )
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def fetch_product(url):
    """The details on the product page at `url`; raises safe_fetch.FetchError."""
    page = safe_fetch.fetch(
        url, _read_page, user_agent=USER_AGENT, accept="text/html, application/xhtml+xml", server="Der Shop"
    )
    if "validateCaptcha" in page or "api-services-support@amazon.com" in page:
        raise safe_fetch.FetchError("Amazon hat die Abfrage gerade blockiert.")
    product = parse_product_page(page, url)
    if product is None:
        raise safe_fetch.FetchError("Auf der Seite wurden keine Produktdaten gefunden.")
    return product


def refresh_item(user, item):
    """Reads the item's page again and copies its details onto `item` (unsaved); returns None, or the
    German reason why nothing could be read. Throttled per user: each call fetches a page."""
    if count_hit(f"wishlist:fetches:{user.pk}", FETCH_WINDOW_SECONDS) > FETCHES_PER_WINDOW:
        return "Zu viele Abfragen auf einmal. Versuch es in ein paar Minuten noch einmal."
    try:
        product = fetch_product(item.url)
    except safe_fetch.FetchError as error:
        return str(error)
    canonical = urlsplit(product.pop("canonical"))
    match = _ASIN_RE.search(canonical.path)
    # Only for Amazon short links: an old product's canonical can point at its successor's ASIN.
    if (
        match
        and not item.asin
        and (urlsplit(item.url).hostname or "") in AMAZON_SHORT_LINK_HOSTS
        and _is_amazon_host((canonical.hostname or "").lower())
    ):
        item.asin = match.group(1).upper()
        item.url = f"https://{canonical.hostname.lower()}/dp/{item.asin}"
    for field, value in product.items():
        setattr(item, field, value)
    item.fetched_at = timezone.now()
    return None


def record_price_point(item):
    """Stores `item`'s current price as a history point, unless it matches the last one recorded.
    A new point that reaches the target price coming from above it raises a price alert."""
    if item.price is None:
        return
    latest = item.price_points.order_by("-recorded_at", "-id").first()
    if latest is not None and latest.price == item.price:
        return
    point = WishListItemPricePoint.objects.create(item=item, price=item.price)
    target = item.target_price
    if target is not None and item.price <= target and latest is not None and latest.price > target:
        materialize_wish_price_alert(
            item,
            point,
            price_label=money(item.price, item.currency),
            target_label=money(target, item.currency),
        )


def sparkline_points(points):
    """SVG polyline points ("x,y x,y …") of the price history in a SPARKLINE_WIDTH × SPARKLINE_HEIGHT
    box, the cheapest price at the bottom; "" with fewer than two points."""
    prices = [point.price for point in points]
    if len(prices) < 2:
        return ""
    low, high = min(prices), max(prices)
    padding = 2
    usable = SPARKLINE_HEIGHT - 2 * padding
    coordinates = []
    for index, price in enumerate(prices):
        x = SPARKLINE_WIDTH * index / (len(prices) - 1)
        y = SPARKLINE_HEIGHT / 2 if high == low else padding + usable * float((high - price) / (high - low))
        coordinates.append(f"{x:.1f},{y:.1f}")
    return " ".join(coordinates)


def refresh_due_prices(*, now=None):
    """Re-reads the pages of items with a target price that weren't checked for PRICE_CHECK_INTERVAL,
    oldest check first, at most MAX_PRICE_CHECKS_PER_RUN per pass (run by run_automations)."""
    current_time = now or timezone.now()
    due = list(
        WishListItem.objects.filter(target_price__isnull=False, wish_list__owner__is_active=True)
        .filter(
            Q(price_checked_at__isnull=True) | Q(price_checked_at__lt=current_time - PRICE_CHECK_INTERVAL)
        )
        .select_related("wish_list__owner")
        .order_by(F("price_checked_at").asc(nulls_first=True), "id")[:MAX_PRICE_CHECKS_PER_RUN]
    )
    checked = failed = 0
    for item in due:
        # Stamped before fetching, so a shop that keeps failing waits a whole interval as well.
        WishListItem.objects.filter(pk=item.pk).update(price_checked_at=current_time)
        item.price_checked_at = current_time
        if refresh_item(item.wish_list.owner, item):
            failed += 1
            continue
        item.save()
        record_price_point(item)
        checked += 1
    return {"checked": checked, "failed": failed}


def price_change(item, points):
    """("+12,00 €"/"-5,00 €", is_increase) of `item.price` against `points` (its price history,
    oldest first) since it was added, or ("", False) when there's nothing to compare."""
    if not points or item.price is None or points[0].price == item.price:
        return "", False
    diff = item.price - points[0].price
    return f"{'+' if diff > 0 else '-'}{money(abs(diff), item.currency)}", diff > 0


def _is_duplicate(wish_list, item):
    items = wish_list.items.exclude(pk=item.pk) if item.pk else wish_list.items.all()
    return items.filter(asin=item.asin).exists() if item.asin else items.filter(url=item.url).exists()


def add_item(user, wish_list, raw_url):
    """(new item, None or the reason its details are missing); raises safe_fetch.FetchError when the
    link can't be added at all."""
    url, asin = product_url(raw_url)
    if wish_list.items.count() >= MAX_ITEMS:
        raise safe_fetch.FetchError(f"Eine Liste kann höchstens {MAX_ITEMS} Artikel haben.")
    item = WishListItem(wish_list=wish_list, url=url, asin=asin, title=title_from_url(raw_url))
    if _is_duplicate(wish_list, item):
        raise safe_fetch.FetchError("Dieser Artikel steht schon auf der Liste.")
    problem = refresh_item(user, item)
    if item.asin != asin and _is_duplicate(wish_list, item):
        raise safe_fetch.FetchError("Dieser Artikel steht schon auf der Liste.")
    item.save()
    record_price_point(item)
    return item, problem


def image_version(item):
    """A short hash of the picture's address, so a refreshed picture gets a fresh browser cache entry."""
    return hashlib.sha256(item.image_url.encode()).hexdigest()[:10]


def _jpeg_thumbnail(data):
    """`data` re-encoded as a JPEG of at most THUMBNAIL_SIZE px, so nothing of the shop's file
    reaches the browser; raises ValueError or OSError for anything that isn't a plain picture."""
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in IMAGE_FORMATS or image.width * image.height > MAX_IMAGE_PIXELS:
            raise ValueError("not a supported picture")
        image.thumbnail((THUMBNAIL_SIZE, THUMBNAIL_SIZE))
        image = image.convert("RGBA")
        # Transparent product shots go on white, like the shops show them.
        flat = Image.new("RGB", image.size, "white")
        flat.paste(image, mask=image.getchannel("A"))
        output = io.BytesIO()
        flat.save(output, "JPEG", quality=85, optimize=True)
        return output.getvalue()


def thumbnail(user, image_url):
    """JPEG bytes of the picture at `image_url`, or None. Cached for everyone by address; misses
    are throttled per user because each one fetches from a server of the shop's choosing."""
    key = "wishlist:thumbnail:" + hashlib.sha256(image_url.encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached or None
    if count_hit(f"wishlist:images:{user.pk}", FETCH_WINDOW_SECONDS) > IMAGE_FETCHES_PER_WINDOW:
        return None
    try:
        data = safe_fetch.fetch(
            image_url,
            lambda response: safe_fetch.read_capped(response, MAX_IMAGE_BYTES, "Das Bild ist zu groß."),
            user_agent=USER_AGENT,
            accept="image/avif, image/webp, image/png, image/jpeg, image/*;q=0.8",
            server="Der Bildserver",
        )
        result = _jpeg_thumbnail(data)
    except (safe_fetch.FetchError, ValueError, OSError, Image.DecompressionBombError):
        result = b""
    cache.set(key, result, THUMBNAIL_CACHE_SECONDS if result else FAILED_THUMBNAIL_CACHE_SECONDS)
    return result or None


def money(amount, currency):
    return euro(amount).replace("€", currency)


def list_totals(items):
    """Labels of the list's total per currency ("1.234,50 €"), counting each item's quantity."""
    totals = {}
    for item in items:
        if item.price is not None:
            totals[item.currency] = totals.get(item.currency, 0) + item.price * item.quantity
    return [money(amount, currency) for currency, amount in totals.items()]


def sorted_items(items):
    """Most wanted first, then in the order they were added."""
    return sorted(items, key=lambda item: (PRIORITY_ORDER.get(item.priority, 1), item.created_at, item.pk))


def accessible_wish_lists(user):
    """Own lists plus those shared with `user`; a subquery, so no duplicates."""
    shared_ids = WishListShare.objects.filter(user=user).values("wish_list_id")
    return WishList.objects.filter(Q(owner=user) | Q(pk__in=shared_ids, owner__is_active=True))


def share_wish_list(wish_list, identifier):
    """Shares the owner's list with the user named by e-mail or username; returns an error or None."""
    user, error = resolve_share_recipient(wish_list.owner, identifier)
    if error:
        return error
    if wish_list.shares.count() >= MAX_WISH_SHARES:
        return f"Eine Wunschliste lässt sich mit höchstens {MAX_WISH_SHARES} Personen teilen."
    share, created = WishListShare.objects.get_or_create(wish_list=wish_list, user=user)
    if not created:
        return "Die Liste ist mit diesem Konto bereits geteilt."
    materialize_wish_list_share_notification(share)
    return None


def end_wish_list_share(wish_list_id, user_id):
    """Ends a share (the owner removes someone, or they leave); their reservations go with it."""
    for share in WishListShare.objects.filter(wish_list_id=wish_list_id, user_id=user_id):
        WishListReservation.objects.filter(item__wish_list_id=wish_list_id, user_id=user_id).delete()
        UserNotification.objects.filter(
            recipient_id=user_id, source_key=f"wish-list-share:{share.pk}"
        ).delete()
        share.delete()


def reserve_item(user, item):
    """Reserves `item` as `user`'s gift; returns an error or None. For list members, never the owner."""
    try:
        with transaction.atomic():
            WishListReservation.objects.create(item=item, user=user)
    except IntegrityError:
        return "Diesen Artikel schenkt schon jemand."
    return None


def release_item(user, item):
    WishListReservation.objects.filter(item=item, user=user).delete()
