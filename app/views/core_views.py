import json
from urllib.parse import urlencode, urlparse, urlunparse

from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from app.forms import (
    AppearanceForm,
    CalendarSourceForm,
    NotificationCategoryPreferencesForm,
    NotificationPreferencesForm,
    ProfileForm,
    RegionPreferencesForm,
    WeatherPreferencesForm,
)
from app.models import CalendarSource, Profile
from app.services.calendar_sync_queue import queue_calendar_sources
from app.services.dashboard import normalize_dashboard_layout, validate_dashboard_layout
from app.services.system_settings import disabled_feature_response, feature_enabled
from app.services.user_preferences import format_user_datetime
from app.view_models import get_dashboard_context, get_settings_context


def get_safe_settings_return_url(request):
    """Return the page the user came from before opening settings."""
    fallback_url = reverse("home")
    settings_path = reverse("settings").rstrip("/")
    forbidden_paths = {
        reverse(name).rstrip("/")
        for name in (
            "login",
            "accounts_login",
            "logout",
            "register",
            "password_reset",
            "password_reset_done",
            "password_reset_complete",
        )
    }
    candidates = (
        request.POST.get("return_to"),
        request.GET.get("next"),
        request.META.get("HTTP_REFERER"),
    )

    for candidate in candidates:
        if not candidate:
            continue

        candidate = candidate.strip()
        if not url_has_allowed_host_and_scheme(
            candidate,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            continue

        parsed = urlparse(candidate)
        path = parsed.path or "/"
        normalized_path = path.rstrip("/") or "/"
        if (
            normalized_path == settings_path
            or normalized_path in forbidden_paths
            or normalized_path.startswith("/reset/")
        ):
            continue

        if parsed.netloc:
            candidate = urlunparse(("", "", path, "", parsed.query, parsed.fragment))

        if candidate.startswith("/"):
            return candidate

    return fallback_url


def _settings_section_url(section, return_to):
    allowed_sections = {"appearance", "profile", "calendar", "notifications", "weather", "app"}
    if section not in allowed_sections:
        raise ValueError(f"Unknown settings section: {section}")
    query = urlencode({"next": return_to})
    return f"{reverse('settings')}?{query}#settings-{section}"


def get_or_create_profile(user):
    profile, _created = Profile.objects.get_or_create(
        user=user,
        defaults={"display_name": user.first_name or user.get_username()},
    )
    return profile


def _calendar_source_status(source, user):
    if source.sync_requested_at:
        return {"label": "Synchronisierung vorgemerkt", "kind": "pending"}
    if source.last_error:
        return {"label": source.last_error, "kind": "error"}
    if source.last_synced_at:
        return {"label": f"Zuletzt: {format_user_datetime(source.last_synced_at, user)}", "kind": "synced"}
    if source.enabled:
        return {"label": "Noch nicht synchronisiert", "kind": "idle"}
    return {"label": "Sync deaktiviert", "kind": "muted"}


def _calendar_source_form_items(sources, user, bound_form=None, bound_source_id=None):
    items = []
    for source in sources:
        form = (
            bound_form
            if source.id == bound_source_id
            else CalendarSourceForm(
                instance=source,
                user=user,
                prefix=f"source-{source.id}",
            )
        )
        items.append({"source": source, "form": form, "status": _calendar_source_status(source, user)})
    return items


@login_required
def home(request):
    profile = get_or_create_profile(request.user)
    context = get_dashboard_context(request.user)
    if profile.dashboard_layout != context["dashboard_layout"]:
        profile.dashboard_layout = context["dashboard_layout"]
        profile.save(update_fields=["dashboard_layout"])
    return render(request, "app/home.html", context)


@login_required
@require_http_methods(["PATCH"])
def dashboard_layout_update(request):
    if not feature_enabled("dashboard_customization"):
        return disabled_feature_response(request, "dashboard_customization", json_response=True)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "Das Layout muss gültiges JSON sein."}, status=400)

    is_valid, error = validate_dashboard_layout(payload)
    if not is_valid:
        return JsonResponse({"ok": False, "error": error}, status=400)

    profile = get_or_create_profile(request.user)
    layout = normalize_dashboard_layout(payload)
    profile.dashboard_layout = layout
    profile.save(update_fields=["dashboard_layout"])
    return JsonResponse({"ok": True, "layout": layout})


@login_required
def settings(request):
    profile = get_or_create_profile(request.user)
    return_to = get_safe_settings_return_url(request)
    form_name = request.POST.get("form_name") if request.method == "POST" else ""
    bound_calendar_form = None
    bound_calendar_source_id = None
    calendar_sync_enabled = feature_enabled("calendar_sync")
    calendar_add_form = CalendarSourceForm(user=request.user, prefix="new")
    profile_form = ProfileForm(instance=profile)
    appearance_form = AppearanceForm(instance=profile)
    region_form = RegionPreferencesForm(instance=profile)
    notification_form = NotificationPreferencesForm(instance=profile)
    weather_form = WeatherPreferencesForm(instance=profile)
    notification_preferences_form = NotificationCategoryPreferencesForm(user=request.user)

    if form_name == "profile":
        profile_form = ProfileForm(request.POST, request.FILES, instance=profile)
        if profile_form.is_valid():
            profile = profile_form.save()
            request.user.first_name = profile.display_name
            request.user.save(update_fields=["first_name"])
            django_messages.success(request, "Profil gespeichert.")
            return redirect(_settings_section_url("profile", return_to))
    elif form_name == "appearance":
        appearance_form = AppearanceForm(request.POST, instance=profile)
        if appearance_form.is_valid():
            appearance_form.save()
            django_messages.success(request, "Darstellung gespeichert.")
            return redirect(_settings_section_url("appearance", return_to))
    elif form_name == "region":
        region_form = RegionPreferencesForm(request.POST, instance=profile)
        if region_form.is_valid():
            region_form.save()
            django_messages.success(request, "Sprache & Region gespeichert.")
            return redirect(_settings_section_url("profile", return_to))
    elif form_name == "calendar_source_add":
        if not calendar_sync_enabled:
            django_messages.warning(request, "Kalendersynchronisierung ist vorübergehend deaktiviert.")
            return redirect(_settings_section_url("calendar", return_to))
        calendar_add_form = CalendarSourceForm(request.POST, user=request.user, prefix="new")
        if calendar_add_form.is_valid():
            calendar_source = calendar_add_form.save(commit=False)
            calendar_source.user = request.user
            calendar_source.save()
            queue_result = queue_calendar_sources([calendar_source])
            if queue_result.get("queued"):
                django_messages.success(
                    request,
                    f"{calendar_source.name} gespeichert. Synchronisierung wurde im Hintergrund vorgemerkt.",
                )
            else:
                django_messages.success(request, f"{calendar_source.name} gespeichert. Sync ist deaktiviert.")
            return redirect(_settings_section_url("calendar", return_to))
    elif form_name == "calendar_source_update":
        if not calendar_sync_enabled:
            django_messages.warning(request, "Kalendersynchronisierung ist vorübergehend deaktiviert.")
            return redirect(_settings_section_url("calendar", return_to))
        calendar_source = CalendarSource.objects.filter(
            user=request.user, pk=request.POST.get("source_id")
        ).first()
        if not calendar_source:
            django_messages.error(request, "Kalender wurde nicht gefunden.")
            return redirect(_settings_section_url("calendar", return_to))
        bound_calendar_source_id = calendar_source.id
        old_ical_url = calendar_source.ical_url
        was_enabled = calendar_source.enabled
        bound_calendar_form = CalendarSourceForm(
            request.POST,
            instance=calendar_source,
            user=request.user,
            prefix=f"source-{calendar_source.id}",
        )
        if bound_calendar_form.is_valid():
            calendar_source = bound_calendar_form.save(commit=False)
            calendar_source.user = request.user
            calendar_source.save()
            if not calendar_source.enabled:
                CalendarSource.objects.filter(pk=calendar_source.pk).update(sync_requested_at=None)
                calendar_source.sync_requested_at = None
            if calendar_source.ical_url != old_ical_url:
                calendar_source.events.all().delete()
                if calendar_source.enabled:
                    queue_calendar_sources([calendar_source])
                    django_messages.success(
                        request,
                        f"{calendar_source.name} gespeichert. Neue Synchronisierung wurde vorgemerkt.",
                    )
                else:
                    django_messages.success(
                        request, f"{calendar_source.name} gespeichert. Sync ist deaktiviert."
                    )
            elif calendar_source.enabled and not was_enabled:
                queue_calendar_sources([calendar_source])
                django_messages.success(
                    request,
                    f"{calendar_source.name} aktiviert. Synchronisierung wurde vorgemerkt.",
                )
            elif not calendar_source.enabled:
                django_messages.success(request, f"{calendar_source.name} gespeichert. Sync ist deaktiviert.")
            else:
                django_messages.success(request, f"{calendar_source.name} gespeichert.")
            return redirect(_settings_section_url("calendar", return_to))
    elif form_name == "calendar_source_delete":
        if not calendar_sync_enabled:
            django_messages.warning(request, "Kalendersynchronisierung ist vorübergehend deaktiviert.")
            return redirect(_settings_section_url("calendar", return_to))
        deleted_count, _details = CalendarSource.objects.filter(
            user=request.user,
            pk=request.POST.get("source_id"),
        ).delete()
        if deleted_count:
            django_messages.success(request, "Kalender gelöscht.")
        return redirect(_settings_section_url("calendar", return_to))
    elif form_name == "calendar_sync_all":
        if not calendar_sync_enabled:
            django_messages.warning(request, "Kalendersynchronisierung ist vorübergehend deaktiviert.")
            return redirect(_settings_section_url("calendar", return_to))
        sync_result = queue_calendar_sources(
            CalendarSource.objects.filter(user=request.user).order_by("name", "id"),
        )
        if sync_result.get("queued"):
            django_messages.success(request, sync_result.get("message", "Synchronisierung vorgemerkt."))
        else:
            django_messages.info(request, sync_result.get("message", "Kalender ist aktuell."))
        return redirect(_settings_section_url("calendar", return_to))
    elif form_name == "notifications":
        notification_form = NotificationPreferencesForm(request.POST, instance=profile)
        notification_preferences_form = NotificationCategoryPreferencesForm(
            request.POST,
            user=request.user,
        )
        if notification_form.is_valid() and notification_preferences_form.is_valid():
            with transaction.atomic():
                notification_form.save()
                notification_preferences_form.save()
            django_messages.success(request, "Benachrichtigungseinstellungen gespeichert.")
            return redirect(_settings_section_url("notifications", return_to))
    elif form_name == "weather":
        weather_form = WeatherPreferencesForm(request.POST, instance=profile)
        if weather_form.is_valid():
            weather_form.save()
            django_messages.success(request, "Wettereinstellungen gespeichert.")
            return redirect(_settings_section_url("weather", return_to))

    calendar_sources = list(CalendarSource.objects.filter(user=request.user).order_by("name", "id"))
    context = get_settings_context(notification_form, notification_preferences_form)
    context.update(
        {
            "profile": profile,
            "profile_form": profile_form,
            "appearance_form": appearance_form,
            "region_form": region_form,
            "calendar_source": calendar_sources[0] if calendar_sources else None,
            "calendar_source_form": calendar_add_form,
            "calendar_source_forms": _calendar_source_form_items(
                calendar_sources,
                request.user,
                bound_form=bound_calendar_form,
                bound_source_id=bound_calendar_source_id,
            ),
            "notification_form": notification_form,
            "notification_preferences_form": notification_preferences_form,
            "weather_form": weather_form,
            "return_to": return_to,
            "calendar_sync_enabled": calendar_sync_enabled,
        }
    )
    return render(request, "app/settings.html", context)
