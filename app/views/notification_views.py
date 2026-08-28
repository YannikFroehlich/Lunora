from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from app.models import UserNotification
from app.services.notifications import (
    claim_due_desktop_reminders,
    claim_due_desktop_tasks,
    claim_due_event_invitations,
    claim_due_note_activity,
    claim_due_weather_alerts,
    materialize_user_notification_sources,
)
from app.services.system_settings import feature_enabled, feature_flags
from app.services.user_preferences import format_user_datetime


NOTIFICATION_PRESENTATION = {
    UserNotification.KIND_CALENDAR_REMINDER: ("fa-calendar-check", "Kalender", "calendar"),
    UserNotification.KIND_TASK_DUE: ("fa-list-check", "Aufgabe", "task"),
    UserNotification.KIND_EVENT_INVITATION: ("fa-user-clock", "Einladung", "invitation"),
    UserNotification.KIND_NOTE_MENTION: ("fa-at", "Erwähnung", "note"),
    UserNotification.KIND_NOTE_COMMENT: ("fa-comment-dots", "Kommentar", "note"),
    UserNotification.KIND_NOTE_SHARE: ("fa-share-nodes", "Freigabe", "note"),
    UserNotification.KIND_WEATHER_ALERT: ("fa-cloud-bolt", "Wetter", "weather"),
}


def _notification_items(notifications, user):
    items = []
    for notification in notifications:
        icon, label, tone = NOTIFICATION_PRESENTATION.get(
            notification.kind,
            ("fa-bell", "Hinweis", "default"),
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


@login_required
def notification_center(request):
    flags = feature_flags()
    materialize_user_notification_sources(
        request.user,
        include_reminders=flags.get("calendar_reminders", False),
        include_tasks=flags.get("tasks", False),
    )

    selected_filter = request.GET.get("status", "unread")
    if selected_filter not in {"unread", "all"}:
        selected_filter = "unread"

    notifications = UserNotification.objects.filter(recipient=request.user).select_related("actor")
    total_count = notifications.count()
    unread_count = notifications.filter(read_at__isnull=True).count()
    if selected_filter == "unread":
        notifications = notifications.filter(read_at__isnull=True)

    paginator = Paginator(notifications, 25)
    page = paginator.get_page(request.GET.get("page"))
    page.object_list = _notification_items(page.object_list, request.user)
    return render(
        request,
        "app/notifications.html",
        {
            "active_page": "notifications",
            "notification_page": page,
            "selected_filter": selected_filter,
            "total_notification_count": total_count,
            "unread_notification_count": unread_count,
        },
    )


@login_required
@require_POST
def notification_open(request, notification_id):
    notification = get_object_or_404(
        UserNotification,
        pk=notification_id,
        recipient=request.user,
    )
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])

    target = notification.url
    if not target.startswith("/") or not url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        target = reverse("notification_center")
    return redirect(target)


@login_required
@require_POST
def notification_toggle_read(request, notification_id):
    notification = get_object_or_404(
        UserNotification,
        pk=notification_id,
        recipient=request.user,
    )
    notification.read_at = None if notification.read_at else timezone.now()
    notification.save(update_fields=["read_at"])
    selected_filter = request.POST.get("status", "unread")
    if selected_filter not in {"unread", "all"}:
        selected_filter = "unread"
    return redirect(f"{reverse('notification_center')}?status={selected_filter}")


@login_required
@require_POST
def notification_mark_all_read(request):
    UserNotification.objects.filter(
        recipient=request.user,
        read_at__isnull=True,
    ).update(read_at=timezone.now())
    return redirect(f"{reverse('notification_center')}?status=all")


@login_required
@require_POST
def claim_desktop_notifications(request):
    notifications = []
    calendar_url = reverse("calendar")

    if feature_enabled("calendar_reminders"):
        notifications.extend(
            {**reminder, "url": calendar_url} for reminder in claim_due_desktop_reminders(request.user)
        )

    if feature_enabled("calendar_event_creation"):
        notifications.extend(
            {**invitation, "url": calendar_url} for invitation in claim_due_event_invitations(request.user)
        )

    if feature_enabled("notes"):
        notifications.extend(claim_due_note_activity(request.user))

    if feature_enabled("weather"):
        notifications.extend(claim_due_weather_alerts(request.user))

    if feature_enabled("tasks"):
        tasks_url = reverse("tasks")
        notifications.extend({**task, "url": tasks_url} for task in claim_due_desktop_tasks(request.user))

    return JsonResponse({"notifications": notifications})
