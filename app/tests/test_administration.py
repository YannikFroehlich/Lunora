from datetime import timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, TestCase
from django.utils import timezone

from app.models import (
    CalendarEvent,
    CalendarReminder,
    Profile,
    SystemSettings,
)


class AdministrationFeatureFlagTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com",
            email="mira@example.com",
            password="secret-12345",
        )
        Profile.objects.create(user=self.user, display_name="Mira")
        self.staff = User.objects.create_user(
            username="staff@example.com",
            email="staff@example.com",
            password="secret-12345",
            is_staff=True,
        )
        self.superuser = User.objects.create_superuser(
            username="admin@example.com",
            email="admin@example.com",
            password="secret-12345",
        )

    def test_administration_requires_superuser(self):
        response = self.client.get("/administration/")
        self.assertRedirects(response, "/login/?next=/administration/")

        self.client.login(username="mira@example.com", password="secret-12345")
        response = self.client.get("/administration/")
        self.assertEqual(response.status_code, 403)

        self.client.logout()
        self.client.login(username="staff@example.com", password="secret-12345")
        response = self.client.get("/administration/")
        self.assertEqual(response.status_code, 403)

        self.client.logout()
        self.client.login(username="admin@example.com", password="secret-12345")
        response = self.client.get("/administration/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Administration")

    def test_superuser_can_save_system_settings(self):
        self.client.login(username="admin@example.com", password="secret-12345")

        response = self.client.post(
            "/administration/",
            {
                "form_name": "system_settings",
                "normal_login_enabled": "on",
                "calendar_reminders_enabled": "on",
                "weather_enabled": "on",
            },
        )

        self.assertRedirects(response, "/administration/")
        settings_obj = SystemSettings.objects.get(pk=1)
        self.assertTrue(settings_obj.normal_login_enabled)
        self.assertFalse(settings_obj.calendar_event_creation_enabled)
        self.assertTrue(settings_obj.calendar_reminders_enabled)
        self.assertFalse(settings_obj.calendar_feed_enabled)
        self.assertFalse(settings_obj.messages_enabled)
        self.assertTrue(settings_obj.weather_enabled)
        self.assertEqual(settings_obj.updated_by, self.superuser)

    def test_login_lock_blocks_regular_login_and_registration_but_allows_admin(self):
        SystemSettings.objects.create(normal_login_enabled=False)

        response = self.client.post(
            "/login/",
            {"username": "mira@example.com", "password": "secret-12345"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)
        self.assertContains(response, "Der Login ist für Nutzer vorübergehend deaktiviert")

        response = self.client.post(
            "/register/",
            {
                "name": "Neue Person",
                "email": "neu@example.com",
                "password1": "secret-12345",
                "password2": "secret-12345",
            },
        )
        self.assertEqual(response.status_code, 503)
        self.assertFalse(User.objects.filter(username="neu@example.com").exists())

        response = self.client.post(
            "/login/",
            {"username": "admin@example.com", "password": "secret-12345"},
        )
        self.assertRedirects(response, "/home/")

    def test_login_lock_blocks_password_reset_requests(self):
        SystemSettings.objects.create(normal_login_enabled=False)

        response = self.client.post("/password-reset/", {"email": "mira@example.com"})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(mail.outbox), 0)

    def test_disabled_calendar_event_creation_blocks_direct_post(self):
        SystemSettings.objects.create(calendar_event_creation_enabled=False)
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/calendar/",
            {
                "form_name": "calendar_event_add",
                "title": "Blockierter Termin",
                "event_date": "2026-08-05",
                "start_time": "10:00",
                "end_time": "11:00",
            },
        )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(CalendarEvent.objects.filter(title="Blockierter Termin").exists())

    def test_disabled_calendar_event_creation_blocks_delete(self):
        event = CalendarEvent.objects.create(
            user=self.user,
            title="Bleibt bestehen",
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, hours=1),
        )
        SystemSettings.objects.create(calendar_event_creation_enabled=False)
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/calendar/",
            {"form_name": "calendar_event_delete", "event_id": str(event.id)},
        )

        self.assertEqual(response.status_code, 503)
        self.assertTrue(CalendarEvent.objects.filter(pk=event.id).exists())

    def test_disabled_calendar_event_creation_hides_empty_state_actions(self):
        SystemSettings.objects.create(calendar_event_creation_enabled=False)
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/calendar/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-calendar-empty-action="event-today"')
        self.assertNotContains(response, 'data-calendar-empty-action="event-upcoming"')

    def test_disabled_reminders_block_direct_post(self):
        SystemSettings.objects.create(calendar_reminders_enabled=False)
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/calendar/",
            {"form_name": "reminder_add", "title": "Blockierte Erinnerung"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(CalendarReminder.objects.filter(title="Blockierte Erinnerung").exists())

    def test_disabled_calendar_feed_hides_settings_link_and_blocks_feed(self):
        profile = Profile.objects.get(user=self.user)
        profile.calendar_feed_token = "disabled-feed-token"
        profile.save(update_fields=["calendar_feed_token"])
        SystemSettings.objects.create(calendar_feed_enabled=False)
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/settings/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kalender-Abo pausiert")
        self.assertNotContains(response, "disabled-feed-token")

        response = self.client.post("/settings/", {"form_name": "calendar_feed_reset"})
        self.assertEqual(response.status_code, 503)
        profile.refresh_from_db()
        self.assertEqual(profile.calendar_feed_token, "disabled-feed-token")

        self.client.logout()
        response = self.client.get("/calendar/feed/disabled-feed-token.ics")
        self.assertEqual(response.status_code, 503)

    def test_disabled_messages_and_weather_return_unavailable(self):
        SystemSettings.objects.create(messages_enabled=False, weather_enabled=False)
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/messages/")
        self.assertEqual(response.status_code, 503)
        response = self.client.get("/messages/live/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["ok"], False)

        response = self.client.get("/weather/")
        self.assertEqual(response.status_code, 503)
        response = self.client.get("/weather/suggest/?q=Berlin")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["ok"], False)

    def test_force_logout_removes_other_authenticated_sessions(self):
        admin_client = Client()
        user_client = Client()
        other_admin_client = Client()
        self.assertTrue(admin_client.login(username="admin@example.com", password="secret-12345"))
        self.assertTrue(user_client.login(username="mira@example.com", password="secret-12345"))
        self.assertTrue(other_admin_client.login(username="admin@example.com", password="secret-12345"))

        response = admin_client.post("/administration/", {"form_name": "force_logout_all"})
        self.assertRedirects(response, "/administration/")

        self.assertEqual(admin_client.get("/administration/").status_code, 200)
        self.assertRedirects(user_client.get("/home/"), "/login/?next=/home/")
        self.assertRedirects(other_admin_client.get("/administration/"), "/login/?next=/administration/")
