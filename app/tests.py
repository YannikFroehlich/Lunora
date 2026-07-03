from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from app.models import CalendarEvent, CalendarReminder, CalendarSource, Profile
from app.services.calendar_service import parse_ical_events


class SettingsProfileTests(TestCase):
    def test_settings_page_requires_login(self):
        response = self.client.get("/settings/")

        self.assertRedirects(response, "/login/?next=/settings/")

    def test_default_django_login_path_is_supported(self):
        response = self.client.get("/accounts/login/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Anmelden")

    def test_registration_creates_user_and_profile(self):
        response = self.client.post(
            "/register/",
            {
                "name": "Mira Beispiel",
                "email": "mira@example.com",
                "password1": "sicheres-passwort-42",
                "password2": "sicheres-passwort-42",
            },
        )

        self.assertRedirects(response, "/home/")
        user = User.objects.get(username="mira@example.com")
        self.assertTrue(user.check_password("sicheres-passwort-42"))
        self.assertEqual(user.profile.display_name, "Mira Beispiel")

    def test_logged_in_user_can_update_profile(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/settings/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="form_name" value="profile"')
        self.assertContains(response, "Profil speichern")

        response = self.client.post(
            "/settings/",
            {
                "form_name": "profile",
                "display_name": "Mira Neu",
            },
        )

        self.assertRedirects(response, "/settings/")
        user.refresh_from_db()
        self.assertEqual(user.profile.display_name, "Mira Neu")
        self.assertEqual(user.first_name, "Mira Neu")

    def test_logged_in_user_can_save_appearance_settings(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/settings/",
            {
                "form_name": "appearance",
                "theme": "dark",
                "accent_color": "#7f916b",
                "background_softness": "82",
                "density": "compact",
            },
        )

        self.assertRedirects(response, "/settings/")
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.theme, "dark")
        self.assertEqual(user.profile.accent_color, "#7f916b")
        self.assertEqual(user.profile.background_softness, 82)
        self.assertEqual(user.profile.density, "compact")

    def test_calendar_source_can_be_saved(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        with patch("app.views.sync_calendar_source", return_value={"synced": True, "message": "1 Termine synchronisiert."}):
            response = self.client.post(
                "/calendar/",
                {
                    "form_name": "calendar_source",
                    "ical_url": "https://calendar.google.com/calendar/ical/example/private/basic.ics",
                    "enabled": "on",
                },
            )

        self.assertRedirects(response, "/calendar/")
        source = CalendarSource.objects.get(user=user)
        self.assertEqual(source.ical_url, "https://calendar.google.com/calendar/ical/example/private/basic.ics")
        self.assertTrue(source.enabled)

    def test_calendar_page_displays_saved_events(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        source = CalendarSource.objects.create(
            user=user,
            ical_url="https://calendar.google.com/calendar/ical/example/private/basic.ics",
        )
        start_at = timezone.make_aware(datetime(2026, 7, 8, 9, 0))
        CalendarEvent.objects.create(
            user=user,
            source=source,
            external_id="event-1",
            title="Design Review",
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        with patch("app.views.sync_calendar_source", return_value={"synced": False, "message": "Kalender ist aktuell."}):
            response = self.client.get("/calendar/?year=2026&month=7")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Juli 2026")
        self.assertContains(response, "Design Review")

    def test_home_page_shows_upcoming_calendar_events(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        source = CalendarSource.objects.create(
            user=user,
            ical_url="https://calendar.google.com/calendar/ical/example/private/basic.ics",
        )
        start_at = timezone.now() + timedelta(days=2)
        CalendarEvent.objects.create(
            user=user,
            source=source,
            external_id="home-event-1",
            title="Sprint Planning",
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/home/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nächste Termine")
        self.assertContains(response, "Sprint Planning")
        self.assertNotContains(response, "Meine Notizen")

    def test_calendar_reminders_can_be_added_and_completed(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/calendar/",
            {
                "form_name": "reminder_add",
                "title": "Rechnung bezahlen",
            },
        )

        self.assertRedirects(response, "/calendar/")
        reminder = CalendarReminder.objects.get(user=user)
        self.assertEqual(reminder.title, "Rechnung bezahlen")
        self.assertFalse(reminder.is_done)

        response = self.client.post(
            "/calendar/",
            {
                "form_name": "reminder_toggle",
                "reminder_id": str(reminder.id),
                "is_done": "on",
            },
        )

        self.assertRedirects(response, "/calendar/")
        reminder.refresh_from_db()
        self.assertTrue(reminder.is_done)

    def test_ical_parser_reads_google_events_and_weekly_recurrence(self):
        ical = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:abc@example.com
SUMMARY:Team Sync
DTSTART;TZID=Europe/Berlin:20260706T090000
DTEND;TZID=Europe/Berlin:20260706T100000
RRULE:FREQ=WEEKLY;COUNT=2
END:VEVENT
END:VCALENDAR
"""
        events = parse_ical_events(
            ical,
            window_start=timezone.make_aware(datetime(2026, 7, 1)),
            window_end=timezone.make_aware(datetime(2026, 7, 31)),
        )

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].title, "Team Sync")
        self.assertEqual(events[1].start_at.day, 13)
