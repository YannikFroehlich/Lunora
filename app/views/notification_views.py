from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_POST

from app.services.notifications import claim_due_desktop_reminders
from app.services.system_settings import feature_enabled


@login_required
@require_POST
def claim_desktop_notifications(request):
    if not feature_enabled("calendar_reminders"):
        return JsonResponse({"notifications": []})

    reminders = claim_due_desktop_reminders(request.user)
    calendar_url = reverse("calendar")
    return JsonResponse(
        {
            "notifications": [
                {**reminder, "url": calendar_url}
                for reminder in reminders
            ]
        }
    )
