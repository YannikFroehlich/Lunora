import importlib
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from app.models import (
    CalendarEvent,
    CalendarEventAttendee,
    Profile,
    SystemSettings,
    VacationPeriod,
    VacationYear,
)


class CalendarFeedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        self.profile = Profile.objects.create(
            user=self.user,
            display_name="Mira",
            timezone_name="Europe/Berlin",
            calendar_feed_token="mira-feed-token",
        )
        self.feed_url = "/calendar/feed/mira-feed-token.ics"

    def create_event(self, user, title, start_at, **extra):
        return CalendarEvent.objects.create(
            user=user,
            title=title,
            start_at=start_at,
            end_at=extra.pop("end_at", start_at + timedelta(hours=1)),
            **extra,
        )

    def test_feed_is_public_with_token_and_404_for_unknown_or_inactive(self):
        response = self.client.get(self.feed_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/calendar; charset=utf-8")
        body = response.content.decode("utf-8")
        self.assertTrue(body.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"))
        self.assertTrue(body.endswith("END:VCALENDAR\r\n"))
        self.assertIn("X-WR-CALNAME:Lunora – Mira", body)

        self.assertEqual(self.client.get("/calendar/feed/wrong-token.ics").status_code, 404)

        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(self.feed_url).status_code, 404)

    def test_feed_contains_own_events_and_accepted_invitations_only(self):
        other = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        Profile.objects.create(user=other, display_name="Lukas")
        start_at = timezone.now() + timedelta(days=3)
        own = self.create_event(
            self.user, "Quartalsplanung", start_at, category=CalendarEvent.CATEGORY_MEETING
        )
        accepted = self.create_event(other, "Gemeinsames Essen", start_at)
        declined = self.create_event(other, "Abgesagtes Treffen", start_at)
        pending = self.create_event(other, "Offene Einladung", start_at)
        self.create_event(other, "Fremder Termin", start_at)
        self.create_event(self.user, "Uralt", timezone.now() - timedelta(days=400))
        CalendarEventAttendee.objects.create(
            event=accepted, user=self.user, status=CalendarEventAttendee.STATUS_ACCEPTED
        )
        CalendarEventAttendee.objects.create(
            event=declined, user=self.user, status=CalendarEventAttendee.STATUS_DECLINED
        )
        CalendarEventAttendee.objects.create(event=pending, user=self.user)

        body = self.client.get(self.feed_url).content.decode("utf-8")

        self.assertIn(f"UID:lunora-event-{own.id}\r\n", body)
        self.assertIn("SUMMARY:[Meeting] Quartalsplanung\r\n", body)
        self.assertIn("CATEGORIES:Meeting\r\n", body)
        self.assertIn("SUMMARY:Gemeinsames Essen\r\n", body)
        self.assertIn("CATEGORIES:Termin\r\n", body)
        self.assertEqual(body.count("BEGIN:VEVENT"), 2)
        for title in ("Abgesagtes Treffen", "Offene Einladung", "Fremder Termin", "Uralt"):
            self.assertNotIn(title, body)

    def test_feed_formats_times_escapes_text_and_folds_long_lines(self):
        start_at = timezone.make_aware(datetime(2099, 3, 2, 8, 15), ZoneInfo("Europe/Berlin"))
        self.create_event(
            self.user,
            "Physik; Kapitel 3, Optik",
            start_at,
            location="Raum 1\nNeubau",
            description="ä" * 60,
            category=CalendarEvent.CATEGORY_PERSONAL,
        )
        all_day_start = timezone.make_aware(datetime(2099, 3, 5), ZoneInfo("Europe/Berlin"))
        self.create_event(
            self.user,
            "Projektabgabe",
            all_day_start,
            end_at=all_day_start + timedelta(days=1),
            is_all_day=True,
            category=CalendarEvent.CATEGORY_DEADLINE,
        )

        body = self.client.get(self.feed_url).content.decode("utf-8")

        self.assertIn("DTSTART:20990302T071500Z\r\nDTEND:20990302T081500Z\r\n", body)
        self.assertIn("SUMMARY:[Privat] Physik\\; Kapitel 3\\, Optik\r\n", body)
        self.assertIn("LOCATION:Raum 1\\nNeubau\r\n", body)
        self.assertIn("DTSTART;VALUE=DATE:20990305\r\nDTEND;VALUE=DATE:20990306\r\n", body)
        self.assertIn("SUMMARY:[Deadline] Projektabgabe\r\n", body)
        for line in body.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75, line)
        unfolded = body.replace("\r\n ", "")
        self.assertIn("DESCRIPTION:" + "ä" * 60 + "\r\n", unfolded)


class CalendarVacationAndRangeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(
            user=self.user,
            display_name="Mira",
            timezone_name="Europe/Berlin",
            calendar_feed_token="mira-token",
        )
        self.client.login(username="mira@example.com", password="secret-12345")

    def _days(self, response):
        return {day["date_input"]: day for week in response.context["calendar_rows"] for day in week}

    def test_month_grid_shows_vacations_and_holidays_as_markers(self):
        VacationYear.objects.create(user=self.user, year=2026, allowance_days=Decimal("30"), subdivision="NW")
        VacationPeriod.objects.create(
            user=self.user, name="tarifurlaub", start_date=date(2026, 12, 21), end_date=date(2026, 12, 23)
        )

        days = self._days(self.client.get("/calendar/?year=2026&month=12"))

        self.assertEqual([marker["label"] for marker in days["2026-12-22"]["markers"]], ["Tarifurlaub"])
        self.assertEqual(days["2026-12-22"]["markers"][0]["tone"], "green")
        self.assertEqual(
            days["2026-12-22"]["markers"][0]["url"], "/vacation-planner/?year=2026&month=12#vacation-calendar"
        )
        self.assertEqual(days["2026-12-22"]["events"], [])
        self.assertEqual(
            days["2026-12-25"]["markers"],
            [
                {
                    "label": "Erster Weihnachtstag",
                    "tone": "red",
                    "url": "/vacation-planner/?year=2026&month=12#vacation-calendar",
                }
            ],
        )
        self.assertEqual(days["2026-12-24"]["markers"], [])

        SystemSettings.objects.update_or_create(pk=1, defaults={"vacation_planner_enabled": False})
        days = self._days(self.client.get("/calendar/?year=2026&month=12"))
        self.assertEqual(days["2026-12-22"]["markers"], [])

    def test_upcoming_lists_include_vacations_in_date_order(self):
        today = timezone.localdate(timezone=ZoneInfo("Europe/Berlin"))
        VacationPeriod.objects.create(
            user=self.user,
            name="sonderurlaub",
            start_date=today + timedelta(days=5),
            end_date=today + timedelta(days=6),
        )
        start_at = timezone.make_aware(
            datetime.combine(today + timedelta(days=9), datetime.min.time()), ZoneInfo("Europe/Berlin")
        )
        CalendarEvent.objects.create(user=self.user, title="Zahnarzt", start_at=start_at, end_at=start_at)

        calendar_items = self.client.get("/calendar/").context["upcoming_events"]
        dashboard_items = self.client.get("/home/").context["upcoming_dashboard_events"]

        self.assertEqual([item["title"] for item in calendar_items], ["Sonderurlaub", "Zahnarzt"])
        self.assertTrue(calendar_items[0]["category"].startswith("Urlaub bis "))
        self.assertEqual([item["title"] for item in dashboard_items], ["Sonderurlaub", "Zahnarzt"])
        self.assertTrue(dashboard_items[0]["time"].startswith("Urlaub bis "))
        self.assertTrue(dashboard_items[0]["url"].startswith("/vacation-planner/?year="))

    def test_feed_contains_vacations_as_all_day_events(self):
        period = VacationPeriod.objects.create(
            user=self.user,
            name="tarifurlaub",
            start_date=date(2099, 7, 1),
            end_date=date(2099, 7, 14),
            notes="Ostsee",
        )

        body = self.client.get("/calendar/feed/mira-token.ics").content.decode("utf-8")

        self.assertIn(
            f"BEGIN:VEVENT\r\nUID:lunora-vacation-{period.pk}\r\n",
            body,
        )
        self.assertIn(
            "DTSTART;VALUE=DATE:20990701\r\nDTEND;VALUE=DATE:20990715\r\nSUMMARY:Tarifurlaub\r\n", body
        )
        self.assertIn("CATEGORIES:Urlaub\r\nDESCRIPTION:Ostsee\r\n", body)

    def test_event_can_span_several_days_with_an_end_date(self):
        response = self.client.post(
            "/calendar/?year=2099&month=8",
            {
                "form_name": "calendar_event_add",
                "title": "Messe",
                "event_date": "2099-08-10",
                "end_date": "2099-08-12",
                "is_all_day": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        event = CalendarEvent.objects.get(user=self.user, title="Messe")
        berlin = ZoneInfo("Europe/Berlin")
        self.assertEqual(event.start_at.astimezone(berlin), datetime(2099, 8, 10, tzinfo=berlin))
        self.assertEqual(event.end_at.astimezone(berlin), datetime(2099, 8, 13, tzinfo=berlin))
        days = self._days(self.client.get("/calendar/?year=2099&month=8"))
        self.assertEqual(
            [len(days[f"2099-08-{day:02d}"]["events"]) for day in (9, 10, 11, 12, 13)], [0, 1, 1, 1, 0]
        )
        self.assertEqual(days["2099-08-11"]["events"][0]["edit_end_date"], "2099-08-12")

        response = self.client.post(
            "/calendar/?year=2099&month=8",
            {
                "form_name": "calendar_event_add",
                "title": "Rückwärts",
                "event_date": "2099-08-10",
                "end_date": "2099-08-09",
                "is_all_day": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("end_date", response.context["event_form"].errors)
        self.assertFalse(CalendarEvent.objects.filter(title="Rückwärts").exists())


class CalendarCategoryMigrationTests(TestCase):
    def test_school_categories_become_appointments(self):
        migration = importlib.import_module("app.migrations.0052_general_calendar_categories")
        user = User.objects.create_user(username="mira", password="secret-12345")
        start_at = timezone.now()
        for category in ("exam", "test", "presentation", "deadline", "other"):
            CalendarEvent.objects.create(
                user=user, title=category, category=category, start_at=start_at, end_at=start_at
            )

        migration.map_school_categories(django_apps, None)

        self.assertEqual(
            dict(CalendarEvent.objects.values_list("title", "category")),
            {
                "exam": "appointment",
                "test": "appointment",
                "presentation": "appointment",
                "deadline": "deadline",
                "other": "other",
            },
        )
