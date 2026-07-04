from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from app.forms import CalendarReminderForm, CalendarSourceForm
from app.models import CalendarReminder, CalendarSource
from app.services.calendar_service import sync_calendar_source
from app.services.user_preferences import format_user_datetime
from app.view_models import get_calendar_context


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
            sync_calendar_source(source, force=True)
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
            "calendar_last_synced_label": (
                format_user_datetime(source.last_synced_at, request.user)
                if source and source.last_synced_at
                else ""
            ),
        }
    )
    return render(request, "app/calendar.html", context)
