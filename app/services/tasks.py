import calendar
from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Max, Q
from django.utils import timezone

from app.models import (
    Task,
    TaskComment,
    TaskLabel,
    TaskList,
    TaskShare,
    TaskTemplate,
    TaskTimeEntry,
    UserNotification,
)
from app.services.notes import display_name
from app.services.user_preferences import (
    format_user_datetime,
    format_user_time,
    get_user_zoneinfo,
    localtime_for_user,
)

UPCOMING_WINDOW_DAYS = 7
MAX_TASK_TEMPLATES = 50
TASK_POSITION_STEP = 1000
# Time of day for a task that only gets a date: dropped onto a calendar day, or typed as "morgen".
DEFAULT_DUE_TIME = time(9)


def _add_months(value, months):
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _step_recurrence(due_at, rule, interval=1, weekdays=""):
    """The next due date of a recurring task, stepped in ``due_at``'s own (local) time zone so the
    time of day survives DST changes."""
    if rule == "DAILY":
        return due_at + timedelta(days=interval)
    if rule == "WEEKLY":
        days = sorted(int(day) for day in weekdays.split(",") if day)
        later = [day for day in days if day > due_at.weekday()]
        if later:
            return due_at + timedelta(days=later[0] - due_at.weekday())
        if days:
            # Past this week's last weekday: the first one of the week ``interval`` weeks on.
            return due_at + timedelta(days=7 * interval - due_at.weekday() + days[0])
        return due_at + timedelta(weeks=interval)
    if rule == "MONTHLY":
        return _add_months(due_at, interval)
    if rule == "YEARLY":
        return _add_months(due_at, 12 * interval)
    return due_at


def _shared_task_ids(user):
    return TaskShare.objects.filter(user=user).values("task_id")


def visible_tasks(user):
    """The user's own tasks plus top-level tasks assigned to or shared with them, with their subtasks.

    Shares are matched through a subquery rather than a join, so a task never shows up twice.
    """
    shared = _shared_task_ids(user)
    return Task.objects.filter(
        Q(user=user)
        | Q(assignee=user)
        | Q(parent__assignee=user)
        | Q(pk__in=shared)
        | Q(parent_id__in=shared)
    )


def task_participants(task):
    """Everyone involved in a top-level task: owner, assignee and the people it is shared with."""
    user_ids = {task.user_id, *task.shares.values_list("user_id", flat=True)}
    if task.assignee_id:
        user_ids.add(task.assignee_id)
    return user_ids


def toggle_task(user, task_id, is_done, *, now=None):
    """Toggle a task done/open; completing a recurring task also spawns its next occurrence.

    The assignee and people the task is shared with may tick it off too; the next occurrence stays
    the owner's and keeps the assignee and shares.
    """
    task = visible_tasks(user).filter(pk=task_id).first()
    if not task:
        return None
    return _apply_task_status(task, Task.STATUS_DONE if is_done else Task.STATUS_OPEN, now=now)


def set_task_status(user, task_id, status, *, now=None):
    """Move a visible task to open, in progress or done (the board's columns)."""
    if status not in dict(Task.STATUS_CHOICES):
        return None
    task = visible_tasks(user).filter(pk=task_id).first()
    if not task:
        return None
    return _apply_task_status(task, status, now=now)


def _apply_task_status(task, status, *, now=None):
    was_done = task.is_done
    task.is_done = status == Task.STATUS_DONE
    task.is_in_progress = status == Task.STATUS_IN_PROGRESS
    task.save(update_fields=["is_done", "is_in_progress", "updated_at"])
    is_done = task.is_done and not was_done
    if is_done:
        # Finishing a task stops every timer still running on it.
        TaskTimeEntry.objects.filter(task=task, ended_at__isnull=True).update(ended_at=now or timezone.now())

    if is_done and task.recurrence_rule != Task.RECURRENCE_NONE:
        current_time = now or timezone.now()
        base_due = (task.due_at or current_time).astimezone(get_user_zoneinfo(task.user))
        next_task = Task.objects.create(
            user=task.user,
            assignee=task.assignee,
            task_list=task.task_list,
            parent=task.parent,
            title=task.title,
            due_at=_step_recurrence(
                base_due, task.recurrence_rule, task.recurrence_interval, task.recurrence_weekdays
            ),
            priority=task.priority,
            recurrence_rule=task.recurrence_rule,
            recurrence_interval=task.recurrence_interval,
            recurrence_weekdays=task.recurrence_weekdays,
        )
        next_task.labels.set(task.labels.all())
        TaskShare.objects.bulk_create(
            [TaskShare(task=next_task, user_id=share.user_id) for share in task.shares.all()]
        )
    return task


@transaction.atomic
def update_task_shares(task, users):
    """Share a top-level task with exactly ``users``; returns the newly created share rows.

    People who lose access also lose the inbox row about the share, so sharing again notifies again.
    """
    wanted = {user.pk for user in users} - {task.user_id}
    existing = set(task.shares.values_list("user_id", flat=True))
    removed = existing - wanted
    if removed:
        task.shares.filter(user_id__in=removed).delete()
        UserNotification.objects.filter(
            source_key__in=[f"task-share:{task.pk}:{user_id}" for user_id in removed]
        ).delete()
    return TaskShare.objects.bulk_create(
        [TaskShare(task=task, user_id=user_id) for user_id in sorted(wanted - existing)]
    )


def leave_task_share(user, task_id):
    """Someone a task was shared with removes it from their own tasks; returns the task or None."""
    share = TaskShare.objects.filter(user=user, task_id=task_id).select_related("task__user").first()
    if share is None:
        return None
    share.delete()
    UserNotification.objects.filter(source_key=f"task-share:{task_id}:{user.pk}").delete()
    return share.task


def _clean_comment_body(body):
    clean_body = (body or "").strip()
    if not clean_body:
        raise ValidationError("Der Kommentar darf nicht leer sein.")
    if len(clean_body) > 2000:
        raise ValidationError("Der Kommentar ist zu lang (höchstens 2000 Zeichen).")
    return clean_body


def _own_task_comments(user):
    """Comments the user wrote on tasks they still own, are assigned to or share."""
    return TaskComment.objects.filter(author=user).filter(
        Q(task__user=user) | Q(task__assignee=user) | Q(task_id__in=_shared_task_ids(user))
    )


def add_task_comment(user, task_id, body):
    """Comment on an assigned or shared top-level task as one of its participants; None otherwise."""
    clean_body = _clean_comment_body(body)
    task = (
        Task.objects.filter(
            Q(user=user) | Q(assignee=user) | Q(pk__in=_shared_task_ids(user)),
            Q(assignee__isnull=False) | Q(shares__isnull=False),
            pk=task_id,
            parent__isnull=True,
        )
        .select_related("user")
        .first()
    )
    if task is None:
        return None
    return TaskComment.objects.create(task=task, author=user, body=clean_body)


def edit_task_comment(user, comment_id, body):
    """Change one of the user's own comments; the other side's unread notification follows along."""
    clean_body = _clean_comment_body(body)
    comment = _own_task_comments(user).filter(pk=comment_id).first()
    if comment is None:
        return None
    if comment.body != clean_body:
        comment.body = clean_body
        comment.edited_at = timezone.now()
        comment.save(update_fields=["body", "edited_at"])
        UserNotification.objects.filter(source_key=f"task-comment:{comment.pk}", read_at__isnull=True).update(
            body=clean_body[:300]
        )
    return comment


def delete_task_comment(user, comment_id):
    """Delete one of the user's own comments together with its notification; returns its task id."""
    comment = _own_task_comments(user).filter(pk=comment_id).first()
    if comment is None:
        return None
    UserNotification.objects.filter(source_key=f"task-comment:{comment.pk}").delete()
    comment.delete()
    return comment.task_id


def return_task(user, task_id):
    """The assignee declines or hands back an assigned task; it becomes the owner's alone again."""
    task = Task.objects.filter(assignee=user, pk=task_id).select_related("user").first()
    if task is None:
        return None
    task.assignee = None
    task.save(update_fields=["assignee", "updated_at"])
    # Assigning the same person again later should notify them again.
    UserNotification.objects.filter(source_key=f"task-assignment:{task.pk}:{user.pk}").delete()
    return task


def save_task_template(user, task_id):
    """Save an own top-level task with its subtasks (as a checklist) as a reusable template."""
    task = Task.objects.filter(user=user, pk=task_id, parent__isnull=True).first()
    if task is None:
        return None
    if TaskTemplate.objects.filter(owner=user).count() >= MAX_TASK_TEMPLATES:
        raise ValidationError(f"Du kannst höchstens {MAX_TASK_TEMPLATES} Vorlagen speichern.")
    template = TaskTemplate.objects.create(
        owner=user,
        title=task.title,
        priority=task.priority or Task.PRIORITY_NONE,
        task_list=task.task_list,
        subtask_titles=list(task.subtasks.order_by("created_at", "id").values_list("title", flat=True)),
    )
    template.labels.set(task.labels.all())
    return template


@transaction.atomic
def create_task_from_template(user, template_id, *, due_at=None):
    """Create a fresh open task (and its checklist of subtasks) from one of the user's templates."""
    template = TaskTemplate.objects.filter(owner=user, pk=template_id).first()
    if template is None:
        return None
    task = Task.objects.create(
        user=user,
        title=template.title,
        priority=template.priority,
        task_list=template.task_list,
        due_at=due_at,
    )
    task.labels.set(template.labels.all())
    # One at a time, so each subtask gets its own created_at and the checklist keeps its order.
    for title in template.subtask_titles:
        Task.objects.create(user=user, parent=task, title=title)
    return task


def delete_task_template(user, template_id):
    deleted, _details = TaskTemplate.objects.filter(owner=user, pk=template_id).delete()
    return bool(deleted)


def rearm_task_reminders(task, previous_due_at, *, now=None):
    """Let a task that was moved to a later due date remind again, at the new time.

    Clears the delivered-at columns and drops the inbox row of the old due date, so e-mail,
    desktop, Web Push and the inbox all notify once more when the new date arrives. Call it before
    saving the task with its new due_at.
    """
    current_time = now or timezone.now()
    if task.due_at == previous_due_at or task.due_at is None or task.due_at <= current_time:
        return
    task.email_notified_at = None
    task.desktop_notified_at = None
    if task.pk:
        # Owner or assignee may hold the row, depending on who was responsible back then.
        UserNotification.objects.filter(source_key=f"task:{task.pk}").delete()


def reschedule_task(user, task_id, *, due_date, due_time=None, now=None):
    """Move an open task to another day (e.g. dropped onto a calendar day), keeping its time of day
    unless a new time is given; a task without a due date gets DEFAULT_DUE_TIME."""
    task = Task.objects.filter(user=user, pk=task_id, is_done=False).first()
    if task is None:
        return None
    if due_time is None:
        due_time = localtime_for_user(task.due_at, user).time() if task.due_at else DEFAULT_DUE_TIME
    previous_due_at = task.due_at
    task.due_at = datetime.combine(due_date, due_time.replace(tzinfo=None), tzinfo=get_user_zoneinfo(user))
    rearm_task_reminders(task, previous_due_at, now=now)
    task.save(update_fields=["due_at", "email_notified_at", "desktop_notified_at", "updated_at"])
    return task


def _reindex_tasks(tasks):
    for index, task in enumerate(tasks, start=1):
        task.position = index * TASK_POSITION_STEP
    Task.objects.bulk_update(tasks, ["position"])


@transaction.atomic
def move_task(user, *, task_id, target_id, placement):
    """Reorder a task relative to a sibling. Only tasks at the same level (both
    top-level, or both subtasks of the same parent) can be reordered against
    each other — dragging across levels would silently change the hierarchy.
    """
    if placement not in {"before", "after"}:
        raise ValidationError("Das Verschiebeziel ist ungültig.")
    task = Task.objects.select_for_update().get(user=user, pk=task_id)
    target = Task.objects.select_for_update().get(user=user, pk=target_id)
    if task.parent_id != target.parent_id:
        raise ValidationError("Aufgaben können nur innerhalb derselben Ebene sortiert werden.")

    siblings = list(
        Task.objects.filter(user=user, parent_id=task.parent_id)
        .exclude(pk=task.id)
        .select_for_update()
        .order_by("position", "id")
    )
    target_index = next(index for index, sibling in enumerate(siblings) if sibling.id == target.id)
    insertion_index = target_index if placement == "before" else target_index + 1
    siblings.insert(insertion_index, task)
    _reindex_tasks(siblings)


def _reindex_task_lists(task_lists):
    for index, task_list in enumerate(task_lists, start=1):
        task_list.position = index * TASK_POSITION_STEP
    TaskList.objects.bulk_update(task_lists, ["position"])


@transaction.atomic
def move_task_list(user, *, task_list_id, target_id, placement):
    if placement not in {"before", "after"}:
        raise ValidationError("Das Verschiebeziel ist ungültig.")
    task_list = TaskList.objects.select_for_update().get(owner=user, pk=task_list_id)
    target = TaskList.objects.select_for_update().get(owner=user, pk=target_id)

    siblings = list(
        TaskList.objects.filter(owner=user)
        .exclude(pk=task_list.id)
        .select_for_update()
        .order_by("position", "id")
    )
    target_index = next(index for index, sibling in enumerate(siblings) if sibling.id == target.id)
    insertion_index = target_index if placement == "before" else target_index + 1
    siblings.insert(insertion_index, task_list)
    _reindex_task_lists(siblings)


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
    tasks = (
        visible_tasks(user)
        .filter(is_done=False)
        .order_by(F("due_at").asc(nulls_last=True), "-created_at")[:limit]
    )
    return [
        {
            "id": task.id,
            "title": task.title,
            "due_label": _task_due_label(task, now, user),
            "due_state": _task_due_state(task, now, user),
            "priority": task.priority,
        }
        for task in tasks
    ]


def dashboard_today_tasks(user, now, limit=5):
    """Open tasks due today or overdue, for the calendar sidebar."""
    candidates = (
        visible_tasks(user)
        .filter(is_done=False, due_at__isnull=False)
        .order_by(F("due_at").asc(nulls_last=True))
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


def get_tasks_context(user, *, now=None, sort=None):
    now = now or localtime_for_user(profile_or_user=user)

    order_by = (
        ("is_done", "position", "id")
        if sort == "manual"
        else ("is_done", F("due_at").asc(nulls_last=True), "-created_at")
    )
    all_tasks = list(
        visible_tasks(user)
        .select_related("task_list", "user__profile", "assignee__profile", "parent")
        .prefetch_related("labels", "comments__author__profile", "shares__user__profile", "parent__shares")
        .order_by(*order_by)
    )

    subtasks_by_parent = {}
    top_level_tasks = []
    for task in all_tasks:
        if task.parent_id:
            subtasks_by_parent.setdefault(task.parent_id, []).append(task)
        else:
            top_level_tasks.append(task)

    counts = {
        "all": 0,
        "open": 0,
        "done": 0,
        "overdue": 0,
        "today": 0,
        "upcoming": 0,
        "assigned": 0,
        "shared": 0,
        "in_progress": 0,
    }
    task_items = []

    def build_item(task, *, is_subtask, filter_view=None, filter_task_list_id=None):
        due_state = _task_due_state(task, now, user)
        # Assigned tasks (and their subtasks) belong to someone else: the assignee only ticks them
        # off, and the owner's lists and labels mean nothing to them.
        is_own = task.user_id == user.pk
        state = "done" if task.is_done else "overdue" if due_state == "is-overdue" else "open"
        counts["all"] += 1
        counts[state] += 1
        if state == "overdue":
            counts["open"] += 1
        view_bucket = _task_view_bucket(task, now, user)
        if view_bucket:
            counts[view_bucket] += 1
        if task.is_in_progress and not task.is_done:
            counts["in_progress"] += 1
        shares = list(task.shares.all())
        share_owner = task.parent if is_subtask and task.parent else task
        shared_with_me = any(share.user_id == user.pk for share in share_owner.shares.all())

        return {
            "task": task,
            "title": task.title,
            "is_done": task.is_done,
            "status": task.status,
            "status_name": dict(Task.STATUS_CHOICES)[task.status],
            "due_label": _task_due_detail(task, now, user),
            "due_state": due_state,
            "state": state,
            "status_label": _task_status_label(task, now, user),
            "status_tone": _task_status_tone(task, now, user),
            "due_sort": task.due_at.isoformat() if task.due_at else "",
            "created_sort": task.created_at.isoformat(),
            "priority": task.priority,
            "task_list_id": task.task_list_id if is_own else None,
            "task_list_name": task.task_list.name if is_own and task.task_list else "",
            "task_list_color": task.task_list.color if is_own and task.task_list else "",
            "labels": [
                {"id": label.id, "name": label.name, "color": label.color} for label in task.labels.all()
            ]
            if is_own
            else [],
            "is_own": is_own,
            "assignee_id": task.assignee_id,
            "assignee_name": display_name(task.assignee) if task.assignee else "",
            "owner_name": "" if is_own else display_name(task.user),
            "assigned_to_me": task.assignee_id == user.pk
            or (task.parent is not None and task.parent.assignee_id == user.pk),
            "shared_with_me": shared_with_me,
            "shared_names": [display_name(share.user) for share in shares],
            "share_ids": ",".join(str(share.user_id) for share in shares),
            # Only assigned or shared top-level tasks carry a conversation between the people involved.
            "comments": [
                {
                    "id": comment.pk,
                    "author_name": display_name(comment.author),
                    "is_own": comment.author_id == user.pk,
                    "time_label": format_user_datetime(comment.created_at, user),
                    "is_edited": comment.edited_at is not None,
                    "body": comment.body,
                }
                for comment in task.comments.all()
            ]
            if (task.assignee_id or shares) and not is_subtask
            else None,
            "is_subtask": is_subtask,
            "parent_id": task.parent_id,
            "view": view_bucket,
            # A subtask row filters (by sidebar view/list) as part of its parent, so it
            # never appears orphaned when its parent is filtered out; its own badge/status
            # above still reflects its own due date.
            "filter_view": view_bucket if filter_view is None else filter_view,
            "filter_task_list_id": (task.task_list_id if is_own else None)
            if filter_task_list_id is None
            else filter_task_list_id,
        }

    for task in top_level_tasks:
        children = subtasks_by_parent.get(task.id, [])
        item = build_item(task, is_subtask=False)
        item["subtasks"] = [
            build_item(
                child, is_subtask=True, filter_view=item["view"], filter_task_list_id=item["task_list_id"]
            )
            for child in children
        ]
        item["subtask_count"] = len(children)
        item["open_subtask_count"] = sum(1 for child in children if not child.is_done)
        if item["assigned_to_me"] and not task.is_done:
            counts["assigned"] += 1
        if item["shared_with_me"] and not task.is_done:
            counts["shared"] += 1
        task_items.append(item)

    board_columns = [
        {
            "key": key,
            "label": label,
            "items": [item for item in task_items if item["status"] == key],
        }
        for key, label in Task.STATUS_CHOICES
    ]

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
        "task_board_columns": board_columns,
        "task_status_choices": Task.STATUS_CHOICES,
        "task_counts": counts,
        "task_lists": task_lists,
        "inbox_open_count": inbox_open_count,
        "task_labels": list(TaskLabel.objects.filter(owner=user)),
        "task_templates": [
            {"id": template.pk, "title": template.title, "subtask_count": len(template.subtask_titles)}
            for template in TaskTemplate.objects.filter(owner=user)
        ],
    }
