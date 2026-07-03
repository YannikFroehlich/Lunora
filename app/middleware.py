from django.utils import timezone

from app.services.user_preferences import activate_user_timezone


class UserTimezoneMiddleware:
    """Activate the timezone selected in the signed-in user's profile."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(request, "user", None) and request.user.is_authenticated:
            activate_user_timezone(request.user)
        else:
            timezone.deactivate()

        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()
