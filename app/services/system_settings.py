from dataclasses import dataclass

from django.contrib.sessions.models import Session
from django.db import OperationalError, ProgrammingError
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from app.models import SystemSettings


FEATURE_FIELDS = {
    "calendar_event_creation": "calendar_event_creation_enabled",
    "calendar_reminders": "calendar_reminders_enabled",
    "calendar_sync": "calendar_sync_enabled",
    "messages": "messages_enabled",
    "notes": "notes_enabled",
    "vacation_planner": "vacation_planner_enabled",
    "weather": "weather_enabled",
    "dashboard_customization": "dashboard_customization_enabled",
    "tasks": "tasks_enabled",
}


FEATURE_LABELS = {
    "calendar_event_creation": "Termin-Erstellung",
    "calendar_reminders": "Erinnerungen",
    "calendar_sync": "Kalendersynchronisierung",
    "messages": "Nachrichten",
    "notes": "Notizen",
    "vacation_planner": "Urlaubsplaner",
    "weather": "Wetter",
    "dashboard_customization": "Dashboard-Personalisierung",
    "tasks": "Aufgaben",
}


@dataclass(frozen=True)
class DefaultSystemSettings:
    normal_login_enabled: bool = True
    calendar_event_creation_enabled: bool = True
    calendar_reminders_enabled: bool = True
    calendar_sync_enabled: bool = True
    messages_enabled: bool = True
    notes_enabled: bool = True
    vacation_planner_enabled: bool = True
    weather_enabled: bool = True
    dashboard_customization_enabled: bool = True
    tasks_enabled: bool = True
    updated_by: object = None
    updated_at: object = None


def get_system_settings():
    try:
        settings_obj, _created = SystemSettings.objects.get_or_create(pk=1)
    except (OperationalError, ProgrammingError):
        return DefaultSystemSettings()
    return settings_obj


def normal_user_login_enabled():
    return get_system_settings().normal_login_enabled


def user_can_login(user):
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True
    return normal_user_login_enabled()


def feature_enabled(feature_key):
    field_name = FEATURE_FIELDS[feature_key]
    return getattr(get_system_settings(), field_name)


def feature_flags():
    settings_obj = get_system_settings()
    return {
        feature_key: getattr(settings_obj, field_name)
        for feature_key, field_name in FEATURE_FIELDS.items()
    }


def disabled_feature_response(request, feature_key, *, json_response=False):
    label = FEATURE_LABELS.get(feature_key, "Diese Funktion")
    message = f"{label} ist vorübergehend deaktiviert."
    if json_response:
        return JsonResponse({"ok": False, "error": message}, status=503)
    return render(
        request,
        "app/feature_unavailable.html",
        {
            "active_page": feature_key,
            "feature_label": label,
            "feature_message": message,
        },
        status=503,
    )


def logout_other_authenticated_sessions(current_session_key):
    active_sessions = Session.objects.filter(expire_date__gte=timezone.now())
    sessions_to_delete = []
    affected_user_ids = set()

    for session in active_sessions:
        if current_session_key and session.session_key == current_session_key:
            continue
        data = session.get_decoded()
        user_id = data.get("_auth_user_id")
        if user_id:
            sessions_to_delete.append(session.session_key)
            affected_user_ids.add(user_id)

    if sessions_to_delete:
        Session.objects.filter(session_key__in=sessions_to_delete).delete()

    return {
        "deleted_sessions": len(sessions_to_delete),
        "affected_users": len(affected_user_ids),
    }
