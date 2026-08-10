import logging
from datetime import timedelta

from django.utils import timezone

from app.models import CalendarSource
from app.services.calendar_service import sync_calendar_source
from app.services.notifications import send_due_reminder_emails, send_weekly_summaries
from app.services.system_settings import feature_enabled


logger = logging.getLogger(__name__)


def run_scheduled_tasks(*, now=None):
    current_time = now or timezone.now()
    sync_result = sync_due_calendars(now=current_time)
    if feature_enabled("calendar_reminders"):
        reminder_result = send_due_reminder_emails(now=current_time)
    else:
        reminder_result = {"sent": 0, "failed": 0, "disabled": True}
    weekly_result = send_weekly_summaries(now=current_time)
    return {
        "calendar_sync": sync_result,
        "reminder_emails": reminder_result,
        "weekly_summaries": weekly_result,
    }


def sync_due_calendars(*, now=None):
    current_time = now or timezone.now()
    if not feature_enabled("calendar_sync"):
        return {"synced": 0, "failed": 0, "skipped": 0, "disabled": True}

    synced = 0
    failed = 0
    skipped = 0
    sources = CalendarSource.objects.filter(enabled=True).select_related("user").order_by("id")
    for source in sources.iterator():
        if source.last_synced_at:
            next_sync_at = source.last_synced_at + timedelta(minutes=source.sync_interval_minutes)
            if next_sync_at > current_time:
                skipped += 1
                continue
        try:
            result = sync_calendar_source(source, force=True)
        except Exception:
            failed += 1
            logger.exception("Scheduled calendar sync failed for source %s", source.pk)
            continue
        if result.get("synced"):
            synced += 1
        else:
            failed += 1

    return {"synced": synced, "failed": failed, "skipped": skipped}
