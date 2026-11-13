"""The audit log: security and administration events, shown to superusers on /administration/.

Entries hold who (``actor`` plus a label that survives account deletion), what (an action and a
short German summary without secrets), when and from which IP address. Failed logins are only
recorded with an actor when the typed identifier belongs to an existing account, so typos (or a
password typed into the wrong field) never end up in the log.
"""

import ipaddress
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from app.models import AuditLogEntry
from app.services.user_preferences import format_user_datetime

logger = logging.getLogger(__name__)

AUDIT_LOG_RETENTION = timedelta(days=180)
AUDIT_LOG_PAGE_SIZE = 50
SECURITY_ACTIONS = {
    AuditLogEntry.ACTION_LOGIN_FAILED,
    AuditLogEntry.ACTION_TWO_FACTOR_FAILED,
    AuditLogEntry.ACTION_PASSWORD_RESET,
    AuditLogEntry.ACTION_PASSWORD_CHANGED,
    AuditLogEntry.ACTION_TWO_FACTOR_ENABLED,
    AuditLogEntry.ACTION_TWO_FACTOR_DISABLED,
    AuditLogEntry.ACTION_RECOVERY_CODES,
    AuditLogEntry.ACTION_PASSKEY_ADDED,
    AuditLogEntry.ACTION_PASSKEY_REMOVED,
    AuditLogEntry.ACTION_SESSIONS_REVOKED,
    AuditLogEntry.ACTION_CALENDAR_FEED,
    AuditLogEntry.ACTION_ACCOUNT_DELETED,
    AuditLogEntry.ACTION_SETTINGS_CHANGED,
    AuditLogEntry.ACTION_FORCE_LOGOUT,
}
# Highlighted in the list: things an attacker would do or that affect everyone.
WARNING_ACTIONS = {
    AuditLogEntry.ACTION_LOGIN_FAILED,
    AuditLogEntry.ACTION_TWO_FACTOR_FAILED,
    AuditLogEntry.ACTION_TWO_FACTOR_DISABLED,
    AuditLogEntry.ACTION_ACCOUNT_DELETED,
    AuditLogEntry.ACTION_FORCE_LOGOUT,
}
FILTERS = {
    "all": ("Alle", None),
    "security": ("Sicherheit", SECURITY_ACTIONS),
    "logins": (
        "Anmeldungen",
        {
            AuditLogEntry.ACTION_LOGIN,
            AuditLogEntry.ACTION_LOGIN_FAILED,
            AuditLogEntry.ACTION_TWO_FACTOR_FAILED,
            AuditLogEntry.ACTION_LOGOUT,
            AuditLogEntry.ACTION_REGISTER,
        },
    ),
    "warnings": ("Auffällig", WARNING_ACTIONS),
}


def client_ip(request):
    """The client's address: from ``LUNORA_CLIENT_IP_HEADER`` (set behind a trusted proxy such as
    Cloudflare's ``CF-Connecting-IP``) or ``REMOTE_ADDR``; None when it isn't a valid IP."""
    if request is None:
        return None
    header = getattr(settings, "LUNORA_CLIENT_IP_HEADER", "")
    raw = ""
    if header:
        raw = request.META.get("HTTP_" + header.upper().replace("-", "_"), "")
        # X-Forwarded-For style lists: the first entry is the original client.
        raw = raw.split(",")[0].strip()
    raw = raw or request.META.get("REMOTE_ADDR", "")
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


def actor_label(user):
    if user is None:
        return ""
    return (user.email or user.get_username() or f"#{user.pk}")[:254]


def record(action, request=None, *, actor=None, summary=""):
    """Write one entry; never lets a logging problem break the request that triggered it."""
    if actor is None and request is not None and getattr(request, "user", None) is not None:
        actor = request.user if request.user.is_authenticated else None
    if actor is not None and actor.pk is None:
        # A just-deleted account: keep the label, there is no row to point at any more.
        label, actor = actor_label(actor), None
    else:
        label = actor_label(actor)
    try:
        # Its own savepoint, so a failed insert can't poison the surrounding transaction.
        with transaction.atomic():
            return AuditLogEntry.objects.create(
                actor=actor,
                actor_label=label,
                action=action,
                summary=summary[:300],
                ip_address=client_ip(request),
            )
    except Exception:
        logger.exception("Audit log entry %s could not be written", action)
        return None


def known_user_for_identifier(identifier):
    """The account an attempted login identifier (e-mail or username) belongs to, if exactly one."""
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    users = list(User.objects.filter(Q(username__iexact=identifier) | Q(email__iexact=identifier))[:2])
    return users[0] if len(users) == 1 else None


def purge_old_entries(*, now=None):
    cutoff = (now or timezone.now()) - AUDIT_LOG_RETENTION
    deleted, _details = AuditLogEntry.objects.filter(created_at__lt=cutoff).delete()
    return deleted


def audit_log_page(viewer, *, filter_key="all", account="", page=None):
    """One page of entries for the administration page, newest first, optionally for one account."""
    if filter_key not in FILTERS:
        filter_key = "all"
    account = account.strip()[:254]
    entries = AuditLogEntry.objects.select_related("actor")
    actions = FILTERS[filter_key][1]
    if actions is not None:
        entries = entries.filter(action__in=actions)
    if account:
        entries = entries.filter(actor_label__icontains=account)
    paginator = Paginator(entries, AUDIT_LOG_PAGE_SIZE)
    page_obj = paginator.get_page(page)
    items = [
        {
            "action": entry.action,
            "label": entry.get_action_display(),
            "actor": entry.actor_label or "unbekannt",
            "actor_deleted": bool(entry.actor_label) and entry.actor_id is None,
            "summary": entry.summary,
            "ip_address": entry.ip_address or "",
            "created_label": format_user_datetime(entry.created_at, viewer),
            "created_iso": entry.created_at.isoformat(),
            "is_warning": entry.action in WARNING_ACTIONS,
        }
        for entry in page_obj.object_list
    ]
    return {
        "items": items,
        "page": page_obj,
        "filter": filter_key,
        "account": account,
        "filters": [{"key": key, "label": label} for key, (label, _actions) in FILTERS.items()],
        "retention_days": AUDIT_LOG_RETENTION.days,
    }


def connect_signals():
    """Record logins, logouts and failed logins from Django's auth signals (called in AppConfig.ready)."""
    from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed

    user_logged_in.connect(_on_login, dispatch_uid="lunora_audit_login")
    user_logged_out.connect(_on_logout, dispatch_uid="lunora_audit_logout")
    user_login_failed.connect(_on_login_failed, dispatch_uid="lunora_audit_login_failed")


def _on_login(sender, request, user, **kwargs):
    record(AuditLogEntry.ACTION_LOGIN, request, actor=user)


def _on_logout(sender, request, user, **kwargs):
    # Deleting an account logs it out afterwards; that is already recorded as the deletion.
    if user is not None and user.pk is not None:
        record(AuditLogEntry.ACTION_LOGOUT, request, actor=user)


def _on_login_failed(sender, credentials, request=None, **kwargs):
    user = known_user_for_identifier(credentials.get("username"))
    record(
        AuditLogEntry.ACTION_LOGIN_FAILED,
        request,
        actor=user,
        summary="" if user else "Unbekanntes Konto",
    )
