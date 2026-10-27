from django.utils import timezone

from app.services.user_preferences import activate_user_timezone
from app.services.user_sessions import track_session


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


class UserSessionMiddleware:
    """Keep the signed-in device list (app.services.user_sessions) up to date."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # Checked after the view, so the request that logs in is tracked with its new session key.
        if getattr(request, "user", None) and request.user.is_authenticated and hasattr(request, "session"):
            track_session(request)
        return response
