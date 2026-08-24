import logging
from datetime import timedelta

from django.utils import timezone

from app.models import CalendarSource
from app.services.calendar_service import sync_calendar_source
from app.services.notifications import (
    send_due_reminder_emails,
    send_new_invitation_emails,
    send_note_activity_emails,
    send_weekly_summaries,
)
from app.services.system_settings import feature_enabled


logger = logging.getLogger(__name__)


def run_scheduled_tasks(*, now=None):
    current_time = now or timezone.now()
    sync_result = sync_due_calendars(now=current_time)
    if feature_enabled("calendar_reminders"):
        reminder_result = send_due_reminder_emails(now=current_time)
    else:
        reminder_result = {"sent": 0, "failed": 0, "disabled": True}
    if feature_enabled("calendar_event_creation"):
        invitation_result = send_new_invitation_emails(now=current_time)
    else:
        invitation_result = {"sent": 0, "failed": 0, "disabled": True}
    if feature_enabled("notes"):
        note_activity_result = send_note_activity_emails(now=current_time)
    else:
        note_activity_result = {"sent": 0, "failed": 0, "disabled": True}
    weekly_result = send_weekly_summaries(now=current_time)
    return {
        "calendar_sync": sync_result,
        "reminder_emails": reminder_result,
        "event_invitation_emails": invitation_result,
        "note_activity_emails": note_activity_result,
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
        manually_requested = source.sync_requested_at is not None
        last_activity_at = source.last_sync_attempt_at or source.last_synced_at
        if not manually_requested and last_activity_at:
            next_sync_at = last_activity_at + timedelta(minutes=source.sync_interval_minutes)
            if next_sync_at > current_time:
                skipped += 1
                continue

        claimed = CalendarSource.objects.filter(
            pk=source.pk,
            enabled=True,
            sync_requested_at=source.sync_requested_at,
            last_sync_attempt_at=source.last_sync_attempt_at,
        ).update(
            sync_requested_at=None,
            last_sync_attempt_at=current_time,
            updated_at=current_time,
        )
        if not claimed:
            skipped += 1
            continue

        source.sync_requested_at = None
        source.last_sync_attempt_at = current_time
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
