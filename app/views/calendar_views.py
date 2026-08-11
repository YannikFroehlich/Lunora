from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from app.forms import CalendarEventForm, CalendarReminderForm
from app.models import CalendarEventAttendee, CalendarReminder, CalendarSource
from app.services.calendar_service import sync_calendar_sources
from app.services.system_settings import disabled_feature_response, feature_enabled
from app.services.user_preferences import format_user_datetime
from app.view_models import get_calendar_context


def _calendar_source_items(sources, user):
    items = []
    for source in sources:
        if source.last_error:
            status_label = source.last_error
            status_kind = "error"
        elif source.last_synced_at:
            status_label = f"Zuletzt: {format_user_datetime(source.last_synced_at, user)}"
            status_kind = "synced"
        elif source.enabled:
            status_label = "Noch nicht synchronisiert"
            status_kind = "idle"
        else:
            status_label = "Sync deaktiviert"
            status_kind = "muted"
        items.append({"source": source, "status_label": status_label, "status_kind": status_kind})
    return items


@login_required
def calendar(request):
    sources = list(CalendarSource.objects.filter(user=request.user).order_by("name", "id"))
    sync_result = None
    calendar_sync_enabled = feature_enabled("calendar_sync")
    calendar_event_creation_enabled = feature_enabled("calendar_event_creation")
    calendar_reminders_enabled = feature_enabled("calendar_reminders")
    event_form = CalendarEventForm(user=request.user) if calendar_event_creation_enabled else None

    if request.method == "POST" and request.POST.get("form_name") == "calendar_sync_all":
        if not calendar_sync_enabled:
            django_messages.warning(request, "Kalendersynchronisierung ist voruebergehend deaktiviert.")
            return redirect(request.get_full_path())
        sync_result = sync_calendar_sources(sources, force=True)
        if sync_result.get("synced"):
            django_messages.success(request, sync_result.get("message", "Kalender synchronisiert."))
        else:
            django_messages.info(request, sync_result.get("message", "Kalender ist aktuell."))
        return redirect(request.get_full_path())

    if request.method == "POST" and request.POST.get("form_name") == "calendar_visibility":
        visible_ids = {
            int(source_id)
            for source_id in request.POST.getlist("visible_source_ids")
            if source_id.isdigit()
        }
        owned_visible_ids = list(
            CalendarSource.objects.filter(user=request.user, pk__in=visible_ids).values_list("pk", flat=True)
        )
        CalendarSource.objects.filter(user=request.user).update(is_visible=False)
        CalendarSource.objects.filter(user=request.user, pk__in=owned_visible_ids).update(is_visible=True)
        return redirect(request.get_full_path())

    if request.method == "POST" and request.POST.get("form_name") == "calendar_event_add":
        if not calendar_event_creation_enabled:
            return disabled_feature_response(request, "calendar_event_creation")
        event_form = CalendarEventForm(request.POST, user=request.user)
        if event_form.is_valid():
            event_form.save(user=request.user)
            django_messages.success(request, "Termin erstellt.")
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
        reminder = CalendarReminder.objects.filter(user=request.user, pk=request.POST.get("reminder_id")).first()
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

    sources = list(CalendarSource.objects.filter(user=request.user).order_by("name", "id"))
    context = get_calendar_context(
        request.user,
        year=request.GET.get("year"),
        month=request.GET.get("month"),
    )
    context.update(
        {
            "calendar_source": sources[0] if sources else None,
            "calendar_sources": _calendar_source_items(sources, request.user),
            "visible_calendar_count": sum(1 for source in sources if source.is_visible),
            "event_form": event_form,
            "reminder_form": reminder_form,
            "sync_result": sync_result,
            "calendar_sync_enabled": calendar_sync_enabled,
            "calendar_event_creation_enabled": calendar_event_creation_enabled,
            "calendar_reminders_enabled": calendar_reminders_enabled,
        }
    )
    return render(request, "app/calendar.html", context)
