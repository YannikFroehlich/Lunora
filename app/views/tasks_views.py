from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from app.forms import TaskForm
from app.models import Task
from app.services.system_settings import disabled_feature_response, feature_enabled
from app.view_models import get_tasks_context


@login_required
def tasks(request):
    if not feature_enabled("tasks"):
        return disabled_feature_response(request, "tasks")

    if request.method == "POST" and request.POST.get("form_name") == "task_add":
        task_form = TaskForm(request.POST)
        if task_form.is_valid():
            task = task_form.save(commit=False)
            task.user = request.user
            task.save()
            django_messages.success(request, "Aufgabe erstellt.")
            return redirect(request.get_full_path())
    elif request.method == "POST" and request.POST.get("form_name") == "task_toggle":
        task = Task.objects.filter(user=request.user, pk=request.POST.get("task_id")).first()
        if task:
            task.is_done = request.POST.get("is_done") == "on"
            task.save(update_fields=["is_done", "updated_at"])
        return redirect(request.get_full_path())
    elif request.method == "POST" and request.POST.get("form_name") == "task_delete":
        deleted_count, _details = Task.objects.filter(
            user=request.user,
            pk=request.POST.get("task_id"),
        ).delete()
        if deleted_count:
            django_messages.success(request, "Aufgabe gelöscht.")
        return redirect(request.get_full_path())
    else:
        task_form = TaskForm()

    context = get_tasks_context(request.user)
    context["task_form"] = task_form
    return render(request, "app/tasks.html", context)
