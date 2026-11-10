from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import content_disposition_header

from app.forms import (
    TaskForm,
    TaskLabelForm,
    TaskListForm,
    TaskRescheduleForm,
    TaskTemplateUseForm,
    TaskTimeEntryForm,
)
from app.models import Task, TaskLabel, TaskList
from app.services.notes import display_name
from app.services.notifications import (
    materialize_task_assignment_notifications,
    materialize_task_comment_notification,
    materialize_task_return_notification,
    materialize_task_share_notifications,
)
from app.services.system_settings import disabled_feature_response, feature_enabled
from app.services.tasks import (
    add_task_comment,
    create_task_from_template,
    create_task_label,
    create_task_list,
    delete_task_comment,
    delete_task_label,
    delete_task_list,
    delete_task_template,
    edit_task_comment,
    get_tasks_context,
    leave_task_share,
    move_task,
    move_task_list,
    rearm_task_reminders,
    rename_task_list,
    reschedule_task,
    return_task,
    save_task_template,
    set_task_status,
    toggle_task,
    visible_tasks,
)
from app.services.time_tracking import (
    add_manual_entry,
    annotate_task_items,
    delete_entry,
    month_start_for,
    running_timer_context,
    stop_timer,
    time_report,
    time_report_csv,
    toggle_timer,
    week_start_for,
)
from app.services.trash import trash_task
from app.services.user_preferences import format_user_datetime
from app.views.navigation import local_return_to
from app.views.trash_views import trashed_message


@login_required
def tasks(request):
    if not feature_enabled("tasks"):
        return disabled_feature_response(request, "tasks")

    form_name = request.POST.get("form_name") if request.method == "POST" else None
    task_form = TaskForm(user=request.user)
    edit_form = None
    editing_task_id = None

    if form_name == "task_add":
        task_form = TaskForm(request.POST, user=request.user)
        if task_form.is_valid():
            task = task_form.save(commit=False)
            task.user = request.user
            # A partial POST (e.g. the inline "add subtask" quick form) omits these
            # optional selects entirely, which ModelForm cleans to "" rather than the
            # model's "none" default — normalize so both mean the same thing in the DB.
            task.priority = task.priority or Task.PRIORITY_NONE
            task.recurrence_rule = task.recurrence_rule or Task.RECURRENCE_NONE
            task.save()
            task_form.save_m2m()
            materialize_task_assignment_notifications([task])
            materialize_task_share_notifications(task_form.save_shares(task))
            django_messages.success(request, _created_message(task, task_form.quick_add_result, request.user))
            return redirect(request.get_full_path())
    elif form_name == "task_timer":
        task = visible_tasks(request.user).filter(pk=_positive_int(request.POST.get("task_id"))).first()
        if task is None:
            django_messages.error(request, "Diese Aufgabe wurde nicht gefunden.")
        else:
            try:
                result = toggle_timer(request.user, task)
            except ValueError as error:
                django_messages.error(request, str(error))
            else:
                django_messages.success(
                    request, f"Timer für „{task.title}“ {'gestartet' if result == 'started' else 'gestoppt'}."
                )
        return redirect(local_return_to(request) or request.get_full_path())
    elif form_name == "task_toggle":
        toggle_task(request.user, request.POST.get("task_id"), request.POST.get("is_done") == "on")
        return redirect(local_return_to(request) or request.get_full_path())
    elif form_name == "task_status":
        # The board's columns (and their move buttons): open, in progress or done.
        if (
            set_task_status(request.user, request.POST.get("task_id") or 0, request.POST.get("status"))
            is None
        ):
            django_messages.error(request, "Status konnte nicht geändert werden.")
        return redirect(local_return_to(request) or request.get_full_path())
    elif form_name == "task_share_leave":
        task = leave_task_share(request.user, request.POST.get("task_id") or 0)
        if task is not None:
            django_messages.success(request, f"„{task.title}“ wird nicht mehr mit dir geteilt.")
        return redirect(request.get_full_path())
    elif form_name == "task_reschedule":
        # Sent by dropping a task onto another day (or time slot) in the calendar.
        reschedule_form = TaskRescheduleForm(request.POST)
        if not reschedule_form.is_valid() or not reschedule_task(
            request.user,
            reschedule_form.cleaned_data["task_id"],
            due_date=reschedule_form.cleaned_data["due_date"],
            due_time=reschedule_form.cleaned_data["due_time"],
        ):
            django_messages.error(request, "Aufgabe konnte nicht verschoben werden.")
        return redirect(local_return_to(request) or request.get_full_path())
    elif form_name == "task_comment":
        try:
            comment = add_task_comment(
                request.user, request.POST.get("task_id") or 0, request.POST.get("body")
            )
        except ValidationError as error:
            django_messages.error(request, " ".join(error.messages))
        else:
            if comment is None:
                django_messages.error(request, "Diese Aufgabe kann nicht kommentiert werden.")
            else:
                materialize_task_comment_notification(comment)
                # Reopen the conversation that was just written in.
                return redirect(f"{request.get_full_path()}#task-comments-{comment.task_id}")
        return redirect(request.get_full_path())
    elif form_name in ("task_comment_edit", "task_comment_delete"):
        comment_id = request.POST.get("comment_id") or 0
        try:
            if form_name == "task_comment_edit":
                comment = edit_task_comment(request.user, comment_id, request.POST.get("body"))
                task_id = comment.task_id if comment else None
            else:
                task_id = delete_task_comment(request.user, comment_id)
        except ValidationError as error:
            django_messages.error(request, " ".join(error.messages))
            return redirect(request.get_full_path())
        if task_id is None:
            django_messages.error(request, "Dieser Kommentar kann nicht geändert werden.")
            return redirect(request.get_full_path())
        return redirect(f"{request.get_full_path()}#task-comments-{task_id}")
    elif form_name == "task_return":
        task = return_task(request.user, request.POST.get("task_id") or 0)
        if task is None:
            django_messages.error(request, "Diese Aufgabe kann nicht zurückgegeben werden.")
        else:
            materialize_task_return_notification(
                task, request.user, (request.POST.get("reason") or "").strip()
            )
            django_messages.success(request, f"„{task.title}“ an {display_name(task.user)} zurückgegeben.")
        return redirect(request.get_full_path())
    elif form_name == "task_template_save":
        try:
            template = save_task_template(request.user, request.POST.get("task_id") or 0)
        except ValidationError as error:
            django_messages.error(request, " ".join(error.messages))
        else:
            if template:
                django_messages.success(request, f"Vorlage „{template.title}“ gespeichert.")
        return redirect(request.get_full_path())
    elif form_name == "task_template_use":
        use_form = TaskTemplateUseForm(request.POST)
        task = (
            create_task_from_template(
                request.user, use_form.cleaned_data["template_id"], due_at=use_form.cleaned_data["due_at"]
            )
            if use_form.is_valid()
            else None
        )
        if task is None:
            django_messages.error(request, "Aufgabe konnte nicht aus der Vorlage erstellt werden.")
        else:
            django_messages.success(request, f"Aufgabe „{task.title}“ aus Vorlage erstellt.")
        return redirect(request.get_full_path())
    elif form_name == "task_template_delete":
        if delete_task_template(request.user, request.POST.get("template_id") or 0):
            django_messages.success(request, "Vorlage gelöscht.")
        return redirect(request.get_full_path())
    elif form_name == "task_delete":
        if item := trash_task(request.user, request.POST.get("task_id") or 0):
            trashed_message(request, item, "Aufgabe in den Papierkorb gelegt.")
        return redirect(request.get_full_path())
    elif form_name == "task_edit":
        editing_task_id = request.POST.get("task_id")
        task = Task.objects.filter(user=request.user, pk=editing_task_id).first()
        if task is None:
            django_messages.error(request, "Aufgabe konnte nicht bearbeitet werden.")
            return redirect(request.get_full_path())
        previous_due_at = task.due_at
        edit_form = TaskForm(request.POST, instance=task, user=request.user, auto_id="id_edit_%s")
        if edit_form.is_valid():
            edited_task = edit_form.save(commit=False)
            rearm_task_reminders(edited_task, previous_due_at)
            edited_task.priority = edited_task.priority or Task.PRIORITY_NONE
            edited_task.recurrence_rule = edited_task.recurrence_rule or Task.RECURRENCE_NONE
            edited_task.save()
            edit_form.save_m2m()
            materialize_task_assignment_notifications([edited_task])
            materialize_task_share_notifications(edit_form.save_shares(edited_task))
            django_messages.success(request, "Aufgabe aktualisiert.")
            return redirect(request.get_full_path())
    elif form_name == "task_list_add":
        list_form = TaskListForm(request.POST)
        if list_form.is_valid():
            try:
                create_task_list(
                    request.user,
                    name=list_form.cleaned_data["name"],
                    color=list_form.cleaned_data["color"],
                )
                django_messages.success(request, "Liste erstellt.")
            except ValidationError as error:
                django_messages.error(request, " ".join(error.messages))
        else:
            django_messages.error(request, "Der Listenname darf nicht leer sein.")
        return redirect(request.get_full_path())
    elif form_name == "task_list_rename":
        try:
            rename_task_list(request.user, request.POST.get("task_list_id"), name=request.POST.get("name"))
        except TaskList.DoesNotExist:
            pass
        except ValidationError as error:
            django_messages.error(request, " ".join(error.messages))
        return redirect(request.get_full_path())
    elif form_name == "task_list_delete":
        try:
            delete_task_list(request.user, request.POST.get("task_list_id"))
            django_messages.success(request, "Liste gelöscht.")
        except TaskList.DoesNotExist:
            pass
        return redirect(request.get_full_path())
    elif form_name == "task_label_add":
        label_form = TaskLabelForm(request.POST)
        if label_form.is_valid():
            try:
                create_task_label(
                    request.user,
                    name=label_form.cleaned_data["name"],
                    color=label_form.cleaned_data["color"],
                )
                django_messages.success(request, "Label erstellt.")
            except ValidationError as error:
                django_messages.error(request, " ".join(error.messages))
        else:
            django_messages.error(request, "Der Labelname darf nicht leer sein.")
        return redirect(request.get_full_path())
    elif form_name == "task_label_delete":
        try:
            delete_task_label(request.user, request.POST.get("task_label_id"))
            django_messages.success(request, "Label gelöscht.")
        except TaskLabel.DoesNotExist:
            pass
        return redirect(request.get_full_path())
    elif form_name == "task_reorder":
        try:
            move_task(
                request.user,
                task_id=request.POST.get("task_id"),
                target_id=request.POST.get("target_id"),
                placement=request.POST.get("placement"),
            )
        except (Task.DoesNotExist, ValidationError):
            pass
        return redirect(f"{request.path}?sort=manual")
    elif form_name == "task_list_reorder":
        try:
            move_task_list(
                request.user,
                task_list_id=request.POST.get("task_list_id"),
                target_id=request.POST.get("target_id"),
                placement=request.POST.get("placement"),
            )
        except (TaskList.DoesNotExist, ValidationError):
            pass
        return redirect(request.get_full_path())

    sort = request.GET.get("sort")
    context = get_tasks_context(request.user, sort=sort)
    annotate_task_items(context["tasks"], request.user)
    context["running_timer"] = running_timer_context(request.user)
    context["sort"] = sort
    context["task_form"] = task_form
    context["task_list_form"] = TaskListForm()
    context["task_label_form"] = TaskLabelForm()
    # Shares the page with the add form, so its fields get their own ids for <label for>.
    context["edit_form"] = edit_form or TaskForm(user=request.user, auto_id="id_edit_%s")
    # Editing keeps the title as typed, so the quick-add example belongs to the add form only.
    context["edit_form"].fields["title"].widget.attrs["placeholder"] = "Titel der Aufgabe"
    context["editing_task_id"] = editing_task_id
    return render(request, "app/tasks.html", context)


@login_required
def task_time_report(request):
    """Weekly (``?week=``) or monthly (``?month=``) time report, with manual entries and CSV download."""
    if not feature_enabled("tasks"):
        return disabled_feature_response(request, "tasks")

    entry_form = TaskTimeEntryForm(user=request.user)
    if request.method == "POST":
        form_name = request.POST.get("form_name")
        if form_name == "time_entry_add":
            entry_form = TaskTimeEntryForm(request.POST, user=request.user)
            if entry_form.is_valid():
                try:
                    add_manual_entry(
                        request.user,
                        entry_form.cleaned_data["task"],
                        entry_form.cleaned_data["day"],
                        entry_form.cleaned_data["minutes"],
                    )
                except ValueError as error:
                    entry_form.add_error(None, str(error))
                else:
                    django_messages.success(request, "Zeit nachgetragen.")
                    return redirect(request.get_full_path())
        elif form_name == "time_entry_delete":
            if delete_entry(request.user, _positive_int(request.POST.get("entry_id"))):
                django_messages.success(request, "Eintrag gelöscht.")
            return redirect(request.get_full_path())
        elif form_name == "timer_stop":
            stop_timer(request.user)
            return redirect(request.get_full_path())

    if "month" in request.GET:
        report = time_report(request.user, "month", month_start_for(request.GET["month"], request.user))
    else:
        report = time_report(request.user, "week", week_start_for(request.GET.get("week"), request.user))
    if request.GET.get("format") == "csv":
        response = HttpResponse(time_report_csv(report), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = content_disposition_header(
            True, f"lunora-zeiten-{report['value']}.csv"
        )
        return response
    return render(
        request,
        "app/tasks_time.html",
        {
            "active_page": "tasks",
            "report": report,
            "entry_form": entry_form,
            "running_timer": running_timer_context(request.user),
        },
    )


def _positive_int(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 0
    return max(value, 0)


def _created_message(task, quick_add_result, user):
    """Confirm a new task; when its title was read as quick-add text, say what was recognized."""
    if quick_add_result is None:
        return "Aufgabe erstellt."
    details = []
    if task.due_at:
        details.append(f"fällig {format_user_datetime(task.due_at, user)}")
    if quick_add_result.priority:
        details.append(f"Priorität {task.get_priority_display()}")
    details.extend(f"#{label.name}" for label in quick_add_result.labels)
    return f"Aufgabe „{task.title}“ erstellt – {' · '.join(details)}."
