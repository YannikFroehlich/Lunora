from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_POST

from app.services.notifications import (
    claim_due_desktop_reminders,
    claim_due_event_invitations,
    claim_due_note_activity,
)
from app.services.system_settings import feature_enabled


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

    return JsonResponse({"notifications": notifications})
