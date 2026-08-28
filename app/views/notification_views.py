import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from app.models import UserNotification
from app.services.notifications import (
    claim_due_desktop_reminders,
    claim_due_desktop_tasks,
    claim_due_event_invitations,
    claim_due_note_activity,
    claim_due_weather_alerts,
    materialize_user_notification_sources,
    notification_display_items,
)
from app.services.system_settings import feature_enabled, feature_flags
from app.services.notification_preferences import CHANNEL_INBOX, enabled_notification_kinds
from app.services.web_push import (
    WebPushTestError,
    register_web_push_subscription,
    remove_web_push_subscription,
    send_test_web_push,
)


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

    visible_kinds = enabled_notification_kinds(request.user, CHANNEL_INBOX)
    notifications = UserNotification.objects.filter(
        recipient=request.user,
        kind__in=visible_kinds,
    ).select_related("actor")
    total_count = notifications.count()
    unread_count = notifications.filter(read_at__isnull=True).count()
    if selected_filter == "unread":
        notifications = notifications.filter(read_at__isnull=True)

    paginator = Paginator(notifications, 25)
    page = paginator.get_page(request.GET.get("page"))
    page.object_list = notification_display_items(page.object_list, request.user)
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

    return_to = request.POST.get("return_to")
    if (
        return_to
        and return_to.startswith("/")
        and url_has_allowed_host_and_scheme(
            return_to,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        )
    ):
        return redirect(return_to)

    selected_filter = request.POST.get("status", "unread")
    if selected_filter not in {"unread", "all"}:
        selected_filter = "unread"
    return redirect(f"{reverse('notification_center')}?status={selected_filter}")


@login_required
@require_POST
def notification_mark_all_read(request):
    UserNotification.objects.filter(
        recipient=request.user,
        kind__in=enabled_notification_kinds(request.user, CHANNEL_INBOX),
        read_at__isnull=True,
    ).update(read_at=timezone.now())
    return redirect(f"{reverse('notification_center')}?status=all")


@login_required
@require_http_methods(["POST", "DELETE"])
def web_push_subscription(request):
    if not settings.WEB_PUSH_ENABLED:
        return JsonResponse(
            {"ok": False, "error": "Web Push ist auf dem Server nicht eingerichtet."},
            status=503,
        )
    if len(request.body) > 8192:
        return JsonResponse({"ok": False, "error": "Die Push-Daten sind zu groß."}, status=400)

    try:
        payload = json.loads(request.body or b"{}")
    except (TypeError, ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Ungültige Push-Daten."}, status=400)

    try:
        if request.method == "POST":
            _subscription, created = register_web_push_subscription(
                request.user,
                payload,
                user_agent=request.headers.get("User-Agent", ""),
            )
            return JsonResponse({"ok": True, "active": True}, status=201 if created else 200)

        removed = remove_web_push_subscription(request.user, payload.get("endpoint"))
        return JsonResponse({"ok": True, "active": False, "removed": removed})
    except (AttributeError, ValueError) as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)


@login_required
@require_POST
def web_push_test(request):
    if len(request.body) > 4096:
        return JsonResponse({"ok": False, "error": "Die Push-Daten sind zu groß."}, status=400)
    try:
        payload = json.loads(request.body or b"{}")
        endpoint = payload.get("endpoint")
    except (AttributeError, TypeError, ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Ungültige Push-Daten."}, status=400)

    try:
        send_test_web_push(request.user, endpoint)
    except WebPushTestError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=error.status_code)
    return JsonResponse(
        {"ok": True, "message": "Testbenachrichtigung wurde an dieses Gerät gesendet."}
    )


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
