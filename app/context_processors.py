from django.conf import settings as django_settings
from django.db import OperationalError, ProgrammingError

from app.models import Profile, UserNotification
from app.services.notifications import materialize_due_user_notifications
from app.services.system_settings import feature_flags, get_system_settings


def _hex_to_rgb(hex_color):
    value = hex_color.lstrip("#")
    if len(value) != 6:
        return 194, 162, 118
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _mix_colors(hex_color, target=(63, 55, 46), amount=0.28):
    source = _hex_to_rgb(hex_color)
    mixed = tuple(round(source[index] * (1 - amount) + target[index] * amount) for index in range(3))
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def _appearance_from_profile(profile):
    softness = max(0, min(100, profile.background_softness))
    normalized = softness / 100
    return {
        "theme": profile.theme,
        "accent_color": profile.accent_color,
        "accent_strong": _mix_colors(profile.accent_color),
        "background_softness": softness,
        "background_overlay_alpha": f"{0.14 + normalized * 0.34:.2f}",
        "background_highlight_alpha": f"{0.20 + normalized * 0.28:.2f}",
        "glass_blur": f"{18 + normalized * 18:.0f}px",
        "density": profile.density,
        "date_format": profile.date_format,
        "time_format": profile.time_format,
        "timezone_name": profile.timezone_name,
    }


def appearance_settings(request):
    default_profile = Profile(
        display_name="",
        theme="light",
        accent_color="#c2a276",
        background_softness=55,
        density="comfortable",
        date_format="de_numeric",
        time_format="24h",
        timezone_name="Europe/Berlin",
    )

    profile = default_profile
    if request.user.is_authenticated:
        try:
            profile = request.user.profile
        except Profile.DoesNotExist:
            pass

    return {"appearance": _appearance_from_profile(profile)}


def system_settings(request):
    flags = feature_flags()
    unread_notification_count = 0
    if request.user.is_authenticated:
        try:
            materialize_due_user_notifications(
                user=request.user,
                include_reminders=flags.get("calendar_reminders", False),
                include_tasks=flags.get("tasks", False),
            )
            unread_notification_count = UserNotification.objects.filter(
                recipient=request.user,
                read_at__isnull=True,
            ).count()
        except (OperationalError, ProgrammingError):
            # Keep pages usable while a deployment is between code update and migration.
            unread_notification_count = 0

    return {
        "system_settings": get_system_settings(),
        "feature_flags": flags,
        "unread_notification_count": unread_notification_count,
        "web_push_enabled": django_settings.WEB_PUSH_ENABLED,
        "web_push_public_key": (
            django_settings.WEB_PUSH_VAPID_PUBLIC_KEY
            if request.user.is_authenticated and django_settings.WEB_PUSH_ENABLED
            else ""
        ),
    }
