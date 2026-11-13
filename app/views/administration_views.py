from datetime import timedelta

from django.conf import settings
from django.contrib import messages as django_messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.sessions.models import Session
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone

from app.forms import StorageLimitForm, SystemSettingsForm
from app.models import AuditLogEntry, Profile, UserSession
from app.services import audit_log, deployment, health
from app.services.storage_quota import top_storage_users, total_storage_used
from app.services.system_settings import get_system_settings, logout_other_authenticated_sessions
from app.services.user_preferences import format_user_datetime

HEALTH_LABELS = {"database": "Datenbank", "cache": "Cache", "automations": "Automationen"}
HEALTH_STATES = {
    health.OK: ("OK", "ok"),
    health.FAILING: ("Fehler", "error"),
    health.STALE: ("Überfällig", "warning"),
    health.UNKNOWN: ("Unbekannt", "warning"),
}


def _active_session_stats():
    # UserSession rows index the signed-in sessions by user (see app.services.user_sessions).
    live_rows = UserSession.objects.filter(
        session_key__in=Session.objects.filter(expire_date__gte=timezone.now()).values("session_key")
    )
    return {
        "authenticated_sessions": live_rows.count(),
        "authenticated_users": live_rows.values("user_id").distinct().count(),
    }


def _account_stats():
    cutoff = timezone.now() - timedelta(days=7)
    # Long sessions mean active people rarely log in again, so recent device activity counts too.
    active = (
        get_user_model()
        .objects.filter(Q(last_login__gte=cutoff) | Q(device_sessions__last_seen_at__gte=cutoff))
        .distinct()
    )
    return {
        "total": get_user_model().objects.count(),
        "active_week": active.count(),
        "unconfirmed": Profile.objects.filter(email_confirmation_pending=True).count(),
        "two_factor": Profile.objects.filter(totp_enabled_at__isnull=False).count(),
    }


def _system_health(user):
    _healthy, checks = health.health_report()
    items = []
    for name, state in checks.items():
        label, tone = HEALTH_STATES[state]
        if name == "automations" and state == health.UNKNOWN and checks["database"] == health.OK:
            label = "Noch nie gelaufen"
        items.append({"label": HEALTH_LABELS[name], "state": label, "tone": tone})
    try:
        last_run = health.last_automation_run() if checks["database"] == health.OK else None
    except Exception:  # the health check has already logged why the database is unreadable
        last_run = None
    return {"items": items, "last_automation_run": format_user_datetime(last_run, user)}


def _configuration_status():
    """Which outside services are set up, from settings alone; never shows a value."""
    smtp = settings.MAILERS["default"]["BACKEND"] == "django.core.mail.backends.smtp.EmailBackend"
    checks = [
        ("E-Mail-Versand", smtp, "SMTP", "Nur Konsole"),
        ("Web Push", settings.WEB_PUSH_ENABLED, "Eingerichtet", "Fehlt"),
        ("Wetterdaten", bool(settings.WEATHER_API_KEY), "OpenWeather", "Demodaten"),
        ("Registrierungsschutz", settings.CLOUDFLARE_TURNSTILE_REQUIRED, "Turnstile", "Aus"),
        ("Fehlermails", bool(settings.ADMINS), "An Admins", "Aus"),
    ]
    return [
        {"label": label, "state": on if ok else off, "tone": "ok" if ok else "warning"}
        for label, ok, on, off in checks
    ]


@login_required
def administration(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden("Diese Seite ist nur für Superuser verfügbar.")

    settings_obj = get_system_settings()
    storage_form = StorageLimitForm(instance=settings_obj)

    if request.method == "POST" and request.POST.get("form_name") == "storage_limit":
        storage_form = StorageLimitForm(request.POST, instance=settings_obj)
        if storage_form.is_valid():
            settings_obj = storage_form.save(commit=False)
            settings_obj.updated_by = request.user
            settings_obj.save()
            if storage_form.changed_data:
                audit_log.record(
                    AuditLogEntry.ACTION_SETTINGS_CHANGED,
                    request,
                    summary=f"Speicherlimit pro Nutzer: {settings_obj.user_storage_limit_mb} MB",
                )
            django_messages.success(request, "Speicherlimit gespeichert.")
            return redirect("administration")
        form = SystemSettingsForm(instance=settings_obj)
    elif request.method == "POST" and request.POST.get("form_name") == "system_settings":
        form = SystemSettingsForm(request.POST, instance=settings_obj)
        if form.is_valid():
            changes = [
                f"{form.fields[name].label}: {'an' if form.cleaned_data[name] else 'aus'}"
                for name in form.changed_data
            ]
            settings_obj = form.save(commit=False)
            settings_obj.updated_by = request.user
            settings_obj.save()
            if changes:
                audit_log.record(AuditLogEntry.ACTION_SETTINGS_CHANGED, request, summary=", ".join(changes))
            django_messages.success(request, "Administrationseinstellungen gespeichert.")
            return redirect("administration")
    elif request.method == "POST" and request.POST.get("form_name") == "test_mail":
        if not request.user.email:
            django_messages.error(request, "Dein Konto hat keine E-Mail-Adresse für die Testmail.")
        elif deployment.send_test_mail(request.user):
            django_messages.success(request, f"Testmail an {request.user.email} verschickt.")
        else:
            django_messages.error(
                request, "Die Testmail konnte nicht verschickt werden. Die Ursache steht im Server-Log."
            )
        return redirect("administration")
    elif request.method == "POST" and request.POST.get("form_name") == "force_logout_all":
        result = logout_other_authenticated_sessions(request.session.session_key)
        audit_log.record(
            AuditLogEntry.ACTION_FORCE_LOGOUT,
            request,
            summary=f"{result['deleted_sessions']} Sitzung(en), {result['affected_users']} Konto/Konten",
        )
        django_messages.warning(
            request,
            (
                f"{result['deleted_sessions']} aktive Sitzung(en) beendet. "
                f"{result['affected_users']} Nutzerkonto/-konten waren betroffen."
            ),
        )
        return redirect("administration")
    else:
        form = SystemSettingsForm(instance=settings_obj)

    return render(
        request,
        "app/administration.html",
        {
            "active_page": "administration",
            "form": form,
            "storage_form": storage_form,
            "system_settings": settings_obj,
            "session_stats": _active_session_stats(),
            "account_stats": _account_stats(),
            "system_health": _system_health(request.user),
            "configuration": _configuration_status(),
            "deployed_commit": deployment.deployed_commit(),
            "total_storage_used": total_storage_used(),
            "top_storage_users": top_storage_users(),
            "audit_log": audit_log.audit_log_page(
                request.user,
                filter_key=request.GET.get("audit", "all"),
                account=request.GET.get("account", ""),
                page=request.GET.get("audit_page"),
            ),
        },
    )
