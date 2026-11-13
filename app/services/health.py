"""Liveness/readiness report for /healthz/ (uptime monitors, the deploy script).

Only fixed status words leave this module: no versions, hostnames, exception texts or counts, because
the endpoint is public.
"""

import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.utils import timezone

from app.models import AutomationHeartbeat

OK = "ok"
FAILING = "error"
STALE = "stale"
UNKNOWN = "unknown"

logger = logging.getLogger(__name__)


def record_automation_run(*, now=None):
    """Called by ``run_automations`` after each pass in which no step failed."""
    AutomationHeartbeat.objects.update_or_create(pk=1, defaults={"finished_at": now or timezone.now()})


def automation_max_age():
    """A loop pass is overdue after three intervals, but never sooner than five minutes."""
    interval = getattr(settings, "LUNORA_AUTOMATION_INTERVAL_SECONDS", 60)
    return max(timedelta(seconds=3 * interval), timedelta(minutes=5))


def _check_database():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # any failure means "not healthy"; the detail only goes to the server log
        logger.warning("Health check: database unavailable", exc_info=True)
        return FAILING
    return OK


def _check_cache():
    key = f"healthz:{uuid.uuid4().hex}"
    try:
        cache.set(key, "1", timeout=10)
        value = cache.get(key)
        cache.delete(key)
    except Exception:
        logger.warning("Health check: cache unavailable", exc_info=True)
        return FAILING
    return OK if value == "1" else FAILING


def last_automation_run():
    """When ``run_automations`` last finished a pass without a failed step, or None."""
    return AutomationHeartbeat.objects.filter(pk=1).values_list("finished_at", flat=True).first()


def _check_automations(now):
    try:
        heartbeat = last_automation_run()
    except Exception:
        logger.warning("Health check: automation heartbeat unreadable", exc_info=True)
        return FAILING
    if heartbeat is None:
        return UNKNOWN
    return OK if now - heartbeat <= automation_max_age() else STALE


def health_report(*, now=None):
    """``(healthy, checks)``; the automation check only counts where it is required (production)."""
    current_time = now or timezone.now()
    checks = {"database": _check_database(), "cache": _check_cache()}
    checks["automations"] = _check_automations(current_time) if checks["database"] == OK else UNKNOWN
    required = ["database", "cache"]
    if getattr(settings, "LUNORA_HEALTH_REQUIRE_AUTOMATIONS", False):
        required.append("automations")
    return all(checks[name] == OK for name in required), checks
