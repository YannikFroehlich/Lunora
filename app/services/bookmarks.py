"""Bookmarks: tags, a short preview read from the linked page, and search.

The preview (page title and description) is fetched once when a bookmark is added, through the
SSRF-safe fetcher and throttled per user; a page that can't be read just leaves it empty."""

import re
from html.parser import HTMLParser
from urllib.parse import urlparse

from django.db.models import Q

from app.models import Bookmark
from app.services import safe_fetch
from app.services.note_search import filter_by_query
from app.services.throttle import count_hit

MAX_BOOKMARKS = 200
# The dashboard widget is a quick-access grid; the full collection lives on the bookmarks page.
DASHBOARD_BOOKMARKS = 24
MAX_TAGS = 10
MAX_TAG_LENGTH = 30
MAX_TAGS_LENGTH = 200  # Bookmark.tags
MAX_PREVIEW_BYTES = 1_000_000
PREVIEWS_PER_WINDOW = 30
PREVIEW_WINDOW_SECONDS = 600
USER_AGENT = "Lunora-Lesezeichen/1.0"

_TAG_SEPARATOR = re.compile(r"[\s,;]+")
_TAG_INVALID = re.compile(r"[^\w-]+", re.UNICODE)


def normalize_tags(raw):
    """Lower-case tags without "#", unique, in input order, joined by single spaces."""
    tags = []
    for part in _TAG_SEPARATOR.split(str(raw or "")):
        tag = _TAG_INVALID.sub("", part.lstrip("#").lower())[:MAX_TAG_LENGTH]
        if tag and tag not in tags and len(tags) < MAX_TAGS:
            tags.append(tag)
    joined = " ".join(tags)
    while len(joined) > MAX_TAGS_LENGTH:
        tags.pop()
        joined = " ".join(tags)
    return joined


def host_of(url):
    return urlparse(url).hostname or ""


class _PagePreview(HTMLParser):
    """The <title> text and the first value of every <meta> tag, up to the end of <head>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = None
        self.meta = {}
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title" and self.title is None:
            self._in_title = True
            self.title = ""
        elif tag == "meta":
            attributes = dict(attrs)
            key = attributes.get("property") or attributes.get("name")
            if key and attributes.get("content"):
                self.meta.setdefault(key.strip().lower(), attributes["content"])

    def handle_data(self, data):
        if self._in_title:
            self.title += data

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False


def _clean(value, limit):
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def parse_page_preview(page):
    """{"title", "description"} of an HTML page, from Open Graph tags or <title>/<meta description>."""
    parser = _PagePreview()
    # Only the head matters; a broken page must not break adding the bookmark.
    head_end = page.lower().find("</head>")
    try:
        parser.feed(page[: head_end if head_end >= 0 else MAX_PREVIEW_BYTES])
        parser.close()
    except (AssertionError, ValueError):
        pass
    meta = parser.meta
    return {
        "title": _clean(meta.get("og:title") or parser.title, Bookmark._meta.get_field("title").max_length),
        "description": _clean(
            meta.get("og:description") or meta.get("description") or meta.get("twitter:description"),
            Bookmark._meta.get_field("description").max_length,
        ),
    }


def _read_page(response):
    body = safe_fetch.read_capped(response, MAX_PREVIEW_BYTES, "Die Seite ist zu groß.")
    charset = (
        response.headers.get_content_charset() if hasattr(response.headers, "get_content_charset") else None
    )
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def fetch_page_preview(user, url):
    """The page's preview, or {} when it can't be read or `user` fetched too many pages lately."""
    if count_hit(f"bookmarks:previews:{user.pk}", PREVIEW_WINDOW_SECONDS) > PREVIEWS_PER_WINDOW:
        return {}
    try:
        page = safe_fetch.fetch(
            safe_fetch.normalize_url(url),
            _read_page,
            user_agent=USER_AGENT,
            accept="text/html, application/xhtml+xml",
            server="Die Seite",
        )
    except safe_fetch.FetchError:
        return {}
    return parse_page_preview(page)


def add_bookmark(user, form):
    """Saves a valid BookmarkForm for `user`, filling a missing title and the description from the
    page. Returns (bookmark, None) or (None, error message)."""
    if Bookmark.objects.filter(owner=user).count() >= MAX_BOOKMARKS:
        return None, f"Du kannst höchstens {MAX_BOOKMARKS} Lesezeichen anlegen."
    bookmark = form.save(commit=False)
    bookmark.owner = user
    preview = fetch_page_preview(user, bookmark.url)
    bookmark.title = bookmark.title or preview.get("title") or host_of(bookmark.url)[:80]
    bookmark.description = preview.get("description", "")
    bookmark.save()
    return bookmark, None


def refresh_preview(user, bookmark):
    """Reads the page again and replaces the description; False when nothing could be read."""
    preview = fetch_page_preview(user, bookmark.url)
    if not preview.get("description"):
        return False
    bookmark.description = preview["description"]
    bookmark.save(update_fields=["description"])
    return True


def search_bookmarks(queryset, query="", tag=""):
    if tag:
        # Tags are space-separated words; match whole tags only.
        queryset = queryset.filter(
            Q(tags=tag)
            | Q(tags__startswith=f"{tag} ")
            | Q(tags__endswith=f" {tag}")
            | Q(tags__contains=f" {tag} ")
        )
    if query:
        queryset = filter_by_query(queryset, query, ["title", "url", "description", "tags"])
    return queryset


def tag_counts(user):
    """[(tag, count)] over all of `user`'s bookmarks, most used first."""
    counts = {}
    for tags in Bookmark.objects.filter(owner=user).values_list("tags", flat=True):
        for tag in tags.split():
            counts[tag] = counts.get(tag, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))
