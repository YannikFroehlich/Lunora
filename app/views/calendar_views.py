from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from app.forms import CalendarEventEditForm, CalendarEventForm, CalendarQuickAddForm, CalendarReminderForm
from app.models import CalendarEvent, CalendarEventAttendee, CalendarReminder, Profile
from app.services.calendar_service import SERIES_SCOPE_THIS, build_calendar_feed, series_scope_events
from app.services.system_settings import disabled_feature_response, feature_enabled
from app.services.user_preferences import format_user_date, format_user_time
from app.view_models import get_calendar_context


@login_required
def calendar(request):
    calendar_event_creation_enabled = feature_enabled("calendar_event_creation")
    calendar_reminders_enabled = feature_enabled("calendar_reminders")
    event_form = CalendarEventForm(user=request.user) if calendar_event_creation_enabled else None
    edit_form = None
    editing_event_id = None

    if request.method == "POST" and request.POST.get("form_name") == "calendar_event_add":
        if not calendar_event_creation_enabled:
            return disabled_feature_response(request, "calendar_event_creation")
        event_form = CalendarEventForm(request.POST, user=request.user)
        if event_form.is_valid():
            event_form.save(user=request.user)
            django_messages.success(request, "Termin erstellt.")
            return redirect(request.get_full_path())

    quick_add_form = CalendarQuickAddForm(user=request.user) if calendar_event_creation_enabled else None
    if request.method == "POST" and request.POST.get("form_name") == "calendar_quick_add":
        if not calendar_event_creation_enabled:
            return disabled_feature_response(request, "calendar_event_creation")
        quick_add_form = CalendarQuickAddForm(request.POST, user=request.user)
        if quick_add_form.is_valid():
            event = quick_add_form.save()
            when = format_user_date(event.start_at, request.user)
            if not event.is_all_day:
                when = f"{when}, {format_user_time(event.start_at, request.user)}"
            django_messages.success(request, f"Termin „{event.title}“ am {when} eingetragen.")
            return redirect(request.get_full_path())

    if request.method == "POST" and request.POST.get("form_name") == "calendar_event_edit":
        if not calendar_event_creation_enabled:
            return disabled_feature_response(request, "calendar_event_creation")
        editing_event_id = request.POST.get("event_id")
        event = CalendarEvent.objects.filter(
            pk=editing_event_id,
            user=request.user,
        ).first()
        if event is None:
            django_messages.error(request, "Termin konnte nicht bearbeitet werden.")
            return redirect(request.get_full_path())
        edit_form = CalendarEventEditForm(request.POST, user=request.user, instance=event)
        if edit_form.is_valid():
            edit_form.save()
            if event.recurrence_id and edit_form.cleaned_data["scope"] != SERIES_SCOPE_THIS:
                django_messages.success(request, "Terminserie aktualisiert.")
            else:
                django_messages.success(request, "Termin aktualisiert.")
            return redirect(request.get_full_path())

    if request.method == "POST" and request.POST.get("form_name") == "calendar_event_delete":
        if not calendar_event_creation_enabled:
            return disabled_feature_response(request, "calendar_event_creation")
        event = CalendarEvent.objects.filter(pk=request.POST.get("event_id"), user=request.user).first()
        if event is not None:
            # The edit dialog's delete button sends the chosen series scope along.
            _total, deleted = series_scope_events(event, request.POST.get("scope")).delete()
            event_count = deleted.get(CalendarEvent._meta.label, 0)
            django_messages.success(request, "Termine gelöscht." if event_count > 1 else "Termin gelöscht.")
        return redirect(request.get_full_path())

    if request.method == "POST" and request.POST.get("form_name") == "calendar_event_delete_series":
        if not calendar_event_creation_enabled:
            return disabled_feature_response(request, "calendar_event_creation")
        recurrence_id = request.POST.get("recurrence_id")
        if recurrence_id:
            CalendarEvent.objects.filter(
                recurrence_id=recurrence_id,
                user=request.user,
            ).delete()
            django_messages.success(request, "Terminserie gelöscht.")
        return redirect(request.get_full_path())

    if request.method == "POST" and request.POST.get("form_name") == "event_rsvp":
        status = request.POST.get("status")
        if status in {CalendarEventAttendee.STATUS_ACCEPTED, CalendarEventAttendee.STATUS_DECLINED}:
            CalendarEventAttendee.objects.filter(
                pk=request.POST.get("attendee_id"),
                user=request.user,
            ).update(status=status, responded_at=timezone.now())
        return redirect(request.get_full_path())

    if request.method == "POST" and request.POST.get("form_name") == "reminder_add":
        if not calendar_reminders_enabled:
            return disabled_feature_response(request, "calendar_reminders")
        reminder_form = CalendarReminderForm(request.POST)
        if reminder_form.is_valid():
            reminder = reminder_form.save(commit=False)
            reminder.user = request.user
            reminder.save()
            django_messages.success(request, "Erinnerung erstellt.")
            return redirect(request.get_full_path())
    elif request.method == "POST" and request.POST.get("form_name") == "reminder_toggle":
        if not calendar_reminders_enabled:
            return disabled_feature_response(request, "calendar_reminders")
        reminder = CalendarReminder.objects.filter(
            user=request.user, pk=request.POST.get("reminder_id")
        ).first()
        if reminder:
            reminder.is_done = request.POST.get("is_done") == "on"
            reminder.save(update_fields=["is_done", "updated_at"])
        return redirect(request.get_full_path())
    elif request.method == "POST" and request.POST.get("form_name") == "reminder_delete":
        if not calendar_reminders_enabled:
            return disabled_feature_response(request, "calendar_reminders")
        deleted_count, _details = CalendarReminder.objects.filter(
            user=request.user,
            pk=request.POST.get("reminder_id"),
        ).delete()
        if deleted_count:
            django_messages.success(request, "Erinnerung gelöscht.")
        return redirect(request.get_full_path())
    else:
        reminder_form = CalendarReminderForm() if calendar_reminders_enabled else None

    context = get_calendar_context(
        request.user,
        year=request.GET.get("year"),
        month=request.GET.get("month"),
        view=request.GET.get("view"),
        day=request.GET.get("date"),
    )
    context.update(
        {
            "event_form": event_form,
            "quick_add_form": quick_add_form,
            "edit_form": edit_form
            or (CalendarEventEditForm(user=request.user) if calendar_event_creation_enabled else None),
            "editing_event_id": editing_event_id,
            # Keeps the series choice visible when an invalid edit reopens the dialog.
            "editing_event_series": bool(
                edit_form and edit_form.instance and edit_form.instance.recurrence_id
            ),
            "reminder_form": reminder_form,
            "calendar_event_creation_enabled": calendar_event_creation_enabled,
            "calendar_reminders_enabled": calendar_reminders_enabled,
        }
    )
    return render(request, "app/calendar.html", context)


@require_GET
def calendar_feed(request, token):
    """Public iCal subscription feed; the secret token in the URL is the only credential."""
    if not feature_enabled("calendar_feed"):
        return disabled_feature_response(request, "calendar_feed")
    profile = (
        Profile.objects.select_related("user").filter(calendar_feed_token=token, user__is_active=True).first()
    )
    if profile is None:
        raise Http404
    body = build_calendar_feed(profile.user, calendar_name=f"Lunora – {profile.name}")
    response = HttpResponse(body, content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = 'inline; filename="lunora.ics"'
    response["Cache-Control"] = "private, no-cache"
    response["X-Robots-Tag"] = "noindex"
    return response
