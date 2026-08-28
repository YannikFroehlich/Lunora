from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from app.forms import TaskForm, TaskLabelForm, TaskListForm
from app.models import Task, TaskLabel, TaskList
from app.services.system_settings import disabled_feature_response, feature_enabled
from app.services.tasks import (
    create_task_label,
    create_task_list,
    delete_task,
    delete_task_label,
    delete_task_list,
    get_tasks_context,
    rename_task_list,
    toggle_task,
)


@login_required
def tasks(request):
    if not feature_enabled("tasks"):
        return disabled_feature_response(request, "tasks")

    form_name = request.POST.get("form_name") if request.method == "POST" else None

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
            django_messages.success(request, "Aufgabe erstellt.")
            return redirect(request.get_full_path())
    elif form_name == "task_toggle":
        toggle_task(request.user, request.POST.get("task_id"), request.POST.get("is_done") == "on")
        return_to = request.POST.get("return_to")
        if (
            return_to
            and return_to.startswith("/")
            and url_has_allowed_host_and_scheme(
                return_to,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            )
        ):
            return redirect(return_to)
        return redirect(request.get_full_path())
    elif form_name == "task_delete":
        if delete_task(request.user, request.POST.get("task_id")):
            django_messages.success(request, "Aufgabe gelöscht.")
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
    else:
        task_form = TaskForm(user=request.user)

    context = get_tasks_context(request.user)
    context["task_form"] = task_form
    context["task_list_form"] = TaskListForm()
    context["task_label_form"] = TaskLabelForm()
    return render(request, "app/tasks.html", context)
