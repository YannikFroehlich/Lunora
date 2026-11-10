"""Trash for deleted tasks, calendar events, habits, vocabulary lists and recipes.

Deleting stores a Django serializer snapshot of the rows in a TrashedItem and then deletes them as
before, so no query on the live tables needs a trash filter. Restoring re-inserts the rows with
their old primary keys (calendar feed UIDs and inbox source keys stay valid) after dropping
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
    Habit,
    HabitCheck,
    MealPlanEntry,
    Recipe,
    Task,
    TaskComment,
    TaskLabel,
    TaskList,
    TaskShare,
    TaskTimeEntry,
    TrashedItem,
    VocabularyCard,
    VocabularyList,
    VocabularyListShare,
)
from app.services.calendar_service import sync_event_reminders
from app.services.notes import TRASH_RETENTION
from app.services.system_settings import feature_enabled
from app.services.user_preferences import format_user_date, format_user_datetime

KIND_ICONS = {
    TrashedItem.KIND_TASK: "fa-regular fa-square-check",
    TrashedItem.KIND_EVENT: "fa-regular fa-calendar",
    TrashedItem.KIND_HABIT: "fa-solid fa-seedling",
    TrashedItem.KIND_VOCABULARY: "fa-solid fa-language",
    TrashedItem.KIND_RECIPE: "fa-solid fa-utensils",
}
# Each kind is gated by the flag that gates deleting it.
KIND_FLAGS = {
    TrashedItem.KIND_TASK: "tasks",
    TrashedItem.KIND_EVENT: "calendar_event_creation",
    TrashedItem.KIND_HABIT: "tools",
    TrashedItem.KIND_VOCABULARY: "tools",
    TrashedItem.KIND_RECIPE: "tools",
}

# The deleted things themselves and the field holding their owner.
ROOT_OWNER = {
    Task: "user_id",
    CalendarEvent: "user_id",
    Habit: "owner_id",
    VocabularyList: "owner_id",
    Recipe: "owner_id",
}
# Rows hanging off a root: the field pointing at it, and the people who must still exist.
CHILDREN = {
    TaskShare: ("task_id", ("user_id",)),
    TaskComment: ("task_id", ("author_id",)),
    TaskTimeEntry: ("task_id", ("user_id",)),
    CalendarEventAttendee: ("event_id", ("user_id",)),
    CalendarEventComment: ("event_id", ("author_id",)),
    HabitCheck: ("habit_id", ()),
    VocabularyCard: ("vocabulary_list_id", ()),
    VocabularyListShare: ("vocabulary_list_id", ("user_id",)),
    MealPlanEntry: ("recipe_id", ("owner_id",)),
}
PERSON_FIELDS = ("user_id", "author_id", "owner_id", "assignee_id", "invited_by_id")


class RestoreError(Exception):
    pass


def enabled_kinds():
    return [kind for kind, flag in KIND_FLAGS.items() if feature_enabled(flag)]


def _store(user, kind, title, objects, count, *, now=None):
    return TrashedItem.objects.create(
        user=user,
        kind=kind,
        title=title,
        item_count=count,
        payload=json.loads(serializers.serialize("json", objects)),
        deleted_at=now or timezone.now(),
    )


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
    objects = [
        *tasks,
        *TaskShare.objects.filter(task__in=tasks),
        *TaskComment.objects.filter(task__in=tasks),
        *entries,
    ]
    item = _store(user, TrashedItem.KIND_TASK, task.title, objects, len(tasks), now=now)
    task.delete()
    return item


@transaction.atomic
def trash_events(user, events):
    """Move the user's own events of the queryset (one, or part of a series) with their invitees and
    comments to the trash as one item. Events of subscribed calendars are never touched."""
    events = list(events.filter(user=user, subscription=None).select_for_update().order_by("start_at", "pk"))
    if not events:
        return None
    objects = [
        *events,
        *CalendarEventAttendee.objects.filter(event__in=events),
        *CalendarEventComment.objects.filter(event__in=events),
    ]
    item = _store(user, TrashedItem.KIND_EVENT, events[0].title, objects, len(events))
    CalendarEvent.objects.filter(pk__in=[event.pk for event in events]).delete()
    return item


@transaction.atomic
def trash_habit(user, habit_id):
    habit = Habit.objects.select_for_update().filter(owner=user, pk=habit_id).first()
    if habit is None:
        return None
    checks = list(habit.checks.all())
    item = _store(user, TrashedItem.KIND_HABIT, habit.title, [habit, *checks], len(checks))
    habit.delete()
    return item


@transaction.atomic
def trash_vocabulary_list(user, list_id):
    vocabulary_list = VocabularyList.objects.select_for_update().filter(owner=user, pk=list_id).first()
    if vocabulary_list is None:
        return None
    cards = list(vocabulary_list.cards.all())
    objects = [vocabulary_list, *cards, *vocabulary_list.shares.all()]
    item = _store(user, TrashedItem.KIND_VOCABULARY, vocabulary_list.title, objects, len(cards))
    vocabulary_list.delete()
    return item


@transaction.atomic
def trash_recipe(user, recipe):
    """Move the user's own recipe with its places in the meal plan to the trash."""
    objects = [recipe, *recipe.plan_entries.filter(owner=user)]
    item = _store(user, TrashedItem.KIND_RECIPE, recipe.title, objects, 1)
    recipe.delete()
    return item


def _restorable(user, item, deserialized):
    """The snapshot objects that can go back, with references to vanished rows cut."""
    objects = [entry.object for entry in deserialized]
    person_ids = {getattr(obj, name) for obj in objects for name in PERSON_FIELDS if getattr(obj, name, None)}
    people = set(get_user_model().objects.filter(pk__in=person_ids).values_list("pk", flat=True))
    roots = {
        (type(obj), obj.pk)
        for obj in objects
        if type(obj) in ROOT_OWNER and getattr(obj, ROOT_OWNER[type(obj)]) == user.pk
    }
    root_ids = {pk for _model, pk in roots}
    lists = set(TaskList.objects.filter(owner=user).values_list("pk", flat=True))
    labels = set(TaskLabel.objects.filter(owner=user).values_list("pk", flat=True))
    task_ids = {pk for model, pk in roots if model is Task}
    parent_ids = {obj.parent_id for obj in objects if isinstance(obj, Task) and obj.parent_id}
    # Parents outside the snapshot must still be the user's own top-level tasks.
    parents = task_ids | set(
        Task.objects.filter(user=user, parent=None, pk__in=parent_ids - task_ids).values_list("pk", flat=True)
    )

    kept = []
    for entry in deserialized:
        obj = entry.object
        model = type(obj)
        if model in ROOT_OWNER:
            if (model, obj.pk) not in roots:
                continue
        elif model in CHILDREN:
            parent_field, person_fields = CHILDREN[model]
            # Every child model points at a different root model, so the id alone is unambiguous
            # within one snapshot (a snapshot holds a single kind).
            if getattr(obj, parent_field) not in root_ids:
                continue
            if any(getattr(obj, name) not in people for name in person_fields):
                continue
        else:
            continue

        if isinstance(obj, Task):
            obj.task_list_id = obj.task_list_id if obj.task_list_id in lists else None
            obj.parent_id = obj.parent_id if obj.parent_id in parents else None
            obj.assignee_id = obj.assignee_id if obj.assignee_id in people and obj.parent_id is None else None
            entry.m2m_data = {"labels": [pk for pk in entry.m2m_data.get("labels", []) if pk in labels]}
        elif isinstance(obj, CalendarEvent):
            obj.subscription_id = None
        elif isinstance(obj, TaskTimeEntry):
            obj.ended_at = obj.ended_at or item.deleted_at
        elif isinstance(obj, CalendarEventAttendee):
            obj.invited_by_id = obj.invited_by_id if obj.invited_by_id in people else None
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


def _plural(count, singular, plural):
    return f"{count} {singular if count == 1 else plural}"


def _detail(item):
    count = item.item_count
    if item.kind == TrashedItem.KIND_TASK and count > 1:
        return "Aufgabe", f"mit {_plural(count - 1, 'Unteraufgabe', 'Unteraufgaben')}"
    if item.kind == TrashedItem.KIND_EVENT and count > 1:
        return "Terminserie", f"mit {count} Terminen"
    if item.kind == TrashedItem.KIND_HABIT and count:
        return "Gewohnheit", f"mit {_plural(count, 'abgehaktem Tag', 'abgehakten Tagen')}"
    if item.kind == TrashedItem.KIND_VOCABULARY:
        return "Vokabelliste", f"mit {_plural(count, 'Karte', 'Karten')}"
    return item.get_kind_display(), ""


def trash_items(user):
    items = []
    for item in TrashedItem.objects.filter(user=user, kind__in=enabled_kinds()).defer("payload"):
        kind_label, detail = _detail(item)
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
