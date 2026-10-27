from django.utils import timezone

from app.services.notifications import (
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


def run_scheduled_tasks(*, now=None):
    current_time = now or timezone.now()
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
    if feature_enabled("tasks"):
        task_reminder_result = send_due_task_reminder_emails(now=current_time)
    else:
        task_reminder_result = {"sent": 0, "failed": 0, "disabled": True}
    weekly_result = send_weekly_summaries(now=current_time)
    if feature_enabled("weather"):
        weather_push_result = materialize_web_push_weather_alerts(now=current_time)
    else:
        weather_push_result = {"created": 0, "failed": 0, "disabled": True}
    notification_email_result = send_pending_user_notification_emails(
        now=current_time,
        include_note_shares=feature_enabled("notes"),
        include_vocabulary_shares=feature_enabled("tools"),
        include_weather=feature_enabled("weather"),
    )
    web_push_result = send_pending_web_push_notifications(now=current_time)
    return {
        "reminder_emails": reminder_result,
        "event_invitation_emails": invitation_result,
        "note_activity_emails": note_activity_result,
        "task_reminder_emails": task_reminder_result,
        "weekly_summaries": weekly_result,
        "weather_push_alerts": weather_push_result,
        "notification_emails": notification_email_result,
        "web_push": web_push_result,
    }
