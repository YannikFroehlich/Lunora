from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from app.models import (
    CalendarEvent,
    CalendarEventAttendee,
    Profile,
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
        own = self.create_event(self.user, "Mathe", start_at, category=CalendarEvent.CATEGORY_EXAM)
        accepted = self.create_event(other, "Gemeinsames Referat", start_at)
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
        self.assertIn("SUMMARY:[Klassenarbeit] Mathe\r\n", body)
        self.assertIn("CATEGORIES:Klassenarbeit\r\n", body)
        self.assertIn("SUMMARY:Gemeinsames Referat\r\n", body)
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
            category=CalendarEvent.CATEGORY_TEST,
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
        self.assertIn("SUMMARY:[Test] Physik\\; Kapitel 3\\, Optik\r\n", body)
        self.assertIn("LOCATION:Raum 1\\nNeubau\r\n", body)
        self.assertIn("DTSTART;VALUE=DATE:20990305\r\nDTEND;VALUE=DATE:20990306\r\n", body)
        self.assertIn("SUMMARY:[Abgabe] Projektabgabe\r\n", body)
        for line in body.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75, line)
        unfolded = body.replace("\r\n ", "")
        self.assertIn("DESCRIPTION:" + "ä" * 60 + "\r\n", unfolded)
