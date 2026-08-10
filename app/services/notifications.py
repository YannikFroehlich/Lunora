import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from app.models import CalendarEvent, CalendarReminder, NoteShare, Profile, WeeklySummaryDelivery
from app.services.message_queries import unread_total_for_user
from app.services.user_preferences import format_user_datetime, localtime_for_user


logger = logging.getLogger(__name__)


def claim_due_desktop_reminders(user, *, now=None, limit=5):
    """Atomically claim due reminders for one browser notification batch."""
    current_time = now or timezone.now()
    try:
        profile = user.profile
    except Profile.DoesNotExist:
        return []

    if not profile.notify_reminders or not profile.notify_desktop:
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
        CalendarReminder.objects.filter(user=user, is_done=False)
        .order_by("due_at", "created_at")[:5]
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
