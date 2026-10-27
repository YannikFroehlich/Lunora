import json
import tempfile
from urllib.parse import urlencode, urlparse, urlunparse

from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import FileResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_http_methods

from app.forms import (
    AppearanceForm,
    NotificationCategoryPreferencesForm,
    NotificationPreferencesForm,
    ProfileForm,
    RegionPreferencesForm,
    WeatherPreferencesForm,
)
from app.models import Profile
from app.services import two_factor
from app.services.calendar_service import disable_calendar_feed, reset_calendar_feed_token
from app.services.dashboard import normalize_dashboard_layout, validate_dashboard_layout
from app.services.data_export import export_filename, write_user_export
from app.services.system_settings import disabled_feature_response, feature_enabled
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
    allowed_sections = {"appearance", "profile", "calendar", "notifications", "weather", "security", "app"}
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
    calendar_feed_enabled = feature_enabled("calendar_feed")
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
    elif form_name in {"calendar_feed_reset", "calendar_feed_disable"}:
        if not calendar_feed_enabled:
            return disabled_feature_response(request, "calendar_feed")
        if form_name == "calendar_feed_reset":
            had_link = bool(profile.calendar_feed_token)
            reset_calendar_feed_token(profile)
            django_messages.success(
                request,
                "Neuer Abo-Link erstellt. Der alte Link funktioniert nicht mehr."
                if had_link
                else "Abo-Link erstellt.",
            )
        else:
            disable_calendar_feed(profile)
            django_messages.success(request, "Abo-Link deaktiviert.")
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
    elif form_name.startswith("two_factor_"):
        _handle_two_factor_form(request, profile, form_name)
        return redirect(_settings_section_url("security", return_to))

    setup_secret = request.session.get(TWO_FACTOR_SETUP_SESSION_KEY) if not profile.totp_enabled_at else None
    setup_uri = two_factor.provisioning_uri(request.user, setup_secret) if setup_secret else ""
    calendar_feed_url = (
        request.build_absolute_uri(reverse("calendar_feed", args=[profile.calendar_feed_token]))
        if profile.calendar_feed_token
        else ""
    )
    context = get_settings_context(notification_form, notification_preferences_form)
    context.update(
        {
            "profile": profile,
            "profile_form": profile_form,
            "appearance_form": appearance_form,
            "region_form": region_form,
            "calendar_feed_url": calendar_feed_url,
            "notification_form": notification_form,
            "notification_preferences_form": notification_preferences_form,
            "weather_form": weather_form,
            "return_to": return_to,
            "calendar_feed_enabled": calendar_feed_enabled,
            "two_factor_enabled": bool(profile.totp_enabled_at),
            "two_factor_recovery_remaining": len(profile.totp_recovery_codes),
            "two_factor_setup_secret": setup_secret,
            "two_factor_setup_uri": setup_uri,
            # The SVG is generated server-side from our own URI, never from user input.
            "two_factor_setup_qr": mark_safe(two_factor.qr_code_svg(setup_uri)) if setup_uri else "",
            "two_factor_new_codes": request.session.pop(TWO_FACTOR_NEW_CODES_SESSION_KEY, None),
        }
    )
    return render(request, "app/settings.html", context)


TWO_FACTOR_SETUP_SESSION_KEY = "two_factor_setup_secret"
TWO_FACTOR_NEW_CODES_SESSION_KEY = "two_factor_new_codes"


def _handle_two_factor_form(request, profile, form_name):
    session = request.session
    if form_name == "two_factor_setup" and not profile.totp_enabled_at:
        session[TWO_FACTOR_SETUP_SESSION_KEY] = two_factor.generate_secret()
    elif form_name == "two_factor_cancel":
        session.pop(TWO_FACTOR_SETUP_SESSION_KEY, None)
    elif form_name == "two_factor_enable" and session.get(TWO_FACTOR_SETUP_SESSION_KEY):
        codes = two_factor.enable(
            profile, session[TWO_FACTOR_SETUP_SESSION_KEY], request.POST.get("code", "")
        )
        if codes is None:
            django_messages.error(
                request, "Der Code stimmt nicht. Prüfe die Uhrzeit deines Handys und versuche es erneut."
            )
            return
        session.pop(TWO_FACTOR_SETUP_SESSION_KEY, None)
        session[TWO_FACTOR_NEW_CODES_SESSION_KEY] = codes
        django_messages.success(request, "Zwei-Faktor-Anmeldung aktiviert.")
    elif form_name in {"two_factor_disable", "two_factor_recovery_codes"} and profile.totp_enabled_at:
        # Re-entering the password keeps someone at an unlocked, signed-in device from switching 2FA off.
        if not request.user.check_password(request.POST.get("password", "")):
            django_messages.error(request, "Das Passwort ist falsch.")
        elif form_name == "two_factor_disable":
            two_factor.disable(profile)
            django_messages.success(request, "Zwei-Faktor-Anmeldung deaktiviert.")
        else:
            session[TWO_FACTOR_NEW_CODES_SESSION_KEY] = two_factor.regenerate_recovery_codes(profile)
            django_messages.success(
                request, "Neue Wiederherstellungscodes erstellt. Die alten gelten nicht mehr."
            )


@login_required
def data_export(request):
    # A temporary file keeps memory flat even when notes carry large attachments.
    archive = tempfile.TemporaryFile()
    write_user_export(request.user, archive)
    archive.seek(0)
    response = FileResponse(
        archive, as_attachment=True, filename=export_filename(request.user), content_type="application/zip"
    )
    response["Cache-Control"] = "private, no-store"
    return response
