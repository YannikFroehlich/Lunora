from urllib.parse import urlparse, urlunparse

from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from app.forms import AppearanceForm, CalendarSourceForm, ProfileForm, ProfilePreferencesForm
from app.models import CalendarSource, Profile
from app.services.calendar_service import sync_calendar_source, sync_calendar_sources
from app.services.user_preferences import format_user_datetime
from app.view_models import get_dashboard_context, get_settings_context


def get_safe_settings_return_url(request):
    """Return the page the user came from before opening settings."""
    fallback_url = reverse("home")
    settings_path = reverse("settings").rstrip("/")
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
        if path.rstrip("/") == settings_path:
            continue

        if parsed.netloc:
            candidate = urlunparse(("", "", path, "", parsed.query, parsed.fragment))

        if candidate.startswith("/"):
            return candidate

    return fallback_url


def get_or_create_profile(user):
    profile, _created = Profile.objects.get_or_create(
        user=user,
        defaults={"display_name": user.first_name or user.get_username()},
    )
    return profile


def _calendar_source_status(source, user):
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
        form = bound_form if source.id == bound_source_id else CalendarSourceForm(
            instance=source,
            user=user,
            prefix=f"source-{source.id}",
        )
        items.append({"source": source, "form": form, "status": _calendar_source_status(source, user)})
    return items


@login_required
def home(request):
    return render(request, "app/home.html", get_dashboard_context(request.user))


@login_required
def settings(request):
    profile = get_or_create_profile(request.user)
    return_to = get_safe_settings_return_url(request)
    bound_calendar_form = None
    bound_calendar_source_id = None
    calendar_add_form = CalendarSourceForm(user=request.user, prefix="new")

    if request.method == "POST" and request.POST.get("form_name") == "profile":
        form = ProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            profile = form.save()
            request.user.first_name = profile.display_name
            request.user.save(update_fields=["first_name"])
            django_messages.success(request, "Profil gespeichert.")
            return redirect(return_to)
        appearance_form = AppearanceForm(instance=profile)
        preferences_form = ProfilePreferencesForm(instance=profile)
    elif request.method == "POST" and request.POST.get("form_name") == "appearance":
        appearance_form = AppearanceForm(request.POST, instance=profile)
        form = ProfileForm(instance=profile)
        preferences_form = ProfilePreferencesForm(instance=profile)
        if appearance_form.is_valid():
            appearance_form.save()
            django_messages.success(request, "Darstellung und Region gespeichert.")
            return redirect(return_to)
    elif request.method == "POST" and request.POST.get("form_name") == "calendar_source_add":
        calendar_add_form = CalendarSourceForm(request.POST, user=request.user, prefix="new")
        form = ProfileForm(instance=profile)
        appearance_form = AppearanceForm(instance=profile)
        preferences_form = ProfilePreferencesForm(instance=profile)
        if calendar_add_form.is_valid():
            calendar_source = calendar_add_form.save(commit=False)
            calendar_source.user = request.user
            calendar_source.save()
            sync_result = sync_calendar_source(calendar_source, force=True)
            if sync_result.get("synced"):
                django_messages.success(request, f"{calendar_source.name} gespeichert und synchronisiert.")
            else:
                django_messages.warning(request, f"{calendar_source.name} gespeichert: {sync_result.get('message')}")
            return redirect(return_to)
    elif request.method == "POST" and request.POST.get("form_name") == "calendar_source_update":
        calendar_source = CalendarSource.objects.filter(user=request.user, pk=request.POST.get("source_id")).first()
        form = ProfileForm(instance=profile)
        appearance_form = AppearanceForm(instance=profile)
        preferences_form = ProfilePreferencesForm(instance=profile)
        if not calendar_source:
            django_messages.error(request, "Kalender wurde nicht gefunden.")
            return redirect(return_to)
        bound_calendar_source_id = calendar_source.id
        old_ical_url = calendar_source.ical_url
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
            if calendar_source.ical_url != old_ical_url:
                calendar_source.events.all().delete()
                sync_result = sync_calendar_source(calendar_source, force=True)
                if sync_result.get("synced"):
                    django_messages.success(request, f"{calendar_source.name} gespeichert und neu synchronisiert.")
                else:
                    django_messages.warning(request, f"{calendar_source.name} gespeichert: {sync_result.get('message')}")
            else:
                django_messages.success(request, f"{calendar_source.name} gespeichert.")
            return redirect(return_to)
    elif request.method == "POST" and request.POST.get("form_name") == "calendar_source_delete":
        deleted_count, _details = CalendarSource.objects.filter(
            user=request.user,
            pk=request.POST.get("source_id"),
        ).delete()
        if deleted_count:
            django_messages.success(request, "Kalender geloescht.")
        return redirect(return_to)
    elif request.method == "POST" and request.POST.get("form_name") == "calendar_sync_all":
        sync_result = sync_calendar_sources(
            CalendarSource.objects.filter(user=request.user).order_by("name", "id"),
            force=True,
        )
        if sync_result.get("synced"):
            django_messages.success(request, sync_result.get("message", "Kalender synchronisiert."))
        else:
            django_messages.info(request, sync_result.get("message", "Kalender ist aktuell."))
        return redirect(return_to)
    elif request.method == "POST" and request.POST.get("form_name") == "preferences":
        preferences_form = ProfilePreferencesForm(request.POST, instance=profile)
        form = ProfileForm(instance=profile)
        appearance_form = AppearanceForm(instance=profile)
        if preferences_form.is_valid():
            preferences_form.save()
            django_messages.success(request, "Präferenzen gespeichert.")
            return redirect(return_to)
    else:
        form = ProfileForm(instance=profile)
        appearance_form = AppearanceForm(instance=profile)
        preferences_form = ProfilePreferencesForm(instance=profile)

    calendar_sources = list(CalendarSource.objects.filter(user=request.user).order_by("name", "id"))
    context = get_settings_context(preferences_form)
    context.update(
        {
            "profile": profile,
            "profile_form": form,
            "appearance_form": appearance_form,
            "calendar_source": calendar_sources[0] if calendar_sources else None,
            "calendar_source_form": calendar_add_form,
            "calendar_source_forms": _calendar_source_form_items(
                calendar_sources,
                request.user,
                bound_form=bound_calendar_form,
                bound_source_id=bound_calendar_source_id,
            ),
            "preferences_form": preferences_form,
            "return_to": return_to,
        }
    )
    return render(request, "app/settings.html", context)
