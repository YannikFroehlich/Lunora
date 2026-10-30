from datetime import date, datetime, time
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from app.models import CalendarEvent, Profile, Task, TaskLabel
from app.services.quick_add import parse_quick_add

# A Friday afternoon.
TODAY = date(2026, 10, 30)
NOW = time(15, 0)
BERLIN = ZoneInfo("Europe/Berlin")


class QuickAddParserTests(SimpleTestCase):
    def parse(self, text, **kwargs):
        result = parse_quick_add(text, today=TODAY, now_time=NOW, **kwargs)
        return result.title, result.due_date, result.due_time, result.priority

    def test_relative_days_weekdays_and_periods(self):
        cases = {
            "Zahnarzt morgen": ("Zahnarzt", date(2026, 10, 31)),
            "Müll raus heute": ("Müll raus", TODAY),
            "Paket übermorgen abholen": ("Paket abholen", date(2026, 11, 1)),
            "Bericht bis Montag": ("Bericht", date(2026, 11, 2)),
            # The weekday itself means today, "nächsten" the one a week later.
            "Sport Freitag": ("Sport", TODAY),
            "Sport nächsten Freitag": ("Sport", date(2026, 11, 6)),
            "Steuer in 2 Wochen": ("Steuer", date(2026, 11, 13)),
            "Ölwechsel in einem Monat": ("Ölwechsel", date(2026, 11, 30)),
            "Gießen in drei Tagen": ("Gießen", date(2026, 11, 2)),
        }
        for text, (title, due_date) in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.parse(text)[:2], (title, due_date))

    def test_explicit_dates_roll_into_next_year_once_passed(self):
        cases = {
            "Geburtstag 15.3.": date(2027, 3, 15),
            "Termin am 3.11.": date(2026, 11, 3),
            "Vertrag 01.02.2028": date(2028, 2, 1),
            "Vertrag 1.2.28": date(2028, 2, 1),
            "Reise 2027-05-04": date(2027, 5, 4),
            "Konzert 5. Dezember": date(2026, 12, 5),
            "Konzert am 4. Mai 2027": date(2027, 5, 4),
            "Abgabe 2. Okt.": date(2027, 10, 2),
        }
        for text, due_date in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.parse(text)[1], due_date)

    def test_times_and_a_bare_time_that_passed_means_tomorrow(self):
        self.assertEqual(self.parse("Call morgen 14 Uhr"), ("Call", date(2026, 10, 31), time(14), None))
        self.assertEqual(self.parse("Call morgen um 9:30")[2], time(9, 30))
        self.assertEqual(self.parse("Call Montag 14.45 Uhr.")[1:3], (date(2026, 11, 2), time(14, 45)))
        self.assertEqual(self.parse("Anruf 18:00")[1:3], (TODAY, time(18)))
        self.assertEqual(self.parse("Anruf um 8")[1:3], (date(2026, 10, 31), time(8)))

    def test_priority_and_known_labels(self):
        work = object()
        result = parse_quick_add(
            "Bericht #Arbeit Freitag !hoch #unbekannt", today=TODAY, now_time=NOW, labels={"arbeit": work}
        )

        self.assertEqual(result.title, "Bericht #unbekannt")
        self.assertEqual(result.priority, "high")
        self.assertEqual(result.labels, [work])
        self.assertEqual(self.parse("Lesen !3")[3], "low")

    def test_phrases_inside_words_and_invalid_values_stay_in_the_title(self):
        for text in (
            "Montag-Meeting vorbereiten",
            "Morgenlauf",
            "Version 2.0 testen",
            "Raum 25 Uhrwerk",
            "31.2. prüfen",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.parse(text), (text, None, None, None))
        self.assertEqual(self.parse("Wecker 25 Uhr")[1:3], (None, None))

    def test_only_a_date_keeps_the_text_as_title(self):
        result = parse_quick_add("morgen", today=TODAY, now_time=NOW)

        self.assertEqual((result.title, result.due_date), ("morgen", None))


@mock.patch("app.forms.localtime_for_user", return_value=datetime(2026, 10, 30, 15, 0, tzinfo=BERLIN))
class QuickAddFormTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mira@example.com", password="secret-12345")
        Profile.objects.create(user=self.user, display_name="Mira", timezone_name="Europe/Berlin")
        self.client.login(username="mira@example.com", password="secret-12345")

    def test_new_task_takes_date_priority_and_labels_from_its_title(self, _now):
        label = TaskLabel.objects.create(owner=self.user, name="Arbeit")

        response = self.client.post(
            "/tasks/",
            {"form_name": "task_add", "title": "Bericht abgeben Montag 14 Uhr !hoch #arbeit"},
            follow=True,
        )

        task = Task.objects.get()
        self.assertEqual(task.title, "Bericht abgeben")
        self.assertEqual(task.due_at, datetime(2026, 11, 2, 14, 0, tzinfo=BERLIN))
        self.assertEqual(task.priority, Task.PRIORITY_HIGH)
        self.assertEqual(list(task.labels.all()), [label])
        self.assertContains(response, "Aufgabe „Bericht abgeben“ erstellt – fällig")

    def test_date_only_gets_the_default_time_and_explicit_fields_win(self, _now):
        self.client.post("/tasks/", {"form_name": "task_add", "title": "Steuer morgen"})
        self.client.post(
            "/tasks/",
            {
                "form_name": "task_add",
                "title": "Miete morgen !niedrig",
                "due_at": "2026-11-05T08:00",
                "priority": Task.PRIORITY_HIGH,
            },
        )

        self.assertEqual(Task.objects.get(title="Steuer").due_at, datetime(2026, 10, 31, 9, 0, tzinfo=BERLIN))
        rent = Task.objects.get(title="Miete")
        self.assertEqual(rent.due_at, datetime(2026, 11, 5, 8, 0, tzinfo=BERLIN))
        self.assertEqual(rent.priority, Task.PRIORITY_HIGH)

    def test_editing_keeps_the_title_as_typed(self, _now):
        task = Task.objects.create(user=self.user, title="Alt")

        self.client.post("/tasks/", {"form_name": "task_edit", "task_id": task.id, "title": "Treffen morgen"})

        task.refresh_from_db()
        self.assertEqual((task.title, task.due_at), ("Treffen morgen", None))

    def test_calendar_quick_add_creates_timed_and_all_day_events(self, _now):
        self.client.post("/calendar/", {"form_name": "calendar_quick_add", "text": "Zahnarzt morgen 14 Uhr"})
        response = self.client.post(
            "/calendar/", {"form_name": "calendar_quick_add", "text": "Urlaub buchen 15.3."}, follow=True
        )

        dentist = CalendarEvent.objects.get(title="Zahnarzt")
        self.assertEqual(
            (dentist.start_at, dentist.end_at, dentist.is_all_day),
            (datetime(2026, 10, 31, 14, tzinfo=BERLIN), datetime(2026, 10, 31, 15, tzinfo=BERLIN), False),
        )
        trip = CalendarEvent.objects.get(title="Urlaub buchen")
        self.assertEqual((trip.start_at, trip.is_all_day), (datetime(2027, 3, 15, tzinfo=BERLIN), True))
        self.assertContains(response, "Termin „Urlaub buchen“ am")

    def test_calendar_quick_add_without_a_date_shows_an_error(self, _now):
        response = self.client.post(
            "/calendar/", {"form_name": "calendar_quick_add", "text": "Irgendwann Zahnarzt"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kein Datum erkannt")
        self.assertFalse(CalendarEvent.objects.exists())
