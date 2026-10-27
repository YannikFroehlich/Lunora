"""Signed-in devices: track each authenticated session and let users revoke them."""

import re
import time

from django.contrib.sessions.models import Session
from django.utils import timezone

from app.models import UserSession

TRACKING_SESSION_KEY = "_lunora_device_seen"
# Refresh last_seen_at at most this often, so ordinary page views don't write on every request.
TRACKING_INTERVAL_SECONDS = 5 * 60

BROWSERS = [
    ("Edge", r"Edg(e|A|iOS)?/"),
    ("Opera", r"OPR/|Opera"),
    ("Samsung Internet", r"SamsungBrowser/"),
    ("Firefox", r"Firefox/|FxiOS/"),
    ("Chrome", r"Chrome/|CriOS/"),
    ("Safari", r"Safari/"),
]
SYSTEMS = [
    ("iPhone", r"iPhone"),
    ("iPad", r"iPad"),
    ("Android", r"Android"),
    ("Windows", r"Windows"),
    ("macOS", r"Mac OS X|Macintosh"),
    ("ChromeOS", r"CrOS"),
    ("Linux", r"Linux"),
]


def device_label(user_agent):
    """Short human label like "Firefox unter Windows" from a User-Agent header."""
    browser = next((name for name, pattern in BROWSERS if re.search(pattern, user_agent or "")), None)
    system = next((name for name, pattern in SYSTEMS if re.search(pattern, user_agent or "")), None)
    if browser and system:
        return f"{browser} unter {system}"
    return browser or system or "Unbekanntes Gerät"


def track_session(request):
    """Record or refresh the device row for the current authenticated session."""
    session = request.session
    key = session.session_key
    if not key:
        return
    seen = session.get(TRACKING_SESSION_KEY)
    now = time.time()
    if seen and seen[0] == key and now - seen[1] < TRACKING_INTERVAL_SECONDS:
        return
    UserSession.objects.update_or_create(
        session_key=key,
        defaults={
            "user": request.user,
            "user_agent": request.headers.get("User-Agent", "")[:300],
            "last_seen_at": timezone.now(),
        },
    )
    # Stored with the key, so a rotated session key (login, password change) is tracked anew.
    session[TRACKING_SESSION_KEY] = [key, now]


def active_sessions(user):
    """The user's devices with a still-valid session, newest activity first; stale rows are pruned."""
    rows = list(UserSession.objects.filter(user=user))
    alive = set(
        Session.objects.filter(
            session_key__in=[row.session_key for row in rows], expire_date__gt=timezone.now()
        ).values_list("session_key", flat=True)
    )
    stale = [row.pk for row in rows if row.session_key not in alive]
    if stale:
        UserSession.objects.filter(pk__in=stale).delete()
    return [row for row in rows if row.session_key in alive]


def revoke_session(user, session_id, *, current_key):
    """Sign out one of the user's other devices; returns True when a session was ended."""
    row = UserSession.objects.filter(user=user, pk=session_id).exclude(session_key=current_key).first()
    if row is None:
        return False
    Session.objects.filter(session_key=row.session_key).delete()
    row.delete()
    return True


def revoke_other_sessions(user, *, current_key):
    """Sign out every other session of the user, including ones not tracked yet."""
    # ponytail: decodes every live session (like the admin's global logout); index sessions by
    # user if the session table grows large.
    keys = {
        session.session_key
        for session in Session.objects.filter(expire_date__gt=timezone.now()).exclude(session_key=current_key)
        if session.get_decoded().get("_auth_user_id") == str(user.pk)
    }
    keys.update(
        UserSession.objects.filter(user=user)
        .exclude(session_key=current_key)
        .values_list("session_key", flat=True)
    )
    Session.objects.filter(session_key__in=keys).delete()
    UserSession.objects.filter(user=user).exclude(session_key=current_key).delete()
    return len(keys)
