"""Amazon wish lists: product details read from the public product page.

There is no Amazon API without a partner account, so the page is fetched like a browser would, through
the SSRF-safe fetcher with an honest User-Agent, and a few well-known parts of its markup are read.
Amazon sometimes answers automated requests with a block page; the item is kept anyway, named after
its link, and title and price can be filled in by hand.
"""

import html
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import unquote, urlsplit

from django.utils import timezone

from app.models import WishListItem
from app.services import safe_fetch
from app.services.expenses import euro
from app.services.throttle import count_hit

MAX_LISTS = 50
MAX_ITEMS = 200
MAX_PAGE_BYTES = 6_000_000
USER_AGENT = "Mozilla/5.0 (compatible; Lunora-Wunschliste/1.0)"
FETCHES_PER_WINDOW = 30
FETCH_WINDOW_SECONDS = 600

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
SHORT_LINK_HOSTS = ("amzn.to", "amzn.eu", "a.co")
# Also listed in SECURE_CSP's img-src; the browser loads the pictures straight from there.
IMAGE_HOSTS = ("m.media-amazon.com", "images-na.ssl-images-amazon.com", "images-eu.ssl-images-amazon.com")

_ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d|exec/obidos/asin)/([A-Z0-9]{10})(?=[/?#]|$)", re.IGNORECASE)
_IMAGE_SIZE_RE = re.compile(r"\._[A-Za-z0-9_,]+_\.(jpe?g|png|webp)$")


def _is_amazon_host(host):
    return any(host == domain or host.endswith("." + domain) for domain in AMAZON_DOMAINS)


def amazon_url(raw_url):
    """(address to store, ASIN or "") for an Amazon product link; raises safe_fetch.FetchError.

    With a known ASIN the address is cut down to /dp/<ASIN>, so tracking and partner parameters go.
    Short links (amzn.to …) are kept as they are and resolved when the page is fetched.
    """
    url = safe_fetch.normalize_url(raw_url)
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in SHORT_LINK_HOSTS:
        return url, ""
    if not _is_amazon_host(host):
        raise safe_fetch.FetchError("Bitte füge einen Link zu einem Amazon-Artikel ein.")
    match = _ASIN_RE.search(parts.path)
    if not match:
        raise safe_fetch.FetchError("Der Link zeigt auf keinen Amazon-Artikel.")
    return f"https://{host}/dp/{match.group(1).upper()}", match.group(1).upper()


def title_from_url(url):
    """A stand-in title from the words Amazon puts before /dp/ in product links."""
    path = unquote(urlsplit(url).path)
    match = re.search(r"/([^/]+)/(?:dp|gp/product)/", path)
    words = match.group(1).replace("-", " ").strip() if match else ""
    return words[:300] if words and words.lower() != "dp" else "Amazon-Artikel"


def _text(value):
    return " ".join(html.unescape(value).split())


def _search(pattern, page):
    match = re.search(pattern, page)
    return _text(match.group(1)) if match else ""


def _amount(text):
    """Decimal from "1.299,99", "1,299.99" or "39.99"; None when it isn't a price."""
    text = text.strip()
    if "," in text and "." in text:
        decimal_mark = "," if text.rfind(",") > text.rfind(".") else "."
    elif "," in text:
        decimal_mark = "," if len(text) - text.rfind(",") <= 3 else None
    else:
        decimal_mark = "." if len(text) - text.rfind(".") <= 3 else None
    thousands = {",", "."} - {decimal_mark}
    for mark in thousands:
        text = text.replace(mark, "")
    try:
        value = Decimal(text.replace(",", ".")).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return value if Decimal(0) <= value < Decimal("100000000") else None


def _image(page):
    url = _search(r'data-old-hires="(https://[^"]+)"', page) or _search(
        r'id="landingImage"[^>]*data-a-dynamic-image="\{&quot;(https://[^&]+)&quot;', page
    )
    if (urlsplit(url).hostname or "") not in IMAGE_HOSTS:
        return ""
    # A 400 px version instead of the 1500 px original.
    return _IMAGE_SIZE_RE.sub(r"._AC_SL400_.\1", url)[:500]


def parse_product_page(page):
    """{title, price, currency, image_url, rating, review_count, availability, canonical} from an
    Amazon product page, or None when it shows no product."""
    title = _search(r'id="productTitle"[^>]*>([^<]+)<', page)
    if not title:
        return None
    price = None
    if match := re.search(r'"priceAmount":\s*(\d+(?:\.\d+)?)', page):
        price = _amount(match.group(1))
    elif label := _search(r'id="apex-pricetopay-accessibility-label"[^>]*>\s*([\d.,]+)', page):
        price = _amount(label)
    currency = _search(r'"currencySymbol":"([^"]{1,3})"', page) or "€"
    rating = None
    if value := _search(r'id="acrPopover"[^>]*title="([\d.,]+)', page):
        try:
            rating = Decimal(value.replace(",", ".")).quantize(Decimal("0.1"))
        except InvalidOperation:
            rating = None
        rating = rating if rating is not None and Decimal(0) <= rating <= Decimal(5) else None
    reviews = re.sub(r"\D", "", _search(r'id="acrCustomerReviewText"[^>]*>([^<]*)<', page))
    availability = _search(r'primary-availability-message[^"]*"[^>]*>([^<]+)<', page) or _search(
        r'id="availability"[^>]*>\s*<span[^>]*>([^<]+)<', page
    )
    return {
        "title": title[:300],
        "price": price,
        "currency": currency,
        "image_url": _image(page),
        "rating": rating,
        "review_count": int(reviews) if reviews and len(reviews) <= 9 else None,
        "availability": availability[:120],
        "canonical": _search(r'<link rel="canonical" href="([^"]+)"', page),
    }


def _read_page(response):
    body = safe_fetch.read_capped(response, MAX_PAGE_BYTES, "Die Amazon-Seite ist zu groß.")
    return body.decode("utf-8", errors="replace")


def fetch_product(url):
    """The details on the product page at `url`; raises safe_fetch.FetchError."""
    page = safe_fetch.fetch(
        url, _read_page, user_agent=USER_AGENT, accept="text/html, application/xhtml+xml", server="Amazon"
    )
    if "validateCaptcha" in page or "api-services-support@amazon.com" in page:
        raise safe_fetch.FetchError("Amazon hat die Abfrage gerade blockiert.")
    product = parse_product_page(page)
    if product is None:
        raise safe_fetch.FetchError("Auf der Seite wurde kein Amazon-Artikel gefunden.")
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
    # Only for short links: an old product's canonical can point at its successor's ASIN.
    if match and not item.asin and _is_amazon_host((canonical.hostname or "").lower()):
        item.asin = match.group(1).upper()
        item.url = f"https://{canonical.hostname.lower()}/dp/{item.asin}"
    for field, value in product.items():
        setattr(item, field, value)
    item.fetched_at = timezone.now()
    return None


def add_item(user, wish_list, raw_url):
    """(new item, None or the reason its details are missing); raises safe_fetch.FetchError when the
    link can't be added at all."""
    url, asin = amazon_url(raw_url)
    if wish_list.items.count() >= MAX_ITEMS:
        raise safe_fetch.FetchError(f"Eine Liste kann höchstens {MAX_ITEMS} Artikel haben.")
    if asin and wish_list.items.filter(asin=asin).exists():
        raise safe_fetch.FetchError("Dieser Artikel steht schon auf der Liste.")
    item = WishListItem(wish_list=wish_list, url=url, asin=asin, title=title_from_url(raw_url))
    problem = refresh_item(user, item)
    if not asin and item.asin and wish_list.items.filter(asin=item.asin).exists():
        raise safe_fetch.FetchError("Dieser Artikel steht schon auf der Liste.")
    item.save()
    return item, problem


def money(amount, currency):
    return euro(amount).replace("€", currency)


def list_totals(items):
    """Labels of the list's total per currency ("1.234,50 €"), counting each item's quantity."""
    totals = {}
    for item in items:
        if item.price is not None:
            totals[item.currency] = totals.get(item.currency, 0) + item.price * item.quantity
    return [money(amount, currency) for currency, amount in totals.items()]
