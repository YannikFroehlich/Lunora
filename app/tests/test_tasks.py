from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.utils import timezone

from app.models import (
    NotificationPreference,
    Profile,
    SystemSettings,
    Task,
    TaskComment,
    TaskLabel,
    TaskList,
    TaskShare,
    TaskTemplate,
    UserNotification,
)
from app.services.notifications import (
    materialize_task_assignment_notifications,
    send_due_task_reminder_emails,
)
from app.services.scheduled_tasks import run_scheduled_tasks
from app.services.tasks import dashboard_today_tasks, toggle_task, visible_tasks
from app.services.user_preferences import get_user_zoneinfo


class TaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com",
            email="mira@example.com",
            password="secret-12345",
        )
        self.profile = Profile.objects.create(
            user=self.user,
            display_name="Mira",
            notify_email=True,
            notify_reminders=True,
            notify_desktop=True,
        )

    def _login(self):
        self.client.login(username="mira@example.com", password="secret-12345")

    def test_disabled_tasks_returns_503(self):
        SystemSettings.objects.create(tasks_enabled=False)
        self._login()

        response = self.client.get("/tasks/")

        self.assertEqual(response.status_code, 503)

    def test_disabled_tasks_blocks_direct_post(self):
        SystemSettings.objects.create(tasks_enabled=False)
        self._login()

        response = self.client.post(
            "/tasks/",
            {"form_name": "task_add", "title": "Blockierte Aufgabe"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(Task.objects.filter(title="Blockierte Aufgabe").exists())

    def test_task_can_be_added_toggled_and_deleted(self):
        self._login()

        response = self.client.post(
            "/tasks/",
            {"form_name": "task_add", "title": "Wäsche waschen"},
        )

        self.assertRedirects(response, "/tasks/")
        task = Task.objects.get(user=self.user)
        self.assertEqual(task.title, "Wäsche waschen")
        self.assertFalse(task.is_done)

        response = self.client.post(
            "/tasks/",
            {"form_name": "task_toggle", "task_id": str(task.id), "is_done": "on"},
        )

        self.assertRedirects(response, "/tasks/")
        task.refresh_from_db()
        self.assertTrue(task.is_done)

        response = self.client.post(
            "/tasks/",
            {"form_name": "task_delete", "task_id": str(task.id)},
        )

        self.assertRedirects(response, "/tasks/")
        self.assertFalse(Task.objects.filter(pk=task.id).exists())

    def test_task_can_store_due_date(self):
        self._login()
        due_at = timezone.localtime(timezone.now() + timedelta(days=1)).replace(second=0, microsecond=0)

        response = self.client.post(
            "/tasks/",
            {
                "form_name": "task_add",
                "title": "Hausaufgaben abgeben",
                "due_at": due_at.strftime("%Y-%m-%dT%H:%M"),
            },
        )

        self.assertRedirects(response, "/tasks/")
        task = Task.objects.get(user=self.user)
        self.assertEqual(
            timezone.localtime(task.due_at).strftime("%Y-%m-%dT%H:%M"),
            due_at.strftime("%Y-%m-%dT%H:%M"),
        )

        response = self.client.get("/tasks/")

        self.assertContains(response, "Hausaufgaben abgeben")
        self.assertContains(response, "Morgen")

    def test_task_page_exposes_filter_counts_and_statuses(self):
        now = timezone.now()
        Task.objects.create(user=self.user, title="Ohne Termin")
        Task.objects.create(user=self.user, title="Zu spät", due_at=now - timedelta(hours=1))
        Task.objects.create(user=self.user, title="Fertig", is_done=True)
        self._login()

        response = self.client.get("/tasks/")

        self.assertEqual(
            response.context["task_counts"],
            {
                "all": 3,
                "open": 2,
                "done": 1,
                "overdue": 1,
                "today": 1,
                "upcoming": 0,
                "assigned": 0,
                "shared": 0,
                "in_progress": 0,
            },
        )
        self.assertContains(response, 'data-task-state="overdue"')
        self.assertContains(response, "Überfällig")
        self.assertContains(response, 'data-task-filter="done"')

    def test_user_cannot_toggle_or_delete_another_users_task(self):
        other = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        Profile.objects.create(user=other, display_name="Lukas")
        task = Task.objects.create(user=self.user, title="Privat")
        self.client.login(username="lukas@example.com", password="secret-12345")

        self.client.post("/tasks/", {"form_name": "task_toggle", "task_id": str(task.id), "is_done": "on"})
        task.refresh_from_db()
        self.assertFalse(task.is_done)

        self.client.post("/tasks/", {"form_name": "task_delete", "task_id": str(task.id)})
        self.assertTrue(Task.objects.filter(pk=task.id).exists())

        response = self.client.get("/tasks/")
        self.assertNotContains(response, "Privat")

    def test_task_edit_updates_fields(self):
        self._login()
        task_list = TaskList.objects.create(owner=self.user, name="Projekt")
        label = TaskLabel.objects.create(owner=self.user, name="Wichtig")
        task = Task.objects.create(user=self.user, title="Alter Titel")
        due_at = timezone.localtime(timezone.now() + timedelta(days=2)).replace(second=0, microsecond=0)

        response = self.client.post(
            "/tasks/",
            {
                "form_name": "task_edit",
                "task_id": str(task.id),
                "title": "Neuer Titel",
                "due_at": due_at.strftime("%Y-%m-%dT%H:%M"),
                "task_list": str(task_list.id),
                "priority": "high",
                "recurrence_rule": "WEEKLY",
                "labels": [str(label.id)],
            },
        )

        self.assertRedirects(response, "/tasks/")
        task.refresh_from_db()
        self.assertEqual(task.title, "Neuer Titel")
        self.assertEqual(
            timezone.localtime(task.due_at).strftime("%Y-%m-%dT%H:%M"),
            due_at.strftime("%Y-%m-%dT%H:%M"),
        )
        self.assertEqual(task.task_list_id, task_list.id)
        self.assertEqual(task.priority, "high")
        self.assertEqual(task.recurrence_rule, "WEEKLY")
        self.assertEqual(list(task.labels.all()), [label])

    def test_task_edit_cannot_target_another_users_task(self):
        other = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        Profile.objects.create(user=other, display_name="Lukas")
        task = Task.objects.create(user=other, title="Fremde Aufgabe")
        self._login()

        self.client.post(
            "/tasks/",
            {"form_name": "task_edit", "task_id": str(task.id), "title": "Übernommen"},
        )

        task.refresh_from_db()
        self.assertEqual(task.title, "Fremde Aufgabe")

    def test_task_edit_rejects_blank_title(self):
        self._login()
        task = Task.objects.create(user=self.user, title="Bleibt gleich")

        self.client.post(
            "/tasks/",
            {"form_name": "task_edit", "task_id": str(task.id), "title": ""},
        )

        task.refresh_from_db()
        self.assertEqual(task.title, "Bleibt gleich")

    def test_task_reorder_updates_positions(self):
        self._login()
        first = Task.objects.create(user=self.user, title="Erstens")
        second = Task.objects.create(user=self.user, title="Zweitens")
        third = Task.objects.create(user=self.user, title="Drittens")

        response = self.client.post(
            "/tasks/",
            {
                "form_name": "task_reorder",
                "task_id": str(third.id),
                "target_id": str(first.id),
                "placement": "before",
            },
        )

        self.assertRedirects(response, "/tasks/?sort=manual")
        ordered_ids = list(
            Task.objects.filter(user=self.user).order_by("position", "id").values_list("id", flat=True)
        )
        self.assertEqual(ordered_ids, [third.id, first.id, second.id])

    def test_task_reorder_rejects_cross_parent_move(self):
        self._login()
        parent_a = Task.objects.create(user=self.user, title="Eltern A")
        parent_b = Task.objects.create(user=self.user, title="Eltern B")
        subtask = Task.objects.create(user=self.user, title="Unteraufgabe", parent=parent_a)

        self.client.post(
            "/tasks/",
            {
                "form_name": "task_reorder",
                "task_id": str(subtask.id),
                "target_id": str(parent_b.id),
                "placement": "after",
            },
        )

        subtask.refresh_from_db()
        self.assertEqual(subtask.parent_id, parent_a.id)

    def test_task_reorder_cannot_target_another_users_task(self):
        other = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        Profile.objects.create(user=other, display_name="Lukas")
        mine = Task.objects.create(user=self.user, title="Meine Aufgabe", position=1000)
        theirs = Task.objects.create(user=other, title="Fremde Aufgabe", position=2000)
        self._login()

        self.client.post(
            "/tasks/",
            {
                "form_name": "task_reorder",
                "task_id": str(mine.id),
                "target_id": str(theirs.id),
                "placement": "before",
            },
        )

        mine.refresh_from_db()
        theirs.refresh_from_db()
        self.assertEqual(mine.position, 1000)
        self.assertEqual(theirs.position, 2000)

    def test_task_list_reorder_updates_positions(self):
        self._login()
        first = TaskList.objects.create(owner=self.user, name="Erste")
        second = TaskList.objects.create(owner=self.user, name="Zweite")
        third = TaskList.objects.create(owner=self.user, name="Dritte")

        response = self.client.post(
            "/tasks/",
            {
                "form_name": "task_list_reorder",
                "task_list_id": str(third.id),
                "target_id": str(first.id),
                "placement": "before",
            },
        )

        self.assertRedirects(response, "/tasks/")
        ordered_ids = list(
            TaskList.objects.filter(owner=self.user).order_by("position", "id").values_list("id", flat=True)
        )
        self.assertEqual(ordered_ids, [third.id, first.id, second.id])

    def test_manual_sort_orders_tasks_by_position(self):
        self._login()
        now = timezone.now()
        # due_at order would put "Später fällig" first; position order should reverse that.
        Task.objects.create(
            user=self.user, title="Früher fällig", due_at=now + timedelta(days=1), position=2000
        )
        Task.objects.create(
            user=self.user, title="Später fällig", due_at=now + timedelta(days=5), position=1000
        )

        response = self.client.get("/tasks/?sort=manual")

        titles = [item["title"] for item in response.context["tasks"]]
        self.assertEqual(titles, ["Später fällig", "Früher fällig"])

    def test_due_task_reminder_email_is_sent_only_once(self):
        task = Task.objects.create(
            user=self.user,
            title="Rechnung bezahlen",
            due_at=timezone.now() - timedelta(minutes=1),
        )

        first_result = send_due_task_reminder_emails()
        second_result = send_due_task_reminder_emails()

        self.assertEqual(first_result, {"sent": 1, "failed": 0})
        self.assertEqual(second_result, {"sent": 0, "failed": 0})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Rechnung bezahlen", mail.outbox[0].subject)
        task.refresh_from_db()
        self.assertIsNotNone(task.email_notified_at)

    def test_task_email_category_can_be_disabled(self):
        task = Task.objects.create(
            user=self.user,
            title="Nicht per E-Mail senden",
            due_at=timezone.now() - timedelta(minutes=1),
        )
        NotificationPreference.objects.create(
            user=self.user,
            category=NotificationPreference.CATEGORY_TASKS,
            email_enabled=False,
        )

        result = send_due_task_reminder_emails()

        self.assertEqual(result, {"sent": 0, "failed": 0})
        self.assertEqual(len(mail.outbox), 0)
        task.refresh_from_db()
        self.assertIsNone(task.email_notified_at)

    def test_task_desktop_notification_claim_is_one_time(self):
        task = Task.objects.create(
            user=self.user,
            title="Präsentation vorbereiten",
            due_at=timezone.now() - timedelta(minutes=1),
        )
        self.client.force_login(self.user)

        first_response = self.client.post("/notifications/claim/")
        second_response = self.client.post("/notifications/claim/")

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(first_response.json()["notifications"][0]["title"], "Präsentation vorbereiten")
        self.assertEqual(first_response.json()["notifications"][0]["url"], "/tasks/")
        self.assertEqual(second_response.json(), {"notifications": []})
        task.refresh_from_db()
        self.assertIsNotNone(task.desktop_notified_at)

    def test_dashboard_widget_appears_when_enabled(self):
        self._login()
        Task.objects.create(user=self.user, title="Offene Aufgabe")

        response = self.client.get("/home/")

        self.assertContains(response, "Offene Aufgabe")
        self.assertContains(response, "Aufgaben")

    def test_dashboard_widget_and_nav_tile_hidden_when_disabled(self):
        SystemSettings.objects.create(tasks_enabled=False)
        self._login()
        Task.objects.create(user=self.user, title="Offene Aufgabe")

        response = self.client.get("/home/")

        self.assertNotContains(response, "Offene Aufgabe")

    def test_scheduled_tasks_send_task_reminder_emails_when_enabled(self):
        Task.objects.create(
            user=self.user,
            title="Rechnung bezahlen",
            due_at=timezone.now() - timedelta(minutes=1),
        )

        result = run_scheduled_tasks(now=timezone.now())

        self.assertEqual(result["task_reminder_emails"], {"sent": 1, "failed": 0})

    def test_scheduled_tasks_skip_task_reminder_emails_when_disabled(self):
        SystemSettings.objects.create(tasks_enabled=False)
        Task.objects.create(
            user=self.user,
            title="Rechnung bezahlen",
            due_at=timezone.now() - timedelta(minutes=1),
        )

        result = run_scheduled_tasks(now=timezone.now())

        self.assertEqual(result["task_reminder_emails"], {"sent": 0, "failed": 0, "disabled": True})

    def test_task_list_crud_and_task_falls_back_to_inbox_when_list_deleted(self):
        self._login()

        self.client.post("/tasks/", {"form_name": "task_list_add", "name": "Projekt Alpha", "color": "blue"})
        task_list = TaskList.objects.get(owner=self.user, name="Projekt Alpha")

        response = self.client.post(
            "/tasks/",
            {"form_name": "task_add", "title": "Kickoff vorbereiten", "task_list": str(task_list.id)},
        )
        self.assertRedirects(response, "/tasks/")
        task = Task.objects.get(title="Kickoff vorbereiten")
        self.assertEqual(task.task_list_id, task_list.id)

        self.client.post(
            "/tasks/",
            {"form_name": "task_list_rename", "task_list_id": str(task_list.id), "name": "Projekt Beta"},
        )
        task_list.refresh_from_db()
        self.assertEqual(task_list.name, "Projekt Beta")

        self.client.post("/tasks/", {"form_name": "task_list_delete", "task_list_id": str(task_list.id)})

        self.assertFalse(TaskList.objects.filter(pk=task_list.id).exists())
        task.refresh_from_db()
        self.assertIsNone(task.task_list_id)

    def test_task_label_create_assign_and_delete(self):
        self._login()
        self.client.post("/tasks/", {"form_name": "task_label_add", "name": "Dringend", "color": "red"})
        label = TaskLabel.objects.get(owner=self.user, name="Dringend")

        response = self.client.post(
            "/tasks/",
            {"form_name": "task_add", "title": "Kunde anrufen", "labels": [str(label.id)]},
        )
        self.assertRedirects(response, "/tasks/")
        task = Task.objects.get(title="Kunde anrufen")
        self.assertIn(label, task.labels.all())

        self.client.post("/tasks/", {"form_name": "task_label_delete", "task_label_id": str(label.id)})

        self.assertFalse(TaskLabel.objects.filter(pk=label.id).exists())
        self.assertEqual(task.labels.count(), 0)

    def test_subtask_creation_and_one_level_depth_enforcement(self):
        self._login()
        parent = Task.objects.create(user=self.user, title="Umzug organisieren")

        response = self.client.post(
            "/tasks/",
            {"form_name": "task_add", "title": "Umzugswagen mieten", "parent": str(parent.id)},
        )
        self.assertRedirects(response, "/tasks/")
        subtask = Task.objects.get(title="Umzugswagen mieten")
        self.assertEqual(subtask.parent_id, parent.id)

        # The parent picker only lists top-level tasks, so a subtask can never be chosen
        # as a parent itself -- this caps nesting at one level.
        response = self.client.post(
            "/tasks/",
            {"form_name": "task_add", "title": "Noch tiefer", "parent": str(subtask.id)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Task.objects.filter(title="Noch tiefer").exists())

    def test_subtasks_cascade_delete_with_parent(self):
        parent = Task.objects.create(user=self.user, title="Elternaufgabe")
        Task.objects.create(user=self.user, title="Kindaufgabe", parent=parent)
        self._login()

        self.client.post("/tasks/", {"form_name": "task_delete", "task_id": str(parent.id)})

        self.assertFalse(Task.objects.filter(title="Elternaufgabe").exists())
        self.assertFalse(Task.objects.filter(title="Kindaufgabe").exists())

    def test_completing_recurring_task_creates_next_occurrence(self):
        due_at = timezone.now().replace(microsecond=0)
        task = Task.objects.create(
            user=self.user, title="Müll rausbringen", due_at=due_at, recurrence_rule="WEEKLY"
        )

        result = toggle_task(self.user, task.id, True)

        self.assertIsNotNone(result)
        task.refresh_from_db()
        self.assertTrue(task.is_done)
        siblings = Task.objects.filter(user=self.user, title="Müll rausbringen").exclude(pk=task.id)
        self.assertEqual(siblings.count(), 1)
        next_task = siblings.first()
        self.assertFalse(next_task.is_done)
        self.assertEqual(next_task.due_at, due_at + timedelta(weeks=1))
        self.assertIsNone(next_task.email_notified_at)
        self.assertIsNone(next_task.desktop_notified_at)

    def test_completing_non_recurring_task_does_not_duplicate(self):
        task = Task.objects.create(user=self.user, title="Einmalig")

        toggle_task(self.user, task.id, True)

        self.assertEqual(Task.objects.filter(title="Einmalig").count(), 1)

    def test_task_counts_include_today_and_upcoming_buckets(self):
        now = timezone.now()
        Task.objects.create(user=self.user, title="Heute fällig", due_at=now + timedelta(hours=2))
        Task.objects.create(user=self.user, title="Nächste Woche", due_at=now + timedelta(days=3))
        Task.objects.create(user=self.user, title="Weit weg", due_at=now + timedelta(days=30))
        self._login()

        response = self.client.get("/tasks/")

        self.assertEqual(response.context["task_counts"]["today"], 1)
        self.assertEqual(response.context["task_counts"]["upcoming"], 1)

    def test_user_cannot_manage_another_users_task_list_or_label(self):
        other = User.objects.create_user(
            username="lukas2@example.com", email="lukas2@example.com", password="secret-12345"
        )
        Profile.objects.create(user=other, display_name="Lukas2")
        task_list = TaskList.objects.create(owner=self.user, name="Privatliste")
        label = TaskLabel.objects.create(owner=self.user, name="Privatlabel")
        self.client.login(username="lukas2@example.com", password="secret-12345")

        self.client.post("/tasks/", {"form_name": "task_list_delete", "task_list_id": str(task_list.id)})
        self.client.post("/tasks/", {"form_name": "task_label_delete", "task_label_id": str(label.id)})

        self.assertTrue(TaskList.objects.filter(pk=task_list.id).exists())
        self.assertTrue(TaskLabel.objects.filter(pk=label.id).exists())

    def test_dashboard_today_tasks_includes_overdue_and_today_but_not_upcoming(self):
        # Local midday, so "+2 hours" stays on the same local date.
        now = datetime(2026, 6, 17, 12, 0, tzinfo=get_user_zoneinfo(self.user))
        Task.objects.create(user=self.user, title="Überfällig", due_at=now - timedelta(days=1))
        Task.objects.create(user=self.user, title="Heute fällig", due_at=now + timedelta(hours=2))
        Task.objects.create(user=self.user, title="Nächste Woche", due_at=now + timedelta(days=3))
        Task.objects.create(user=self.user, title="Ohne Fälligkeit")
        Task.objects.create(user=self.user, title="Erledigt", due_at=now, is_done=True)

        result = dashboard_today_tasks(self.user, now)

        titles = {item["title"] for item in result}
        self.assertEqual(titles, {"Überfällig", "Heute fällig"})

    def test_task_toggle_redirects_to_safe_return_to_url(self):
        task = Task.objects.create(user=self.user, title="Vom Dashboard erledigen")
        self._login()

        response = self.client.post(
            "/tasks/",
            {"form_name": "task_toggle", "task_id": str(task.id), "is_done": "on", "return_to": "/home/"},
        )

        self.assertRedirects(response, "/home/")
        task.refresh_from_db()
        self.assertTrue(task.is_done)

    def test_task_toggle_ignores_unsafe_return_to_url(self):
        task = Task.objects.create(user=self.user, title="Sicherheitscheck")
        self._login()

        response = self.client.post(
            "/tasks/",
            {
                "form_name": "task_toggle",
                "task_id": str(task.id),
                "is_done": "on",
                "return_to": "https://evil.example/",
            },
        )

        self.assertRedirects(response, "/tasks/")


class TaskRescheduleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mira@example.com", password="secret-12345")
        Profile.objects.create(user=self.user, display_name="Mira", timezone_name="Europe/Berlin")
        self.client.login(username="mira@example.com", password="secret-12345")
        self.berlin = ZoneInfo("Europe/Berlin")

    def reschedule(self, task, **fields):
        return self.client.post(
            "/tasks/",
            {
                "form_name": "task_reschedule",
                "task_id": task.id,
                "return_to": "/calendar/?view=week&date=2099-08-12",
                **fields,
            },
        )

    def test_drop_on_a_day_keeps_the_time_and_returns_to_the_calendar(self):
        task = Task.objects.create(
            user=self.user, title="Bericht", due_at=datetime(2099, 8, 10, 14, 30, tzinfo=self.berlin)
        )

        response = self.reschedule(task, due_date="2099-08-13")

        self.assertRedirects(response, "/calendar/?view=week&date=2099-08-12", fetch_redirect_response=False)
        task.refresh_from_db()
        self.assertEqual(task.due_at, datetime(2099, 8, 13, 14, 30, tzinfo=self.berlin))

    def test_drop_on_the_time_grid_sets_the_time_and_undated_tasks_get_a_default_time(self):
        timed = Task.objects.create(
            user=self.user, title="Anruf", due_at=datetime(2099, 8, 10, 8, tzinfo=self.berlin)
        )
        undated = Task.objects.create(user=self.user, title="Irgendwann")

        self.reschedule(timed, due_date="2099-08-11", due_time="16:30")
        self.reschedule(undated, due_date="2099-08-12")

        timed.refresh_from_db()
        undated.refresh_from_db()
        self.assertEqual(timed.due_at, datetime(2099, 8, 11, 16, 30, tzinfo=self.berlin))
        self.assertEqual(undated.due_at, datetime(2099, 8, 12, 9, tzinfo=self.berlin))

    def test_moving_a_notified_task_later_rearms_its_reminders(self):
        now = timezone.now()
        task = Task.objects.create(
            user=self.user,
            title="Überfällig",
            due_at=now - timedelta(hours=1),
            email_notified_at=now,
            desktop_notified_at=now,
        )
        UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_TASK_DUE,
            title="Aufgabe fällig",
            source_key=f"task:{task.pk}",
        )

        self.reschedule(task, due_date="2099-01-05")

        task.refresh_from_db()
        self.assertIsNone(task.email_notified_at)
        self.assertIsNone(task.desktop_notified_at)
        self.assertFalse(UserNotification.objects.filter(source_key=f"task:{task.pk}").exists())

    def test_editing_a_task_to_a_later_due_date_rearms_its_reminders(self):
        now = timezone.now()
        task = Task.objects.create(
            user=self.user, title="Steuer", due_at=now - timedelta(hours=1), email_notified_at=now
        )

        self.client.post(
            "/tasks/",
            {"form_name": "task_edit", "task_id": task.id, "title": "Steuer", "due_at": "2099-03-01T10:00"},
        )

        task.refresh_from_db()
        self.assertIsNone(task.email_notified_at)

    def test_done_foreign_and_invalid_tasks_are_not_moved(self):
        other = User.objects.create_user(username="eve@example.com", password="secret-12345")
        due = datetime(2099, 8, 10, 9, tzinfo=self.berlin)
        foreign = Task.objects.create(user=other, title="Fremd", due_at=due)
        done = Task.objects.create(user=self.user, title="Fertig", due_at=due, is_done=True)

        self.reschedule(foreign, due_date="2099-08-13")
        self.reschedule(done, due_date="2099-08-13")
        response = self.reschedule(done, due_date="kein-datum")

        self.assertEqual(response.status_code, 302)
        for task in (foreign, done):
            task.refresh_from_db()
            self.assertEqual(task.due_at, due)

    def test_calendar_task_chips_are_draggable(self):
        Task.objects.create(
            user=self.user, title="Bericht", due_at=datetime(2099, 8, 10, 14, 30, tzinfo=self.berlin)
        )

        for url in ("/calendar/?year=2099&month=8", "/calendar/?view=week&date=2099-08-10"):
            response = self.client.get(url)
            self.assertContains(response, 'data-task-date="2099-08-10"')
            self.assertContains(response, 'draggable="true"')


class TaskAssignmentTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="mira", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=self.owner, display_name="Mira", notify_email=True, notify_reminders=True)
        self.assignee = User.objects.create_user(
            username="jonas", email="jonas@example.com", password="secret-12345"
        )
        Profile.objects.create(
            user=self.assignee, display_name="Jonas", notify_email=True, notify_reminders=True
        )

    def test_assigning_notifies_the_assignee_who_sees_the_task_read_only(self):
        self.client.login(username="mira", password="secret-12345")
        self.client.post(
            "/tasks/", {"form_name": "task_add", "title": "Folien bauen", "assignee": self.assignee.pk}
        )
        task = Task.objects.get(title="Folien bauen")
        self.assertEqual((task.user, task.assignee), (self.owner, self.assignee))
        notification = UserNotification.objects.get(recipient=self.assignee)
        self.assertEqual(notification.kind, UserNotification.KIND_TASK_ASSIGNMENT)
        self.assertEqual(notification.title, "Mira hat dir eine Aufgabe zugewiesen")

        self.client.login(username="jonas", password="secret-12345")
        response = self.client.get("/tasks/")
        [item] = response.context["tasks"]
        self.assertFalse(item["is_own"])
        self.assertEqual(item["owner_name"], "Mira")
        self.assertEqual(response.context["task_counts"]["assigned"], 1)
        self.assertNotContains(response, 'value="task_delete"')

    def test_assignee_can_tick_off_but_not_delete_or_edit(self):
        task = Task.objects.create(user=self.owner, assignee=self.assignee, title="Folien bauen")
        self.client.login(username="jonas", password="secret-12345")

        self.client.post("/tasks/", {"form_name": "task_delete", "task_id": task.pk})
        self.client.post("/tasks/", {"form_name": "task_edit", "task_id": task.pk, "title": "Gekapert"})
        self.client.post("/tasks/", {"form_name": "task_toggle", "task_id": task.pk, "is_done": "on"})

        task.refresh_from_db()
        self.assertEqual(task.title, "Folien bauen")
        self.assertTrue(task.is_done)

    def test_completing_a_recurring_assigned_task_keeps_owner_and_assignee(self):
        task = Task.objects.create(
            user=self.owner,
            assignee=self.assignee,
            title="Müll rausbringen",
            due_at=timezone.now(),
            recurrence_rule="WEEKLY",
        )

        toggle_task(self.assignee, task.pk, True)

        next_task = Task.objects.get(is_done=False)
        self.assertEqual((next_task.user, next_task.assignee), (self.owner, self.assignee))

    def test_subtasks_cannot_have_their_own_assignee(self):
        parent = Task.objects.create(user=self.owner, title="Projekt")
        self.client.login(username="mira", password="secret-12345")

        self.client.post(
            "/tasks/",
            {"form_name": "task_add", "title": "Teil", "parent": parent.pk, "assignee": self.assignee.pk},
        )

        self.assertFalse(Task.objects.filter(title="Teil").exists())

    def test_due_reminder_goes_to_the_assignee(self):
        Task.objects.create(
            user=self.owner,
            assignee=self.assignee,
            title="Rechnung prüfen",
            due_at=timezone.now() - timedelta(minutes=5),
        )

        result = send_due_task_reminder_emails()

        self.assertEqual(result, {"sent": 1, "failed": 0})
        self.assertEqual(mail.outbox[0].to, ["jonas@example.com"])
        due_row = UserNotification.objects.get(kind=UserNotification.KIND_TASK_DUE)
        self.assertEqual(due_row.recipient, self.assignee)

    def test_assignment_email_is_sent_once_by_the_automations(self):
        task = Task.objects.create(user=self.owner, assignee=self.assignee, title="Folien bauen")
        materialize_task_assignment_notifications([task])

        run_scheduled_tasks()
        run_scheduled_tasks()

        assignment_mails = [message for message in mail.outbox if "zugewiesen" in message.subject]
        self.assertEqual(len(assignment_mails), 1)
        self.assertEqual(assignment_mails[0].to, ["jonas@example.com"])

    def test_owner_and_assignee_discuss_an_assigned_task(self):
        task = Task.objects.create(user=self.owner, assignee=self.assignee, title="Folien bauen")
        own_only = Task.objects.create(user=self.owner, title="Privat")
        self.client.login(username="jonas", password="secret-12345")

        response = self.client.post(
            "/tasks/", {"form_name": "task_comment", "task_id": task.pk, "body": "Bis Freitag?"}
        )
        self.client.post("/tasks/", {"form_name": "task_comment", "task_id": own_only.pk, "body": "Fremd"})
        self.client.post("/tasks/", {"form_name": "task_comment", "task_id": task.pk, "body": "   "})

        self.assertRedirects(response, f"/tasks/#task-comments-{task.pk}", fetch_redirect_response=False)
        self.assertEqual(
            list(TaskComment.objects.values_list("author__username", "body")), [("jonas", "Bis Freitag?")]
        )
        notification = UserNotification.objects.get(kind=UserNotification.KIND_TASK_COMMENT)
        self.assertEqual(
            (notification.recipient, notification.title), (self.owner, "Jonas hat „Folien bauen“ kommentiert")
        )

        self.client.login(username="mira", password="secret-12345")
        self.client.post("/tasks/", {"form_name": "task_comment", "task_id": task.pk, "body": "Ja, passt."})
        response = self.client.get("/tasks/")
        item = next(item for item in response.context["tasks"] if item["task"] == task)
        self.assertEqual(
            [(comment["author_name"], comment["is_own"]) for comment in item["comments"]],
            [("Jonas", False), ("Mira", True)],
        )
        self.assertIsNone(
            next(item for item in response.context["tasks"] if item["task"] == own_only)["comments"]
        )
        self.assertEqual(
            UserNotification.objects.filter(
                kind=UserNotification.KIND_TASK_COMMENT, recipient=self.assignee
            ).count(),
            1,
        )

    def test_authors_edit_and_delete_only_their_own_comments(self):
        task = Task.objects.create(user=self.owner, assignee=self.assignee, title="Folien bauen")
        self.client.login(username="jonas", password="secret-12345")
        self.client.post("/tasks/", {"form_name": "task_comment", "task_id": task.pk, "body": "Bis Freitag?"})
        comment = TaskComment.objects.get()

        response = self.client.post(
            "/tasks/", {"form_name": "task_comment_edit", "comment_id": comment.pk, "body": "Bis Montag?"}
        )
        comment.refresh_from_db()
        self.assertRedirects(response, f"/tasks/#task-comments-{task.pk}", fetch_redirect_response=False)
        self.assertEqual(comment.body, "Bis Montag?")
        self.assertIsNotNone(comment.edited_at)
        self.assertEqual(
            UserNotification.objects.get(kind=UserNotification.KIND_TASK_COMMENT).body, "Bis Montag?"
        )

        self.client.login(username="mira", password="secret-12345")
        self.client.post(
            "/tasks/", {"form_name": "task_comment_edit", "comment_id": comment.pk, "body": "Fremd"}
        )
        self.client.post("/tasks/", {"form_name": "task_comment_delete", "comment_id": comment.pk})
        comment.refresh_from_db()
        self.assertEqual(comment.body, "Bis Montag?")

        self.client.login(username="jonas", password="secret-12345")
        self.client.post(
            "/tasks/",
            # The delete button's form_name comes after the hidden field and wins, as in the browser.
            {
                "form_name": ["task_comment_edit", "task_comment_delete"],
                "comment_id": comment.pk,
                "body": "x",
            },
        )
        self.assertFalse(TaskComment.objects.exists())
        self.assertFalse(UserNotification.objects.filter(kind=UserNotification.KIND_TASK_COMMENT).exists())

    def test_assignee_returns_a_task_to_its_owner(self):
        task = Task.objects.create(user=self.owner, assignee=self.assignee, title="Folien bauen")
        materialize_task_assignment_notifications([task])
        self.client.login(username="mira", password="secret-12345")
        self.client.post("/tasks/", {"form_name": "task_return", "task_id": task.pk})
        task.refresh_from_db()
        self.assertEqual(task.assignee, self.assignee)

        self.client.login(username="jonas", password="secret-12345")
        self.client.post("/tasks/", {"form_name": "task_return", "task_id": task.pk, "reason": "Keine Zeit"})

        task.refresh_from_db()
        self.assertIsNone(task.assignee)
        returned = UserNotification.objects.get(kind=UserNotification.KIND_TASK_RETURNED)
        self.assertEqual(
            (returned.recipient, returned.title, returned.body),
            (self.owner, "Jonas hat „Folien bauen“ zurückgegeben", "Keine Zeit"),
        )
        self.assertNotIn(task, [item["task"] for item in self.client.get("/tasks/").context["tasks"]])

        # Assigning the same person again notifies them again.
        task.assignee = self.assignee
        task.save()
        self.assertEqual(materialize_task_assignment_notifications([task]), 1)

    def test_unrelated_users_cannot_comment(self):
        task = Task.objects.create(user=self.owner, assignee=self.assignee, title="Folien bauen")
        User.objects.create_user(username="lea", email="lea@example.com", password="secret-12345")
        self.client.login(username="lea", password="secret-12345")

        self.client.post("/tasks/", {"form_name": "task_comment", "task_id": task.pk, "body": "Hallo"})

        self.assertFalse(TaskComment.objects.exists())


class TaskTemplateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=self.user, display_name="Mira", timezone_name="Europe/Berlin")
        self.client.login(username="mira", password="secret-12345")

    def test_a_task_with_its_checklist_is_saved_and_reused_as_a_template(self):
        label = TaskLabel.objects.create(owner=self.user, name="Büro")
        task = Task.objects.create(user=self.user, title="Monatsabschluss", priority=Task.PRIORITY_HIGH)
        task.labels.add(label)
        for title in ("Belege sammeln", "Rechnungen prüfen", "Bericht senden"):
            Task.objects.create(user=self.user, parent=task, title=title)

        self.client.post("/tasks/", {"form_name": "task_template_save", "task_id": task.pk})
        template = TaskTemplate.objects.get()
        self.assertEqual(template.subtask_titles, ["Belege sammeln", "Rechnungen prüfen", "Bericht senden"])

        self.client.post(
            "/tasks/",
            {"form_name": "task_template_use", "template_id": template.pk, "due_at": "2099-03-31T17:00"},
        )

        copy = Task.objects.exclude(pk=task.pk).get(parent__isnull=True)
        self.assertEqual((copy.title, copy.priority, copy.is_done), ("Monatsabschluss", "high", False))
        self.assertEqual(copy.due_at, datetime(2099, 3, 31, 17, tzinfo=ZoneInfo("Europe/Berlin")))
        self.assertEqual(list(copy.labels.all()), [label])
        self.assertEqual(
            list(copy.subtasks.order_by("created_at", "id").values_list("title", flat=True)),
            ["Belege sammeln", "Rechnungen prüfen", "Bericht senden"],
        )
        context = self.client.get("/tasks/").context
        self.assertEqual(
            context["task_templates"], [{"id": template.pk, "title": "Monatsabschluss", "subtask_count": 3}]
        )

    def test_templates_stay_with_their_owner(self):
        other = User.objects.create_user(username="lukas", email="lukas@example.com", password="secret-12345")
        foreign_task = Task.objects.create(user=other, title="Fremd")
        foreign_template = TaskTemplate.objects.create(owner=other, title="Fremd")

        self.client.post("/tasks/", {"form_name": "task_template_save", "task_id": foreign_task.pk})
        self.client.post("/tasks/", {"form_name": "task_template_use", "template_id": foreign_template.pk})
        self.client.post("/tasks/", {"form_name": "task_template_delete", "template_id": foreign_template.pk})

        self.assertEqual(list(TaskTemplate.objects.values_list("owner__username", flat=True)), ["lukas"])
        self.assertFalse(Task.objects.filter(user=self.user).exists())


class TaskBoardAndShareTests(TestCase):
    def setUp(self):
        self.mira = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        self.lukas = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        self.anna = User.objects.create_user(
            username="anna@example.com", email="anna@example.com", password="secret-12345"
        )
        for user, name in ((self.mira, "Mira"), (self.lukas, "Lukas"), (self.anna, "Anna")):
            Profile.objects.create(user=user, display_name=name)
        self.client.login(username="mira@example.com", password="secret-12345")

    def login(self, user):
        self.client.logout()
        self.client.login(username=user.username, password="secret-12345")

    def set_status(self, task, status):
        return self.client.post("/tasks/", {"form_name": "task_status", "task_id": task.pk, "status": status})

    def share(self, title="Umzug planen", users=()):
        response = self.client.post(
            "/tasks/",
            {"form_name": "task_add", "title": title, "shared_with": [user.pk for user in users]},
        )
        self.assertEqual(response.status_code, 302)
        return Task.objects.get(title=title)

    def test_status_moves_between_open_in_progress_and_done(self):
        task = Task.objects.create(user=self.mira, title="Bericht")

        self.set_status(task, "in_progress")
        task.refresh_from_db()
        self.assertEqual(task.status, "in_progress")

        self.set_status(task, "done")
        task.refresh_from_db()
        self.assertEqual((task.is_done, task.is_in_progress), (True, False))

        self.set_status(task, "open")
        task.refresh_from_db()
        self.assertEqual(task.status, "open")

        response = self.set_status(task, "bogus")
        self.assertEqual(response.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, "open")

    def test_ticking_off_an_in_progress_task_clears_the_flag(self):
        task = Task.objects.create(user=self.mira, title="Bericht", is_in_progress=True)
        toggle_task(self.mira, task.pk, True)
        task.refresh_from_db()
        self.assertFalse(task.is_in_progress)

    def test_recurring_task_spawns_its_next_occurrence_only_once(self):
        task = Task.objects.create(
            user=self.mira, title="Gießen", recurrence_rule="DAILY", due_at=timezone.now()
        )
        TaskShare.objects.create(task=task, user=self.lukas)
        self.set_status(task, "done")
        self.set_status(task, "done")
        self.assertEqual(Task.objects.filter(title="Gießen").count(), 2)
        next_task = Task.objects.get(title="Gießen", is_done=False)
        self.assertEqual(list(next_task.shares.values_list("user_id", flat=True)), [self.lukas.pk])

    def test_board_groups_top_level_tasks_by_status(self):
        Task.objects.create(user=self.mira, title="Offen")
        parent = Task.objects.create(user=self.mira, title="Läuft", is_in_progress=True)
        Task.objects.create(user=self.mira, title="Teil", parent=parent)
        Task.objects.create(user=self.mira, title="Fertig", is_done=True)

        response = self.client.get("/tasks/")

        columns = {
            column["key"]: [item["title"] for item in column["items"]]
            for column in response.context["task_board_columns"]
        }
        self.assertEqual(columns, {"open": ["Offen"], "in_progress": ["Läuft"], "done": ["Fertig"]})
        self.assertEqual(response.context["task_counts"]["in_progress"], 1)
        self.assertContains(response, 'data-task-board-column="in_progress"')
        self.assertContains(response, 'id="id_title"', count=1)
        self.assertContains(response, 'id="id_edit_title"', count=1)

    def test_sharing_notifies_and_lets_people_work_on_the_task(self):
        task = self.share(users=[self.lukas, self.anna])

        self.assertEqual(set(task.shares.values_list("user_id", flat=True)), {self.lukas.pk, self.anna.pk})
        notification = UserNotification.objects.get(
            recipient=self.lukas, kind=UserNotification.KIND_TASK_SHARE
        )
        self.assertEqual(notification.source_key, f"task-share:{task.pk}:{self.lukas.pk}")

        self.login(self.lukas)
        response = self.client.get("/tasks/")
        item = next(item for item in response.context["tasks"] if item["task"].pk == task.pk)
        self.assertTrue(item["shared_with_me"])
        self.assertFalse(item["is_own"])
        self.assertEqual(response.context["task_counts"]["shared"], 1)
        # Visible exactly once, although shared with two people.
        self.assertEqual(visible_tasks(self.lukas).filter(pk=task.pk).count(), 1)

        self.set_status(task, "in_progress")
        task.refresh_from_db()
        self.assertTrue(task.is_in_progress)

        # Editing and deleting stay with the owner.
        self.client.post("/tasks/", {"form_name": "task_delete", "task_id": task.pk})
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())

        self.client.post("/tasks/", {"form_name": "task_comment", "task_id": task.pk, "body": "Mache ich!"})
        recipients = set(
            UserNotification.objects.filter(kind=UserNotification.KIND_TASK_COMMENT).values_list(
                "recipient_id", flat=True
            )
        )
        self.assertEqual(recipients, {self.mira.pk, self.anna.pk})

    def test_shared_subtasks_are_visible_and_unrelated_users_see_nothing(self):
        task = self.share(users=[self.lukas])
        Task.objects.create(user=self.mira, title="Kartons", parent=task)

        self.assertEqual(
            set(visible_tasks(self.lukas).values_list("title", flat=True)), {"Umzug planen", "Kartons"}
        )
        self.assertFalse(visible_tasks(self.anna).exists())
        self.login(self.anna)
        self.set_status(task, "done")
        task.refresh_from_db()
        self.assertFalse(task.is_done)

    def test_leaving_and_unsharing_remove_access_and_notification(self):
        task = self.share(users=[self.lukas, self.anna])

        self.login(self.lukas)
        self.client.post("/tasks/", {"form_name": "task_share_leave", "task_id": task.pk})
        self.assertFalse(visible_tasks(self.lukas).exists())
        self.assertFalse(UserNotification.objects.filter(recipient=self.lukas).exists())

        self.login(self.mira)
        response = self.client.post(
            "/tasks/",
            {"form_name": "task_edit", "task_id": task.pk, "title": task.title, "shared_with": []},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(task.shares.exists())
        self.assertFalse(UserNotification.objects.filter(recipient=self.anna).exists())

    def test_subtasks_cannot_be_shared(self):
        parent = Task.objects.create(user=self.mira, title="Haupt")
        response = self.client.post(
            "/tasks/",
            {"form_name": "task_add", "title": "Teil", "parent": parent.pk, "shared_with": [self.lukas.pk]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Task.objects.filter(title="Teil").exists())

    def test_shares_appear_in_the_data_export(self):
        task = self.share(users=[self.lukas])
        from app.services.data_export import _tasks

        exported = next(row for row in _tasks(self.mira)["tasks"] if row["id"] == task.pk)
        self.assertEqual(exported["shared_with"], ["lukas@example.com"])
        self.assertFalse(exported["is_in_progress"])


class TaskTimeTrackingTests(TestCase):
    def setUp(self):
        from app.models import Profile

        self.user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="pw-12345"
        )
        self.other = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="pw-12345"
        )
        Profile.objects.create(user=self.user, display_name="Mira", timezone_name="Europe/Berlin")
        Profile.objects.create(user=self.other, display_name="Lukas", timezone_name="Europe/Berlin")
        self.task = Task.objects.create(user=self.user, title="Bericht schreiben")
        self.second = Task.objects.create(user=self.user, title="Folien")
        self.client.login(username="mira@example.com", password="pw-12345")

    def timer(self, task):
        return self.client.post("/tasks/", {"form_name": "task_timer", "task_id": task.id}, follow=True)

    def test_timer_starts_and_stops_from_the_task_row(self):
        from app.models import TaskTimeEntry

        response = self.timer(self.task)
        self.assertContains(response, "Timer für „Bericht schreiben“ gestartet.")
        self.assertContains(response, "data-running-timer")
        self.assertContains(response, 'class="task-timer-button is-running"')
        entry = TaskTimeEntry.objects.get()
        self.assertTrue(entry.is_running)

        TaskTimeEntry.objects.filter(pk=entry.pk).update(started_at=timezone.now() - timedelta(minutes=25))
        response = self.timer(self.task)

        entry.refresh_from_db()
        self.assertFalse(entry.is_running)
        self.assertContains(response, "25 min")
        self.assertNotContains(response, "data-running-timer")

    def test_starting_another_task_stops_the_running_one(self):
        from app.models import TaskTimeEntry
        from app.services.time_tracking import start_timer

        start = timezone.now() - timedelta(hours=1)
        start_timer(self.user, self.task, now=start)
        start_timer(self.user, self.second, now=start + timedelta(minutes=30))

        first, second = TaskTimeEntry.objects.order_by("started_at")
        self.assertEqual(first.duration_seconds(), 30 * 60)
        self.assertTrue(second.is_running)
        self.assertEqual(TaskTimeEntry.objects.filter(ended_at__isnull=True).count(), 1)

    def test_a_timer_started_by_mistake_is_dropped(self):
        from app.models import TaskTimeEntry

        self.timer(self.task)
        self.timer(self.task)

        self.assertFalse(TaskTimeEntry.objects.exists())

    def test_finishing_the_task_stops_its_timer(self):
        from app.models import TaskTimeEntry
        from app.services.time_tracking import start_timer

        start_timer(self.user, self.task, now=timezone.now() - timedelta(minutes=10))
        toggle_task(self.user, self.task.id, True)

        self.assertFalse(TaskTimeEntry.objects.get().is_running)

    def test_only_visible_open_tasks_can_be_tracked(self):
        from app.models import TaskTimeEntry

        foreign = Task.objects.create(user=self.other, title="Fremd")
        done = Task.objects.create(user=self.user, title="Fertig", is_done=True)

        self.timer(foreign)
        response = self.timer(done)

        self.assertContains(response, "Für erledigte Aufgaben läuft kein Timer.")
        self.assertFalse(TaskTimeEntry.objects.exists())

    def test_shared_tasks_can_be_tracked_and_times_stay_private(self):
        from app.models import TaskShare
        from app.services.time_tracking import add_manual_entry

        TaskShare.objects.create(task=self.task, user=self.other)
        add_manual_entry(self.other, self.task, timezone.localdate(), 90)

        response = self.client.get("/tasks/")

        item = next(item for item in response.context["tasks"] if item["task"] == self.task)
        self.assertEqual(item["time_label"], "")
        self.client.force_login(self.other)
        item = next(item for item in self.client.get("/tasks/").context["tasks"] if item["task"] == self.task)
        self.assertEqual(item["time_label"], "1 h 30 min")

    def test_weekly_report_with_manual_entries_and_csv(self):
        from app.services.time_tracking import week_start_for

        today = timezone.localtime(timezone.now(), ZoneInfo("Europe/Berlin")).date()
        week = "{}-W{:02d}".format(*today.isocalendar()[:2])
        self.client.post(
            "/tasks/time/",
            {"form_name": "time_entry_add", "task": self.task.id, "day": today.isoformat(), "minutes": 45},
        )
        self.client.post(
            "/tasks/time/",
            {"form_name": "time_entry_add", "task": self.second.id, "day": today.isoformat(), "minutes": 15},
        )

        response = self.client.get("/tasks/time/", {"week": week})

        report = response.context["report"]
        self.assertEqual(report["total_label"], "1 h 00 min")
        self.assertEqual([task["title"] for task in report["tasks"]], ["Bericht schreiben", "Folien"])
        self.assertEqual(report["tasks"][0]["percent"], 75)
        self.assertEqual(sum(day["seconds"] for day in report["buckets"]), 3600)
        self.assertEqual(week_start_for(week, self.user), today - timedelta(days=today.weekday()))

        csv = self.client.get("/tasks/time/", {"week": week, "format": "csv"})
        self.assertEqual(csv["Content-Type"], "text/csv; charset=utf-8")
        content = csv.content.decode("utf-8-sig")
        self.assertIn("Datum;Zeit;Aufgabe;Dauer", content)
        self.assertIn("Bericht schreiben;45 min", content)
        self.assertIn("Summe;;;1 h 00 min", content)

    def track(self, task, start, minutes):
        from app.models import TaskTimeEntry

        return TaskTimeEntry.objects.create(
            task=task, user=self.user, started_at=start, ended_at=start + timedelta(minutes=minutes)
        )

    def test_monthly_report_sums_per_week_and_downloads_as_csv(self):
        from app.services.time_tracking import time_report

        berlin = ZoneInfo("Europe/Berlin")
        self.track(self.task, datetime(2026, 10, 31, 10, 0, tzinfo=berlin), 60)  # previous month
        self.track(self.task, datetime(2026, 11, 1, 23, 30, tzinfo=berlin), 30)  # Sunday, KW 44
        self.track(self.second, datetime(2026, 11, 2, 9, 0, tzinfo=berlin), 90)  # Monday, KW 45

        report = time_report(
            self.user, "month", datetime(2026, 11, 1).date(), now=datetime(2026, 11, 20, tzinfo=berlin)
        )

        self.assertEqual(report["label"], "November 2026")
        self.assertEqual((report["previous_value"], report["next_value"]), ("2026-10", "2026-12"))
        self.assertEqual(report["total_label"], "2 h 00 min")
        self.assertEqual(
            [(bucket["short"], bucket["seconds"]) for bucket in report["buckets"]],
            [("KW 44", 1800), ("KW 45", 5400), ("KW 46", 0), ("KW 47", 0), ("KW 48", 0), ("KW 49", 0)],
        )
        self.assertEqual(report["week_value"], "2026-W47")

        response = self.client.get("/tasks/time/", {"month": "2026-11"})
        self.assertContains(response, "November 2026")
        self.assertContains(response, 'href="?month=2026-11" aria-current="page"')
        csv = self.client.get("/tasks/time/", {"month": "2026-11", "format": "csv"})
        self.assertIn("lunora-zeiten-2026-11.csv", csv["Content-Disposition"])
        self.assertIn("Summe;;;2 h 00 min", csv.content.decode("utf-8-sig"))

    def test_report_groups_by_list_and_label_only_when_that_splits_the_time(self):
        from app.models import TaskLabel, TaskList
        from app.services.time_tracking import time_report

        today = timezone.localtime(timezone.now(), ZoneInfo("Europe/Berlin")).date()
        week_start = today - timedelta(days=today.weekday())
        start = timezone.now() - timedelta(hours=3)
        self.track(self.task, start, 60)
        self.track(self.second, start + timedelta(hours=1), 30)

        report = time_report(self.user, "week", week_start)
        self.assertEqual((report["lists"], report["labels"]), ([], []))

        work = TaskList.objects.create(owner=self.user, name="Arbeit")
        urgent = TaskLabel.objects.create(owner=self.user, name="dringend")
        client = TaskLabel.objects.create(owner=self.user, name="Kunde")
        self.task.task_list = work
        self.task.save(update_fields=["task_list"])
        self.task.labels.set([urgent, client])
        self.second.labels.set([urgent])

        report = time_report(self.user, "week", week_start)

        self.assertEqual(
            [(row["name"], row["label"], row["percent"]) for row in report["lists"]],
            [("Arbeit", "1 h 00 min", 67), ("Eingang", "30 min", 33)],
        )
        self.assertEqual(
            [(row["name"], row["label"]) for row in report["labels"]],
            [("dringend", "1 h 30 min"), ("Kunde", "1 h 00 min")],
        )
        response = self.client.get("/tasks/time/")
        self.assertContains(response, "Nach Liste")
        self.assertContains(response, "Nach Label")

    def test_manual_entries_are_validated(self):
        future = (timezone.localdate() + timedelta(days=2)).isoformat()

        response = self.client.post(
            "/tasks/time/",
            {"form_name": "time_entry_add", "task": self.task.id, "day": future, "minutes": 30},
        )
        self.assertContains(response, "nicht für die Zukunft")

        response = self.client.post(
            "/tasks/time/",
            {"form_name": "time_entry_add", "task": self.task.id, "day": timezone.localdate(), "minutes": 0},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.task.time_entries.exists())

    def test_entries_can_only_be_deleted_by_their_owner(self):
        from app.services.time_tracking import add_manual_entry

        entry = add_manual_entry(self.user, self.task, timezone.localdate(), 30)
        self.client.force_login(self.other)
        self.client.post("/tasks/time/", {"form_name": "time_entry_delete", "entry_id": entry.id})
        self.assertTrue(self.task.time_entries.exists())

        self.client.force_login(self.user)
        self.client.post("/tasks/time/", {"form_name": "time_entry_delete", "entry_id": entry.id})
        self.assertFalse(self.task.time_entries.exists())

    def test_format_duration(self):
        from app.services.time_tracking import format_duration

        self.assertEqual(
            [format_duration(seconds) for seconds in (30, 60, 59 * 60, 3600, 3725)],
            ["< 1 min", "1 min", "59 min", "1 h 00 min", "1 h 02 min"],
        )

    def test_manual_entry_form_defaults_to_today_as_iso_date(self):
        response = self.client.get("/tasks/time/")

        today = timezone.localtime(timezone.now(), ZoneInfo("Europe/Berlin")).date().isoformat()
        self.assertContains(response, f'value="{today}"')
