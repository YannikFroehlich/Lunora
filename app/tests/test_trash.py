from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from app.models import (
    CalendarEvent,
    CalendarEventAttendee,
    CalendarEventComment,
    CalendarReminder,
    Profile,
    SystemSettings,
    Task,
    TaskComment,
    TaskLabel,
    TaskList,
    TaskShare,
    TaskTimeEntry,
    TrashedItem,
)
from app.services.data_export import _trash
from app.services.trash import purge_expired_trash


class TrashTests(TestCase):
    def setUp(self):
        self.user = self._user("mira@example.com", "Mira")
        self.other = self._user("noah@example.com", "Noah")
        self.client.login(username="mira@example.com", password="secret-12345")

    def _user(self, email, name):
        user = User.objects.create_user(username=email, email=email, password="secret-12345")
        Profile.objects.create(user=user, display_name=name, timezone_name="Europe/Berlin")
        return user

    def _delete_task(self, task):
        return self.client.post("/tasks/", {"form_name": "task_delete", "task_id": task.pk})

    def _restore(self, item):
        return self.client.post("/trash/", {"form_name": "trash_restore", "item_id": item.pk})

    def test_deleted_task_comes_back_with_subtasks_labels_comments_shares_and_time(self):
        task_list = TaskList.objects.create(owner=self.user, name="Projekt")
        label = TaskLabel.objects.create(owner=self.user, name="Wichtig")
        task = Task.objects.create(user=self.user, title="Umzug", task_list=task_list, assignee=self.other)
        task.labels.add(label)
        subtask = Task.objects.create(user=self.user, title="Kartons", parent=task)
        TaskShare.objects.create(task=task, user=self.other)
        TaskComment.objects.create(task=task, author=self.other, body="Mache ich")
        TaskTimeEntry.objects.create(
            task=subtask, user=self.user, started_at=timezone.now() - timedelta(hours=1)
        )

        response = self._delete_task(task)

        self.assertRedirects(response, "/tasks/")
        self.assertFalse(Task.objects.filter(pk__in=[task.pk, subtask.pk]).exists())
        item = TrashedItem.objects.get(user=self.user)
        self.assertEqual((item.kind, item.title, item.item_count), ("task", "Umzug", 2))
        self.assertContains(self.client.get("/trash/"), "Umzug")

        self.assertRedirects(self._restore(item), "/trash/")

        restored = Task.objects.get(pk=task.pk)
        self.assertEqual(restored.task_list, task_list)
        self.assertEqual(restored.assignee, self.other)
        self.assertEqual(list(restored.labels.all()), [label])
        self.assertEqual(Task.objects.get(pk=subtask.pk).parent_id, task.pk)
        self.assertTrue(TaskShare.objects.filter(task=task, user=self.other).exists())
        self.assertEqual(TaskComment.objects.get(task=task).body, "Mache ich")
        # The running timer was stopped when the task went to the trash.
        self.assertIsNotNone(TaskTimeEntry.objects.get(task_id=subtask.pk).ended_at)
        self.assertFalse(TrashedItem.objects.exists())

    def test_restore_drops_references_to_rows_that_are_gone(self):
        task_list = TaskList.objects.create(owner=self.user, name="Weg")
        label = TaskLabel.objects.create(owner=self.user, name="Weg")
        parent = Task.objects.create(user=self.user, title="Eltern")
        subtask = Task.objects.create(user=self.user, title="Kind", parent=parent, task_list=task_list)
        subtask.labels.add(label)
        TaskComment.objects.create(task=subtask, author=self.other, body="Hallo")
        self._delete_task(subtask)
        task_list.delete()
        label.delete()
        parent.delete()
        self.other.delete()

        self._restore(TrashedItem.objects.get())

        restored = Task.objects.get(pk=subtask.pk)
        self.assertIsNone(restored.parent_id)
        self.assertIsNone(restored.task_list_id)
        self.assertFalse(restored.labels.exists())
        self.assertFalse(TaskComment.objects.exists())

    def test_deleted_series_comes_back_with_invitees_comments_and_reminders(self):
        start = timezone.now() + timedelta(days=3)
        recurrence_id = "2b0f8f5e-7a8c-4c1e-9e57-0d3f5b9d6a11"
        events = [
            CalendarEvent.objects.create(
                user=self.user,
                title="Training",
                start_at=start + timedelta(weeks=week),
                end_at=start + timedelta(weeks=week, hours=1),
                recurrence_id=recurrence_id,
                reminder_minutes=15,
            )
            for week in range(2)
        ]
        CalendarEventAttendee.objects.create(
            event=events[0], user=self.other, status=CalendarEventAttendee.STATUS_ACCEPTED
        )
        CalendarEventComment.objects.create(event=events[0], author=self.other, body="Bin dabei")

        self.client.post(
            "/calendar/", {"form_name": "calendar_event_delete", "event_id": events[0].pk, "scope": "all"}
        )

        self.assertFalse(CalendarEvent.objects.exists())
        item = TrashedItem.objects.get()
        self.assertEqual((item.kind, item.item_count), ("event", 2))

        self._restore(item)

        self.assertEqual(
            set(CalendarEvent.objects.values_list("pk", flat=True)), {event.pk for event in events}
        )
        attendee = CalendarEventAttendee.objects.get()
        self.assertEqual((attendee.event_id, attendee.status), (events[0].pk, "accepted"))
        self.assertTrue(CalendarEventComment.objects.filter(event_id=events[0].pk).exists())
        # Owner on both events plus the accepted invitee on the first.
        self.assertEqual(CalendarReminder.objects.filter(event__isnull=False).count(), 3)

    def test_series_delete_button_trashes_the_whole_series(self):
        start = timezone.now() + timedelta(days=1)
        recurrence_id = "8d7b1a52-0c0e-4f3a-a3a4-3d0e3f7d2b10"
        for week in range(3):
            CalendarEvent.objects.create(
                user=self.user,
                title="Serie",
                start_at=start + timedelta(weeks=week),
                end_at=start + timedelta(weeks=week, hours=1),
                recurrence_id=recurrence_id,
            )

        self.client.post(
            "/calendar/", {"form_name": "calendar_event_delete_series", "recurrence_id": recurrence_id}
        )

        self.assertEqual(TrashedItem.objects.get().item_count, 3)
        self.assertFalse(CalendarEvent.objects.exists())

    def test_items_of_other_users_are_untouchable(self):
        task = Task.objects.create(user=self.other, title="Fremd")
        self.client.post("/tasks/", {"form_name": "task_delete", "task_id": task.pk})
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())

        self.client.login(username="noah@example.com", password="secret-12345")
        self.client.post("/tasks/", {"form_name": "task_delete", "task_id": task.pk})
        item = TrashedItem.objects.get(user=self.other)
        self.client.login(username="mira@example.com", password="secret-12345")

        self.assertNotContains(self.client.get("/trash/"), "Fremd")
        self._restore(item)
        self.client.post("/trash/", {"form_name": "trash_delete", "item_id": item.pk})
        self.client.post("/trash/", {"form_name": "trash_empty"})

        self.assertTrue(TrashedItem.objects.filter(pk=item.pk).exists())
        self.assertFalse(Task.objects.filter(pk=task.pk).exists())

    def test_delete_and_empty_remove_items_for_good(self):
        for title in ["Eins", "Zwei", "Drei"]:
            self._delete_task(Task.objects.create(user=self.user, title=title))
        first = TrashedItem.objects.get(title="Eins")

        self.client.post("/trash/", {"form_name": "trash_delete", "item_id": first.pk})
        self.assertEqual(TrashedItem.objects.count(), 2)

        self.client.post("/trash/", {"form_name": "trash_empty"})
        self.assertFalse(TrashedItem.objects.exists())

    def test_purge_removes_items_older_than_the_retention(self):
        now = timezone.now()
        TrashedItem.objects.create(
            user=self.user, kind="task", title="Alt", deleted_at=now - timedelta(days=31)
        )
        TrashedItem.objects.create(
            user=self.user, kind="task", title="Neu", deleted_at=now - timedelta(days=29)
        )

        self.assertEqual(purge_expired_trash(now=now), 1)
        self.assertEqual(list(TrashedItem.objects.values_list("title", flat=True)), ["Neu"])

    def test_disabled_tasks_hide_task_items_and_refuse_restore(self):
        task = Task.objects.create(user=self.user, title="Versteckt")
        self._delete_task(task)
        SystemSettings.objects.update_or_create(pk=1, defaults={"tasks_enabled": False})

        self.assertNotContains(self.client.get("/trash/"), "Versteckt")
        self._restore(TrashedItem.objects.get())
        self.assertFalse(Task.objects.filter(pk=task.pk).exists())

        SystemSettings.objects.filter(pk=1).update(calendar_event_creation_enabled=False)
        self.assertEqual(self.client.get("/trash/").status_code, 503)

    def test_export_lists_trashed_rows_without_other_peoples_ids(self):
        task = Task.objects.create(user=self.user, title="Export", assignee=self.other)
        self._delete_task(task)

        exported = _trash(self.user)

        self.assertEqual(exported[0]["kind"], "task")
        self.assertEqual(exported[0]["items"][0]["title"], "Export")
        self.assertNotIn("assignee", exported[0]["items"][0])
