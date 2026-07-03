from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect
from django.shortcuts import render

from app.forms import AppearanceForm, CalendarReminderForm, CalendarSourceForm, ProfileForm, RegistrationForm
from app.models import CalendarReminder, CalendarSource, Profile
from app.services.calendar_service import sync_calendar_source
from app.services.weather_service import get_location_suggestions, get_weather_context
from app.view_models import (
    get_calendar_context,
    get_dashboard_context,
    get_messages_context,
    get_settings_context,
)


def get_or_create_profile(user):
    profile, _created = Profile.objects.get_or_create(
        user=user,
        defaults={"display_name": user.first_name or user.get_username()},
    )
    return profile


def register(request):
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("home")
    else:
        form = RegistrationForm()

    return render(request, "app/register.html", {"form": form})


@login_required
def home(request):
    return render(request, "app/home.html", get_dashboard_context(request.user))


@login_required
def settings(request):
    profile = get_or_create_profile(request.user)

    if request.method == "POST" and request.POST.get("form_name") == "profile":
        form = ProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            profile = form.save()
            request.user.first_name = profile.display_name
            request.user.save(update_fields=["first_name"])
            return redirect("settings")
        appearance_form = AppearanceForm(instance=profile)
    elif request.method == "POST" and request.POST.get("form_name") == "appearance":
        appearance_form = AppearanceForm(request.POST, instance=profile)
        form = ProfileForm(instance=profile)
        if appearance_form.is_valid():
            appearance_form.save()
            return redirect("settings")
    else:
        form = ProfileForm(instance=profile)
        appearance_form = AppearanceForm(instance=profile)

    context = get_settings_context()
    context.update(
        {
            "profile": profile,
            "profile_form": form,
            "appearance_form": appearance_form,
        }
    )
    return render(request, "app/settings.html", context)


@login_required
def weather(request):
    return render(request, "app/weather.html", get_weather_context(request.GET))


@login_required
def weather_suggestions(request):
    query = request.GET.get("q", "")
    return JsonResponse({"results": get_location_suggestions(query)})


@login_required
def calendar(request):
    source = CalendarSource.objects.filter(user=request.user).first()
    sync_result = None

    if request.method == "POST" and request.POST.get("form_name") == "calendar_source":
        source_form = CalendarSourceForm(request.POST, instance=source)
        if source_form.is_valid():
            source = source_form.save(commit=False)
            source.user = request.user
            source.name = "Google Kalender"
            source.save()
            sync_result = sync_calendar_source(source, force=True)
            return redirect("calendar")
    elif request.method == "POST" and request.POST.get("form_name") == "calendar_sync" and source:
        sync_result = sync_calendar_source(source, force=True)
        source_form = CalendarSourceForm(instance=source)
    elif request.method == "POST" and request.POST.get("form_name") == "reminder_add":
        reminder_form = CalendarReminderForm(request.POST)
        if reminder_form.is_valid():
            reminder = reminder_form.save(commit=False)
            reminder.user = request.user
            reminder.save()
            return redirect(request.get_full_path())
        source_form = CalendarSourceForm(instance=source)
    elif request.method == "POST" and request.POST.get("form_name") == "reminder_toggle":
        reminder = CalendarReminder.objects.filter(user=request.user, pk=request.POST.get("reminder_id")).first()
        if reminder:
            reminder.is_done = request.POST.get("is_done") == "on"
            reminder.save(update_fields=["is_done", "updated_at"])
        return redirect(request.get_full_path())
    else:
        source_form = CalendarSourceForm(instance=source)
        if source:
            sync_result = sync_calendar_source(source)
    if "reminder_form" not in locals():
        reminder_form = CalendarReminderForm()

    context = get_calendar_context(
        request.user,
        year=request.GET.get("year"),
        month=request.GET.get("month"),
    )
    context.update(
        {
            "calendar_source": source,
            "calendar_source_form": source_form,
            "reminder_form": reminder_form,
            "sync_result": sync_result,
        }
    )
    return render(request, "app/calendar.html", context)


@login_required
def messages(request):
    return render(request, "app/messages.html", get_messages_context())
