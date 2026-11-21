"""Daily notes, note reminders, checklist-to-tasks and presentation mode."""

import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from app.models import (
    CalendarEvent,
    Note,
    NoteLink,
    NoteReminder,
    NoteShare,
    SystemSettings,
    Task,
    UserNotification,
)
from app.services.notifications import materialize_due_note_reminders
from app.services.user_preferences import localtime_for_user


def text(value):
    return {"type": "text", "text": value}


def paragraph(value):
    return {"type": "paragraph", "content": [text(value)]}


def task_item(value, *, checked=False, due=None, children=None):
    attrs = {"checked": checked}
    if due:
        attrs["dueDate"] = due
    return {"type": "taskItem", "attrs": attrs, "content": [paragraph(value), *(children or [])]}


class NoteExtrasBase(TestCase):
    def setUp(self):
        self.lea = User.objects.create_user(
            username="lea@example.com", email="lea@example.com", password="pw"
        )
        self.tom = User.objects.create_user(
            username="tom@example.com", email="tom@example.com", password="pw"
        )
        self.client.force_login(self.lea)

    def local_today(self):
        return localtime_for_user(timezone.now(), self.lea).date()


class DailyNoteTests(NoteExtrasBase):
    def test_today_creates_one_note_with_agenda_and_a_link_to_the_previous_day(self):
        today = self.local_today()
        yesterday = Note.objects.create(owner=self.lea, title="Gestern", daily_date=today - timedelta(days=1))
        now = timezone.now()
        CalendarEvent.objects.create(
            user=self.lea, title="Zahnarzt", start_at=now, end_at=now + timedelta(hours=1)
        )
        Task.objects.create(user=self.lea, title="Steuer abgeben", due_at=now)
        Task.objects.create(user=self.lea, title="Erledigt", due_at=now, is_done=True)

        response = self.client.get("/notes/today/")

        note = Note.objects.get(owner=self.lea, daily_date=today)
        self.assertRedirects(response, f"/notes/{note.pk}/")
        self.assertTrue(note.title.startswith("Tagesnotiz · "))
        self.assertIn("Zahnarzt", note.plain_text)
        self.assertIn("Steuer abgeben", note.plain_text)
        self.assertNotIn("Erledigt", note.plain_text)
        self.assertIn("[[Gestern]]", note.plain_text)
        self.assertTrue(NoteLink.objects.filter(source_note=note, target_note=yesterday).exists())

        self.client.get("/notes/today/")
        self.assertEqual(Note.objects.filter(owner=self.lea, daily_date=today).count(), 1)

    def test_trashed_daily_note_opens_in_the_trash_instead_of_a_second_one(self):
        note = Note.objects.create(
            owner=self.lea, title="Heute", daily_date=self.local_today(), deleted_at=timezone.now()
        )

        response = self.client.get("/notes/today/")

        self.assertRedirects(response, f"/notes/{note.pk}/?status=trash")


class NoteReminderTests(NoteExtrasBase):
    def setUp(self):
        super().setUp()
        self.note = Note.objects.create(owner=self.lea, title="Angebot", plain_text="Preis nachverhandeln")
        self.url = f"/notes/{self.note.pk}/reminder/"

    def test_set_change_and_clear_a_reminder(self):
        when = localtime_for_user(timezone.now() + timedelta(days=2), self.lea).strftime("%Y-%m-%dT%H:%M")

        response = self.client.post(
            self.url, {"form_name": "note_reminder_set", "remind_at": when}, follow=True
        )

        reminder = NoteReminder.objects.get(note=self.note, user=self.lea)
        self.assertContains(response, "Erinnerung am")
        self.assertContains(response, 'data-command="reminder" aria-label="Erinnerung am')
        self.client.post(self.url, {"form_name": "note_reminder_set", "remind_at": when})
        self.assertEqual(NoteReminder.objects.count(), 1)
        self.assertEqual(NoteReminder.objects.get().pk, reminder.pk)

        self.client.post(self.url, {"form_name": "note_reminder_clear"})
        self.assertFalse(NoteReminder.objects.exists())

    def test_past_times_and_foreign_notes_are_refused(self):
        past = localtime_for_user(timezone.now() - timedelta(hours=1), self.lea).strftime("%Y-%m-%dT%H:%M")
        response = self.client.post(
            self.url, {"form_name": "note_reminder_set", "remind_at": past}, follow=True
        )
        self.assertContains(response, "in der Zukunft")
        self.assertFalse(NoteReminder.objects.exists())

        tom = Client()
        tom.force_login(self.tom)
        future = localtime_for_user(timezone.now() + timedelta(days=1), self.tom).strftime("%Y-%m-%dT%H:%M")
        self.assertEqual(tom.post(self.url, {"remind_at": future}).status_code, 404)

    def test_due_reminders_become_inbox_rows_unless_the_note_is_gone(self):
        shared = Note.objects.create(owner=self.tom, title="Geteilt")
        NoteShare.objects.create(note=shared, user=self.lea)
        trashed = Note.objects.create(owner=self.lea, title="Weg", deleted_at=timezone.now())
        unshared = Note.objects.create(owner=self.tom, title="Nicht mehr geteilt")
        past = timezone.now() - timedelta(minutes=1)
        for note in (self.note, shared, trashed, unshared):
            NoteReminder.objects.create(note=note, user=self.lea, remind_at=past)
        NoteReminder.objects.create(
            note=self.note, user=self.tom, remind_at=timezone.now() + timedelta(days=1)
        )

        self.assertEqual(materialize_due_note_reminders(), 2)

        rows = UserNotification.objects.filter(recipient=self.lea, kind=UserNotification.KIND_NOTE_REMINDER)
        self.assertEqual(
            sorted(rows.values_list("title", flat=True)), ["Erinnerung: Angebot", "Erinnerung: Geteilt"]
        )
        self.assertEqual(rows.get(title="Erinnerung: Angebot").url, f"/notes/{self.note.pk}/")
        self.assertEqual(list(NoteReminder.objects.values_list("user", flat=True)), [self.tom.pk])


class ChecklistTasksTests(NoteExtrasBase):
    def setUp(self):
        super().setUp()
        self.note = Note.objects.create(
            owner=self.lea,
            title="Umzug",
            document={
                "type": "doc",
                "content": [
                    paragraph("Vorher erledigen:"),
                    {
                        "type": "taskList",
                        "content": [
                            task_item(
                                "Kartons kaufen",
                                due="2099-03-01",
                                children=[{"type": "taskList", "content": [task_item("Klebeband")]}],
                            ),
                            task_item("Nachsendeauftrag", checked=True),
                            task_item("  "),
                        ],
                    },
                ],
            },
        )

    def action(self, client=None):
        return (client or self.client).post(
            f"/notes/api/{self.note.pk}/actions/",
            data=json.dumps({"action": "checklist_tasks"}),
            content_type="application/json",
        )

    def test_open_items_become_tasks_once(self):
        response = self.action()

        self.assertEqual(response.json(), {"ok": True, "created": 2, "skipped": 1})
        tasks = {task.title: task for task in Task.objects.filter(user=self.lea)}
        self.assertEqual(set(tasks), {"Kartons kaufen", "Klebeband"})
        due = localtime_for_user(tasks["Kartons kaufen"].due_at, self.lea)
        self.assertEqual((due.date().isoformat(), due.hour), ("2099-03-01", 9))
        self.assertIsNone(tasks["Klebeband"].due_at)

        self.assertEqual(self.action().json(), {"ok": True, "created": 0, "skipped": 3})

    def test_needs_access_and_the_tasks_feature(self):
        tom = Client()
        tom.force_login(self.tom)
        self.assertEqual(self.action(tom).status_code, 404)

        SystemSettings.objects.update_or_create(pk=1, defaults={"tasks_enabled": False})
        self.assertEqual(self.action().status_code, 400)
        self.assertFalse(Task.objects.exists())


class NotePresentationTests(NoteExtrasBase):
    def test_headings_and_rules_split_the_slides(self):
        note = Note.objects.create(
            owner=self.lea,
            title="Quartalsbericht",
            document={
                "type": "doc",
                "content": [
                    paragraph("Stand November"),
                    {"type": "heading", "attrs": {"level": 1}, "content": [text("Zahlen")]},
                    paragraph("Umsatz <steigt>"),
                    {"type": "heading", "attrs": {"level": 3}, "content": [text("Details")]},
                    {"type": "horizontalRule"},
                    {"type": "heading", "attrs": {"level": 2}, "content": [text("Ausblick")]},
                ],
            },
        )

        response = self.client.get(f"/notes/{note.pk}/present/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Stand November", response.context["title_slide"])
        slides = response.context["slides"]
        # The rule and the heading right after it make one break, not an empty slide.
        self.assertEqual(len(slides), 2)
        self.assertIn("Zahlen", slides[0])
        self.assertIn("Details", slides[0])
        self.assertIn("Umsatz &lt;steigt&gt;", slides[0])
        self.assertIn("Ausblick", slides[1])
        self.assertContains(response, "1 / 3")
        self.assertFalse(response.context["has_math"])

    def test_others_cannot_present_a_note(self):
        note = Note.objects.create(owner=self.tom, title="Privat")

        self.assertEqual(self.client.get(f"/notes/{note.pk}/present/").status_code, 404)
