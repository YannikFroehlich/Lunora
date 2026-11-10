"""Trash for deleted tasks and calendar events.

Deleting stores a Django serializer snapshot of the rows in a TrashedItem and then deletes them as
before, so every task and event query stays free of trash filters. Restoring re-inserts the rows
with their old primary keys (calendar feed UIDs and inbox source keys stay valid) after dropping
references to anything that disappeared meanwhile: lists, labels, a parent task, other people.
"""

import json

from django.contrib.auth import get_user_model
from django.core import serializers
from django.db import IntegrityError, transaction
from django.utils import timezone

from app.models import (
    CalendarEvent,
    CalendarEventAttendee,
    CalendarEventComment,
    Task,
    TaskComment,
    TaskLabel,
    TaskList,
    TaskShare,
    TaskTimeEntry,
    TrashedItem,
)
from app.services.calendar_service import sync_event_reminders
from app.services.notes import TRASH_RETENTION
from app.services.system_settings import feature_enabled
from app.services.user_preferences import format_user_date, format_user_datetime

KIND_ICONS = {
    TrashedItem.KIND_TASK: "fa-regular fa-square-check",
    TrashedItem.KIND_EVENT: "fa-regular fa-calendar",
}
# Deleting events is gated by event creation, so is getting them back.
KIND_FLAGS = {TrashedItem.KIND_TASK: "tasks", TrashedItem.KIND_EVENT: "calendar_event_creation"}


class RestoreError(Exception):
    pass


def enabled_kinds():
    return [kind for kind, flag in KIND_FLAGS.items() if feature_enabled(flag)]


def _snapshot(objects):
    return json.loads(serializers.serialize("json", objects))


@transaction.atomic
def trash_task(user, task_id):
    """Move the user's own task with its subtasks, shares, comments and tracked time to the trash."""
    task = Task.objects.select_for_update().filter(user=user, pk=task_id).first()
    if task is None:
        return None
    tasks = [task, *task.subtasks.all()]
    now = timezone.now()
    entries = list(TaskTimeEntry.objects.filter(task__in=tasks))
    for entry in entries:
        # A timer can't keep running in the trash; it comes back stopped at the deletion.
        entry.ended_at = entry.ended_at or now
    payload = _snapshot(
        [
            *tasks,
            *TaskShare.objects.filter(task__in=tasks),
            *TaskComment.objects.filter(task__in=tasks),
            *entries,
        ]
    )
    item = TrashedItem.objects.create(
        user=user,
        kind=TrashedItem.KIND_TASK,
        title=task.title,
        item_count=len(tasks),
        payload=payload,
        deleted_at=now,
    )
    task.delete()
    return item


@transaction.atomic
def trash_events(user, events):
    """Move the user's own events of the queryset (one, or part of a series) with their invitees and
    comments to the trash as one item. Events of subscribed calendars are never touched."""
    events = list(events.filter(user=user, subscription=None).select_for_update().order_by("start_at", "pk"))
    if not events:
        return None
    payload = _snapshot(
        [
            *events,
            *CalendarEventAttendee.objects.filter(event__in=events),
            *CalendarEventComment.objects.filter(event__in=events),
        ]
    )
    item = TrashedItem.objects.create(
        user=user,
        kind=TrashedItem.KIND_EVENT,
        title=events[0].title,
        item_count=len(events),
        payload=payload,
    )
    CalendarEvent.objects.filter(pk__in=[event.pk for event in events]).delete()
    return item


def _restorable(user, item, deserialized):
    """The snapshot objects that can go back, with references to vanished rows cut."""
    objects = [entry.object for entry in deserialized]
    person_ids = {
        getattr(obj, name)
        for obj in objects
        for name in ("user_id", "author_id", "assignee_id", "invited_by_id")
        if getattr(obj, name, None)
    }
    people = set(get_user_model().objects.filter(pk__in=person_ids).values_list("pk", flat=True))
    lists = set(TaskList.objects.filter(owner=user).values_list("pk", flat=True))
    labels = set(TaskLabel.objects.filter(owner=user).values_list("pk", flat=True))
    task_ids = {obj.pk for obj in objects if isinstance(obj, Task) and obj.user_id == user.pk}
    event_ids = {obj.pk for obj in objects if isinstance(obj, CalendarEvent) and obj.user_id == user.pk}
    parent_ids = {obj.parent_id for obj in objects if isinstance(obj, Task) and obj.parent_id}
    # Parents outside the snapshot must still be the user's own top-level tasks.
    parents = task_ids | set(
        Task.objects.filter(user=user, parent=None, pk__in=parent_ids - task_ids).values_list("pk", flat=True)
    )

    kept = []
    for entry in deserialized:
        obj = entry.object
        if isinstance(obj, Task):
            if obj.pk not in task_ids:
                continue
            obj.task_list_id = obj.task_list_id if obj.task_list_id in lists else None
            obj.parent_id = obj.parent_id if obj.parent_id in parents else None
            obj.assignee_id = obj.assignee_id if obj.assignee_id in people and obj.parent_id is None else None
            entry.m2m_data = {"labels": [pk for pk in entry.m2m_data.get("labels", []) if pk in labels]}
        elif isinstance(obj, CalendarEvent):
            if obj.pk not in event_ids:
                continue
            obj.subscription_id = None
        elif isinstance(obj, (TaskShare, TaskTimeEntry)):
            if obj.task_id not in task_ids or obj.user_id not in people:
                continue
            if isinstance(obj, TaskTimeEntry):
                obj.ended_at = obj.ended_at or item.deleted_at
        elif isinstance(obj, TaskComment):
            if obj.task_id not in task_ids or obj.author_id not in people:
                continue
        elif isinstance(obj, CalendarEventAttendee):
            if obj.event_id not in event_ids or obj.user_id not in people:
                continue
            obj.invited_by_id = obj.invited_by_id if obj.invited_by_id in people else None
        elif isinstance(obj, CalendarEventComment):
            if obj.event_id not in event_ids or obj.author_id not in people:
                continue
        else:
            continue
        kept.append(entry)
    return kept


def restore_item(user, item_id):
    """Put a trashed item of the user back. Returns the item (now deleted), None when there is no
    such item, and raises RestoreError when its rows can't go back."""
    with transaction.atomic():
        item = TrashedItem.objects.select_for_update().filter(user=user, pk=item_id).first()
        if item is None or item.kind not in enabled_kinds():
            return None
        deserialized = list(serializers.deserialize("python", item.payload, ignorenonexistent=True))
        restored = _restorable(user, item, deserialized)
        try:
            with transaction.atomic():
                for entry in restored:
                    # force_insert: a raw save would otherwise overwrite a row that took the key.
                    entry.save(force_insert=True)
        except IntegrityError as error:
            raise RestoreError("Der Eintrag konnte nicht wiederhergestellt werden.") from error
        sync_event_reminders([entry.object for entry in restored if isinstance(entry.object, CalendarEvent)])
        item.delete()
    return item


def delete_item(user, item_id):
    return bool(TrashedItem.objects.filter(user=user, pk=item_id, kind__in=enabled_kinds()).delete()[0])


def empty_trash(user):
    return TrashedItem.objects.filter(user=user, kind__in=enabled_kinds()).delete()[0]


def purge_expired_trash(*, now=None):
    cutoff = (now or timezone.now()) - TRASH_RETENTION
    return TrashedItem.objects.filter(deleted_at__lte=cutoff).delete()[0]


def trash_items(user):
    items = []
    for item in TrashedItem.objects.filter(user=user, kind__in=enabled_kinds()).defer("payload"):
        extra = item.item_count - 1
        kind_label = item.get_kind_display()
        if item.kind == TrashedItem.KIND_TASK:
            detail = f"mit {extra} Unteraufgabe{'n' if extra != 1 else ''}" if extra else ""
        elif extra:
            kind_label, detail = "Terminserie", f"mit {item.item_count} Terminen"
        else:
            detail = ""
        items.append(
            {
                "id": item.pk,
                "kind": item.kind,
                "kind_label": kind_label,
                "icon": KIND_ICONS[item.kind],
                "title": item.title,
                "detail": detail,
                "deleted_label": format_user_datetime(item.deleted_at, user),
                "expires_label": format_user_date(item.deleted_at + TRASH_RETENTION, user),
            }
        )
    return items
