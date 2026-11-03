"""External iCal calendars (Google, iCloud, Outlook, ...) mirrored read-only into a user's calendar.

The subscription URL is a credential: a secret Google or iCloud address reads the whole calendar.
It is never logged, exported or rendered, and no error message here ever contains it.
"""

import http.client
import ipaddress
import logging
import socket
import time
from datetime import timedelta
from urllib.parse import urljoin, urlsplit

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from app.models import CalendarEvent, CalendarSubscription
from app.services.data_import import ics_event_rows

logger = logging.getLogger(__name__)

MAX_SUBSCRIPTIONS = 10
MAX_CALENDAR_BYTES = 5_000_000
MAX_EVENTS = 5000
MAX_REDIRECTS = 4
FETCH_TIMEOUT_SECONDS = 10
FETCH_DEADLINE_SECONDS = 30
SYNC_INTERVAL = timedelta(minutes=30)
# "Jetzt aktualisieren" fetches inside the request, so it is throttled per subscription.
MANUAL_REFRESH_INTERVAL = timedelta(minutes=1)
# Each automation pass syncs at most this many, so slow servers can't hold up reminders for long.
MAX_SYNCS_PER_RUN = 20
WINDOW_PAST = timedelta(days=180)
WINDOW_FUTURE = timedelta(days=400)
DEFAULT_PORTS = {"http": 80, "https": 443}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
USER_AGENT = "Lunora-Kalender/1.0"


class SubscriptionError(Exception):
    """A failure whose German message is safe to show and store: it never contains the URL."""


def normalize_url(raw_url):
    """The URL to fetch (webcal:// becomes https://), or SubscriptionError when it isn't http(s) on the
    default port of a public host. The addresses a name resolves to are checked when connecting."""
    url = (raw_url or "").strip()
    if url[:9].lower() == "webcal://":
        url = "https://" + url[9:]
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise SubscriptionError("Die Adresse ist ungültig.") from None
    if parts.scheme.lower() not in DEFAULT_PORTS or not parts.hostname:
        raise SubscriptionError("Bitte gib eine https-, http- oder webcal-Adresse ein.")
    if parts.username or parts.password:
        raise SubscriptionError("Die Adresse darf keine Zugangsdaten enthalten.")
    if port not in (None, DEFAULT_PORTS[parts.scheme.lower()]):
        raise SubscriptionError("Nur Adressen ohne eigenen Port werden unterstützt.")
    host = _ascii_host(parts.hostname)
    try:
        is_public = ipaddress.ip_address(host).is_global
    except ValueError:
        is_public = "." in host and not host.endswith((".localhost", ".local", ".internal", ".lan"))
    if not is_public:
        raise SubscriptionError("Die Adresse muss auf einen öffentlichen Server zeigen.")
    return url


def _ascii_host(hostname):
    try:
        return hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise SubscriptionError("Die Adresse ist ungültig.") from None


def _public_connection(address, timeout=None, source_address=None):
    """socket.create_connection for http.client that only connects to public addresses. The check
    runs on the addresses actually connected to, so a name re-resolving to 127.0.0.1 (DNS
    rebinding) or a redirect to an internal host can't reach the server's own network."""
    host, port = address
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise SubscriptionError("Der Server der Adresse wurde nicht gefunden.") from None
    if not infos or not all(ipaddress.ip_address(info[4][0]).is_global for info in infos):
        raise SubscriptionError("Die Adresse muss auf einen öffentlichen Server zeigen.")
    error = None
    for family, socket_type, proto, _name, sockaddr in infos:
        sock = socket.socket(family, socket_type, proto)
        sock.settimeout(timeout)
        try:
            sock.connect(sockaddr)
        except OSError as connect_error:
            sock.close()
            error = connect_error
            continue
        return sock
    raise error


def fetch_calendar(url):
    """Download an iCalendar text, following up to MAX_REDIRECTS redirects (each one checked again)."""
    deadline = time.monotonic() + FETCH_DEADLINE_SECONDS
    for _redirect in range(MAX_REDIRECTS + 1):
        url = normalize_url(url)
        parts = urlsplit(url)
        connection_class = (
            http.client.HTTPSConnection if parts.scheme.lower() == "https" else http.client.HTTPConnection
        )
        # An explicit port, or http.client would read the end of an IPv6 literal as one.
        connection = connection_class(
            _ascii_host(parts.hostname), DEFAULT_PORTS[parts.scheme.lower()], timeout=FETCH_TIMEOUT_SECONDS
        )
        # http.client opens its socket through this attribute.
        connection._create_connection = _public_connection
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        try:
            connection.request(
                "GET", path, headers={"User-Agent": USER_AGENT, "Accept": "text/calendar, */*"}
            )
            response = connection.getresponse()
            if response.status in REDIRECT_STATUSES and response.getheader("Location"):
                url = urljoin(url, response.getheader("Location"))
                continue
            if response.status != 200:
                raise SubscriptionError(f"Der Kalender-Server antwortet mit Fehler {response.status}.")
            return _read_calendar(response, deadline)
        except (OSError, http.client.HTTPException):
            raise SubscriptionError("Der Kalender-Server ist nicht erreichbar.") from None
        finally:
            connection.close()
    raise SubscriptionError("Die Adresse leitet zu oft weiter.")


def _read_calendar(response, deadline):
    chunks = []
    size = 0
    while chunk := response.read(65536):
        size += len(chunk)
        if size > MAX_CALENDAR_BYTES:
            raise SubscriptionError("Der Kalender ist größer als 5 MB.")
        if time.monotonic() > deadline:
            raise SubscriptionError("Der Kalender-Server antwortet zu langsam.")
        chunks.append(chunk)
    text = b"".join(chunks).decode("utf-8-sig", errors="replace")
    if "BEGIN:VCALENDAR" not in text:
        raise SubscriptionError("Unter der Adresse liegt kein iCal-Kalender.")
    return text


def sync_subscription(subscription, *, now=None):
    """Fetch the calendar and replace the subscription's events with its current ones (from
    WINDOW_PAST ago to WINDOW_FUTURE ahead). A failure keeps the previous events and stores a
    message in last_error. Returns True on success."""
    now = now or timezone.now()
    subscription.last_attempt_at = now
    try:
        text = fetch_calendar(subscription.url)
        rows, _counts = ics_event_rows(
            subscription.user, text, window=(now - WINDOW_PAST, now + WINDOW_FUTURE), limit=MAX_EVENTS
        )
    except SubscriptionError as error:
        subscription.last_error = str(error)
        CalendarSubscription.objects.filter(pk=subscription.pk).update(
            last_attempt_at=now, last_error=subscription.last_error
        )
        logger.info("Calendar subscription %s failed: %s", subscription.pk, error)
        return False

    for row in rows:
        row.subscription = subscription
    with transaction.atomic():
        # The row lock makes a concurrent sync of the same calendar wait, instead of both deleting
        # the old events and then both inserting new ones.
        if not CalendarSubscription.objects.select_for_update().filter(pk=subscription.pk).exists():
            return False
        CalendarEvent.objects.filter(subscription=subscription).delete()
        CalendarEvent.objects.bulk_create(rows)
        subscription.last_synced_at = now
        subscription.last_error = ""
        subscription.event_count = len(rows)
        CalendarSubscription.objects.filter(pk=subscription.pk).update(
            last_attempt_at=now, last_synced_at=now, last_error="", event_count=len(rows)
        )
    return True


def can_refresh(subscription, *, now=None):
    now = now or timezone.now()
    return (
        subscription.last_attempt_at is None or subscription.last_attempt_at <= now - MANUAL_REFRESH_INTERVAL
    )


def sync_due_subscriptions(*, now=None):
    """Sync the subscriptions not tried for SYNC_INTERVAL, the longest-waiting ones first."""
    now = now or timezone.now()
    due = (
        CalendarSubscription.objects.filter(user__is_active=True)
        .filter(Q(last_attempt_at__isnull=True) | Q(last_attempt_at__lte=now - SYNC_INTERVAL))
        .select_related("user__profile")
        .order_by(F("last_attempt_at").asc(nulls_first=True), "id")
    )
    result = {"synced": 0, "failed": 0}
    for subscription in due[:MAX_SYNCS_PER_RUN]:
        result["synced" if sync_subscription(subscription, now=now) else "failed"] += 1
    return result
