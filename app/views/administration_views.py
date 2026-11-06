from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.contrib.sessions.models import Session
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone

from app.forms import StorageLimitForm, SystemSettingsForm
from app.models import AuditLogEntry, UserSession
from app.services import audit_log
from app.services.storage_quota import total_storage_used
from app.services.system_settings import get_system_settings, logout_other_authenticated_sessions


def _active_session_stats():
    # UserSession rows index the signed-in sessions by user (see app.services.user_sessions).
    live_rows = UserSession.objects.filter(
        session_key__in=Session.objects.filter(expire_date__gte=timezone.now()).values("session_key")
    )
    return {
        "authenticated_sessions": live_rows.count(),
        "authenticated_users": live_rows.values("user_id").distinct().count(),
    }


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
            "total_storage_used": total_storage_used(),
            "audit_log": audit_log.audit_log_page(
                request.user, filter_key=request.GET.get("audit", "all"), page=request.GET.get("audit_page")
            ),
        },
    )
