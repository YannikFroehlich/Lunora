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
    TaskLabel,
    TaskList,
    UserNotification,
)
from app.services.notifications import (
    send_due_task_reminder_emails,
)
from app.services.scheduled_tasks import run_scheduled_tasks
from app.services.tasks import dashboard_today_tasks, toggle_task
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
            {"all": 3, "open": 2, "done": 1, "overdue": 1, "today": 1, "upcoming": 0},
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
