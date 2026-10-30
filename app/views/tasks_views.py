from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from app.forms import TaskForm, TaskLabelForm, TaskListForm, TaskRescheduleForm
from app.models import Task, TaskLabel, TaskList
from app.services.system_settings import disabled_feature_response, feature_enabled
from app.services.tasks import (
    create_task_label,
    create_task_list,
    delete_task,
    delete_task_label,
    delete_task_list,
    get_tasks_context,
    move_task,
    move_task_list,
    rearm_task_reminders,
    rename_task_list,
    reschedule_task,
    toggle_task,
)
from app.services.user_preferences import format_user_datetime
from app.views.navigation import local_return_to


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
            django_messages.success(request, _created_message(task, task_form.quick_add_result, request.user))
            return redirect(request.get_full_path())
    elif form_name == "task_toggle":
        toggle_task(request.user, request.POST.get("task_id"), request.POST.get("is_done") == "on")
        return redirect(local_return_to(request) or request.get_full_path())
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
    elif form_name == "task_delete":
        if delete_task(request.user, request.POST.get("task_id")):
            django_messages.success(request, "Aufgabe gelöscht.")
        return redirect(request.get_full_path())
    elif form_name == "task_edit":
        editing_task_id = request.POST.get("task_id")
        task = Task.objects.filter(user=request.user, pk=editing_task_id).first()
        if task is None:
            django_messages.error(request, "Aufgabe konnte nicht bearbeitet werden.")
            return redirect(request.get_full_path())
        previous_due_at = task.due_at
        edit_form = TaskForm(request.POST, instance=task, user=request.user)
        if edit_form.is_valid():
            edited_task = edit_form.save(commit=False)
            rearm_task_reminders(edited_task, previous_due_at)
            edited_task.priority = edited_task.priority or Task.PRIORITY_NONE
            edited_task.recurrence_rule = edited_task.recurrence_rule or Task.RECURRENCE_NONE
            edited_task.save()
            edit_form.save_m2m()
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
    context["sort"] = sort
    context["task_form"] = task_form
    context["task_list_form"] = TaskListForm()
    context["task_label_form"] = TaskLabelForm()
    context["edit_form"] = edit_form or TaskForm(user=request.user)
    # Editing keeps the title as typed, so the quick-add example belongs to the add form only.
    context["edit_form"].fields["title"].widget.attrs["placeholder"] = "Titel der Aufgabe"
    context["editing_task_id"] = editing_task_id
    return render(request, "app/tasks.html", context)


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
