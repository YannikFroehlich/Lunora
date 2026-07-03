from urllib.parse import urlparse, urlunparse

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from app.forms import AppearanceForm, CalendarSourceForm, ProfileForm
from app.models import CalendarSource, Profile
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


@login_required
def home(request):
    return render(request, "app/home.html", get_dashboard_context(request.user))


@login_required
def settings(request):
    profile = get_or_create_profile(request.user)
    calendar_source = CalendarSource.objects.filter(user=request.user).first()
    return_to = get_safe_settings_return_url(request)

    if request.method == "POST" and request.POST.get("form_name") == "profile":
        form = ProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            profile = form.save()
            request.user.first_name = profile.display_name
            request.user.save(update_fields=["first_name"])
            return redirect(return_to)
        appearance_form = AppearanceForm(instance=profile)
        calendar_source_form = CalendarSourceForm(instance=calendar_source)
    elif request.method == "POST" and request.POST.get("form_name") == "appearance":
        appearance_form = AppearanceForm(request.POST, instance=profile)
        form = ProfileForm(instance=profile)
        calendar_source_form = CalendarSourceForm(instance=calendar_source)
        if appearance_form.is_valid():
            appearance_form.save()
            return redirect(return_to)
    elif request.method == "POST" and request.POST.get("form_name") == "calendar_source":
        calendar_source_form = CalendarSourceForm(request.POST, instance=calendar_source)
        form = ProfileForm(instance=profile)
        appearance_form = AppearanceForm(instance=profile)
        if calendar_source_form.is_valid():
            calendar_source = calendar_source_form.save(commit=False)
            calendar_source.user = request.user
            calendar_source.name = "Google Kalender"
            calendar_source.save()
            return redirect(return_to)
    else:
        form = ProfileForm(instance=profile)
        appearance_form = AppearanceForm(instance=profile)
        calendar_source_form = CalendarSourceForm(instance=calendar_source)

    context = get_settings_context()
    context.update(
        {
            "profile": profile,
            "profile_form": form,
            "appearance_form": appearance_form,
            "calendar_source": calendar_source,
            "calendar_source_form": calendar_source_form,
            "return_to": return_to,
        }
    )
    return render(request, "app/settings.html", context)
