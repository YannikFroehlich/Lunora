from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.contrib.sessions.models import Session
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone

from app.forms import SystemSettingsForm
from app.services.system_settings import get_system_settings, logout_other_authenticated_sessions


def _active_session_stats():
    active_sessions = Session.objects.filter(expire_date__gte=timezone.now())
    authenticated_sessions = 0
    user_ids = set()

    for session in active_sessions:
        user_id = session.get_decoded().get("_auth_user_id")
        if user_id:
            authenticated_sessions += 1
            user_ids.add(user_id)

    return {
        "authenticated_sessions": authenticated_sessions,
        "authenticated_users": len(user_ids),
    }


@login_required
def administration(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden("Diese Seite ist nur fuer Superuser verfuegbar.")

    settings_obj = get_system_settings()

    if request.method == "POST" and request.POST.get("form_name") == "system_settings":
        form = SystemSettingsForm(request.POST, instance=settings_obj)
        if form.is_valid():
            settings_obj = form.save(commit=False)
            settings_obj.updated_by = request.user
            settings_obj.save()
            django_messages.success(request, "Administrationseinstellungen gespeichert.")
            return redirect("administration")
    elif request.method == "POST" and request.POST.get("form_name") == "force_logout_all":
        result = logout_other_authenticated_sessions(request.session.session_key)
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
            "system_settings": settings_obj,
            "session_stats": _active_session_stats(),
        },
    )
