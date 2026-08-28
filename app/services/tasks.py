import calendar
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db.models import F, Max
from django.utils import timezone

from app.models import Task, TaskLabel, TaskList
from app.services.user_preferences import format_user_datetime, format_user_time, localtime_for_user


UPCOMING_WINDOW_DAYS = 7


def _add_months(value, months):
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _step_recurrence(due_at, rule):
    if rule == "DAILY":
        return due_at + timedelta(days=1)
    if rule == "WEEKLY":
        return due_at + timedelta(weeks=1)
    if rule == "MONTHLY":
        return _add_months(due_at, 1)
    if rule == "YEARLY":
        return _add_months(due_at, 12)
    return due_at


def toggle_task(user, task_id, is_done, *, now=None):
    """Toggle a task done/open; completing a recurring task also spawns its next occurrence."""
    task = Task.objects.filter(user=user, pk=task_id).first()
    if not task:
        return None
    task.is_done = is_done
    task.save(update_fields=["is_done", "updated_at"])

    if is_done and task.recurrence_rule != Task.RECURRENCE_NONE:
        current_time = now or timezone.now()
        base_due = task.due_at or current_time
        next_task = Task.objects.create(
            user=user,
            task_list=task.task_list,
            parent=task.parent,
            title=task.title,
            due_at=_step_recurrence(base_due, task.recurrence_rule),
            priority=task.priority,
            recurrence_rule=task.recurrence_rule,
        )
        next_task.labels.set(task.labels.all())
    return task


def delete_task(user, task_id):
    deleted, _details = Task.objects.filter(user=user, pk=task_id).delete()
    return bool(deleted)


def create_task_list(user, *, name, color=None):
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValidationError("Der Listenname darf nicht leer sein.")
    if TaskList.objects.filter(owner=user, name__iexact=clean_name).exists():
        raise ValidationError("Es gibt bereits eine Liste mit diesem Namen.")
    next_position = (TaskList.objects.filter(owner=user).aggregate(Max("position"))["position__max"] or 0) + 1
    return TaskList.objects.create(owner=user, name=clean_name, color=color or "blue", position=next_position)


def rename_task_list(user, task_list_id, *, name):
    task_list = TaskList.objects.get(owner=user, pk=task_list_id)
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValidationError("Der Listenname darf nicht leer sein.")
    if TaskList.objects.filter(owner=user, name__iexact=clean_name).exclude(pk=task_list.pk).exists():
        raise ValidationError("Es gibt bereits eine Liste mit diesem Namen.")
    task_list.name = clean_name
    task_list.save(update_fields=["name"])
    return task_list


def delete_task_list(user, task_list_id):
    task_list = TaskList.objects.get(owner=user, pk=task_list_id)
    task_list.delete()


def create_task_label(user, *, name, color=None):
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValidationError("Der Labelname darf nicht leer sein.")
    if len(clean_name) > 40:
        raise ValidationError("Der Labelname ist zu lang.")
    if TaskLabel.objects.filter(owner=user, name__iexact=clean_name).exists():
        raise ValidationError("Es gibt bereits ein Label mit diesem Namen.")
    return TaskLabel.objects.create(owner=user, name=clean_name, color=color or "blue")


def delete_task_label(user, task_label_id):
    label = TaskLabel.objects.get(owner=user, pk=task_label_id)
    label.delete()


def _task_due_detail(task, now, user):
    if not task.due_at:
        return "Ohne Fälligkeitsdatum"

    due_at = localtime_for_user(task.due_at, user)
    if due_at.date() == now.date():
        return f"Heute {format_user_time(due_at, user)}"
    if due_at.date() == now.date() + timedelta(days=1):
        return f"Morgen {format_user_time(due_at, user)}"
    return format_user_datetime(due_at, user)


def _task_status_label(task, now, user):
    if task.is_done:
        return "Erledigt"
    if not task.due_at:
        return "Offen"

    due_at = localtime_for_user(task.due_at, user)
    if due_at < now:
        return "Überfällig"

    days_until_due = (due_at.date() - now.date()).days
    if days_until_due == 0:
        return "Heute"
    if days_until_due == 1:
        return "Morgen"
    if days_until_due <= 14:
        return f"In {days_until_due} Tagen"
    return "Geplant"


def _task_status_tone(task, now, user):
    if task.is_done:
        return "done"
    if not task.due_at:
        return "neutral"

    due_at = localtime_for_user(task.due_at, user)
    if due_at < now:
        return "danger"
    if due_at.date() == now.date():
        return "today"
    return "upcoming"


def _task_due_label(task, now, user):
    if task.is_done:
        return "Erledigt"
    if not task.due_at:
        return "Ohne Fälligkeitsdatum"

    due_at = localtime_for_user(task.due_at, user)
    today = now.date()
    if due_at < now:
        return f"Überfällig seit {format_user_datetime(due_at, user)}"
    if due_at.date() == today:
        return f"Heute {format_user_time(due_at, user)}"
    if due_at.date() == today + timedelta(days=1):
        return f"Morgen {format_user_time(due_at, user)}"
    return format_user_datetime(due_at, user)


def _task_due_state(task, now, user):
    if task.is_done:
        return "is-done"
    if not task.due_at:
        return ""
    due_at = localtime_for_user(task.due_at, user)
    if due_at < now:
        return "is-overdue"
    if due_at.date() == now.date():
        return "is-due-today"
    return ""


def _task_view_bucket(task, now, user):
    """'today' covers due-today and overdue-and-open; 'upcoming' covers the next 7 days."""
    if task.is_done or not task.due_at:
        return ""
    due_date = localtime_for_user(task.due_at, user).date()
    if due_date <= now.date():
        return "today"
    if due_date <= now.date() + timedelta(days=UPCOMING_WINDOW_DAYS):
        return "upcoming"
    return ""


def dashboard_open_tasks(user, now, limit=5):
    tasks = Task.objects.filter(user=user, is_done=False).order_by(
        F("due_at").asc(nulls_last=True), "-created_at"
    )[:limit]
    return [
        {
            "title": task.title,
            "due_label": _task_due_label(task, now, user),
            "due_state": _task_due_state(task, now, user),
            "priority": task.priority,
        }
        for task in tasks
    ]


def dashboard_today_tasks(user, now, limit=5):
    """Open tasks due today or overdue, for the dashboard notification widget."""
    candidates = Task.objects.filter(user=user, is_done=False, due_at__isnull=False).order_by(
        F("due_at").asc(nulls_last=True)
    )
    today_tasks = [task for task in candidates if _task_view_bucket(task, now, user) == "today"][:limit]
    return [
        {
            "id": task.id,
            "title": task.title,
            "due_label": _task_due_label(task, now, user),
            "due_state": _task_due_state(task, now, user),
            "priority": task.priority,
        }
        for task in today_tasks
    ]


def get_tasks_context(user, *, now=None):
    now = now or localtime_for_user(profile_or_user=user)

    all_tasks = list(
        Task.objects.filter(user=user)
        .select_related("task_list")
        .prefetch_related("labels")
        .order_by("is_done", F("due_at").asc(nulls_last=True), "-created_at")
    )

    subtasks_by_parent = {}
    top_level_tasks = []
    for task in all_tasks:
        if task.parent_id:
            subtasks_by_parent.setdefault(task.parent_id, []).append(task)
        else:
            top_level_tasks.append(task)

    counts = {"all": 0, "open": 0, "done": 0, "overdue": 0, "today": 0, "upcoming": 0}
    task_items = []

    def build_item(task, *, is_subtask, filter_view=None, filter_task_list_id=None):
        due_state = _task_due_state(task, now, user)
        state = "done" if task.is_done else "overdue" if due_state == "is-overdue" else "open"
        counts["all"] += 1
        counts[state] += 1
        if state == "overdue":
            counts["open"] += 1
        view_bucket = _task_view_bucket(task, now, user)
        if view_bucket:
            counts[view_bucket] += 1

        return {
            "task": task,
            "title": task.title,
            "is_done": task.is_done,
            "due_label": _task_due_detail(task, now, user),
            "due_state": due_state,
            "state": state,
            "status_label": _task_status_label(task, now, user),
            "status_tone": _task_status_tone(task, now, user),
            "due_sort": task.due_at.isoformat() if task.due_at else "",
            "created_sort": task.created_at.isoformat(),
            "priority": task.priority,
            "task_list_id": task.task_list_id,
            "task_list_name": task.task_list.name if task.task_list else "",
            "task_list_color": task.task_list.color if task.task_list else "",
            "labels": [{"id": label.id, "name": label.name, "color": label.color} for label in task.labels.all()],
            "is_subtask": is_subtask,
            "parent_id": task.parent_id,
            "view": view_bucket,
            # A subtask row filters (by sidebar view/list) as part of its parent, so it
            # never appears orphaned when its parent is filtered out; its own badge/status
            # above still reflects its own due date.
            "filter_view": view_bucket if filter_view is None else filter_view,
            "filter_task_list_id": task.task_list_id if filter_task_list_id is None else filter_task_list_id,
        }

    for task in top_level_tasks:
        children = subtasks_by_parent.get(task.id, [])
        item = build_item(task, is_subtask=False)
        item["subtasks"] = [
            build_item(child, is_subtask=True, filter_view=item["view"], filter_task_list_id=item["task_list_id"])
            for child in children
        ]
        item["subtask_count"] = len(children)
        item["open_subtask_count"] = sum(1 for child in children if not child.is_done)
        task_items.append(item)

    task_lists = [
        {
            "id": task_list.id,
            "name": task_list.name,
            "color": task_list.color,
            "open_count": sum(
                1 for item in task_items if item["task_list_id"] == task_list.id and not item["is_done"]
            ),
        }
        for task_list in TaskList.objects.filter(owner=user)
    ]
    inbox_open_count = sum(1 for item in task_items if item["task_list_id"] is None and not item["is_done"])

    return {
        "active_page": "tasks",
        "tasks": task_items,
        "task_counts": counts,
        "task_lists": task_lists,
        "inbox_open_count": inbox_open_count,
        "task_labels": list(TaskLabel.objects.filter(owner=user)),
    }
