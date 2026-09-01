import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from app.models import (
    CalendarEvent,
    CalendarEventAttendee,
    CalendarReminder,
    NoteActivityNotification,
    NoteShare,
    Profile,
    Task,
    UserNotification,
    WeatherLocation,
    WeeklySummaryDelivery,
)
from app.services.message_queries import unread_total_for_user
from app.services.notification_preferences import (
    CHANNEL_EMAIL,
    CHANNEL_INBOX,
    CHANNEL_WEB_PUSH,
    enabled_notification_kinds,
    filter_channel_items,
    notification_channel_enabled,
    notification_preference_map,
)
from app.services.user_preferences import format_user_datetime, localtime_for_user
from app.services.weather_service import get_weather_alert_for_location, weather_location_to_dict

logger = logging.getLogger(__name__)

WEATHER_ALERT_COOLDOWN = timedelta(hours=6)

NOTIFICATION_PRESENTATION = {
    UserNotification.KIND_CALENDAR_REMINDER: ("fa-calendar-check", "Kalender", "calendar"),
    UserNotification.KIND_TASK_DUE: ("fa-list-check", "Aufgabe", "task"),
    UserNotification.KIND_EVENT_INVITATION: ("fa-user-clock", "Einladung", "invitation"),
    UserNotification.KIND_NOTE_MENTION: ("fa-at", "Erwähnung", "note"),
    UserNotification.KIND_NOTE_COMMENT: ("fa-comment-dots", "Kommentar", "note"),
    UserNotification.KIND_NOTE_SHARE: ("fa-share-nodes", "Freigabe", "note"),
    UserNotification.KIND_WEATHER_ALERT: ("fa-cloud-bolt", "Wetter", "weather"),
}


def notification_display_items(notifications, user):
    """Attach the shared icon/label/tone presentation to a list of UserNotification rows."""
    items = []
    for notification in notifications:
        icon, label, tone = NOTIFICATION_PRESENTATION.get(
            notification.kind, ("fa-bell", "Hinweis", "default")
        )
        items.append(
            {
                "notification": notification,
                "icon": icon,
                "label": label,
                "tone": tone,
                "created_label": format_user_datetime(notification.created_at, user),
            }
        )
    return items


def dashboard_latest_notifications(user, *, limit=5):
    """Latest unread inbox notifications for the dashboard notification widget."""
    visible_kinds = enabled_notification_kinds(user, CHANNEL_INBOX)
    notifications = (
        UserNotification.objects.filter(recipient=user, kind__in=visible_kinds, read_at__isnull=True)
        .select_related("actor")
        .order_by("-created_at", "-id")[:limit]
    )
    return notification_display_items(notifications, user)


def _create_missing_user_notifications(rows):
    if not rows:
        return 0

    created = 0
    for offset in range(0, len(rows), 400):
        batch = rows[offset : offset + 400]
        source_keys = [row["source_key"] for row in batch]
        recipient_ids = [row["recipient_id"] for row in batch]
        existing_sources = set(
            UserNotification.objects.filter(
                recipient_id__in=recipient_ids,
                source_key__in=source_keys,
            ).values_list("recipient_id", "source_key")
        )
        notifications = [
            UserNotification(**row)
            for row in batch
            if (row["recipient_id"], row["source_key"]) not in existing_sources
        ]
        if notifications:
            UserNotification.objects.bulk_create(notifications, ignore_conflicts=True)
            created += len(notifications)
    return created


def materialize_due_user_notifications(
    *,
    user=None,
    now=None,
    include_reminders=True,
    include_tasks=True,
):
    """Persist due reminder/task events for the notification inbox."""
    current_time = now or timezone.now()
    rows = []

    if include_reminders:
        reminders = CalendarReminder.objects.filter(
            is_done=False,
            due_at__isnull=False,
            due_at__lte=current_time,
        ).select_related("user", "user__profile")
        if user is not None:
            reminders = reminders.filter(user=user)
        for reminder in reminders:
            rows.append(
                {
                    "recipient_id": reminder.user_id,
                    "kind": UserNotification.KIND_CALENDAR_REMINDER,
                    "title": f"Erinnerung fällig: {reminder.title}",
                    "body": f"Fällig am {format_user_datetime(reminder.due_at, reminder.user)}",
                    "url": "/calendar/",
                    "source_key": f"calendar-reminder:{reminder.pk}",
                }
            )

    if include_tasks:
        tasks = Task.objects.filter(
            is_done=False,
            due_at__isnull=False,
            due_at__lte=current_time,
        ).select_related("user", "user__profile")
        if user is not None:
            tasks = tasks.filter(user=user)
        for task in tasks:
            rows.append(
                {
                    "recipient_id": task.user_id,
                    "kind": UserNotification.KIND_TASK_DUE,
                    "title": f"Aufgabe fällig: {task.title}",
                    "body": f"Fällig am {format_user_datetime(task.due_at, task.user)}",
                    "url": "/tasks/",
                    "source_key": f"task:{task.pk}",
                }
            )

    return _create_missing_user_notifications(rows)


def materialize_event_invitation_notifications(invitations):
    rows = []
    for invitation in invitations:
        organizer_name = _display_name(invitation.invited_by) if invitation.invited_by else "Jemand"
        rows.append(
            {
                "recipient_id": invitation.user_id,
                "actor_id": invitation.invited_by_id,
                "kind": UserNotification.KIND_EVENT_INVITATION,
                "title": f"Einladung: {invitation.event.title}",
                "body": (
                    f"{organizer_name} · {format_user_datetime(invitation.event.start_at, invitation.user)}"
                ),
                "url": "/calendar/",
                "source_key": f"event-invitation:{invitation.pk}",
            }
        )
    return _create_missing_user_notifications(rows)


def materialize_note_activity_notifications(notifications):
    rows = []
    for notification in notifications:
        body_parts = [notification.note.title]
        if notification.excerpt:
            body_parts.append(notification.excerpt)
        rows.append(
            {
                "recipient_id": notification.recipient_id,
                "actor_id": notification.actor_id,
                "kind": (
                    UserNotification.KIND_NOTE_MENTION
                    if notification.kind == NoteActivityNotification.KIND_MENTION
                    else UserNotification.KIND_NOTE_COMMENT
                ),
                "title": _note_activity_title(notification),
                "body": " · ".join(body_parts)[:500],
                "url": f"/notes/{notification.note_id}/",
                "source_key": f"note-activity:{notification.pk}",
            }
        )
    return _create_missing_user_notifications(rows)


def materialize_note_share_notifications(shares):
    rows = []
    for share in shares:
        owner_name = _display_name(share.note.owner)
        rows.append(
            {
                "recipient_id": share.user_id,
                "actor_id": share.note.owner_id,
                "kind": UserNotification.KIND_NOTE_SHARE,
                "title": f"{owner_name} hat eine Notiz mit dir geteilt",
                "body": share.note.title,
                "url": f"/notes/{share.note_id}/",
                "source_key": f"note-share:{share.pk}",
            }
        )
    return _create_missing_user_notifications(rows)


def materialize_user_notification_sources(user, *, now=None, include_reminders=True, include_tasks=True):
    """Backfill all inbox-capable sources for one user without duplicating entries."""
    created = materialize_due_user_notifications(
        user=user,
        now=now,
        include_reminders=include_reminders,
        include_tasks=include_tasks,
    )
    invitations = list(
        CalendarEventAttendee.objects.filter(user=user)
        .select_related("event", "user", "user__profile", "invited_by", "invited_by__profile")
        .order_by("created_at", "id")
    )
    note_activity = list(
        NoteActivityNotification.objects.filter(recipient=user)
        .select_related("note", "recipient", "recipient__profile", "actor", "actor__profile")
        .order_by("created_at", "id")
    )
    note_shares = list(
        NoteShare.objects.filter(user=user)
        .select_related("note", "note__owner", "note__owner__profile", "user", "user__profile")
        .order_by("created_at", "id")
    )
    created += materialize_event_invitation_notifications(invitations)
    created += materialize_note_activity_notifications(note_activity)
    created += materialize_note_share_notifications(note_shares)
    return created


def claim_due_desktop_reminders(user, *, now=None, limit=5):
    """Atomically claim due reminders for one browser notification batch."""
    current_time = now or timezone.now()
    materialize_due_user_notifications(
        user=user,
        now=current_time,
        include_reminders=True,
        include_tasks=False,
    )
    try:
        _ = user.profile
    except Profile.DoesNotExist:
        return []

    if not notification_channel_enabled(
        user,
        UserNotification.KIND_CALENDAR_REMINDER,
        CHANNEL_WEB_PUSH,
    ):
        return []

    with transaction.atomic():
        reminders = list(
            CalendarReminder.objects.select_for_update()
            .filter(
                user=user,
                is_done=False,
                due_at__isnull=False,
                due_at__lte=current_time,
                desktop_notified_at__isnull=True,
            )
            .order_by("due_at", "id")[:limit]
        )
        if reminders:
            CalendarReminder.objects.filter(pk__in=[item.pk for item in reminders]).update(
                desktop_notified_at=current_time
            )

    return [
        {
            "id": reminder.id,
            "title": reminder.title,
            "due_label": format_user_datetime(reminder.due_at, user),
        }
        for reminder in reminders
    ]


def send_due_reminder_emails(*, now=None):
    """Send each due reminder email once and leave failed deliveries retryable."""
    current_time = now or timezone.now()
    materialize_due_user_notifications(
        now=current_time,
        include_reminders=True,
        include_tasks=False,
    )
    reminders = list(
        CalendarReminder.objects.filter(
            is_done=False,
            due_at__isnull=False,
            due_at__lte=current_time,
            email_notified_at__isnull=True,
            user__is_active=True,
            user__profile__notify_reminders=True,
            user__profile__notify_email=True,
        )
        .exclude(user__email="")
        .select_related("user", "user__profile")
        .order_by("due_at", "id")
    )
    reminders = filter_channel_items(
        reminders,
        user_id_getter=lambda reminder: reminder.user_id,
        category="calendar",
        channel=CHANNEL_EMAIL,
    )

    sent = 0
    failed = 0
    for reminder in reminders:
        try:
            send_mail(
                subject=f"Lunora-Erinnerung: {reminder.title}",
                message=(
                    f"Deine Erinnerung „{reminder.title}“ ist fällig.\n\n"
                    f"Fällig am: {format_user_datetime(reminder.due_at, reminder.user)}\n"
                    "Öffne Lunora, um sie als erledigt zu markieren."
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[reminder.user.email],
                fail_silently=False,
            )
        except Exception:
            failed += 1
            logger.exception("Reminder email delivery failed for reminder %s", reminder.pk)
            continue

        updated = CalendarReminder.objects.filter(pk=reminder.pk, email_notified_at__isnull=True).update(
            email_notified_at=current_time
        )
        sent += int(bool(updated))

    return {"sent": sent, "failed": failed}


def claim_due_desktop_tasks(user, *, now=None, limit=5):
    """Atomically claim due tasks for one browser notification batch."""
    current_time = now or timezone.now()
    materialize_due_user_notifications(
        user=user,
        now=current_time,
        include_reminders=False,
        include_tasks=True,
    )
    try:
        _ = user.profile
    except Profile.DoesNotExist:
        return []

    if not notification_channel_enabled(
        user,
        UserNotification.KIND_TASK_DUE,
        CHANNEL_WEB_PUSH,
    ):
        return []

    with transaction.atomic():
        tasks = list(
            Task.objects.select_for_update()
            .filter(
                user=user,
                is_done=False,
                due_at__isnull=False,
                due_at__lte=current_time,
                desktop_notified_at__isnull=True,
            )
            .order_by("due_at", "id")[:limit]
        )
        if tasks:
            Task.objects.filter(pk__in=[item.pk for item in tasks]).update(desktop_notified_at=current_time)

    return [
        {
            "id": task.id,
            "title": task.title,
            "due_label": format_user_datetime(task.due_at, user),
        }
        for task in tasks
    ]


def send_due_task_reminder_emails(*, now=None):
    """Send each due task email once and leave failed deliveries retryable."""
    current_time = now or timezone.now()
    materialize_due_user_notifications(
        now=current_time,
        include_reminders=False,
        include_tasks=True,
    )
    tasks = list(
        Task.objects.filter(
            is_done=False,
            due_at__isnull=False,
            due_at__lte=current_time,
            email_notified_at__isnull=True,
            user__is_active=True,
            user__profile__notify_reminders=True,
            user__profile__notify_email=True,
        )
        .exclude(user__email="")
        .select_related("user", "user__profile")
        .order_by("due_at", "id")
    )
    tasks = filter_channel_items(
        tasks,
        user_id_getter=lambda task: task.user_id,
        category="tasks",
        channel=CHANNEL_EMAIL,
    )

    sent = 0
    failed = 0
    for task in tasks:
        try:
            send_mail(
                subject=f"Lunora-Aufgabe: {task.title}",
                message=(
                    f"Deine Aufgabe „{task.title}“ ist fällig.\n\n"
                    f"Fällig am: {format_user_datetime(task.due_at, task.user)}\n"
                    "Öffne Lunora, um sie als erledigt zu markieren."
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[task.user.email],
                fail_silently=False,
            )
        except Exception:
            failed += 1
            logger.exception("Task reminder email delivery failed for task %s", task.pk)
            continue

        updated = Task.objects.filter(pk=task.pk, email_notified_at__isnull=True).update(
            email_notified_at=current_time
        )
        sent += int(bool(updated))

    return {"sent": sent, "failed": failed}


def send_weekly_summaries(*, now=None):
    """Send one Monday summary per opted-in user and ISO week."""
    current_time = now or timezone.now()
    summary_hour = max(0, min(23, getattr(settings, "LUNORA_WEEKLY_SUMMARY_HOUR", 8)))
    profiles = (
        Profile.objects.filter(
            weekly_summary=True,
            notify_email=True,
            user__is_active=True,
        )
        .exclude(user__email="")
        .select_related("user")
    )

    sent = 0
    failed = 0
    skipped = 0
    for profile in profiles:
        user = profile.user
        local_now = localtime_for_user(current_time, user)
        if local_now.weekday() == 0 and local_now.hour < summary_hour:
            skipped += 1
            continue

        week_start = local_now.date() - timedelta(days=local_now.weekday())
        if WeeklySummaryDelivery.objects.filter(user=user, week_start=week_start).exists():
            skipped += 1
            continue

        try:
            send_mail(
                subject="Deine Lunora-Wochenübersicht",
                message=_weekly_summary_text(user, current_time),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=False,
            )
            WeeklySummaryDelivery.objects.create(user=user, week_start=week_start)
        except IntegrityError:
            skipped += 1
            continue
        except Exception:
            failed += 1
            logger.exception("Weekly summary delivery failed for user %s", user.pk)
            continue
        sent += 1

    return {"sent": sent, "failed": failed, "skipped": skipped}


def _display_name(user):
    profile_name = getattr(getattr(user, "profile", None), "display_name", "")
    return profile_name or user.get_full_name() or user.email or user.get_username()


def claim_due_note_activity(user, *, now=None, limit=5):
    """Atomically claim pending mention/comment notifications for one browser notification batch."""
    current_time = now or timezone.now()
    inbox_sources = list(
        NoteActivityNotification.objects.filter(recipient=user)
        .select_related("note", "recipient", "recipient__profile", "actor", "actor__profile")
        .order_by("created_at", "id")
    )
    materialize_note_activity_notifications(inbox_sources)
    try:
        _ = user.profile
    except Profile.DoesNotExist:
        return []

    if not notification_channel_enabled(
        user,
        UserNotification.KIND_NOTE_COMMENT,
        CHANNEL_WEB_PUSH,
    ):
        return []

    with transaction.atomic():
        notifications = list(
            NoteActivityNotification.objects.select_for_update()
            .filter(recipient=user, desktop_notified_at__isnull=True)
            .select_related("note", "actor")
            .order_by("created_at", "id")[:limit]
        )
        if notifications:
            NoteActivityNotification.objects.filter(pk__in=[item.pk for item in notifications]).update(
                desktop_notified_at=current_time
            )

    return [
        {
            "id": f"note-activity-{item.id}",
            "title": _note_activity_title(item),
            "body": item.note.title,
            "url": f"/notes/{item.note_id}/",
        }
        for item in notifications
    ]


def _note_activity_title(item):
    actor_name = _display_name(item.actor) if item.actor else "Jemand"
    if item.kind == NoteActivityNotification.KIND_MENTION:
        return f"{actor_name} hat dich erwähnt"
    return f"{actor_name} hat kommentiert"


def send_note_activity_emails(*, now=None):
    """Send each pending mention/comment email once and leave failed deliveries retryable."""
    current_time = now or timezone.now()
    notifications = list(
        NoteActivityNotification.objects.filter(
            email_notified_at__isnull=True,
            recipient__is_active=True,
            recipient__profile__notify_email=True,
        )
        .exclude(recipient__email="")
        .select_related("note", "recipient", "actor")
        .order_by("created_at", "id")
    )
    notifications = filter_channel_items(
        notifications,
        user_id_getter=lambda notification: notification.recipient_id,
        category="notes",
        channel=CHANNEL_EMAIL,
    )
    materialize_note_activity_notifications(notifications)

    sent = 0
    failed = 0
    for notification in notifications:
        try:
            send_mail(
                subject=f"Lunora: {_note_activity_title(notification)}",
                message=(
                    f"{_note_activity_title(notification)} in der Notiz „{notification.note.title}“.\n\n"
                    f"{notification.excerpt}\n\n"
                    "Öffne Lunora, um die Notiz anzusehen."
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[notification.recipient.email],
                fail_silently=False,
            )
        except Exception:
            failed += 1
            logger.exception("Note activity email delivery failed for notification %s", notification.pk)
            continue

        updated = NoteActivityNotification.objects.filter(
            pk=notification.pk, email_notified_at__isnull=True
        ).update(email_notified_at=current_time)
        sent += int(bool(updated))

    return {"sent": sent, "failed": failed}


def claim_due_event_invitations(user, *, now=None, limit=5):
    """Atomically claim pending calendar event invitations for one browser notification batch."""
    current_time = now or timezone.now()
    inbox_sources = list(
        CalendarEventAttendee.objects.filter(user=user)
        .select_related("event", "user", "user__profile", "invited_by", "invited_by__profile")
        .order_by("created_at", "id")
    )
    materialize_event_invitation_notifications(inbox_sources)
    try:
        _ = user.profile
    except Profile.DoesNotExist:
        return []

    if not notification_channel_enabled(
        user,
        UserNotification.KIND_EVENT_INVITATION,
        CHANNEL_WEB_PUSH,
    ):
        return []

    with transaction.atomic():
        invitations = list(
            CalendarEventAttendee.objects.select_for_update()
            .filter(user=user, desktop_notified_at__isnull=True)
            .select_related("event", "invited_by")
            .order_by("created_at", "id")[:limit]
        )
        if invitations:
            CalendarEventAttendee.objects.filter(pk__in=[item.pk for item in invitations]).update(
                desktop_notified_at=current_time
            )

    return [
        {
            "id": f"event-invite-{item.id}",
            "title": f"Einladung: {item.event.title}",
            "body": format_user_datetime(item.event.start_at, user),
        }
        for item in invitations
    ]


def send_new_invitation_emails(*, now=None):
    """Send each pending calendar invitation email once and leave failed deliveries retryable."""
    current_time = now or timezone.now()
    invitations = list(
        CalendarEventAttendee.objects.filter(
            email_notified_at__isnull=True,
            user__is_active=True,
            user__profile__notify_email=True,
        )
        .exclude(user__email="")
        .select_related("event", "user", "invited_by")
        .order_by("created_at", "id")
    )
    invitations = filter_channel_items(
        invitations,
        user_id_getter=lambda invitation: invitation.user_id,
        category="calendar",
        channel=CHANNEL_EMAIL,
    )
    materialize_event_invitation_notifications(invitations)

    sent = 0
    failed = 0
    for invitation in invitations:
        organizer_name = _display_name(invitation.invited_by) if invitation.invited_by else "Jemand"
        try:
            send_mail(
                subject=f"Lunora: Einladung zu „{invitation.event.title}“",
                message=(
                    f"{organizer_name} hat dich zum Termin „{invitation.event.title}“ eingeladen.\n\n"
                    f"Beginn: {format_user_datetime(invitation.event.start_at, invitation.user)}\n"
                    "Öffne Lunora, um die Einladung anzunehmen oder abzulehnen."
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[invitation.user.email],
                fail_silently=False,
            )
        except Exception:
            failed += 1
            logger.exception("Event invitation email delivery failed for attendee %s", invitation.pk)
            continue

        updated = CalendarEventAttendee.objects.filter(
            pk=invitation.pk, email_notified_at__isnull=True
        ).update(email_notified_at=current_time)
        sent += int(bool(updated))

    return {"sent": sent, "failed": failed}


def send_pending_user_notification_emails(
    *,
    now=None,
    include_note_shares=True,
    include_weather=True,
):
    """Send inbox-backed e-mails for notification types without a source delivery column."""
    current_time = now or timezone.now()
    kinds = []
    if include_note_shares:
        kinds.append(UserNotification.KIND_NOTE_SHARE)
    if include_weather:
        kinds.append(UserNotification.KIND_WEATHER_ALERT)
    if not kinds:
        return {"sent": 0, "failed": 0, "disabled": True}

    notifications = list(
        UserNotification.objects.filter(
            kind__in=kinds,
            email_notified_at__isnull=True,
            recipient__is_active=True,
            recipient__profile__notify_email=True,
        )
        .exclude(recipient__email="")
        .select_related("recipient", "recipient__profile")
        .order_by("created_at", "id")
    )
    preferences = notification_preference_map(notification.recipient_id for notification in notifications)

    sent = 0
    failed = 0
    for notification in notifications:
        if not notification_channel_enabled(
            notification.recipient,
            notification.kind,
            CHANNEL_EMAIL,
            preference_map=preferences,
        ):
            continue
        try:
            send_mail(
                subject=f"Lunora: {notification.title}",
                message=(
                    f"{notification.title}\n\n{notification.body}\n\nÖffne Lunora, um den Hinweis anzusehen."
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[notification.recipient.email],
                fail_silently=False,
            )
        except Exception:
            failed += 1
            logger.exception("Inbox notification email failed for notification %s", notification.pk)
            continue

        updated = UserNotification.objects.filter(
            pk=notification.pk,
            email_notified_at__isnull=True,
        ).update(email_notified_at=current_time)
        sent += int(bool(updated))

    return {"sent": sent, "failed": failed}


def _weekly_summary_text(user, current_time):
    horizon = current_time + timedelta(days=7)
    events = list(
        CalendarEvent.objects.filter(
            Q(source__isnull=True) | Q(source__is_visible=True),
            user=user,
            end_at__gte=current_time,
            start_at__lt=horizon,
        )
        .select_related("source")
        .order_by("start_at", "title")[:5]
    )
    reminders = list(
        CalendarReminder.objects.filter(user=user, is_done=False).order_by("due_at", "created_at")[:5]
    )
    unread_messages = unread_total_for_user(user)
    new_note_shares = NoteShare.objects.filter(
        user=user,
        first_opened_at__isnull=True,
        note__deleted_at__isnull=True,
    ).count()

    lines = ["Hallo,", "", "hier ist dein Lunora-Überblick für die kommende Woche.", ""]
    lines.append("Kommende Termine:")
    if events:
        lines.extend(f"- {format_user_datetime(event.start_at, user)} – {event.title}" for event in events)
    else:
        lines.append("- Keine Termine in den nächsten sieben Tagen")

    lines.extend(["", "Offene Erinnerungen:"])
    if reminders:
        for reminder in reminders:
            due_label = format_user_datetime(reminder.due_at, user) if reminder.due_at else "ohne Fälligkeit"
            lines.append(f"- {reminder.title} ({due_label})")
    else:
        lines.append("- Keine offenen Erinnerungen")

    lines.extend(
        [
            "",
            f"Ungelesene Nachrichten: {unread_messages}",
            f"Neue Notizfreigaben: {new_note_shares}",
            "",
            "Einen ruhigen Start in die Woche wünscht dir Lunora.",
        ]
    )
    return "\n".join(lines)


def materialize_due_weather_alerts(user, *, now=None, limit=5):
    """Detect severe weather and persist cooldown-gated notification events."""
    current_time = now or timezone.now()
    notifications = []
    locations = WeatherLocation.objects.filter(user=user).order_by("order", "id")[:limit]
    for location in locations:
        alert = get_weather_alert_for_location(weather_location_to_dict(location))

        if not alert:
            if location.last_alert_kind:
                WeatherLocation.objects.filter(pk=location.pk).update(last_alert_kind="")
            continue

        already_notified_recently = (
            location.last_alert_kind == alert["kind"]
            and location.last_alert_notified_at is not None
            and current_time - location.last_alert_notified_at < WEATHER_ALERT_COOLDOWN
        )
        if already_notified_recently:
            continue

        WeatherLocation.objects.filter(pk=location.pk).update(
            last_alert_kind=alert["kind"], last_alert_notified_at=current_time
        )
        _create_missing_user_notifications(
            [
                {
                    "recipient_id": user.id,
                    "kind": UserNotification.KIND_WEATHER_ALERT,
                    "title": alert["title"],
                    "body": location.label or location.name,
                    "url": "/weather/",
                    "source_key": (
                        f"weather-alert:{location.pk}:{alert['kind']}:"
                        f"{int(current_time.timestamp() // WEATHER_ALERT_COOLDOWN.total_seconds())}"
                    ),
                }
            ]
        )
        notifications.append(
            {
                "id": f"weather-alert-{location.pk}-{alert['kind']}",
                "title": alert["title"],
                "body": location.label or location.name,
                "url": "/weather/",
            }
        )

    return notifications


def materialize_scheduled_weather_alerts(*, now=None):
    profiles = list(
        Profile.objects.filter(
            user__is_active=True,
            user__weather_locations__isnull=False,
        )
        .select_related("user")
        .distinct()
        .order_by("user_id")
    )
    preferences = notification_preference_map(profile.user_id for profile in profiles)
    created = 0
    failed = 0
    for profile in profiles:
        inbox_enabled = notification_channel_enabled(
            profile.user,
            UserNotification.KIND_WEATHER_ALERT,
            CHANNEL_INBOX,
            preference_map=preferences,
        )
        email_enabled = notification_channel_enabled(
            profile.user,
            UserNotification.KIND_WEATHER_ALERT,
            CHANNEL_EMAIL,
            preference_map=preferences,
        )
        web_push_enabled = settings.WEB_PUSH_ENABLED and notification_channel_enabled(
            profile.user,
            UserNotification.KIND_WEATHER_ALERT,
            CHANNEL_WEB_PUSH,
            preference_map=preferences,
        )
        if not inbox_enabled and not email_enabled and not web_push_enabled:
            continue
        try:
            created += len(materialize_due_weather_alerts(profile.user, now=now))
        except Exception:
            failed += 1
            logger.exception("Scheduled weather alert check failed for user %s", profile.user_id)
    return {"created": created, "failed": failed}


def claim_due_weather_alerts(user, *, now=None, limit=5):
    """Claim detected severe-weather alerts for the browser fallback channel."""
    if not notification_channel_enabled(
        user,
        UserNotification.KIND_WEATHER_ALERT,
        CHANNEL_WEB_PUSH,
    ):
        return []
    return materialize_due_weather_alerts(user, now=now, limit=limit)
