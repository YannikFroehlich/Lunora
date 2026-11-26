"""Bookmark export, date filters in the global search, note links on tasks and events, and
saved-search alerts."""

from datetime import UTC, date, datetime, timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from app.models import (
    Bookmark,
    CalendarEvent,
    Note,
    NoteShare,
    Recipe,
    SavedSearch,
    Task,
    UserNotification,
)
from app.services.bookmarks import import_bookmark_file
from app.services.global_search import check_saved_searches
from app.services.note_search import split_date_filters


def _user(name):
    return User.objects.create_user(username=name, email=f"{name}@example.com", password="pw-12345!")


class BookmarkExportTests(TestCase):
    def setUp(self):
        self.mira = _user("mira")
        self.client.force_login(self.mira)

    def test_export_is_a_bookmark_file_the_import_reads_back(self):
        Bookmark.objects.create(
            owner=self.mira, title='Bank <"Haupt">', url="https://bank.example/?a=1&b=2", tags="geld konto"
        )
        Bookmark.objects.create(owner=self.mira, title="Mail", url="https://mail.example/")
        Bookmark.objects.create(owner=_user("lukas"), title="Fremd", url="https://fremd.example/")

        response = self.client.get("/tools/bookmarks/export/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        body = response.content.decode()
        self.assertIn("&lt;&quot;Haupt&quot;&gt;", body)
        self.assertIn('HREF="https://bank.example/?a=1&amp;b=2"', body)
        self.assertNotIn("fremd.example", body)

        tom = _user("tom")
        upload = SimpleUploadedFile("export.html", response.content, content_type="text/html")
        self.assertEqual(import_bookmark_file(tom, upload), (2, 0))
        bank = Bookmark.objects.get(owner=tom, url="https://bank.example/?a=1&b=2")
        self.assertEqual((bank.title, bank.tags), ('Bank <"Haupt">', "geld konto"))

    def test_export_needs_login(self):
        self.client.logout()
        self.assertEqual(self.client.get("/tools/bookmarks/export/").status_code, 302)


class DateFilterParsingTests(SimpleTestCase):
    def test_bounds_are_taken_out_of_the_text(self):
        self.assertEqual(
            split_date_filters("bericht nach:2026-10-01 vor:31.10.2026 #arbeit"),
            (date(2026, 10, 1), date(2026, 10, 31), "bericht #arbeit"),
        )
        self.assertEqual(split_date_filters("NACH:2026-01-02"), (date(2026, 1, 2), None, ""))

    def test_an_invalid_date_stays_search_text(self):
        self.assertEqual(split_date_filters("vor:morgen plan"), (None, None, "vor:morgen plan"))


class GlobalSearchDateFilterTests(TestCase):
    def setUp(self):
        self.mira = _user("mira")
        self.client.force_login(self.mira)
        aware = lambda day: datetime(2026, 10, day, 12, tzinfo=UTC)  # noqa: E731
        Task.objects.create(user=self.mira, title="Bericht Oktober", due_at=aware(10))
        Task.objects.create(user=self.mira, title="Bericht Anfang", due_at=aware(1))
        Task.objects.create(user=self.mira, title="Bericht ohne Datum")
        CalendarEvent.objects.create(
            user=self.mira,
            title="Bericht vorstellen",
            start_at=aware(20),
            end_at=aware(20) + timedelta(hours=1),
        )
        Recipe.objects.create(owner=self.mira, title="Bericht-Kuchen")

    def titles(self, query, key):
        return [row["title"] for row in self.client.get("/search/", {"q": query}).context[key]]

    def test_dated_areas_keep_hits_within_the_bounds(self):
        self.assertEqual(self.titles("bericht nach:2026-10-05", "task_results"), ["Bericht Oktober"])
        self.assertEqual(self.titles("bericht vor:05.10.2026", "task_results"), ["Bericht Anfang"])
        self.assertEqual(self.titles("bericht nach:2026-10-15", "event_results"), ["Bericht vorstellen"])
        self.assertEqual(self.titles("bericht vor:2026-10-15", "event_results"), [])

    def test_areas_without_a_date_show_nothing_under_a_date_filter(self):
        self.assertEqual(self.titles("bericht", "recipe_results"), ["Bericht-Kuchen"])
        self.assertEqual(self.titles("bericht nach:2026-10-01", "recipe_results"), [])

    def test_a_date_alone_lists_everything_in_the_range(self):
        self.assertEqual(
            self.titles("nach:2026-10-01 vor:2026-10-31", "task_results"),
            ["Bericht Anfang", "Bericht Oktober"],
        )


class NoteLinkTests(TestCase):
    def setUp(self):
        self.mira = _user("mira")
        self.lukas = _user("lukas")
        self.note = Note.objects.create(owner=self.mira, title="Projektplan")
        self.foreign_note = Note.objects.create(owner=self.lukas, title="Geheim")
        self.client.force_login(self.mira)

    def test_task_edit_links_a_note_the_owner_may_open(self):
        task = Task.objects.create(user=self.mira, title="Angebot schreiben")
        post = {"form_name": "task_edit", "task_id": task.pk, "title": "Angebot schreiben"}

        self.client.post("/tasks/", {**post, "note": self.foreign_note.pk})
        task.refresh_from_db()
        self.assertIsNone(task.note_id)

        self.client.post("/tasks/", {**post, "note": self.note.pk})
        task.refresh_from_db()
        self.assertEqual(task.note_id, self.note.pk)
        self.assertContains(self.client.get("/tasks/"), f'href="/notes/{self.note.pk}/"')

    def test_note_lists_linked_tasks_and_events_only_for_viewers_who_see_them(self):
        Task.objects.create(user=self.mira, title="Angebot schreiben", note=self.note)
        start = timezone.now() + timedelta(days=1)
        event = CalendarEvent.objects.create(
            user=self.mira, title="Kickoff", start_at=start, end_at=start + timedelta(hours=1), note=self.note
        )

        response = self.client.get(f"/notes/{self.note.pk}/")
        self.assertContains(response, "Verknüpft mit")
        self.assertContains(response, "Angebot schreiben")
        self.assertContains(response, f"/calendar/events/{event.pk}/")

        NoteShare.objects.create(note=self.note, user=self.lukas, role=NoteShare.ROLE_READER)
        self.client.force_login(self.lukas)
        self.assertNotContains(self.client.get(f"/notes/{self.note.pk}/"), "Verknüpft mit")

    def test_event_owner_links_a_note_and_invitees_without_access_see_no_link(self):
        start = timezone.now() + timedelta(days=1)
        event = CalendarEvent.objects.create(
            user=self.mira, title="Kickoff", start_at=start, end_at=start + timedelta(hours=1)
        )
        url = f"/calendar/events/{event.pk}/"

        self.client.post(url, {"form_name": "event_note", "note_id": self.foreign_note.pk})
        event.refresh_from_db()
        self.assertIsNone(event.note_id)

        self.client.post(url, {"form_name": "event_note", "note_id": self.note.pk})
        event.refresh_from_db()
        self.assertEqual(event.note_id, self.note.pk)
        self.assertContains(self.client.get(url), f'href="/notes/{self.note.pk}/"')

        self.client.post(url, {"form_name": "event_note", "note_id": ""})
        event.refresh_from_db()
        self.assertIsNone(event.note_id)

        event.note = self.note
        event.save()
        event.attendees.create(user=self.lukas)
        self.client.force_login(self.lukas)
        response = self.client.get(url)
        self.assertNotContains(response, f'href="/notes/{self.note.pk}/"')
        self.client.post(url, {"form_name": "event_note", "note_id": ""})
        event.refresh_from_db()
        self.assertEqual(event.note_id, self.note.pk)


class SavedSearchAlertTests(TestCase):
    def setUp(self):
        self.mira = _user("mira")
        self.client.force_login(self.mira)
        Task.objects.create(user=self.mira, title="Steuer 2025")
        self.saved = SavedSearch.objects.create(owner=self.mira, query="steuer")

    def toggle(self):
        self.client.post(
            "/search/saved/", {"form_name": "saved_search_notify", "saved_search_id": self.saved.pk}
        )
        self.saved.refresh_from_db()

    def test_new_hits_after_switching_on_land_in_the_inbox_once(self):
        self.toggle()
        self.assertTrue(self.saved.notify)
        self.assertEqual(len(self.saved.known_hits), 1)

        Task.objects.create(user=self.mira, title="Steuer 2026")
        # Not due yet: checked less than an hour ago.
        self.assertEqual(check_saved_searches(), {"checked": 0, "alerted": 0})

        later = timezone.now() + timedelta(hours=2)
        self.assertEqual(check_saved_searches(now=later), {"checked": 1, "alerted": 1})
        alert = UserNotification.objects.get(recipient=self.mira, kind=UserNotification.KIND_SAVED_SEARCH)
        self.assertEqual(alert.title, "1 neuer Treffer für „steuer“")
        self.assertEqual(alert.body, "Steuer 2026")
        self.assertEqual(alert.url, "/search/?q=steuer")

        self.assertEqual(check_saved_searches(now=later + timedelta(hours=2)), {"checked": 1, "alerted": 0})

    def test_switching_off_stops_the_checks(self):
        self.toggle()
        self.toggle()
        self.assertEqual((self.saved.notify, self.saved.known_hits), (False, []))
        Task.objects.create(user=self.mira, title="Steuer 2026")
        self.assertEqual(check_saved_searches(now=timezone.now() + timedelta(hours=2))["checked"], 0)

    def test_search_page_shows_the_bell(self):
        self.toggle()
        response = self.client.get("/search/", {"q": "steuer"})
        self.assertContains(response, 'aria-pressed="true"')
