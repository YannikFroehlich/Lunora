import logging
import traceback

from django.utils import timezone

from app.services.audit_log import purge_old_entries
from app.services.calendar_subscriptions import sync_due_subscriptions
from app.services.chat import send_due_scheduled_messages
from app.services.notifications import (
    materialize_due_habit_reminders,
    send_due_reminder_emails,
    send_due_task_reminder_emails,
    send_new_invitation_emails,
    send_note_activity_emails,
    send_pending_user_notification_emails,
    send_weekly_summaries,
)
from app.services.system_settings import feature_enabled
from app.services.web_push import (
    materialize_web_push_weather_alerts,
    send_pending_web_push_notifications,
)

logger = logging.getLogger(__name__)


def _guarded(name, task, **kwargs):
    """Run one step of a pass, so that its failure doesn't cost the later steps their turn."""
    try:
        return task(**kwargs)
    except Exception as error:
        # Type and frames only: an exception text can hold a calendar URL or a push endpoint.
        frames = "".join(traceback.format_tb(error.__traceback__))
        logger.error("Scheduled task %s failed: %s\n%s", name, type(error).__name__, frames)
        return {"sent": 0, "failed": 0, "error": True}


def failed_steps(result):
    """The names of the steps of a run_scheduled_tasks result that raised."""
    return [name for name, step in result.items() if step.get("error")]


def run_scheduled_tasks(*, now=None):
    current_time = now or timezone.now()
    if feature_enabled("calendar_reminders"):
        reminder_result = _guarded("reminder_emails", send_due_reminder_emails, now=current_time)
    else:
        reminder_result = {"sent": 0, "failed": 0, "disabled": True}
    if feature_enabled("calendar_event_creation"):
        invitation_result = _guarded("event_invitation_emails", send_new_invitation_emails, now=current_time)
    else:
        invitation_result = {"sent": 0, "failed": 0, "disabled": True}
    if feature_enabled("notes"):
        note_activity_result = _guarded("note_activity_emails", send_note_activity_emails, now=current_time)
    else:
        note_activity_result = {"sent": 0, "failed": 0, "disabled": True}
    if feature_enabled("tasks"):
        task_reminder_result = _guarded(
            "task_reminder_emails", send_due_task_reminder_emails, now=current_time
        )
    else:
        task_reminder_result = {"sent": 0, "failed": 0, "disabled": True}
    weekly_result = _guarded("weekly_summaries", send_weekly_summaries, now=current_time)
    if feature_enabled("calendar_subscriptions"):
        subscription_result = _guarded("calendar_subscriptions", sync_due_subscriptions, now=current_time)
    else:
        subscription_result = {"synced": 0, "failed": 0, "disabled": True}
    if feature_enabled("weather"):
        weather_push_result = _guarded(
            "weather_push_alerts", materialize_web_push_weather_alerts, now=current_time
        )
    else:
        weather_push_result = {"created": 0, "failed": 0, "disabled": True}
    if feature_enabled("tools"):
        habit_reminder_result = _guarded(
            "habit_reminders", lambda: {"created": materialize_due_habit_reminders(now=current_time)}
        )
    else:
        habit_reminder_result = {"created": 0, "disabled": True}
    if feature_enabled("messages"):
        scheduled_message_result = _guarded(
            "scheduled_messages", send_due_scheduled_messages, now=current_time
        )
    else:
        scheduled_message_result = {"sent": 0, "skipped": 0, "disabled": True}
    notification_email_result = _guarded(
        "notification_emails",
        send_pending_user_notification_emails,
        now=current_time,
        include_note_shares=feature_enabled("notes"),
        include_vocabulary_shares=feature_enabled("tools"),
        include_shopping_shares=feature_enabled("tools"),
        include_expense_groups=feature_enabled("tools"),
        include_weather=feature_enabled("weather"),
        include_task_assignments=feature_enabled("tasks"),
        include_habit_reminders=feature_enabled("tools"),
        include_chat_mentions=feature_enabled("messages"),
        include_event_comments=feature_enabled("calendar_event_creation"),
    )
    web_push_result = _guarded("web_push", send_pending_web_push_notifications, now=current_time)
    audit_log_result = _guarded("audit_log", lambda: {"purged": purge_old_entries(now=current_time)})
    return {
        "reminder_emails": reminder_result,
        "event_invitation_emails": invitation_result,
        "note_activity_emails": note_activity_result,
        "task_reminder_emails": task_reminder_result,
        "weekly_summaries": weekly_result,
        "calendar_subscriptions": subscription_result,
        "weather_push_alerts": weather_push_result,
        "habit_reminders": habit_reminder_result,
        "scheduled_messages": scheduled_message_result,
        "notification_emails": notification_email_result,
        "web_push": web_push_result,
        "audit_log": audit_log_result,
    }
