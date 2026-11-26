"""Bookmarks: tags, a short preview read from the linked page, and search.

The preview (page title and description) is fetched once when a bookmark is added, through the
SSRF-safe fetcher and throttled per user; a page that can't be read just leaves it empty."""

import re
from datetime import timedelta
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db.models import F, Q
from django.utils import timezone

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
MAX_IMPORT_BYTES = 5_000_000
LINK_CHECK_INTERVAL = timedelta(days=7)
MAX_LINK_CHECKS_PER_RUN = 20

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


def find_duplicate(user, url, *, exclude_id=None):
    """The user's bookmark with the same address, ignoring a trailing slash, or None."""
    base = url.rstrip("/")
    matches = Bookmark.objects.filter(owner=user, url__in=[base, base + "/"])
    if exclude_id is not None:
        matches = matches.exclude(pk=exclude_id)
    return matches.first()


def add_bookmark(user, form):
    """Saves a valid BookmarkForm for `user`, filling a missing title and the description from the
    page. Returns (bookmark, None) or (None, error message)."""
    if Bookmark.objects.filter(owner=user).count() >= MAX_BOOKMARKS:
        return None, f"Du kannst höchstens {MAX_BOOKMARKS} Lesezeichen anlegen."
    bookmark = form.save(commit=False)
    existing = find_duplicate(user, bookmark.url)
    if existing is not None:
        return None, f"Diese Adresse ist schon als „{existing.title}“ gespeichert."
    bookmark.owner = user
    preview = fetch_page_preview(user, bookmark.url)
    bookmark.title = bookmark.title or preview.get("title") or host_of(bookmark.url)[:80]
    bookmark.description = preview.get("description", "")
    bookmark.save()
    return bookmark, None


def refresh_preview(user, bookmark):
    """Reads the page again and replaces the description; False when nothing could be read."""
    preview = fetch_page_preview(user, bookmark.url)
    if preview:
        # It answered, so whatever the link check found earlier no longer holds.
        bookmark.checked_at, bookmark.unreachable_since = timezone.now(), None
    if preview.get("description"):
        bookmark.description = preview["description"]
    bookmark.save(update_fields=["description", "checked_at", "unreachable_since"])
    return bool(preview.get("description"))


def check_due_links(*, now=None):
    """Requests the pages of bookmarks not checked for LINK_CHECK_INTERVAL, oldest check first, at
    most MAX_LINK_CHECKS_PER_RUN per pass (run by run_automations), and marks the ones that fail."""
    current_time = now or timezone.now()
    due = list(
        Bookmark.objects.filter(owner__is_active=True)
        .filter(Q(checked_at__isnull=True) | Q(checked_at__lt=current_time - LINK_CHECK_INTERVAL))
        .order_by(F("checked_at").asc(nulls_first=True), "id")[:MAX_LINK_CHECKS_PER_RUN]
    )
    checked = unreachable = 0
    for bookmark in due:
        # Shares the per-user preview budget, so the check never fetches more than a user could.
        if count_hit(f"bookmarks:previews:{bookmark.owner_id}", PREVIEW_WINDOW_SECONDS) > PREVIEWS_PER_WINDOW:
            continue
        try:
            safe_fetch.fetch(
                safe_fetch.normalize_url(bookmark.url),
                lambda response: None,
                user_agent=USER_AGENT,
                accept="text/html, application/xhtml+xml, */*",
                server="Die Seite",
            )
        except safe_fetch.FetchError:
            # ponytail: one failed request marks the link (a site blocking bots counts too); the
            # next weekly check or "Vorschau neu laden" clears it again once the page answers.
            bookmark.unreachable_since = bookmark.unreachable_since or current_time
            unreachable += 1
        else:
            bookmark.unreachable_since = None
        bookmark.checked_at = current_time
        bookmark.save(update_fields=["checked_at", "unreachable_since"])
        checked += 1
    return {"checked": checked, "unreachable": unreachable}


class _BookmarkFile(HTMLParser):
    """The links of a browser bookmark export (the Netscape format every browser writes), each with
    the folders it sits in. The toolbar and "other bookmarks" root folders aren't folders to keep."""

    ROOT_FOLDER_MARKERS = {"personal_toolbar_folder", "unfiled_bookmarks_folder"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self._folders = []  # [name or None] per open <DL>
        self._pending_folder = None
        self._in_folder_name = False
        self._link = None

    def handle_starttag(self, tag, attrs):
        attributes = {name.lower(): value or "" for name, value in attrs}
        if tag == "h3":
            self._in_folder_name = True
            is_root = bool(self.ROOT_FOLDER_MARKERS & attributes.keys())
            self._pending_folder = None if is_root else ""
        elif tag == "dl":
            self._folders.append(self._pending_folder)
            self._pending_folder = None
        elif tag == "a":
            self._link = {
                "url": attributes.get("href", "").strip(),
                "title": "",
                "tags": attributes.get("tags", ""),
            }

    def handle_data(self, data):
        if self._in_folder_name and self._pending_folder is not None:
            self._pending_folder += data
        elif self._link is not None:
            self._link["title"] += data

    def handle_endtag(self, tag):
        if tag == "h3":
            self._in_folder_name = False
        elif tag == "dl" and self._folders:
            self._folders.pop()
        elif tag == "a" and self._link is not None:
            folders = [folder.strip() for folder in self._folders if folder and folder.strip()]
            # A folder name is one tag: "Rezepte Italien" becomes "rezepte-italien".
            self._link["tags"] = " ".join(
                ["-".join(folder.split()) for folder in folders] + [self._link["tags"].replace(",", " ")]
            )
            self.links.append(self._link)
            self._link = None


_url_validator = URLValidator(schemes=["http", "https"])


def import_bookmark_file(user, uploaded):
    """Adds the http(s) links of an uploaded browser bookmark export that `user` doesn't have yet,
    folders becoming tags, up to MAX_BOOKMARKS. Returns (imported, skipped) or raises ValidationError.
    No page is fetched: an import can hold hundreds of links; "Vorschau neu laden" fills one in."""
    if uploaded.size > MAX_IMPORT_BYTES:
        raise ValidationError("Die Datei ist zu groß (höchstens 5 MB).")
    text = uploaded.read().decode("utf-8", errors="replace")
    if "<a " not in text.lower():
        raise ValidationError("Die Datei enthält keine Lesezeichen. Exportiere sie im Browser als HTML.")
    parser = _BookmarkFile()
    try:
        parser.feed(text)
        parser.close()
    except (AssertionError, ValueError):
        raise ValidationError("Die Datei konnte nicht gelesen werden.") from None

    url_field_length = Bookmark._meta.get_field("url").max_length
    known = set(Bookmark.objects.filter(owner=user).values_list("url", flat=True))
    room = MAX_BOOKMARKS - len(known)
    new, skipped = [], 0
    for link in parser.links:
        url = link["url"]
        try:
            _url_validator(url)
        except ValidationError:
            skipped += 1
            continue
        if url in known or len(url) > url_field_length or len(new) >= room:
            skipped += 1
            continue
        known.add(url)
        new.append(
            Bookmark(
                owner=user,
                url=url,
                title=_clean(link["title"], 80) or host_of(url)[:80],
                tags=normalize_tags(link["tags"]),
            )
        )
    Bookmark.objects.bulk_create(new)
    return len(new), skipped


def export_bookmark_file(user):
    """`user`'s bookmarks as a Netscape bookmark file, the format every browser imports (and
    import_bookmark_file reads back): one flat list, tags in the TAGS attribute."""
    lines = [
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
        "<TITLE>Bookmarks</TITLE>",
        "<H1>Lunora-Lesezeichen</H1>",
        "<DL><p>",
    ]
    for bookmark in Bookmark.objects.filter(owner=user):
        tags = escape(",".join(bookmark.tag_list))
        lines.append(
            f'    <DT><A HREF="{escape(bookmark.url)}" ADD_DATE="{int(bookmark.created_at.timestamp())}"'
            + (f' TAGS="{tags}"' if tags else "")
            + f">{escape(bookmark.title)}</A>"
        )
        if bookmark.description:
            lines.append(f"    <DD>{escape(bookmark.description)}")
    lines.append("</DL><p>")
    return "\n".join(lines) + "\n"


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
