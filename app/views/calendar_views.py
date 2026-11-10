from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from app.forms import (
    CalendarAvailabilityForm,
    CalendarEventEditForm,
    CalendarEventForm,
    CalendarEventMoveForm,
    CalendarQuickAddForm,
    CalendarReminderForm,
    CalendarSubscriptionForm,
    CalendarSubscriptionStyleForm,
)
from app.models import CalendarEvent, CalendarEventAttendee, CalendarReminder, Profile
from app.services.calendar_service import (
    SERIES_SCOPE_THIS,
    add_event_comment,
    attendee_availability,
    build_calendar_feed,
    can_view_event,
    delete_event_comment,
    free_slots,
    move_calendar_event,
    series_scope_events,
    sync_event_reminders,
)
from app.services.calendar_subscriptions import can_refresh, sync_subscription
from app.services.habits import WEEKDAY_SHORT
from app.services.system_settings import disabled_feature_response, feature_enabled
from app.services.trash import trash_events
from app.services.user_preferences import format_user_date, format_user_time, localtime_for_user
from app.view_models import get_calendar_context, get_calendar_event_detail_context

# The calendars panel edits, refreshes and removes subscriptions; adding one stays in settings.
CALENDAR_PAGE_SUBSCRIPTION_FORMS = {
    "calendar_subscription_style",
    "calendar_subscription_refresh",
    "calendar_subscription_delete",
}


@login_required
def calendar(request):
    calendar_event_creation_enabled = feature_enabled("calendar_event_creation")
    calendar_reminders_enabled = feature_enabled("calendar_reminders")
    event_form = CalendarEventForm(user=request.user) if calendar_event_creation_enabled else None
    edit_form = None
    editing_event_id = None

    form_name = request.POST.get("form_name", "") if request.method == "POST" else ""
    if form_name in CALENDAR_PAGE_SUBSCRIPTION_FORMS:
        if not feature_enabled("calendar_subscriptions"):
            return disabled_feature_response(request, "calendar_subscriptions")
        handle_subscription_form(request, form_name)
        return redirect(request.get_full_path())

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
            pk=editing_event_id, user=request.user, subscription=None
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

    if request.method == "POST" and request.POST.get("form_name") == "calendar_event_move":
        # Sent by dragging an event onto another day or time, or stretching it on the time grid.
        if not calendar_event_creation_enabled:
            return disabled_feature_response(request, "calendar_event_creation")
        move_form = CalendarEventMoveForm(request.POST)
        if not move_form.is_valid() or not move_calendar_event(
            request.user,
            move_form.cleaned_data["event_id"],
            target_date=move_form.cleaned_data["date"],
            start_minute=move_form.cleaned_data["start_minute"],
            end_minute=move_form.cleaned_data["end_minute"],
        ):
            django_messages.error(request, "Termin konnte nicht verschoben werden.")
        return redirect(request.get_full_path())

    if request.method == "POST" and request.POST.get("form_name") == "calendar_event_delete":
        if not calendar_event_creation_enabled:
            return disabled_feature_response(request, "calendar_event_creation")
        event = CalendarEvent.objects.filter(
            pk=request.POST.get("event_id") or 0, user=request.user, subscription=None
        ).first()
        if event is not None:
            # The edit dialog's delete button sends the chosen series scope along.
            item = trash_events(request.user, series_scope_events(event, request.POST.get("scope")))
            django_messages.success(
                request,
                "Termine in den Papierkorb gelegt."
                if item.item_count > 1
                else "Termin in den Papierkorb gelegt.",
            )
        return redirect(request.get_full_path())

    if request.method == "POST" and request.POST.get("form_name") == "calendar_event_delete_series":
        if not calendar_event_creation_enabled:
            return disabled_feature_response(request, "calendar_event_creation")
        recurrence_id = request.POST.get("recurrence_id")
        if recurrence_id and trash_events(
            request.user, CalendarEvent.objects.filter(recurrence_id=recurrence_id, user=request.user)
        ):
            django_messages.success(request, "Terminserie in den Papierkorb gelegt.")
        return redirect(request.get_full_path())

    if request.method == "POST" and request.POST.get("form_name") == "event_rsvp":
        _respond_to_invitation(request)
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
        view=_calendar_view(request),
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


def _respond_to_invitation(request):
    """Accept or decline the requesting user's own invitation (``attendee_id``)."""
    status = request.POST.get("status")
    if status not in {CalendarEventAttendee.STATUS_ACCEPTED, CalendarEventAttendee.STATUS_DECLINED}:
        return
    attendee = (
        CalendarEventAttendee.objects.filter(pk=request.POST.get("attendee_id"), user=request.user)
        .select_related("event")
        .first()
    )
    if attendee is not None:
        attendee.status = status
        attendee.responded_at = timezone.now()
        attendee.save(update_fields=["status", "responded_at"])
        # Accepting adds the event's reminder for this person, declining removes it.
        sync_event_reminders([attendee.event])


@login_required
def calendar_event_detail(request, event_id):
    """One event with its invitees and comments; for the organizer and everyone invited."""
    event = get_object_or_404(
        CalendarEvent.objects.select_related("user__profile", "subscription"), pk=event_id
    )
    if not can_view_event(event, request.user):
        raise Http404
    if event.subscription_id and not feature_enabled("calendar_subscriptions"):
        raise Http404
    comments_enabled = feature_enabled("calendar_event_creation")
    comment_error = ""
    if request.method == "POST":
        form_name = request.POST.get("form_name")
        if form_name == "event_rsvp":
            _respond_to_invitation(request)
            return redirect(request.get_full_path())
        if form_name in {"event_comment", "event_comment_delete"} and not comments_enabled:
            return disabled_feature_response(request, "calendar_event_creation")
        if form_name == "event_comment":
            try:
                comment = add_event_comment(event, request.user, request.POST.get("body"))
            except ValueError as error:
                comment_error = str(error)
            else:
                return redirect(f"{request.path}#comment-{comment.pk}")
        elif form_name == "event_comment_delete":
            if delete_event_comment(event, request.user, request.POST.get("comment_id")):
                django_messages.success(request, "Kommentar gelöscht.")
            return redirect(request.path)

    context = get_calendar_event_detail_context(event, request.user)
    context.update(
        {
            "comments_enabled": comments_enabled,
            "comment_error": comment_error,
            "comment_draft": request.POST.get("body", "") if comment_error else "",
        }
    )
    return render(request, "app/calendar_event_detail.html", context)


def _calendar_view(request):
    """The view to show: one named in the URL is remembered on the profile, and /calendar/ without a
    period opens the remembered one."""
    view = request.GET.get("view")
    profile = getattr(request.user, "profile", None)
    if profile is None:
        return view
    if view in dict(Profile.CALENDAR_VIEW_CHOICES):
        if profile.calendar_view != view:
            profile.calendar_view = view
            profile.save(update_fields=["calendar_view"])
        return view
    if not any(request.GET.get(key) for key in ("year", "month", "date")):
        return profile.calendar_view
    return view


@login_required
@require_GET
def calendar_availability(request):
    """Free/busy of the people ticked in an event dialog for the time entered there (JSON)."""
    if not feature_enabled("calendar_event_creation"):
        return disabled_feature_response(request, "calendar_event_creation", json_response=True)
    form = CalendarAvailabilityForm(request.GET, user=request.user)
    if not form.is_valid() or "start_at" not in form.cleaned_data:
        return JsonResponse({"ok": False, "users": []}, status=400)
    exclude_event = form.cleaned_data.get("exclude_event")
    if exclude_event and not CalendarEvent.objects.filter(pk=exclude_event, user=request.user).exists():
        exclude_event = None
    users = attendee_availability(
        form.cleaned_data["attendees"],
        form.cleaned_data["start_at"],
        form.cleaned_data["end_at"],
        exclude_event_id=exclude_event,
    )
    response = JsonResponse({"ok": True, "users": users})
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_GET
def calendar_free_slots(request):
    """The next times at which the user and the people ticked in an event dialog are all free, for
    the length entered there (JSON). Only times come back, nothing about the events in the way."""
    if not feature_enabled("calendar_event_creation"):
        return disabled_feature_response(request, "calendar_event_creation", json_response=True)
    form = CalendarAvailabilityForm(request.GET, user=request.user)
    if not form.is_valid() or "start_at" not in form.cleaned_data:
        return JsonResponse({"ok": False, "slots": []}, status=400)
    exclude_event = form.cleaned_data.get("exclude_event")
    if exclude_event and not CalendarEvent.objects.filter(pk=exclude_event, user=request.user).exists():
        exclude_event = None
    start_at, end_at = form.cleaned_data["start_at"], form.cleaned_data["end_at"]
    slots = free_slots(
        request.user,
        form.cleaned_data["attendees"],
        end_at - start_at,
        max(start_at, timezone.now()),
        exclude_event_id=exclude_event,
    )
    payload = []
    for begin, end in slots:
        local_begin, local_end = (
            localtime_for_user(begin, request.user),
            localtime_for_user(end, request.user),
        )
        payload.append(
            {
                "date": local_begin.date().isoformat(),
                "start_time": local_begin.strftime("%H:%M"),
                "end_time": local_end.strftime("%H:%M"),
                "label": f"{WEEKDAY_SHORT[local_begin.weekday()]} {format_user_date(local_begin, request.user)}, "
                f"{format_user_time(local_begin, request.user)}–{format_user_time(local_end, request.user)}",
            }
        )
    response = JsonResponse({"ok": True, "slots": payload})
    response["Cache-Control"] = "private, no-store"
    return response


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


def handle_subscription_form(request, form_name):
    """Add, restyle, refresh or remove an external calendar (settings and calendar page); adding and
    refreshing fetch it right away."""
    if form_name == "calendar_subscription_add":
        form = CalendarSubscriptionForm(request.POST, user=request.user)
        if not form.is_valid():
            django_messages.error(
                request, " ".join(error for errors in form.errors.values() for error in errors)
            )
            return
        _sync_with_message(request, form.save())
        return
    subscription = request.user.calendar_subscriptions.filter(
        pk=request.POST.get("subscription_id") or 0
    ).first()
    if subscription is None:
        return
    if form_name == "calendar_subscription_style":
        form = CalendarSubscriptionStyleForm(request.POST, instance=subscription)
        if form.is_valid():
            form.save()
            django_messages.success(request, f"„{subscription.name}“ gespeichert.")
        else:
            django_messages.error(
                request, " ".join(error for errors in form.errors.values() for error in errors)
            )
    elif form_name == "calendar_subscription_delete":
        subscription.delete()
        django_messages.success(request, f"„{subscription.name}“ wird nicht mehr abonniert.")
    elif form_name == "calendar_subscription_refresh":
        if can_refresh(subscription):
            _sync_with_message(request, subscription)
        else:
            django_messages.info(request, f"„{subscription.name}“ wurde gerade erst aktualisiert.")


def _sync_with_message(request, subscription):
    if sync_subscription(subscription):
        django_messages.success(
            request, f"„{subscription.name}“ aktualisiert: {subscription.event_count} Termin(e)."
        )
    else:
        django_messages.error(
            request, f"„{subscription.name}“ konnte nicht geladen werden: {subscription.last_error}"
        )
