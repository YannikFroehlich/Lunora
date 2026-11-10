"""External iCal calendars (Google, iCloud, Outlook, ...) mirrored read-only into a user's calendar.

The subscription URL is a credential: a secret Google or iCloud address reads the whole calendar.
It is never logged, exported or rendered, and no error message here ever contains it.
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from app.models import CalendarEvent, CalendarSubscription
from app.services import safe_fetch
from app.services.data_import import ics_event_rows

logger = logging.getLogger(__name__)

MAX_SUBSCRIPTIONS = 10
MAX_CALENDAR_BYTES = 5_000_000
MAX_EVENTS = 5000
SYNC_INTERVAL = timedelta(minutes=30)
# "Jetzt aktualisieren" fetches inside the request, so it is throttled per subscription.
MANUAL_REFRESH_INTERVAL = timedelta(minutes=1)
# Each automation pass syncs at most this many, so slow servers can't hold up reminders for long.
MAX_SYNCS_PER_RUN = 20
WINDOW_PAST = timedelta(days=180)
WINDOW_FUTURE = timedelta(days=400)
USER_AGENT = "Lunora-Kalender/1.0"

# The SSRF-safe fetcher's errors (app/services/safe_fetch.py) are safe to show and never hold the URL.
SubscriptionError = safe_fetch.FetchError


def normalize_url(raw_url):
    """The URL to fetch (webcal:// becomes https://), or SubscriptionError when it isn't http(s) on the
    default port of a public host. The addresses a name resolves to are checked when connecting."""
    url = (raw_url or "").strip()
    if url[:9].lower() == "webcal://":
        url = "https://" + url[9:]
    if url and "://" in url and url.split("://", 1)[0].lower() not in safe_fetch.DEFAULT_PORTS:
        raise SubscriptionError("Bitte gib eine https-, http- oder webcal-Adresse ein.")
    return safe_fetch.normalize_url(url)


def fetch_calendar(url):
    """Download an iCalendar text, following up to safe_fetch.MAX_REDIRECTS checked redirects."""
    return safe_fetch.fetch(
        normalize_url(url),
        _read_calendar,
        user_agent=USER_AGENT,
        accept="text/calendar, */*",
        server="Der Kalender-Server",
    )


def _read_calendar(response):
    body = safe_fetch.read_capped(response, MAX_CALENDAR_BYTES, "Der Kalender ist größer als 5 MB.")
    text = body.decode("utf-8-sig", errors="replace")
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
    except SubscriptionError as error:
        subscription.last_error = str(error)
        logger.info("Calendar subscription %s failed: %s", subscription.pk, error)
    except Exception as error:
        # The data comes from a stranger's server and must never stop the automation loop that also
        # delivers everyone's notifications. Only the type is logged: a message could hold the URL.
        subscription.last_error = "Der Kalender konnte nicht gelesen werden."
        logger.warning("Calendar subscription %s failed: %s", subscription.pk, type(error).__name__)
    # Stored even for a calendar that always fails, or it would stay first in line on every pass.
    CalendarSubscription.objects.filter(pk=subscription.pk).update(
        last_attempt_at=now, last_error=subscription.last_error
    )
    return False


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
