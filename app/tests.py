import json
import os
import re
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from email.message import Message
from io import StringIO
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from app.forms import CalendarSourceForm, ProfileForm
from app.models import CalendarEvent, CalendarEventAttendee, CalendarReminder, CalendarSource, ChatMessage, ChatMessageAttachment, ChatMessageReaction, Conversation, ConversationMember, CustomHoliday, Note, NoteActivityNotification, NoteAttachment, NoteCommentThread, NoteFolder, NoteLink, NoteShare, NoteTemplate, NoteUserState, NoteVersion, OfficialHoliday, Profile, SystemSettings, VacationPeriod, VacationYear, WeatherLocation, WeeklySummaryDelivery
from app.services.calendar_service import fetch_ical, parse_ical_events
from app.services.calendar_sync_queue import queue_calendar_sources
from app.services.dashboard import DASHBOARD_WIDGET_IDS, default_dashboard_layout, normalize_dashboard_layout
from app.services.image_uploads import PROFILE_IMAGE_MAX_BYTES
from app.services.notifications import (
    claim_due_weather_alerts,
    send_due_reminder_emails,
    send_new_invitation_emails,
    send_note_activity_emails,
    send_weekly_summaries,
)
from app.services.scheduled_tasks import sync_due_calendars
from app.services.weather_service import (
    WEATHER_MAP_LAYERS,
    _build_weather_alert,
    delete_weather_location,
    fetch_weather_map_tile,
    get_location_suggestions,
    get_weather_alert_for_location,
    get_weather_at_coordinates,
    get_weather_context,
    list_weather_locations,
    save_weather_location,
    set_default_weather_location,
)
from app.services.note_content import NOTE_TEMPLATES, empty_note_document, validate_note_document
from app.services.notes import prune_note_versions, purge_expired_notes
from app.services.vacation_planner import annual_summary, calculate_period
from app.views.message_views import _build_inbox_items
from lunora.settings import BASE_DIR, database_config


PNG_1X1_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def note_document(text="Gedanke"):
    return {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "attrs": {"textAlign": None},
                "content": [{"type": "text", "text": text, "marks": [{"type": "bold"}]}],
            }
        ],
    }


class DatabaseConfigurationTests(SimpleTestCase):
    def test_local_development_uses_sqlite(self):
        with patch.dict(
            os.environ,
            {
                "DJANGO_DATABASE_ENGINE": "sqlite",
                "DJANGO_SQLITE_PATH": "local.sqlite3",
            },
            clear=True,
        ):
            config = database_config(debug=True)["default"]

        self.assertEqual(config["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(config["NAME"], BASE_DIR / "local.sqlite3")

    def test_production_rejects_sqlite(self):
        with patch.dict(
            os.environ,
            {"DJANGO_DATABASE_ENGINE": "sqlite"},
            clear=True,
        ):
            with self.assertRaisesMessage(
                ImproperlyConfigured,
                "DJANGO_DATABASE_ENGINE muss bei DJANGO_DEBUG=false auf postgresql gesetzt sein.",
            ):
                database_config(debug=False)

    def test_postgresql_uses_server_connection_settings(self):
        with patch.dict(
            os.environ,
            {
                "DJANGO_DATABASE_ENGINE": "postgresql",
                "DJANGO_DB_NAME": "lunora",
                "DJANGO_DB_USER": "lunora_user",
                "DJANGO_DB_PASSWORD": "secret",
                "DJANGO_DB_HOST": "db.internal",
                "DJANGO_DB_PORT": "5433",
                "DJANGO_DB_CONN_MAX_AGE": "120",
                "DJANGO_DB_CONNECT_TIMEOUT": "7",
                "DJANGO_DB_SSLMODE": "require",
            },
            clear=True,
        ):
            config = database_config(debug=False)["default"]

        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(config["NAME"], "lunora")
        self.assertEqual(config["USER"], "lunora_user")
        self.assertEqual(config["PASSWORD"], "secret")
        self.assertEqual(config["HOST"], "db.internal")
        self.assertEqual(config["PORT"], "5433")
        self.assertEqual(config["CONN_MAX_AGE"], 120)
        self.assertTrue(config["CONN_HEALTH_CHECKS"])
        self.assertEqual(
            config["OPTIONS"],
            {"connect_timeout": 7, "sslmode": "require"},
        )

    def test_postgresql_requires_database_name_and_user(self):
        with patch.dict(
            os.environ,
            {"DJANGO_DATABASE_ENGINE": "postgresql"},
            clear=True,
        ):
            with self.assertRaisesMessage(
                ImproperlyConfigured,
                "DJANGO_DB_NAME, DJANGO_DB_USER, DJANGO_DB_PASSWORD muss für PostgreSQL gesetzt sein.",
            ):
                database_config(debug=False)

    def test_production_rejects_placeholder_database_password(self):
        with patch.dict(
            os.environ,
            {
                "DJANGO_DATABASE_ENGINE": "postgresql",
                "DJANGO_DB_NAME": "lunora",
                "DJANGO_DB_USER": "lunora",
                "DJANGO_DB_PASSWORD": "change-me",
            },
            clear=True,
        ):
            with self.assertRaisesMessage(
                ImproperlyConfigured,
                "DJANGO_DB_PASSWORD muss für PostgreSQL gesetzt sein.",
            ):
                database_config(debug=False)


class FakeJsonResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, _size=-1):
        return self.payload


@override_settings(
    CLOUDFLARE_TURNSTILE_REQUIRED=True,
    CLOUDFLARE_TURNSTILE_SITE_KEY="test-site-key",
    CLOUDFLARE_TURNSTILE_SECRET_KEY="test-secret-key",
    CLOUDFLARE_TURNSTILE_EXPECTED_HOSTNAME="lunora.yfserver.de",
    CLOUDFLARE_TURNSTILE_TIMEOUT=5,
)
class TurnstileValidationTests(SimpleTestCase):
    @patch("app.services.turnstile.urlopen")
    def test_accepts_expected_hostname_and_action(self, mocked_urlopen):
        from app.services.turnstile import verify_registration_token

        mocked_urlopen.return_value = FakeJsonResponse(
            {
                "success": True,
                "hostname": "lunora.yfserver.de",
                "action": "register",
            }
        )

        self.assertTrue(verify_registration_token("valid-token"))

    @patch("app.services.turnstile.urlopen")
    def test_rejects_wrong_hostname(self, mocked_urlopen):
        from app.services.turnstile import verify_registration_token

        mocked_urlopen.return_value = FakeJsonResponse(
            {
                "success": True,
                "hostname": "attacker.example",
                "action": "register",
            }
        )

        self.assertFalse(verify_registration_token("valid-token"))

    @patch("app.services.turnstile.urlopen", side_effect=URLError("offline"))
    def test_fails_closed_when_siteverify_is_unavailable(self, _mocked_urlopen):
        from app.services.turnstile import verify_registration_token

        self.assertFalse(verify_registration_token("valid-token"))



@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SettingsProfileTests(TestCase):
    def test_settings_page_requires_login(self):
        response = self.client.get("/settings/")

        self.assertRedirects(response, "/login/?next=/settings/")

    def test_default_django_login_path_is_supported(self):
        response = self.client.get("/accounts/login/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Anmelden")

    def test_login_accepts_email_for_user_with_separate_username(self):
        User.objects.create_user(username="mira", email="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/login/",
            {"username": "MIRA@example.com", "password": "secret-12345"},
        )

        self.assertRedirects(response, "/home/")
        self.assertTrue(response.wsgi_request.user.is_authenticated)
        self.assertEqual(response.wsgi_request.user.username, "mira")

    def test_login_accepts_username(self):
        User.objects.create_user(username="mira", email="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/login/",
            {"username": "mira", "password": "secret-12345"},
        )

        self.assertRedirects(response, "/home/")
        self.assertTrue(response.wsgi_request.user.is_authenticated)
        self.assertEqual(response.wsgi_request.user.username, "mira")

    def test_repeated_failed_logins_lock_out_further_attempts(self):
        cache.clear()
        User.objects.create_user(username="mira", email="mira@example.com", password="secret-12345")

        for _ in range(5):
            response = self.client.post(
                "/login/",
                {"username": "mira@example.com", "password": "wrong-password"},
            )
            self.assertFalse(response.wsgi_request.user.is_authenticated)

        response = self.client.post(
            "/login/",
            {"username": "mira@example.com", "password": "secret-12345"},
        )

        self.assertFalse(response.wsgi_request.user.is_authenticated)
        self.assertContains(response, "Zu viele fehlgeschlagene Anmeldeversuche")

        response = self.client.post(
            "/login/",
            {"username": "mira", "password": "secret-12345"},
        )

        self.assertFalse(response.wsgi_request.user.is_authenticated)
        self.assertContains(response, "Zu viele fehlgeschlagene Anmeldeversuche")

    def test_successful_login_clears_previous_failed_attempts(self):
        cache.clear()
        User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")

        for _ in range(4):
            self.client.post("/login/", {"username": "mira@example.com", "password": "wrong-password"})

        response = self.client.post(
            "/login/",
            {"username": "mira@example.com", "password": "secret-12345"},
        )

        self.assertRedirects(response, "/home/")
        self.assertTrue(response.wsgi_request.user.is_authenticated)

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

    @override_settings(
        CLOUDFLARE_TURNSTILE_REQUIRED=True,
        CLOUDFLARE_TURNSTILE_SITE_KEY="test-site-key",
        CLOUDFLARE_TURNSTILE_SECRET_KEY="test-secret-key",
        CLOUDFLARE_TURNSTILE_EXPECTED_HOSTNAME="lunora.yfserver.de",
    )
    @patch("app.views.auth_views.verify_registration_token", return_value=False)
    def test_registration_rejects_invalid_turnstile_token(self, mocked_verify):
        response = self.client.post(
            "/register/",
            {
                "name": "Bot Beispiel",
                "email": "bot@example.com",
                "password1": "sicheres-passwort-42",
                "password2": "sicheres-passwort-42",
                "cf-turnstile-response": "invalid-token",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sicherheitsprüfung ist fehlgeschlagen")
        self.assertFalse(User.objects.filter(email="bot@example.com").exists())
        mocked_verify.assert_called_once_with("invalid-token")

    @override_settings(
        CLOUDFLARE_TURNSTILE_REQUIRED=True,
        CLOUDFLARE_TURNSTILE_SITE_KEY="test-site-key",
        CLOUDFLARE_TURNSTILE_SECRET_KEY="test-secret-key",
        CLOUDFLARE_TURNSTILE_EXPECTED_HOSTNAME="lunora.yfserver.de",
    )
    @patch("app.views.auth_views.verify_registration_token", return_value=True)
    def test_registration_accepts_valid_turnstile_token(self, mocked_verify):
        response = self.client.post(
            "/register/",
            {
                "name": "Mira Beispiel",
                "email": "mira-turnstile@example.com",
                "password1": "sicheres-passwort-42",
                "password2": "sicheres-passwort-42",
                "cf-turnstile-response": "valid-token",
            },
        )

        self.assertRedirects(response, "/home/")
        self.assertTrue(User.objects.filter(email="mira-turnstile@example.com").exists())
        mocked_verify.assert_called_once_with("valid-token")

    def test_password_reset_flow_updates_password_and_allows_login(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="old-secret-123"
        )

        self.assertEqual(self.client.get("/password-reset/").status_code, 200)

        response = self.client.post("/password-reset/", {"email": "mira@example.com"})
        self.assertRedirects(response, "/password-reset/done/")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Lunora – Passwort zurücksetzen")
        self.assertEqual(mail.outbox[0].to, ["mira@example.com"])

        reset_url = re.search(r"http://\S+/reset/\S+/", mail.outbox[0].body).group(0)
        reset_path = reset_url.split("testserver", 1)[1]

        redirect_response = self.client.get(reset_path)
        self.assertEqual(redirect_response.status_code, 302)
        confirm_path = redirect_response["Location"]

        confirm_page = self.client.get(confirm_path)
        self.assertContains(confirm_page, "Neues Passwort festlegen")

        response = self.client.post(
            confirm_path,
            {"new_password1": "brandneues-passwort-99", "new_password2": "brandneues-passwort-99"},
        )
        self.assertRedirects(response, "/reset/done/")

        user.refresh_from_db()
        self.assertTrue(user.check_password("brandneues-passwort-99"))

        login_response = self.client.post(
            "/login/", {"username": "mira@example.com", "password": "brandneues-passwort-99"}
        )
        self.assertRedirects(login_response, "/home/")

    def test_password_reset_confirm_rejects_invalid_link(self):
        response = self.client.get("/reset/not-a-real-uid/not-a-real-token/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Link ungültig")

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

        self.assertRedirects(response, "/home/")
        user.refresh_from_db()
        self.assertEqual(user.profile.display_name, "Mira Neu")
        self.assertEqual(user.first_name, "Mira Neu")

    def test_profile_form_accepts_valid_profile_image(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        profile = Profile.objects.create(user=user, display_name="Mira")
        upload = SimpleUploadedFile("avatar.png", PNG_1X1_BYTES, content_type="image/png")

        form = ProfileForm(data={"display_name": "Mira"}, files={"profile_image": upload}, instance=profile)

        self.assertTrue(form.is_valid(), form.errors)

    def test_profile_form_rejects_spoofed_profile_image(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        profile = Profile.objects.create(user=user, display_name="Mira")
        upload = SimpleUploadedFile("avatar.png", b"not really an image", content_type="image/png")

        form = ProfileForm(data={"display_name": "Mira"}, files={"profile_image": upload}, instance=profile)

        self.assertFalse(form.is_valid())
        self.assertIn("profile_image", form.errors)

    def test_profile_form_rejects_oversized_profile_image(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        profile = Profile.objects.create(user=user, display_name="Mira")
        upload = SimpleUploadedFile(
            "avatar.png",
            PNG_1X1_BYTES + (b"x" * PROFILE_IMAGE_MAX_BYTES),
            content_type="image/png",
        )

        form = ProfileForm(data={"display_name": "Mira"}, files={"profile_image": upload}, instance=profile)

        self.assertFalse(form.is_valid())
        self.assertIn("profile_image", form.errors)

    def test_profile_form_deletes_replaced_profile_image(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            user = User.objects.create_user(
                username="mira@example.com",
                email="mira@example.com",
                password="secret-12345",
            )
            profile = Profile.objects.create(user=user, display_name="Mira")
            first_upload = SimpleUploadedFile("avatar.png", PNG_1X1_BYTES, content_type="image/png")
            form = ProfileForm(data={"display_name": "Mira"}, files={"profile_image": first_upload}, instance=profile)
            self.assertTrue(form.is_valid(), form.errors)
            profile = form.save()
            old_image_name = profile.profile_image.name
            self.assertTrue(default_storage.exists(old_image_name))

            second_upload = SimpleUploadedFile("avatar-new.png", PNG_1X1_BYTES, content_type="image/png")
            form = ProfileForm(
                data={"display_name": "Mira Neu"},
                files={"profile_image": second_upload},
                instance=profile,
            )
            self.assertTrue(form.is_valid(), form.errors)
            profile = form.save()

            self.assertFalse(default_storage.exists(old_image_name))
            self.assertTrue(default_storage.exists(profile.profile_image.name))

    def test_profile_form_deletes_cleared_profile_image(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            user = User.objects.create_user(
                username="mira@example.com",
                email="mira@example.com",
                password="secret-12345",
            )
            profile = Profile.objects.create(user=user, display_name="Mira")
            upload = SimpleUploadedFile("avatar.png", PNG_1X1_BYTES, content_type="image/png")
            form = ProfileForm(data={"display_name": "Mira"}, files={"profile_image": upload}, instance=profile)
            self.assertTrue(form.is_valid(), form.errors)
            profile = form.save()
            old_image_name = profile.profile_image.name
            self.assertTrue(default_storage.exists(old_image_name))

            form = ProfileForm(
                data={"display_name": "Mira", "profile_image-clear": "on"},
                instance=profile,
            )
            self.assertTrue(form.is_valid(), form.errors)
            profile = form.save()

            self.assertFalse(profile.profile_image)
            self.assertFalse(default_storage.exists(old_image_name))

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

        self.assertRedirects(response, "/home/")
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.theme, "dark")
        self.assertEqual(user.profile.accent_color, "#7f916b")
        self.assertEqual(user.profile.background_softness, 82)
        self.assertEqual(user.profile.density, "compact")

    def test_logged_in_user_can_save_region_settings(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/settings/",
            {
                "form_name": "appearance",
                "theme": "light",
                "accent_color": "#c2a276",
                "background_softness": "55",
                "density": "comfortable",
                "date_format": "iso",
                "time_format": "12h",
                "timezone_name": "UTC",
            },
        )

        self.assertRedirects(response, "/home/")
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.date_format, "iso")
        self.assertEqual(user.profile.time_format, "12h")
        self.assertEqual(user.profile.timezone_name, "UTC")

    def test_logged_in_user_can_save_preferences(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/settings/",
            {
                "form_name": "preferences",
                "notify_reminders": "on",
                "weekly_summary": "on",
                "usage_data_enabled": "on",
                "weather_default_city": "Berlin,de",
            },
        )

        self.assertRedirects(response, "/home/")
        user.profile.refresh_from_db()
        self.assertFalse(user.profile.notify_email)
        self.assertTrue(user.profile.notify_reminders)
        self.assertFalse(user.profile.notify_desktop)
        self.assertTrue(user.profile.weekly_summary)
        self.assertTrue(user.profile.analytics_enabled)
        self.assertFalse(user.profile.usage_data_enabled)
        self.assertEqual(user.profile.weather_default_city, "Berlin,de")

    def test_settings_hide_unimplemented_analytics_controls(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/settings/")

        self.assertNotContains(response, 'name="analytics_enabled"')
        self.assertNotContains(response, 'name="usage_data_enabled"')
        self.assertContains(response, "Erinnerungszustellung")

    def test_settings_save_shows_feedback_message(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/settings/",
            {
                "form_name": "preferences",
                "notify_email": "on",
                "notify_reminders": "on",
                "notify_desktop": "on",
                "analytics_enabled": "on",
                "weather_default_city": "Bünde,de",
            },
            follow=True,
        )

        self.assertContains(response, "Präferenzen gespeichert.")

    def test_calendar_source_can_be_added_and_queued_from_settings(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        with patch("app.services.calendar_service.fetch_ical") as fetch_calendar:
            response = self.client.post(
                "/settings/",
                {
                    "form_name": "calendar_source_add",
                    "new-name": "Arbeit",
                    "new-ical_url": "https://calendar.google.com/calendar/ical/settings/private/basic.ics",
                    "new-color": "green",
                    "new-enabled": "on",
                },
            )

        self.assertRedirects(response, "/home/")
        source = CalendarSource.objects.get(user=user)
        self.assertEqual(source.name, "Arbeit")
        self.assertEqual(source.ical_url, "https://calendar.google.com/calendar/ical/settings/private/basic.ics")
        self.assertEqual(source.color, "green")
        self.assertTrue(source.is_visible)
        self.assertTrue(source.enabled)
        self.assertIsNotNone(source.sync_requested_at)
        fetch_calendar.assert_not_called()

    def test_calendar_source_is_kept_while_first_sync_is_queued(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/settings/",
            {
                "form_name": "calendar_source_add",
                "new-name": "Familie",
                "new-ical_url": "https://calendar.google.com/calendar/ical/family/private/basic.ics",
                "new-color": "violet",
                "new-enabled": "on",
            },
        )

        self.assertRedirects(response, "/home/")
        source = CalendarSource.objects.get(user=user, name="Familie")
        self.assertIsNotNone(source.sync_requested_at)

    def test_calendar_source_form_normalizes_webcal_urls(self):
        form = CalendarSourceForm(
            data={
                "name": "Arbeit",
                "ical_url": "webcal://calendar.google.com/calendar/ical/example/private/basic.ics",
                "color": "blue",
                "enabled": "on",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.cleaned_data["ical_url"],
            "https://calendar.google.com/calendar/ical/example/private/basic.ics",
        )

    def test_calendar_source_form_rejects_unsafe_targets(self):
        unsafe_urls = [
            "http://example.com/calendar.ics",
            "https://127.0.0.1/private.ics",
            "https://localhost/private.ics",
            "https://metadata.google.internal/private.ics",
        ]

        for url in unsafe_urls:
            with self.subTest(url=url):
                form = CalendarSourceForm(data={"name": "Privat", "ical_url": url, "color": "blue", "enabled": "on"})

                self.assertFalse(form.is_valid())
                self.assertIn("ical_url", form.errors)

    def test_calendar_source_form_rejects_duplicate_urls_for_user(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        CalendarSource.objects.create(
            user=user,
            name="Arbeit",
            ical_url="https://calendar.google.com/calendar/ical/example/private/basic.ics",
        )

        form = CalendarSourceForm(
            user=user,
            data={
                "name": "Duplikat",
                "ical_url": "https://calendar.google.com/calendar/ical/example/private/basic.ics",
                "color": "red",
                "enabled": "on",
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn("ical_url", form.errors)

    def test_settings_calendar_source_is_scoped_to_logged_in_user(self):
        mira = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        lukas = User.objects.create_user(username="lukas@example.com", email="lukas@example.com", password="secret-12345")
        Profile.objects.create(user=mira, display_name="Mira")
        Profile.objects.create(user=lukas, display_name="Lukas")
        private_url = "https://calendar.google.com/calendar/ical/mira/private/basic.ics"
        CalendarSource.objects.create(user=mira, name="Miras Kalender", ical_url=private_url)
        self.client.login(username="lukas@example.com", password="secret-12345")

        response = self.client.get("/settings/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="form_name" value="calendar_source_add"')
        self.assertNotContains(response, private_url)

        response = self.client.post(
            "/settings/",
            {
                "form_name": "calendar_source_add",
                "new-name": "Lukas Kalender",
                "new-ical_url": "https://calendar.google.com/calendar/ical/lukas/private/basic.ics",
                "new-color": "sand",
                "new-enabled": "on",
            },
        )

        self.assertRedirects(response, "/home/")
        self.assertEqual(CalendarSource.objects.get(user=mira).ical_url, private_url)
        self.assertEqual(
            CalendarSource.objects.get(user=lukas).ical_url,
            "https://calendar.google.com/calendar/ical/lukas/private/basic.ics",
        )

    def test_calendar_source_update_clears_events_when_url_changes(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        source = CalendarSource.objects.create(
            user=user,
            name="Alt",
            ical_url="https://calendar.google.com/calendar/ical/old/private/basic.ics",
        )
        start_at = timezone.now() + timedelta(days=3)
        CalendarEvent.objects.create(
            user=user,
            source=source,
            external_id="old-event",
            title="Alter Termin",
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        with patch("app.services.calendar_service.fetch_ical") as fetch_calendar:
            response = self.client.post(
                "/settings/",
                {
                    "form_name": "calendar_source_update",
                    "source_id": str(source.id),
                    f"source-{source.id}-name": "Neu",
                    f"source-{source.id}-ical_url": "https://calendar.google.com/calendar/ical/new/private/basic.ics",
                    f"source-{source.id}-color": "red",
                    f"source-{source.id}-enabled": "on",
                },
            )

        self.assertRedirects(response, "/home/")
        source.refresh_from_db()
        self.assertEqual(source.name, "Neu")
        self.assertEqual(source.color, "red")
        self.assertEqual(source.ical_url, "https://calendar.google.com/calendar/ical/new/private/basic.ics")
        self.assertFalse(CalendarEvent.objects.filter(source=source, external_id="old-event").exists())
        self.assertIsNotNone(source.sync_requested_at)
        fetch_calendar.assert_not_called()

    def test_disabling_calendar_source_clears_pending_sync(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        source = CalendarSource.objects.create(
            user=user,
            name="Privat",
            ical_url="https://calendar.google.com/calendar/ical/private/basic.ics",
            sync_requested_at=timezone.now(),
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/settings/",
            {
                "form_name": "calendar_source_update",
                "source_id": str(source.id),
                f"source-{source.id}-name": "Privat",
                f"source-{source.id}-ical_url": source.ical_url,
                f"source-{source.id}-color": "blue",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sync ist deaktiviert.")
        source.refresh_from_db()
        self.assertFalse(source.enabled)
        self.assertIsNone(source.sync_requested_at)

    def test_calendar_source_delete_removes_events(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        source = CalendarSource.objects.create(
            user=user,
            name="Privat",
            ical_url="https://calendar.google.com/calendar/ical/example/private/basic.ics",
        )
        start_at = timezone.now() + timedelta(days=1)
        CalendarEvent.objects.create(
            user=user,
            source=source,
            external_id="delete-event",
            title="Loeschen",
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/settings/",
            {
                "form_name": "calendar_source_delete",
                "source_id": str(source.id),
            },
        )

        self.assertRedirects(response, "/home/")
        self.assertFalse(CalendarSource.objects.filter(pk=source.id).exists())
        self.assertFalse(CalendarEvent.objects.filter(external_id="delete-event").exists())

    def test_calendar_page_does_not_render_calendar_source_form(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/calendar/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="form_name" value="calendar_source_add"')
        self.assertNotContains(response, "Google Kalender-Link")
        self.assertNotContains(response, "Hinzufuegen")

    def test_calendar_page_get_does_not_sync_calendar_source(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        CalendarSource.objects.create(
            user=user,
            ical_url="https://calendar.google.com/calendar/ical/example/private/basic.ics",
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        with patch("app.views.calendar_views.queue_calendar_sources") as queue_sync:
            response = self.client.get("/calendar/")

        self.assertEqual(response.status_code, 200)
        queue_sync.assert_not_called()

    def test_calendar_page_displays_saved_events(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        source = CalendarSource.objects.create(
            user=user,
            name="Arbeit",
            color="red",
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

        response = self.client.get("/calendar/?year=2026&month=7")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Juli 2026")
        self.assertContains(response, "Design Review")
        self.assertContains(response, "tone-red")

    def test_manual_calendar_event_can_be_created(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira", timezone_name="Europe/Berlin")
        self.client.login(username="mira@example.com", password="secret-12345")
        calendar_url = "/calendar/?year=2099&month=8"

        response = self.client.post(
            calendar_url,
            {
                "form_name": "calendar_event_add",
                "title": "Zahnarzttermin",
                "event_date": "2099-08-12",
                "start_time": "10:30",
                "end_time": "11:15",
                "location": "Praxis am Markt",
            },
        )

        self.assertRedirects(response, calendar_url)
        event = CalendarEvent.objects.get(user=user, title="Zahnarzttermin")
        self.assertIsNone(event.source)
        self.assertEqual(event.external_id, "")
        self.assertEqual(timezone.localtime(event.start_at).strftime("%Y-%m-%d %H:%M"), "2099-08-12 10:30")
        self.assertEqual(timezone.localtime(event.end_at).strftime("%Y-%m-%d %H:%M"), "2099-08-12 11:15")
        self.assertEqual(event.location, "Praxis am Markt")

        response = self.client.get(calendar_url)

        self.assertContains(response, "Zahnarzttermin")
        self.assertContains(response, "tone-sand")
        self.assertContains(response, "Eigener Termin")

    def test_calendar_event_with_attendee_creates_invitation_visible_to_invitee(self):
        organizer = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        invitee = User.objects.create_user(username="lukas@example.com", email="lukas@example.com", password="secret-12345")
        Profile.objects.create(user=organizer, display_name="Mira", timezone_name="Europe/Berlin")
        Profile.objects.create(user=invitee, display_name="Lukas", timezone_name="Europe/Berlin")
        self.client.login(username="mira@example.com", password="secret-12345")
        calendar_url = "/calendar/?year=2099&month=8"

        response = self.client.post(
            calendar_url,
            {
                "form_name": "calendar_event_add",
                "title": "Projektmeeting",
                "event_date": "2099-08-12",
                "start_time": "10:30",
                "end_time": "11:15",
                "attendees": [str(invitee.id)],
            },
        )
        self.assertRedirects(response, calendar_url)

        event = CalendarEvent.objects.get(user=organizer, title="Projektmeeting")
        attendee = CalendarEventAttendee.objects.get(event=event, user=invitee)
        self.assertEqual(attendee.status, CalendarEventAttendee.STATUS_INVITED)
        self.assertEqual(attendee.invited_by, organizer)

        self.client.logout()
        self.client.login(username="lukas@example.com", password="secret-12345")
        response = self.client.get(calendar_url)
        self.assertContains(response, "Projektmeeting")
        self.assertContains(response, "Einladungen")

    def test_invitee_can_accept_and_decline_event_invitation(self):
        organizer = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        invitee = User.objects.create_user(username="lukas@example.com", email="lukas@example.com", password="secret-12345")
        Profile.objects.create(user=organizer, display_name="Mira")
        Profile.objects.create(user=invitee, display_name="Lukas")
        event = CalendarEvent.objects.create(
            user=organizer,
            title="Projektmeeting",
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, hours=1),
        )
        attendee = CalendarEventAttendee.objects.create(event=event, user=invitee, invited_by=organizer)

        self.client.login(username="lukas@example.com", password="secret-12345")
        response = self.client.post(
            "/calendar/",
            {"form_name": "event_rsvp", "attendee_id": str(attendee.id), "status": "accepted"},
        )
        self.assertRedirects(response, "/calendar/")
        attendee.refresh_from_db()
        self.assertEqual(attendee.status, CalendarEventAttendee.STATUS_ACCEPTED)
        self.assertIsNotNone(attendee.responded_at)

        self.client.post(
            "/calendar/",
            {"form_name": "event_rsvp", "attendee_id": str(attendee.id), "status": "declined"},
        )
        attendee.refresh_from_db()
        self.assertEqual(attendee.status, CalendarEventAttendee.STATUS_DECLINED)

    def test_rsvp_action_cannot_target_another_users_invitation(self):
        organizer = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        invitee = User.objects.create_user(username="lukas@example.com", email="lukas@example.com", password="secret-12345")
        outsider = User.objects.create_user(username="anna@example.com", email="anna@example.com", password="secret-12345")
        for user in (organizer, invitee, outsider):
            Profile.objects.create(user=user, display_name=user.first_name or user.username)
        event = CalendarEvent.objects.create(
            user=organizer,
            title="Projektmeeting",
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, hours=1),
        )
        attendee = CalendarEventAttendee.objects.create(event=event, user=invitee, invited_by=organizer)

        self.client.login(username="anna@example.com", password="secret-12345")
        self.client.post(
            "/calendar/",
            {"form_name": "event_rsvp", "attendee_id": str(attendee.id), "status": "accepted"},
        )
        attendee.refresh_from_db()
        self.assertEqual(attendee.status, CalendarEventAttendee.STATUS_INVITED)

    def test_manual_all_day_event_uses_the_full_selected_day(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira", timezone_name="Europe/Berlin")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/calendar/?year=2099&month=8",
            {
                "form_name": "calendar_event_add",
                "title": "Geburtstag",
                "event_date": "2099-08-13",
                "is_all_day": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        event = CalendarEvent.objects.get(user=user, title="Geburtstag")
        self.assertTrue(event.is_all_day)
        self.assertEqual(timezone.localtime(event.start_at).strftime("%Y-%m-%d %H:%M"), "2099-08-13 00:00")
        self.assertEqual(timezone.localtime(event.end_at).strftime("%Y-%m-%d %H:%M"), "2099-08-14 00:00")

    def test_manual_calendar_event_rejects_an_end_before_its_start(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/calendar/?year=2099&month=8",
            {
                "form_name": "calendar_event_add",
                "title": "Ungültiger Termin",
                "event_date": "2099-08-14",
                "start_time": "15:00",
                "end_time": "14:00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Die Endzeit muss nach der Startzeit liegen.")
        self.assertContains(response, 'data-has-errors="true"')
        self.assertFalse(CalendarEvent.objects.filter(user=user).exists())

    def test_recurring_calendar_event_creates_one_row_per_occurrence(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira", timezone_name="Europe/Berlin")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/calendar/?year=2099&month=8",
            {
                "form_name": "calendar_event_add",
                "title": "Müll rausbringen",
                "event_date": "2099-08-04",
                "start_time": "08:00",
                "end_time": "08:15",
                "repeat": "WEEKLY",
                "repeat_until": "2099-08-18",
            },
        )

        self.assertEqual(response.status_code, 302)
        events = list(CalendarEvent.objects.filter(user=user, title="Müll rausbringen").order_by("start_at"))
        self.assertEqual(len(events), 3)
        self.assertEqual(
            [timezone.localtime(event.start_at).date().isoformat() for event in events],
            ["2099-08-04", "2099-08-11", "2099-08-18"],
        )
        recurrence_ids = {event.recurrence_id for event in events}
        self.assertEqual(len(recurrence_ids), 1)
        self.assertIsNotNone(events[0].recurrence_id)
        self.assertTrue(all(event.recurrence_rule == "WEEKLY" for event in events))

    def test_recurring_calendar_event_requires_repeat_until(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/calendar/?year=2099&month=8",
            {
                "form_name": "calendar_event_add",
                "title": "Ohne Enddatum",
                "event_date": "2099-08-04",
                "start_time": "08:00",
                "repeat": "DAILY",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bitte gib ein Enddatum für die Wiederholung an.")
        self.assertFalse(CalendarEvent.objects.filter(user=user).exists())

    def test_recurring_calendar_event_rejects_repeat_until_before_start(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/calendar/?year=2099&month=8",
            {
                "form_name": "calendar_event_add",
                "title": "Rückwärts",
                "event_date": "2099-08-10",
                "start_time": "08:00",
                "repeat": "DAILY",
                "repeat_until": "2099-08-01",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Das Enddatum muss nach dem Startdatum liegen.")
        self.assertFalse(CalendarEvent.objects.filter(user=user).exists())

    def test_recurring_calendar_event_rejects_repeat_until_too_far_out(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/calendar/?year=2099&month=8",
            {
                "form_name": "calendar_event_add",
                "title": "Zu weit weg",
                "event_date": "2099-08-10",
                "start_time": "08:00",
                "repeat": "YEARLY",
                "repeat_until": "2199-08-10",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Die Wiederholung darf höchstens 5 Jahre umfassen.")
        self.assertFalse(CalendarEvent.objects.filter(user=user).exists())

    def test_calendar_event_delete_removes_single_occurrence(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        event = CalendarEvent.objects.create(
            user=user,
            title="Einzeltermin",
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, hours=1),
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/calendar/",
            {"form_name": "calendar_event_delete", "event_id": str(event.id)},
        )

        self.assertRedirects(response, "/calendar/")
        self.assertFalse(CalendarEvent.objects.filter(pk=event.id).exists())

    def test_calendar_event_delete_series_removes_all_occurrences(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira", timezone_name="Europe/Berlin")
        self.client.login(username="mira@example.com", password="secret-12345")
        self.client.post(
            "/calendar/?year=2099&month=8",
            {
                "form_name": "calendar_event_add",
                "title": "Serie",
                "event_date": "2099-08-04",
                "start_time": "08:00",
                "repeat": "WEEKLY",
                "repeat_until": "2099-08-18",
            },
        )
        events = list(CalendarEvent.objects.filter(user=user, title="Serie"))
        self.assertEqual(len(events), 3)
        recurrence_id = events[0].recurrence_id

        response = self.client.post(
            "/calendar/",
            {"form_name": "calendar_event_delete_series", "recurrence_id": str(recurrence_id)},
        )

        self.assertRedirects(response, "/calendar/")
        self.assertFalse(CalendarEvent.objects.filter(recurrence_id=recurrence_id).exists())

    def test_calendar_event_delete_cannot_target_another_users_event(self):
        owner = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        outsider = User.objects.create_user(username="anna@example.com", email="anna@example.com", password="secret-12345")
        for user in (owner, outsider):
            Profile.objects.create(user=user, display_name=user.first_name or user.username)
        event = CalendarEvent.objects.create(
            user=owner,
            title="Fremder Termin",
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, hours=1),
        )
        self.client.login(username="anna@example.com", password="secret-12345")

        self.client.post(
            "/calendar/",
            {"form_name": "calendar_event_delete", "event_id": str(event.id)},
        )

        self.assertTrue(CalendarEvent.objects.filter(pk=event.id).exists())

    def test_calendar_event_delete_ignores_synced_events(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        source = CalendarSource.objects.create(user=user, name="Google Kalender", ical_url="https://example.com/cal.ics")
        event = CalendarEvent.objects.create(
            user=user,
            source=source,
            external_id="synced-1",
            title="Synchronisierter Termin",
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, hours=1),
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        self.client.post(
            "/calendar/",
            {"form_name": "calendar_event_delete", "event_id": str(event.id)},
        )

        self.assertTrue(CalendarEvent.objects.filter(pk=event.id).exists())

    def test_calendar_event_edit_updates_fields_and_attendees(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        invitee = User.objects.create_user(username="anna@example.com", email="anna@example.com", password="secret-12345")
        for account in (user, invitee):
            Profile.objects.create(user=account, display_name=account.first_name or account.username)
        event = CalendarEvent.objects.create(
            user=user,
            title="Altbezeichnung",
            location="Altes Büro",
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, hours=1),
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/calendar/",
            {
                "form_name": "calendar_event_edit",
                "event_id": str(event.id),
                "title": "Neuer Titel",
                "event_date": "2099-09-01",
                "start_time": "09:00",
                "end_time": "10:00",
                "location": "Neues Büro",
                "attendees": [str(invitee.id)],
            },
        )

        self.assertRedirects(response, "/calendar/")
        event.refresh_from_db()
        self.assertEqual(event.title, "Neuer Titel")
        self.assertEqual(event.location, "Neues Büro")
        self.assertTrue(CalendarEventAttendee.objects.filter(event=event, user=invitee).exists())

    def test_calendar_event_edit_cannot_target_another_users_event(self):
        owner = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        outsider = User.objects.create_user(username="anna@example.com", email="anna@example.com", password="secret-12345")
        for account in (owner, outsider):
            Profile.objects.create(user=account, display_name=account.first_name or account.username)
        event = CalendarEvent.objects.create(
            user=owner,
            title="Fremder Termin",
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, hours=1),
        )
        self.client.login(username="anna@example.com", password="secret-12345")

        self.client.post(
            "/calendar/",
            {
                "form_name": "calendar_event_edit",
                "event_id": str(event.id),
                "title": "Manipuliert",
                "event_date": "2099-09-01",
                "start_time": "09:00",
            },
        )

        event.refresh_from_db()
        self.assertEqual(event.title, "Fremder Termin")

    def test_calendar_event_edit_ignores_synced_events(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        source = CalendarSource.objects.create(user=user, name="Google Kalender", ical_url="https://example.com/cal.ics")
        event = CalendarEvent.objects.create(
            user=user,
            source=source,
            external_id="synced-1",
            title="Synchronisierter Termin",
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, hours=1),
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        self.client.post(
            "/calendar/",
            {
                "form_name": "calendar_event_edit",
                "event_id": str(event.id),
                "title": "Manipuliert",
                "event_date": "2099-09-01",
                "start_time": "09:00",
            },
        )

        event.refresh_from_db()
        self.assertEqual(event.title, "Synchronisierter Termin")

    def test_calendar_event_edit_rejects_an_end_before_its_start(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        event = CalendarEvent.objects.create(
            user=user,
            title="Termin",
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, hours=1),
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/calendar/",
            {
                "form_name": "calendar_event_edit",
                "event_id": str(event.id),
                "title": "Termin",
                "event_date": "2099-09-01",
                "start_time": "10:00",
                "end_time": "09:00",
            },
        )

        self.assertEqual(response.status_code, 200)
        event.refresh_from_db()
        self.assertEqual(event.title, "Termin")

    def test_calendar_sync_request_is_queued_and_visible(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        CalendarSource.objects.create(
            user=user,
            ical_url="https://calendar.google.com/calendar/ical/example/private/basic.ics",
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/calendar/",
            {"form_name": "calendar_sync_all"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kalendersynchronisierung wurde im Hintergrund vorgemerkt.")
        source = CalendarSource.objects.get(user=user)
        self.assertIsNotNone(source.sync_requested_at)

    def test_calendar_visibility_filters_calendar_and_dashboard(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        visible_source = CalendarSource.objects.create(
            user=user,
            name="Arbeit",
            color="green",
            ical_url="https://calendar.google.com/calendar/ical/work/private/basic.ics",
        )
        hidden_source = CalendarSource.objects.create(
            user=user,
            name="Privat",
            color="violet",
            is_visible=False,
            ical_url="https://calendar.google.com/calendar/ical/private/private/basic.ics",
        )
        start_at = timezone.localtime(timezone.now() + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
        calendar_url = f"/calendar/?year={start_at.year}&month={start_at.month}"
        CalendarEvent.objects.create(
            user=user,
            source=visible_source,
            external_id="visible-event",
            title="Sichtbar",
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
        )
        CalendarEvent.objects.create(
            user=user,
            source=hidden_source,
            external_id="hidden-event",
            title="Verborgen",
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get(calendar_url)

        self.assertContains(response, "Sichtbar")
        self.assertContains(response, "tone-green")
        self.assertNotContains(response, "Verborgen")

        response = self.client.get("/home/")

        self.assertContains(response, "Sichtbar")
        self.assertNotContains(response, "Verborgen")

        response = self.client.post(
            calendar_url,
            {
                "form_name": "calendar_visibility",
                "visible_source_ids": [str(hidden_source.id)],
            },
        )

        self.assertRedirects(response, calendar_url)
        visible_source.refresh_from_db()
        hidden_source.refresh_from_db()
        self.assertFalse(visible_source.is_visible)
        self.assertTrue(hidden_source.is_visible)

    def test_calendar_visibility_is_scoped_to_logged_in_user(self):
        mira = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        lukas = User.objects.create_user(username="lukas@example.com", email="lukas@example.com", password="secret-12345")
        Profile.objects.create(user=mira, display_name="Mira")
        Profile.objects.create(user=lukas, display_name="Lukas")
        mira_source = CalendarSource.objects.create(
            user=mira,
            name="Mira",
            ical_url="https://calendar.google.com/calendar/ical/mira/private/basic.ics",
        )
        lukas_source = CalendarSource.objects.create(
            user=lukas,
            name="Lukas",
            ical_url="https://calendar.google.com/calendar/ical/lukas/private/basic.ics",
        )
        self.client.login(username="lukas@example.com", password="secret-12345")

        response = self.client.post(
            "/calendar/",
            {
                "form_name": "calendar_visibility",
                "visible_source_ids": [str(mira_source.id)],
            },
        )

        self.assertRedirects(response, "/calendar/")
        mira_source.refresh_from_db()
        lukas_source.refresh_from_db()
        self.assertTrue(mira_source.is_visible)
        self.assertFalse(lukas_source.is_visible)

    def test_sync_queue_processes_hidden_sources_and_skips_disabled_sources(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        hidden_source = CalendarSource.objects.create(
            user=user,
            name="Hidden",
            is_visible=False,
            ical_url="https://calendar.google.com/calendar/ical/hidden/private/basic.ics",
        )
        disabled_source = CalendarSource.objects.create(
            user=user,
            name="Disabled",
            enabled=False,
            ical_url="https://calendar.google.com/calendar/ical/disabled/private/basic.ics",
        )

        result = queue_calendar_sources([hidden_source, disabled_source])

        self.assertEqual(result["queued"], 1)
        hidden_source.refresh_from_db()
        disabled_source.refresh_from_db()
        self.assertIsNotNone(hidden_source.sync_requested_at)
        self.assertIsNone(disabled_source.sync_requested_at)

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
        self.assertNotContains(response, "Projekte")
        self.assertNotContains(response, "Dateien")
        self.assertNotContains(response, "Analysen")

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

        response = self.client.post(
            "/calendar/",
            {
                "form_name": "reminder_delete",
                "reminder_id": str(reminder.id),
            },
        )

        self.assertRedirects(response, "/calendar/")
        self.assertFalse(CalendarReminder.objects.filter(pk=reminder.id).exists())

    def test_calendar_reminders_can_store_due_dates(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")
        due_at = timezone.localtime(timezone.now() + timedelta(days=1)).replace(second=0, microsecond=0)

        response = self.client.post(
            "/calendar/",
            {
                "form_name": "reminder_add",
                "title": "Rechnung bezahlen",
                "due_at": due_at.strftime("%Y-%m-%dT%H:%M"),
            },
        )

        self.assertRedirects(response, "/calendar/")
        reminder = CalendarReminder.objects.get(user=user)
        self.assertEqual(
            timezone.localtime(reminder.due_at).strftime("%Y-%m-%dT%H:%M"),
            due_at.strftime("%Y-%m-%dT%H:%M"),
        )

        response = self.client.get("/calendar/")

        self.assertContains(response, "Rechnung bezahlen")
        self.assertContains(response, "Morgen")

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

    def test_ical_parser_expands_yearly_recurrence_across_multiple_years(self):
        ical = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:birthday@example.com
SUMMARY:Geburtstag
DTSTART;VALUE=DATE:20200315
DTEND;VALUE=DATE:20200316
RRULE:FREQ=YEARLY
END:VEVENT
END:VCALENDAR
"""
        events = parse_ical_events(
            ical,
            window_start=timezone.make_aware(datetime(2026, 1, 1)),
            window_end=timezone.make_aware(datetime(2027, 6, 1)),
        )

        self.assertEqual(len(events), 2)
        self.assertEqual([event.start_at.year for event in events], [2026, 2027])
        self.assertTrue(all(event.start_at.month == 3 and event.start_at.day == 15 for event in events))

    def test_ical_parser_respects_yearly_interval_and_until(self):
        ical = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:anniversary@example.com
SUMMARY:Jubilaeum
DTSTART;VALUE=DATE:20220701
DTEND;VALUE=DATE:20220702
RRULE:FREQ=YEARLY;INTERVAL=2;UNTIL=20280101
END:VEVENT
END:VCALENDAR
"""
        events = parse_ical_events(
            ical,
            window_start=timezone.make_aware(datetime(2022, 1, 1)),
            window_end=timezone.make_aware(datetime(2030, 1, 1)),
        )

        self.assertEqual([event.start_at.year for event in events], [2022, 2024, 2026])


class FakeIcalResponse:
    headers = {"content-type": "text/calendar"}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, _size=-1):
        return b"BEGIN:VCALENDAR\nEND:VCALENDAR\n"


class FakeWeatherResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, _size=-1):
        return json.dumps(self.payload).encode("utf-8")


class FakeWeatherTileResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, _size=-1):
        return b"png-bytes"


class CalendarFetchSafetyTests(TestCase):
    def public_dns_result(self):
        return [(None, None, None, "", ("93.184.216.34", 443))]

    def private_dns_result(self):
        return [(None, None, None, "", ("10.0.0.8", 443))]

    def test_fetch_ical_rejects_private_dns_targets_before_request(self):
        with patch("app.services.url_safety.socket.getaddrinfo", return_value=self.private_dns_result()):
            with patch("app.services.calendar_service._ICAL_OPENER.open") as opener:
                with self.assertRaisesMessage(ValueError, "interne Netzwerkadressen"):
                    fetch_ical("https://example.com/calendar.ics")

        opener.assert_not_called()

    def test_fetch_ical_rejects_private_redirect_targets(self):
        headers = Message()
        headers["Location"] = "https://127.0.0.1/private.ics"
        redirect = HTTPError("https://example.com/calendar.ics", 302, "Found", headers, None)

        with patch("app.services.url_safety.socket.getaddrinfo", return_value=self.public_dns_result()):
            with patch("app.services.calendar_service._ICAL_OPENER.open", side_effect=redirect) as opener:
                with self.assertRaisesMessage(ValueError, "interne Netzwerkadressen"):
                    fetch_ical("https://example.com/calendar.ics")

        self.assertEqual(opener.call_count, 1)

    def test_fetch_ical_reads_public_calendar_response(self):
        with patch("app.services.url_safety.socket.getaddrinfo", return_value=self.public_dns_result()):
            with patch("app.services.calendar_service._ICAL_OPENER.open", return_value=FakeIcalResponse()) as opener:
                text = fetch_ical("https://example.com/calendar.ics")

        self.assertIn("BEGIN:VCALENDAR", text)
        opener.assert_called_once()



@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="Lunora <noreply@example.test>",
    LUNORA_WEEKLY_SUMMARY_HOUR=8,
)
class ScheduledAutomationTests(TestCase):
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
            weekly_summary=True,
            timezone_name="Europe/Berlin",
        )

    def test_due_calendar_sources_are_synced_and_recent_sources_are_skipped(self):
        now = timezone.now()
        due_source = CalendarSource.objects.create(
            user=self.user,
            name="Fällig",
            ical_url="https://calendar.google.com/calendar/ical/due/private/basic.ics",
            last_synced_at=now - timedelta(minutes=20),
            sync_interval_minutes=15,
        )
        CalendarSource.objects.create(
            user=self.user,
            name="Aktuell",
            ical_url="https://calendar.google.com/calendar/ical/current/private/basic.ics",
            last_synced_at=now - timedelta(minutes=5),
            sync_interval_minutes=15,
        )

        with patch(
            "app.services.scheduled_tasks.sync_calendar_source",
            return_value={"synced": True, "message": "Aktualisiert."},
        ) as sync_source:
            result = sync_due_calendars(now=now)

        self.assertEqual(result, {"synced": 1, "failed": 0, "skipped": 1})
        sync_source.assert_called_once_with(due_source, force=True)
        due_source.refresh_from_db()
        self.assertEqual(due_source.last_sync_attempt_at, now)

    def test_manual_sync_request_bypasses_regular_interval(self):
        now = timezone.now()
        source = CalendarSource.objects.create(
            user=self.user,
            name="Manuell",
            ical_url="https://calendar.google.com/calendar/ical/manual/private/basic.ics",
            last_synced_at=now - timedelta(minutes=2),
            last_sync_attempt_at=now - timedelta(minutes=2),
            sync_requested_at=now - timedelta(seconds=5),
            sync_interval_minutes=15,
        )

        with patch(
            "app.services.scheduled_tasks.sync_calendar_source",
            return_value={"synced": True, "message": "Aktualisiert."},
        ) as sync_source:
            result = sync_due_calendars(now=now)

        self.assertEqual(result, {"synced": 1, "failed": 0, "skipped": 0})
        sync_source.assert_called_once_with(source, force=True)
        source.refresh_from_db()
        self.assertIsNone(source.sync_requested_at)
        self.assertEqual(source.last_sync_attempt_at, now)

    def test_failed_sync_is_not_retried_before_interval(self):
        now = timezone.now()
        source = CalendarSource.objects.create(
            user=self.user,
            name="Fehlerhaft",
            ical_url="https://calendar.google.com/calendar/ical/failing/private/basic.ics",
            sync_requested_at=now - timedelta(seconds=5),
            sync_interval_minutes=15,
        )

        with patch(
            "app.services.scheduled_tasks.sync_calendar_source",
            return_value={"synced": False, "message": "Nicht erreichbar."},
        ) as sync_source:
            first_result = sync_due_calendars(now=now)
            second_result = sync_due_calendars(now=now + timedelta(minutes=1))

        self.assertEqual(first_result, {"synced": 0, "failed": 1, "skipped": 0})
        self.assertEqual(second_result, {"synced": 0, "failed": 0, "skipped": 1})
        sync_source.assert_called_once_with(source, force=True)

    def test_due_reminder_email_is_sent_only_once(self):
        reminder = CalendarReminder.objects.create(
            user=self.user,
            title="Rechnung bezahlen",
            due_at=timezone.now() - timedelta(minutes=1),
        )

        first_result = send_due_reminder_emails()
        second_result = send_due_reminder_emails()

        self.assertEqual(first_result, {"sent": 1, "failed": 0})
        self.assertEqual(second_result, {"sent": 0, "failed": 0})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Rechnung bezahlen", mail.outbox[0].subject)
        reminder.refresh_from_db()
        self.assertIsNotNone(reminder.email_notified_at)

    def test_desktop_notification_claim_is_preference_scoped_and_one_time(self):
        reminder = CalendarReminder.objects.create(
            user=self.user,
            title="Präsentation starten",
            due_at=timezone.now() - timedelta(minutes=1),
        )
        self.client.force_login(self.user)

        first_response = self.client.post("/notifications/claim/")
        second_response = self.client.post("/notifications/claim/")

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(first_response.json()["notifications"][0]["title"], "Präsentation starten")
        self.assertEqual(second_response.json(), {"notifications": []})
        reminder.refresh_from_db()
        self.assertIsNotNone(reminder.desktop_notified_at)

    def test_event_invitation_email_and_desktop_claim_are_sent_only_once(self):
        organizer = User.objects.create_user(username="lukas@example.com", email="lukas@example.com", password="secret-12345")
        Profile.objects.create(user=organizer, display_name="Lukas")
        event = CalendarEvent.objects.create(
            user=organizer,
            title="Projektmeeting",
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, hours=1),
        )
        CalendarEventAttendee.objects.create(event=event, user=self.user, invited_by=organizer)

        first_email_result = send_new_invitation_emails()
        second_email_result = send_new_invitation_emails()
        self.assertEqual(first_email_result, {"sent": 1, "failed": 0})
        self.assertEqual(second_email_result, {"sent": 0, "failed": 0})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Projektmeeting", mail.outbox[0].subject)

        self.client.force_login(self.user)
        first_response = self.client.post("/notifications/claim/")
        second_response = self.client.post("/notifications/claim/")
        self.assertEqual(first_response.json()["notifications"][0]["title"], "Einladung: Projektmeeting")
        self.assertEqual(second_response.json(), {"notifications": []})

    def test_note_activity_email_and_desktop_claim_are_sent_only_once(self):
        actor = User.objects.create_user(username="anna@example.com", email="anna@example.com", password="secret-12345")
        Profile.objects.create(user=actor, display_name="Anna")
        note = Note.objects.create(owner=actor, title="Ideen")
        NoteActivityNotification.objects.create(
            note=note,
            recipient=self.user,
            actor=actor,
            kind=NoteActivityNotification.KIND_MENTION,
            excerpt="Hallo @Mira",
        )

        first_email_result = send_note_activity_emails()
        second_email_result = send_note_activity_emails()
        self.assertEqual(first_email_result, {"sent": 1, "failed": 0})
        self.assertEqual(second_email_result, {"sent": 0, "failed": 0})
        self.assertEqual(len(mail.outbox), 1)

        self.client.force_login(self.user)
        first_response = self.client.post("/notifications/claim/")
        second_response = self.client.post("/notifications/claim/")
        self.assertEqual(first_response.json()["notifications"][0]["title"], "Anna hat dich erwähnt")
        self.assertEqual(second_response.json(), {"notifications": []})

    @override_settings(WEATHER_API_KEY="test-key", WEATHER_CACHE_SECONDS=0)
    def test_weather_alert_desktop_claim_respects_cooldown(self):
        location = WeatherLocation.objects.create(
            user=self.user, name="Berlin", lat=52.52, lon=13.405, label="Berlin, DE", is_default=True
        )
        current_payload = {
            "weather": [{"main": "Thunderstorm", "description": "Gewitter"}],
            "main": {"temp": 22, "feels_like": 22},
            "wind": {"speed": 3},
        }
        forecast_payload = {"list": [{"pop": 0.2}]}
        self.client.force_login(self.user)

        with patch(
            "app.services.weather_service.urlopen",
            side_effect=[
                FakeWeatherResponse(current_payload),
                FakeWeatherResponse(forecast_payload),
                FakeWeatherResponse(current_payload),
                FakeWeatherResponse(forecast_payload),
            ],
        ):
            first_response = self.client.post("/notifications/claim/")
            second_response = self.client.post("/notifications/claim/")

        self.assertEqual(first_response.json()["notifications"][0]["title"], "Gewitterwarnung")
        self.assertEqual(second_response.json(), {"notifications": []})
        location.refresh_from_db()
        self.assertEqual(location.last_alert_kind, "storm")
        self.assertIsNotNone(location.last_alert_notified_at)

    def test_weekly_summary_is_sent_once_on_monday(self):
        monday = timezone.make_aware(datetime(2026, 8, 10, 9, 0), ZoneInfo("Europe/Berlin"))
        CalendarEvent.objects.create(
            user=self.user,
            title="Team Sync",
            start_at=monday + timedelta(days=1),
            end_at=monday + timedelta(days=1, hours=1),
        )
        CalendarReminder.objects.create(user=self.user, title="Agenda vorbereiten")

        first_result = send_weekly_summaries(now=monday)
        second_result = send_weekly_summaries(now=monday)

        self.assertEqual(first_result["sent"], 1)
        self.assertEqual(second_result["sent"], 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Team Sync", mail.outbox[0].body)
        self.assertIn("Agenda vorbereiten", mail.outbox[0].body)
        self.assertTrue(WeeklySummaryDelivery.objects.filter(user=self.user, week_start=monday.date()).exists())

    def test_automation_command_runs_one_cycle_by_default(self):
        result = {
            "calendar_sync": {"synced": 1, "failed": 0, "skipped": 2},
            "reminder_emails": {"sent": 1, "failed": 0},
            "weekly_summaries": {"sent": 1, "failed": 0, "skipped": 0},
        }
        output = StringIO()

        with patch("app.management.commands.run_automations.run_scheduled_tasks", return_value=result) as run_tasks:
            call_command("run_automations", stdout=output)

        run_tasks.assert_called_once_with()
        self.assertIn("Kalender: 1 synchronisiert", output.getvalue())


class WeatherMapTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="map@example.com",
            email="map@example.com",
            password="secret-12345",
            first_name="Map",
        )
        Profile.objects.create(user=self.user, display_name="Map")

    @override_settings(WEATHER_API_KEY="")
    def test_weather_page_renders_interactive_weather_map(self):
        self.client.login(username="map@example.com", password="secret-12345")

        response = self.client.get("/weather/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-weather-map")
        self.assertContains(response, "data-weather-map-canvas")
        self.assertContains(response, "data-weather-map-reset")
        self.assertContains(response, "data-weather-map-fullscreen")
        self.assertContains(response, 'data-weather-map-point-url="/weather/point/"')
        self.assertContains(response, "per Klick die Temperatur eines Ortes abrufen")
        self.assertContains(response, "Keine Einfärbung bedeutet aktuell kein Niederschlag.")
        for layer in WEATHER_MAP_LAYERS:
            self.assertContains(response, f'data-weather-map-layer="{layer}"')
        self.assertContains(response, 'aria-disabled="true"', count=5)

    @override_settings(WEATHER_API_KEY="")
    def test_weather_map_tile_requires_api_key(self):
        self.client.login(username="map@example.com", password="secret-12345")

        response = self.client.get("/weather/map/temperature/7/67/43.png")

        self.assertEqual(response.status_code, 404)

    @override_settings(WEATHER_API_KEY="test-key")
    def test_weather_map_tile_proxies_png(self):
        self.client.login(username="map@example.com", password="secret-12345")

        with patch("app.views.weather_views.fetch_weather_map_tile", return_value=b"png-bytes") as fetch_tile:
            response = self.client.get("/weather/map/wind/7/67/43.png")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertIn("max-age=300", response["Cache-Control"])
        self.assertEqual(response.content, b"png-bytes")
        fetch_tile.assert_called_once_with(7, 67, 43, layer="wind")

    def test_weather_map_tile_requires_login(self):
        response = self.client.get("/weather/map/temperature/7/67/43.png")

        self.assertRedirects(
            response,
            "/login/?next=/weather/map/temperature/7/67/43.png",
        )

    def test_weather_point_requires_login(self):
        response = self.client.get("/weather/point/")

        self.assertRedirects(response, "/login/?next=/weather/point/")

    @override_settings(WEATHER_API_KEY="")
    def test_weather_point_requires_api_key(self):
        self.client.login(username="map@example.com", password="secret-12345")

        response = self.client.get("/weather/point/?lat=52.52&lon=13.405")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["ok"])

    @override_settings(WEATHER_API_KEY="test-key")
    def test_weather_point_returns_current_temperature(self):
        self.client.login(username="map@example.com", password="secret-12345")
        weather = {
            "location": "Berlin, DE",
            "latitude": 52.52,
            "longitude": 13.405,
            "temperature": 18.4,
            "feels_like": 17.9,
            "description": "Leicht bewölkt",
        }

        with patch(
            "app.views.weather_views.get_weather_at_coordinates",
            return_value=weather,
        ) as point_lookup:
            response = self.client.get("/weather/point/?lat=52.52&lon=13.405")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "weather": weather})
        point_lookup.assert_called_once_with("52.52", "13.405")

    @override_settings(WEATHER_API_KEY="test-key")
    def test_weather_point_rejects_invalid_coordinates(self):
        self.client.login(username="map@example.com", password="secret-12345")

        for latitude, longitude in [("91", "8"), ("52", "181"), ("x", "8"), ("NaN", "8")]:
            with self.subTest(latitude=latitude, longitude=longitude):
                response = self.client.get(
                    "/weather/point/",
                    {"lat": latitude, "lon": longitude},
                )
                self.assertEqual(response.status_code, 400)

    @override_settings(
        WEATHER_API_KEY="test-key",
        WEATHER_API_BASE_URL="https://weather.example.test/data/2.5",
    )
    def test_weather_point_service_maps_provider_response(self):
        provider_response = {
            "name": "Berlin",
            "sys": {"country": "DE"},
            "main": {"temp": 18.44, "feels_like": 17.86},
            "weather": [{"description": "leicht bewölkt"}],
        }

        with patch(
            "app.services.weather_service._fetch_json",
            return_value=provider_response,
        ) as fetch_json:
            weather = get_weather_at_coordinates("52.52", "13.405")

        self.assertEqual(weather["location"], "Berlin, DE")
        self.assertEqual(weather["temperature"], 18.4)
        self.assertEqual(weather["feels_like"], 17.9)
        self.assertEqual(weather["description"], "Leicht bewölkt")
        fetch_json.assert_called_once_with(
            "https://weather.example.test/data/2.5/weather",
            {
                "lat": 52.52,
                "lon": 13.405,
                "appid": "test-key",
                "units": "metric",
                "lang": "de",
            },
        )

    @override_settings(
        WEATHER_API_KEY="test-key",
        WEATHER_TILE_BASE_URL="https://tiles.example.test/map",
    )
    def test_weather_map_service_maps_all_supported_layers(self):
        expected_layers = {
            "temperature": "temp_new",
            "precipitation": "precipitation_new",
            "clouds": "clouds_new",
            "wind": "wind_new",
            "pressure": "pressure_new",
        }

        for layer, provider_layer in expected_layers.items():
            with self.subTest(layer=layer):
                with patch(
                    "app.services.weather_service.urlopen",
                    return_value=FakeWeatherTileResponse(),
                ) as mocked_urlopen:
                    tile = fetch_weather_map_tile(7, 67, 43, layer=layer)

                self.assertEqual(tile, b"png-bytes")
                request = mocked_urlopen.call_args.args[0]
                self.assertIn(f"/map/{provider_layer}/7/67/43.png", request.full_url)
                self.assertIn("appid=test-key", request.full_url)

    @override_settings(WEATHER_API_KEY="test-key")
    def test_weather_map_service_rejects_invalid_layer_and_coordinates(self):
        with self.assertRaisesMessage(ValueError, "Ungueltige Wetterkarten-Ebene"):
            fetch_weather_map_tile(7, 67, 43, layer="snow")

        for coordinates in [(0, 0, 0), (11, 0, 0), (7, 128, 43), (7, 67, 128)]:
            with self.subTest(coordinates=coordinates):
                with self.assertRaisesMessage(ValueError, "Ungueltige Wetterkarten-Kachel"):
                    fetch_weather_map_tile(*coordinates, layer="temperature")

    @override_settings(WEATHER_API_KEY="")
    def test_weather_forecast_summary_uses_daily_forecast_values(self):
        context = get_weather_context({})

        self.assertEqual(context["forecast_summary"]["average_high"], "24°")
        self.assertEqual(context["forecast_summary"]["rain_days"], "1")
        self.assertEqual(context["forecast_summary"]["trend"], "Nass")

    @override_settings(WEATHER_API_KEY="")
    def test_weather_context_uses_profile_default_city(self):
        self.user.profile.weather_default_city = "Berlin,de"
        self.user.profile.save(update_fields=["weather_default_city"])

        context = get_weather_context({}, user=self.user)

        self.assertEqual(context["current"]["city"], "Berlin")
        self.assertEqual(context["current"]["label"], "Standardort")
        self.assertEqual(context["search_query"], "")
        self.assertEqual(context["weather_map"]["location"], "Berlin")
        self.assertEqual(context["weather_map"]["default_layer"], "temperature")

    @override_settings(WEATHER_API_KEY="")
    def test_weather_page_can_save_current_place_as_default(self):
        self.client.login(username="map@example.com", password="secret-12345")

        response = self.client.post(
            "/weather/",
            {
                "form_name": "weather_default",
                "weather_default_city": "Berlin",
            },
        )

        self.assertRedirects(response, "/weather/")
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.weather_default_city, "Berlin")

    @override_settings(WEATHER_API_KEY="test-key", WEATHER_CACHE_SECONDS=600)
    def test_location_suggestions_cache_api_responses(self):
        cache.clear()
        payload = [{"name": "Berlin", "state": "Berlin", "country": "DE", "lat": 52.52, "lon": 13.405}]

        with patch("app.services.weather_service.urlopen", return_value=FakeWeatherResponse(payload)) as mocked_urlopen:
            first = get_location_suggestions("Berlin")
            second = get_location_suggestions("Berlin")

        self.assertEqual(first, second)
        self.assertEqual(first[0]["name"], "Berlin")
        self.assertEqual(mocked_urlopen.call_count, 1)

    @override_settings(WEATHER_API_KEY="")
    def test_weather_page_renders_calculated_forecast_summary(self):
        self.client.login(username="map@example.com", password="secret-12345")

        response = self.client.get("/weather/")

        self.assertContains(response, "24°")
        self.assertContains(response, "Regentage")
        self.assertContains(response, "Nass")
        self.assertNotContains(response, "31°")

    def test_build_weather_alert_detects_severe_conditions(self):
        base_current = {"main": {"temp": 20}, "wind": {"speed": 0}}
        base_forecast = {"list": [{"pop": 0.1}]}

        thunder = _build_weather_alert(base_current, base_forecast, {"main": "Thunderstorm"})
        self.assertEqual(thunder["kind"], "storm")

        windy = _build_weather_alert({"main": {"temp": 20}, "wind": {"speed": 20}}, base_forecast, {"main": "Clear"})
        self.assertEqual(windy["kind"], "wind")

        rainy = _build_weather_alert(base_current, {"list": [{"pop": 0.9}]}, {"main": "Rain"})
        self.assertEqual(rainy["kind"], "rain")

        hot = _build_weather_alert({"main": {"temp": 36}, "wind": {"speed": 0}}, base_forecast, {"main": "Clear"})
        self.assertEqual(hot["kind"], "heat")

        cold = _build_weather_alert({"main": {"temp": -12}, "wind": {"speed": 0}}, base_forecast, {"main": "Clear"})
        self.assertEqual(cold["kind"], "cold")

        self.assertIsNone(_build_weather_alert(base_current, base_forecast, {"main": "Clear"}))

    @override_settings(WEATHER_API_KEY="")
    def test_get_weather_alert_for_location_requires_api_key(self):
        self.assertIsNone(get_weather_alert_for_location({"lat": 52.5, "lon": 13.4}))

    @override_settings(WEATHER_API_KEY="test-key", WEATHER_CACHE_SECONDS=0)
    def test_get_weather_alert_for_location_maps_provider_response(self):
        current_payload = {
            "weather": [{"main": "Thunderstorm", "description": "Gewitter"}],
            "main": {"temp": 22, "feels_like": 22},
            "wind": {"speed": 3},
        }
        forecast_payload = {"list": [{"pop": 0.2}]}

        with patch(
            "app.services.weather_service.urlopen",
            side_effect=[FakeWeatherResponse(current_payload), FakeWeatherResponse(forecast_payload)],
        ):
            alert = get_weather_alert_for_location({"lat": 52.5, "lon": 13.4})

        self.assertEqual(alert["kind"], "storm")

    def test_save_weather_location_dedupes_and_sets_first_as_default(self):
        location, created = save_weather_location(self.user, name="Berlin", lat=52.52, lon=13.405, details="DE", label="Berlin, DE")
        self.assertTrue(created)
        self.assertTrue(location.is_default)

        duplicate, created_again = save_weather_location(self.user, name="Berlin", lat=52.5203, lon=13.4048, details="DE", label="Berlin, DE")
        self.assertFalse(created_again)
        self.assertEqual(duplicate.pk, location.pk)
        self.assertEqual(list_weather_locations(self.user), [location])

    def test_save_weather_location_enforces_cap(self):
        for index in range(8):
            save_weather_location(self.user, name=f"Ort {index}", lat=10 + index, lon=10 + index, details="", label="")

        with self.assertRaises(ValueError):
            save_weather_location(self.user, name="Ort 9", lat=50, lon=50, details="", label="")

    def test_delete_weather_location_promotes_next_default(self):
        first, _ = save_weather_location(self.user, name="Berlin", lat=52.52, lon=13.405, details="", label="Berlin")
        second, _ = save_weather_location(self.user, name="Hamburg", lat=53.55, lon=9.99, details="", label="Hamburg")

        delete_weather_location(self.user, first.pk)

        second.refresh_from_db()
        self.assertTrue(second.is_default)
        self.assertEqual(list(WeatherLocation.objects.filter(user=self.user)), [second])

    def test_set_default_weather_location_switches_default(self):
        first, _ = save_weather_location(self.user, name="Berlin", lat=52.52, lon=13.405, details="", label="Berlin")
        second, _ = save_weather_location(self.user, name="Hamburg", lat=53.55, lon=9.99, details="", label="Hamburg")

        set_default_weather_location(self.user, second.pk)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertTrue(second.is_default)

    @override_settings(WEATHER_API_KEY="")
    def test_location_from_request_prefers_default_weather_location(self):
        save_weather_location(self.user, name="Hamburg", lat=53.55, lon=9.99, details="DE", label="Hamburg, DE")

        context = get_weather_context({}, user=self.user)

        self.assertEqual(context["current"]["city"], "Hamburg")
        self.assertEqual(context["current"]["label"], "Standardort")

    def test_weather_page_can_save_and_manage_locations(self):
        self.client.login(username="map@example.com", password="secret-12345")

        save_response = self.client.post(
            "/weather/",
            {"form_name": "location_save", "name": "Berlin", "lat": "52.52", "lon": "13.405", "details": "DE", "label": "Berlin, DE"},
        )
        self.assertRedirects(save_response, "/weather/")
        location = WeatherLocation.objects.get(user=self.user, name="Berlin")
        self.assertTrue(location.is_default)

        second_response = self.client.post(
            "/weather/",
            {"form_name": "location_save", "name": "Hamburg", "lat": "53.55", "lon": "9.99", "details": "DE", "label": "Hamburg, DE"},
        )
        self.assertRedirects(second_response, "/weather/")
        second_location = WeatherLocation.objects.get(user=self.user, name="Hamburg")

        set_default_response = self.client.post(
            "/weather/",
            {"form_name": "location_set_default", "location_id": second_location.pk},
        )
        self.assertRedirects(set_default_response, "/weather/")
        location.refresh_from_db()
        second_location.refresh_from_db()
        self.assertFalse(location.is_default)
        self.assertTrue(second_location.is_default)

        delete_response = self.client.post(
            "/weather/",
            {"form_name": "location_delete", "location_id": location.pk},
        )
        self.assertRedirects(delete_response, "/weather/")
        self.assertFalse(WeatherLocation.objects.filter(pk=location.pk).exists())

        overview = self.client.get("/weather/")
        self.assertContains(overview, "Hamburg")




@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class MessagesPageTests(TestCase):
    def setUp(self):
        self.mira = User.objects.create_user(
            username="mira@example.com",
            email="mira@example.com",
            password="secret-12345",
            first_name="Mira",
        )
        self.lukas = User.objects.create_user(
            username="lukas@example.com",
            email="lukas@example.com",
            password="secret-12345",
            first_name="Lukas",
        )
        self.anna = User.objects.create_user(
            username="anna@example.com",
            email="anna@example.com",
            password="secret-12345",
            first_name="Anna",
        )
        Profile.objects.create(user=self.mira, display_name="Mira")
        Profile.objects.create(user=self.lukas, display_name="Lukas")
        Profile.objects.create(user=self.anna, display_name="Anna")

    def test_messages_page_requires_login(self):
        response = self.client.get("/messages/")

        self.assertRedirects(response, "/login/?next=/messages/")

    def test_start_conversation_excludes_inactive_users(self):
        self.anna.is_active = False
        self.anna.save(update_fields=["is_active"])
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/messages/")
        self.assertNotContains(response, "anna@example.com")

        response = self.client.post(
            "/messages/",
            {"form_name": "start_conversation", "recipient": str(self.anna.id), "body": "Hallo"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Conversation.objects.exists())

    def test_user_can_start_direct_conversation_and_send_message(self):
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/messages/",
            {
                "form_name": "start_conversation",
                "recipient": str(self.lukas.id),
                "body": "Hey Lukas!",
            },
        )

        conversation = Conversation.objects.get()
        self.assertRedirects(response, f"/messages/{conversation.id}/")
        self.assertEqual(conversation.participants.count(), 2)
        self.assertTrue(conversation.participants.filter(pk=self.mira.pk).exists())
        self.assertTrue(conversation.participants.filter(pk=self.lukas.pk).exists())
        self.assertEqual(conversation.messages.get().body, "Hey Lukas!")

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message",
                "conversation_id": str(conversation.id),
                "body": "Noch eine Nachricht",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        self.assertEqual(conversation.messages.count(), 2)
        self.assertTrue(conversation.messages.filter(body="Noch eine Nachricht", sender=self.mira).exists())

        response = self.client.post(
            "/messages/",
            {
                "form_name": "start_conversation",
                "recipient": str(self.lukas.id),
                "body": "Nochmal ueber Neue Unterhaltung",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(conversation.messages.count(), 3)

    def test_non_member_cannot_open_conversation(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        ChatMessage.objects.create(conversation=conversation, sender=self.mira, body="Privat")
        self.client.login(username="anna@example.com", password="secret-12345")

        response = self.client.get(f"/messages/{conversation.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Privat")
        self.assertContains(response, "Nachrichtenübersicht")

    def test_inbox_shows_unread_message_until_opened(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        ChatMessage.objects.create(conversation=conversation, sender=self.lukas, body="Neue Antwort")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/messages/?filter=unread")

        self.assertContains(response, "Neue Antwort")
        self.assertContains(response, "unread-badge")

        response = self.client.get(f"/messages/{conversation.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Neue Antwort")
        self.assertFalse(ConversationMember.objects.get(conversation=conversation, user=self.mira).unread_count())

    def test_messages_root_shows_overview_without_auto_opening_chat(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        ChatMessage.objects.create(conversation=conversation, sender=self.lukas, body="Neue Antwort")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/messages/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nachrichtenübersicht")
        self.assertContains(response, "Neue Nachrichten")
        self.assertContains(response, "Lukas: Neue Antwort")
        self.assertNotContains(response, 'name="form_name" value="message"')
        self.assertTrue(ConversationMember.objects.get(conversation=conversation, user=self.mira).unread_count())

    def test_message_search_matches_older_message_bodies(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        ChatMessage.objects.create(conversation=conversation, sender=self.lukas, body="Projekt Alpha")
        ChatMessage.objects.create(conversation=conversation, sender=self.lukas, body="Normale letzte Nachricht")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/messages/?q=Alpha")

        self.assertContains(response, "Lukas")
        self.assertContains(response, "Normale letzte Nachricht")

    def test_inbox_items_use_bounded_queries_for_message_counts(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        for index in range(60):
            ChatMessage.objects.create(
                conversation=conversation,
                sender=self.lukas,
                body=f"Nachricht {index}",
            )
        conversations = list(Conversation.visible_for(self.mira))

        with self.assertNumQueries(3):
            items = _build_inbox_items(conversations, self.mira)

        self.assertEqual(items[0]["unread"], 60)
        self.assertIn("Nachricht 59", items[0]["preview"])

    def test_message_detail_uses_paginated_history_window(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        for index in range(60):
            ChatMessage.objects.create(
                conversation=conversation,
                sender=self.lukas,
                body=f"Nachricht {index}",
            )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get(f"/messages/{conversation.id}/")

        message_bodies = [item["message"].body for item in response.context["message_items"]]
        self.assertEqual(len(message_bodies), 50)
        self.assertEqual(message_bodies[0], "Nachricht 10")
        self.assertEqual(message_bodies[-1], "Nachricht 59")
        self.assertTrue(response.context["has_older_messages"])

        oldest_message_id = response.context["oldest_message_id"]
        response = self.client.get(f"/messages/{conversation.id}/?before={oldest_message_id}")
        older_message_bodies = [item["message"].body for item in response.context["message_items"]]

        self.assertEqual(older_message_bodies, [f"Nachricht {index}" for index in range(10)])
        self.assertFalse(response.context["has_older_messages"])

    def test_user_can_react_to_message(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        message = ChatMessage.objects.create(conversation=conversation, sender=self.mira, body="Gute Idee")
        self.client.login(username="lukas@example.com", password="secret-12345")

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message_action",
                "conversation_id": str(conversation.id),
                "message_id": str(message.id),
                "action": "reaction",
                "emoji": "👍",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        reaction = ChatMessageReaction.objects.get(message=message, user=self.lukas)
        self.assertEqual(reaction.emoji, "👍")

    def test_user_can_pin_and_unpin_message(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        message = ChatMessage.objects.create(conversation=conversation, sender=self.lukas, body="Wichtig")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message_action",
                "conversation_id": str(conversation.id),
                "message_id": str(message.id),
                "action": "pin",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        message.refresh_from_db()
        self.assertTrue(message.is_pinned)
        self.assertEqual(message.pinned_by, self.mira)

        response = self.client.get(f"/messages/{conversation.id}/")
        self.assertContains(response, "Angepinnt")

    def test_own_message_can_be_deleted_with_placeholder(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        message = ChatMessage.objects.create(conversation=conversation, sender=self.mira, body="Soll weg")
        ChatMessageReaction.objects.create(message=message, user=self.lukas, emoji="❤️")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message_action",
                "conversation_id": str(conversation.id),
                "message_id": str(message.id),
                "action": "delete",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        message.refresh_from_db()
        self.assertTrue(message.is_deleted)
        self.assertEqual(message.body, "")
        self.assertFalse(message.reactions.exists())

        response = self.client.get(f"/messages/{conversation.id}/")
        self.assertContains(response, "Diese Nachricht wurde gelöscht.")
        self.assertNotContains(response, "Soll weg")

    def test_other_user_cannot_delete_foreign_message(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        message = ChatMessage.objects.create(conversation=conversation, sender=self.mira, body="Bleibt da")
        self.client.login(username="lukas@example.com", password="secret-12345")

        self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message_action",
                "conversation_id": str(conversation.id),
                "message_id": str(message.id),
                "action": "delete",
            },
        )

        message.refresh_from_db()
        self.assertFalse(message.is_deleted)
        self.assertEqual(message.body, "Bleibt da")


    def test_user_can_mute_and_unmute_conversation(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "member_action",
                "conversation_id": str(conversation.id),
                "action": "mute_8h",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        membership = ConversationMember.objects.get(conversation=conversation, user=self.mira)
        self.assertIsNotNone(membership.muted_until)
        self.assertTrue(membership.muted_until > timezone.now())

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "member_action",
                "conversation_id": str(conversation.id),
                "action": "unmute",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        membership.refresh_from_db()
        self.assertIsNone(membership.muted_until)

    def test_blocked_conversation_disables_sending_until_unblocked(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "member_action",
                "conversation_id": str(conversation.id),
                "action": "block",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        membership = ConversationMember.objects.get(conversation=conversation, user=self.mira)
        self.assertTrue(membership.is_blocked)

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message",
                "conversation_id": str(conversation.id),
                "body": "Soll nicht gesendet werden",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        self.assertFalse(conversation.messages.filter(body="Soll nicht gesendet werden").exists())

        self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "member_action",
                "conversation_id": str(conversation.id),
                "action": "unblock",
            },
        )
        membership.refresh_from_db()
        self.assertFalse(membership.is_blocked)

    def test_being_blocked_prevents_sending_and_hides_compose_bar(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)

        self.client.login(username="lukas@example.com", password="secret-12345")
        self.client.post(
            f"/messages/{conversation.id}/",
            {"form_name": "member_action", "conversation_id": str(conversation.id), "action": "block"},
        )
        self.client.logout()

        self.client.login(username="mira@example.com", password="secret-12345")
        response = self.client.get(f"/messages/{conversation.id}/")
        self.assertContains(response, "Diese Nachricht kann derzeit nicht gesendet werden.")
        self.assertNotContains(response, 'class="compose-bar"')

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message",
                "conversation_id": str(conversation.id),
                "body": "Sollte nicht ankommen",
            },
        )
        self.assertRedirects(response, f"/messages/{conversation.id}/")
        self.assertFalse(conversation.messages.filter(body="Sollte nicht ankommen").exists())

        response = self.client.post(
            "/messages/",
            {"form_name": "start_conversation", "recipient": str(self.lukas.id), "body": "Auch nicht"},
        )
        self.assertRedirects(response, f"/messages/{conversation.id}/")
        self.assertFalse(conversation.messages.filter(body="Auch nicht").exists())

    def test_start_conversation_with_multiple_recipients_creates_group(self):
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/messages/",
            {
                "form_name": "start_conversation",
                "recipient": [str(self.lukas.id), str(self.anna.id)],
                "title": "Projektteam",
                "body": "Hallo zusammen!",
            },
        )

        conversation = Conversation.objects.get()
        self.assertRedirects(response, f"/messages/{conversation.id}/")
        self.assertTrue(conversation.is_group)
        self.assertEqual(conversation.title, "Projektteam")
        self.assertEqual(conversation.participants.count(), 3)
        self.assertEqual(conversation.messages.get().body, "Hallo zusammen!")

    def test_group_avatar_falls_back_to_gr_without_title(self):
        conversation = Conversation.objects.create(created_by=self.mira, is_group=True)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        ConversationMember.objects.create(conversation=conversation, user=self.anna)

        self.assertEqual(conversation.avatar_for(self.mira), "GR")

    def test_member_can_add_and_leave_group(self):
        conversation = Conversation.objects.create(created_by=self.mira, title="Team", is_group=True)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)

        self.client.login(username="mira@example.com", password="secret-12345")
        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "member_action",
                "conversation_id": str(conversation.id),
                "action": "add_member",
                "new_member": str(self.anna.id),
            },
        )
        self.assertRedirects(response, f"/messages/{conversation.id}/")
        self.assertTrue(ConversationMember.objects.filter(conversation=conversation, user=self.anna).exists())

        self.client.logout()
        self.client.login(username="lukas@example.com", password="secret-12345")
        response = self.client.post(
            f"/messages/{conversation.id}/",
            {"form_name": "member_action", "conversation_id": str(conversation.id), "action": "leave_group"},
        )
        self.assertRedirects(response, "/messages/")
        self.assertFalse(ConversationMember.objects.filter(conversation=conversation, user=self.lukas).exists())

    def test_leave_group_action_is_ignored_for_direct_conversations(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)

        self.client.login(username="mira@example.com", password="secret-12345")
        response = self.client.post(
            f"/messages/{conversation.id}/",
            {"form_name": "member_action", "conversation_id": str(conversation.id), "action": "leave_group"},
        )
        self.assertRedirects(response, f"/messages/{conversation.id}/")
        self.assertTrue(ConversationMember.objects.filter(conversation=conversation, user=self.mira).exists())

    def test_sending_attachment_without_body_is_valid_and_downloadable(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)

        self.client.login(username="mira@example.com", password="secret-12345")
        with TemporaryDirectory() as private_root, override_settings(PRIVATE_MEDIA_ROOT=private_root):
            upload = SimpleUploadedFile("moon.png", PNG_1X1_BYTES, content_type="image/png")
            response = self.client.post(
                f"/messages/{conversation.id}/",
                {
                    "form_name": "message",
                    "conversation_id": str(conversation.id),
                    "body": "",
                    "attachment": upload,
                },
            )
            self.assertRedirects(response, f"/messages/{conversation.id}/")

            message = conversation.messages.get()
            attachment = message.attachment
            self.assertEqual(attachment.kind, "image")
            self.assertEqual(attachment.uploaded_by, self.mira)

            download_url = f"/messages/attachments/{attachment.file_id}/inline/"
            response = self.client.get(download_url)
            self.assertEqual(response.status_code, 200)

            self.client.logout()
            self.client.login(username="anna@example.com", password="secret-12345")
            response = self.client.get(download_url)
            self.assertEqual(response.status_code, 404)

    def test_message_without_body_or_attachment_is_invalid(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)

        self.client.login(username="mira@example.com", password="secret-12345")
        response = self.client.post(
            f"/messages/{conversation.id}/",
            {"form_name": "message", "conversation_id": str(conversation.id), "body": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(conversation.messages.exists())

    def test_deleting_message_removes_attachment(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)

        self.client.login(username="mira@example.com", password="secret-12345")
        with TemporaryDirectory() as private_root, override_settings(PRIVATE_MEDIA_ROOT=private_root):
            upload = SimpleUploadedFile("moon.png", PNG_1X1_BYTES, content_type="image/png")
            self.client.post(
                f"/messages/{conversation.id}/",
                {
                    "form_name": "message",
                    "conversation_id": str(conversation.id),
                    "body": "",
                    "attachment": upload,
                },
            )
            message = conversation.messages.get()

            self.client.post(
                f"/messages/{conversation.id}/",
                {
                    "form_name": "message_action",
                    "conversation_id": str(conversation.id),
                    "message_id": str(message.id),
                    "action": "delete",
                },
            )

            self.assertFalse(ChatMessageAttachment.objects.filter(message=message).exists())

    def test_replying_to_message_links_and_renders_quote(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        original = ChatMessage.objects.create(conversation=conversation, sender=self.lukas, body="Erste Nachricht")

        self.client.login(username="mira@example.com", password="secret-12345")
        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message",
                "conversation_id": str(conversation.id),
                "body": "Antwort darauf",
                "reply_to_id": str(original.id),
            },
        )
        self.assertRedirects(response, f"/messages/{conversation.id}/")

        reply = conversation.messages.get(body="Antwort darauf")
        self.assertEqual(reply.reply_to_id, original.id)

        response = self.client.get(f"/messages/{conversation.id}/")
        self.assertContains(response, "Erste Nachricht")
        self.assertContains(response, f'href="#message-{original.id}"')

    def test_replying_to_deleted_message_shows_placeholder(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        original = ChatMessage.objects.create(
            conversation=conversation,
            sender=self.lukas,
            body="",
            is_deleted=True,
        )
        reply = ChatMessage.objects.create(
            conversation=conversation,
            sender=self.mira,
            body="Antwort auf geloeschte Nachricht",
            reply_to=original,
        )

        self.client.login(username="mira@example.com", password="secret-12345")
        response = self.client.get(f"/messages/{conversation.id}/")
        self.assertContains(response, "Diese Nachricht wurde gelöscht.")

    def test_typing_ping_sets_typing_until_and_appears_in_live_updates(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)

        self.client.login(username="mira@example.com", password="secret-12345")
        response = self.client.post(f"/messages/{conversation.id}/typing/")
        self.assertEqual(response.status_code, 200)

        member = ConversationMember.objects.get(conversation=conversation, user=self.mira)
        self.assertIsNotNone(member.typing_until)
        self.assertGreater(member.typing_until, timezone.now())

        self.client.logout()
        self.client.login(username="lukas@example.com", password="secret-12345")
        response = self.client.get(f"/messages/{conversation.id}/live/")
        self.assertEqual(response.json()["typing_label"], "Mira schreibt …")

        member.typing_until = timezone.now() - timedelta(seconds=1)
        member.save(update_fields=["typing_until"])
        response = self.client.get(f"/messages/{conversation.id}/live/")
        self.assertEqual(response.json()["typing_label"], "")


class MessageReadReceiptTests(TestCase):
    def test_outgoing_message_shows_read_receipt_after_recipient_opened_chat(self):
        sender = User.objects.create_user(username="sender@example.com", email="sender@example.com", password="secret-12345")
        recipient = User.objects.create_user(username="recipient@example.com", email="recipient@example.com", password="secret-12345")
        Profile.objects.create(user=sender, display_name="Sender")
        Profile.objects.create(user=recipient, display_name="Recipient")

        conversation = Conversation.objects.create(created_by=sender)
        ConversationMember.objects.create(conversation=conversation, user=sender, last_read_at=timezone.now())
        ConversationMember.objects.create(conversation=conversation, user=recipient)

        ChatMessage.objects.create(conversation=conversation, sender=sender, body="Hallo")

        self.client.login(username="sender@example.com", password="secret-12345")
        response = self.client.get(f"/messages/{conversation.id}/")
        self.assertContains(response, "Gesendet")
        self.client.logout()

        self.client.login(username="recipient@example.com", password="secret-12345")
        self.client.get(f"/messages/{conversation.id}/")
        self.client.logout()

        self.client.login(username="sender@example.com", password="secret-12345")
        response = self.client.get(f"/messages/{conversation.id}/")
        self.assertContains(response, "Gelesen")


class MessageLiveUpdateTests(TestCase):
    def test_live_updates_return_new_messages_and_mark_chat_as_read(self):
        sender = User.objects.create_user(username="sender-live@example.com", email="sender-live@example.com", password="secret-12345")
        recipient = User.objects.create_user(username="recipient-live@example.com", email="recipient-live@example.com", password="secret-12345")
        Profile.objects.create(user=sender, display_name="Sender Live")
        Profile.objects.create(user=recipient, display_name="Recipient Live")

        conversation = Conversation.objects.create(created_by=sender)
        ConversationMember.objects.create(conversation=conversation, user=sender, last_read_at=timezone.now())
        recipient_member = ConversationMember.objects.create(conversation=conversation, user=recipient)
        ChatMessage.objects.create(conversation=conversation, sender=sender, body="Neue Live-Nachricht")

        self.client.login(username="recipient-live@example.com", password="secret-12345")
        response = self.client.get(f"/messages/{conversation.id}/live/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertIn("Neue Live-Nachricht", response.json()["message_stream_html"])

        recipient_member.refresh_from_db()
        self.assertIsNotNone(recipient_member.last_read_at)

    def test_live_updates_report_compose_blocked_state(self):
        sender = User.objects.create_user(username="sender-block@example.com", email="sender-block@example.com", password="secret-12345")
        recipient = User.objects.create_user(username="recipient-block@example.com", email="recipient-block@example.com", password="secret-12345")
        Profile.objects.create(user=sender, display_name="Sender Block")
        Profile.objects.create(user=recipient, display_name="Recipient Block")

        conversation = Conversation.objects.create(created_by=sender)
        ConversationMember.objects.create(conversation=conversation, user=sender)
        ConversationMember.objects.create(conversation=conversation, user=recipient)

        self.client.login(username="sender-block@example.com", password="secret-12345")
        response = self.client.get(f"/messages/{conversation.id}/live/")
        payload = response.json()
        self.assertFalse(payload["compose_blocked"])
        self.assertIn('class="compose-bar"', payload["compose_html"])

        self.client.login(username="recipient-block@example.com", password="secret-12345")
        self.client.post(
            f"/messages/{conversation.id}/",
            {"form_name": "member_action", "conversation_id": str(conversation.id), "action": "block"},
        )

        self.client.login(username="sender-block@example.com", password="secret-12345")
        response = self.client.get(f"/messages/{conversation.id}/live/")
        payload = response.json()
        self.assertTrue(payload["compose_blocked"])
        self.assertIn("Diese Nachricht kann derzeit nicht gesendet werden.", payload["compose_html"])

    def test_overview_live_updates_return_contact_list(self):
        user = User.objects.create_user(username="overview-live@example.com", email="overview-live@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Overview Live")

        self.client.login(username="overview-live@example.com", password="secret-12345")
        response = self.client.get("/messages/live/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertIn("contact_list_html", response.json())
        self.assertIn("overview_html", response.json())


class GlobalSearchTests(TestCase):
    def setUp(self):
        self.mira = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        self.lukas = User.objects.create_user(username="lukas@example.com", email="lukas@example.com", password="secret-12345")
        Profile.objects.create(user=self.mira, display_name="Mira")
        Profile.objects.create(user=self.lukas, display_name="Lukas")

    def test_global_search_requires_login(self):
        response = self.client.get("/search/?q=Rakete")
        self.assertRedirects(response, "/login/?next=/search/%3Fq%3DRakete")

    def test_global_search_returns_matches_across_all_three_categories(self):
        Note.objects.create(owner=self.mira, title="Raketenstart planen", plain_text="Zündfolge prüfen")
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        ChatMessage.objects.create(conversation=conversation, sender=self.lukas, body="Rakete ist startklar")
        CalendarEvent.objects.create(
            user=self.mira,
            title="Raketenstart",
            start_at=timezone.now() + timedelta(days=5),
            end_at=timezone.now() + timedelta(days=5, hours=1),
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/search/?q=Rakete")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Raketenstart planen")
        self.assertContains(response, "Rakete ist startklar")
        self.assertContains(response, "Raketenstart")

    def test_global_search_empty_query_shows_no_results(self):
        Note.objects.create(owner=self.mira, title="Irgendeine Notiz")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/search/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Wonach suchst du?")
        self.assertNotContains(response, "Irgendeine Notiz")

    def test_global_search_does_not_leak_other_users_private_data(self):
        Note.objects.create(owner=self.lukas, title="Raketengeheimnis")
        conversation = Conversation.objects.create(created_by=self.lukas)
        other_user = User.objects.create_user(username="anna@example.com", email="anna@example.com", password="secret-12345")
        Profile.objects.create(user=other_user, display_name="Anna")
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        ConversationMember.objects.create(conversation=conversation, user=other_user)
        ChatMessage.objects.create(conversation=conversation, sender=self.lukas, body="Rakete geheime Unterhaltung")
        CalendarEvent.objects.create(
            user=self.lukas,
            title="Rakete privater Termin",
            start_at=timezone.now() + timedelta(days=5),
            end_at=timezone.now() + timedelta(days=5, hours=1),
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/search/?q=Rakete")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Raketengeheimnis")
        self.assertNotContains(response, "Rakete geheime Unterhaltung")
        self.assertNotContains(response, "Rakete privater Termin")

    def test_global_search_hides_disabled_sections(self):
        SystemSettings.objects.create(notes_enabled=False, messages_enabled=False)
        Note.objects.create(owner=self.mira, title="Raketenidee")
        CalendarEvent.objects.create(
            user=self.mira,
            title="Raketenstart",
            start_at=timezone.now() + timedelta(days=5),
            end_at=timezone.now() + timedelta(days=5, hours=1),
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/search/?q=Rakete")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Notizen deaktiviert")
        self.assertContains(response, "Nachrichten deaktiviert")
        self.assertNotContains(response, "Raketenidee")
        self.assertContains(response, "Raketenstart")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class DashboardCustomizationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com",
            email="mira@example.com",
            password="secret-12345",
        )
        self.profile = Profile.objects.create(user=self.user, display_name="Mira")

    def _login(self):
        self.client.login(username="mira@example.com", password="secret-12345")

    def _layout(self, *, order=None, hidden=None):
        return {
            "version": 1,
            "order": order or list(DASHBOARD_WIDGET_IDS),
            "hidden": hidden or [],
        }

    def test_home_uses_default_layout_and_creates_missing_profile(self):
        self.profile.delete()
        self._login()

        response = self.client.get("/home/")

        self.assertEqual(response.status_code, 200)
        profile = Profile.objects.get(user=self.user)
        self.assertEqual(profile.dashboard_layout, default_dashboard_layout())
        self.assertEqual(
            [widget["id"] for widget in response.context["dashboard_widgets"]],
            list(DASHBOARD_WIDGET_IDS),
        )

    def test_home_renders_saved_order(self):
        order = ["clock", "welcome", "quick_actions", "recent_tools", "upcoming_events", "weather"]
        self.profile.dashboard_layout = self._layout(order=order)
        self.profile.save(update_fields=["dashboard_layout"])
        self._login()

        response = self.client.get("/home/")

        self.assertEqual(
            [widget["id"] for widget in response.context["dashboard_widgets"]],
            order,
        )

    def test_home_keeps_hidden_widgets_for_edit_mode_and_shows_empty_state(self):
        self.profile.dashboard_layout = self._layout(hidden=list(DASHBOARD_WIDGET_IDS))
        self.profile.save(update_fields=["dashboard_layout"])
        self._login()

        response = self.client.get("/home/")

        self.assertFalse(response.context["dashboard_visible_widgets"])
        self.assertContains(response, "Dein Dashboard ist leer")
        self.assertContains(response, 'data-widget-id="welcome"')
        self.assertContains(response, "hidden")

    def test_normalization_adds_missing_widgets_and_drops_obsolete_entries(self):
        broken_layout = {
            "version": 1,
            "order": ["clock", "unknown", "clock", "welcome"],
            "hidden": ["removed", "weather", "weather"],
        }

        normalized = normalize_dashboard_layout(broken_layout)

        self.assertEqual(normalized["order"][:2], ["clock", "welcome"])
        self.assertEqual(set(normalized["order"]), set(DASHBOARD_WIDGET_IDS))
        self.assertEqual(normalized["hidden"], ["weather"])

    def test_saved_hidden_layout_is_ignored_when_customization_is_disabled(self):
        SystemSettings.objects.create(dashboard_customization_enabled=False)
        self.profile.dashboard_layout = self._layout(hidden=list(DASHBOARD_WIDGET_IDS))
        self.profile.save(update_fields=["dashboard_layout"])
        self._login()

        response = self.client.get("/home/")

        self.assertNotContains(response, "Dashboard anpassen")
        self.assertContains(response, "Willkommen zurück")
        self.assertContains(response, "Nächste Termine")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.dashboard_layout["hidden"], list(DASHBOARD_WIDGET_IDS))

    def test_dashboard_layout_api_requires_login(self):
        response = self.client.patch(
            "/home/dashboard-layout/",
            data=json.dumps(default_dashboard_layout()),
            content_type="application/json",
        )

        self.assertRedirects(response, "/login/?next=/home/dashboard-layout/")

    def test_dashboard_layout_api_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(username="mira@example.com", password="secret-12345")

        response = csrf_client.patch(
            "/home/dashboard-layout/",
            data=json.dumps(default_dashboard_layout()),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

        csrf_client.get("/home/")
        token = csrf_client.cookies["csrftoken"].value
        response = csrf_client.patch(
            "/home/dashboard-layout/",
            data=json.dumps(default_dashboard_layout()),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 200)

    def test_dashboard_layout_api_saves_valid_layout_for_current_user_only(self):
        other = User.objects.create_user(username="lukas@example.com", email="lukas@example.com", password="secret-12345")
        other_profile = Profile.objects.create(user=other, display_name="Lukas")
        layout = self._layout(
            order=["recent_tools", "quick_actions", "upcoming_events", "weather", "clock", "welcome"],
            hidden=["clock", "weather"],
        )
        self._login()

        response = self.client.patch(
            "/home/dashboard-layout/",
            data=json.dumps(layout),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "layout": layout})
        self.profile.refresh_from_db()
        other_profile.refresh_from_db()
        self.assertEqual(self.profile.dashboard_layout, layout)
        self.assertEqual(other_profile.dashboard_layout, default_dashboard_layout())

    def test_dashboard_layout_api_rejects_invalid_layouts_without_saving(self):
        original_layout = self.profile.dashboard_layout
        invalid_layouts = [
            {"version": 1, "order": ["welcome"], "hidden": []},
            self._layout(order=["welcome", "welcome", "clock", "weather", "upcoming_events", "quick_actions"]),
            self._layout(order=["welcome", "clock", "weather", "upcoming_events", "quick_actions", "bogus"]),
            {"version": 1, "order": list(DASHBOARD_WIDGET_IDS), "hidden": "weather"},
            ["welcome", "clock"],
        ]
        self._login()

        for layout in invalid_layouts:
            response = self.client.patch(
                "/home/dashboard-layout/",
                data=json.dumps(layout),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 400)
            self.profile.refresh_from_db()
            self.assertEqual(self.profile.dashboard_layout, original_layout)

    def test_dashboard_layout_api_respects_feature_flag(self):
        SystemSettings.objects.create(dashboard_customization_enabled=False)
        layout = self._layout(hidden=["welcome"])
        self._login()

        response = self.client.patch(
            "/home/dashboard-layout/",
            data=json.dumps(layout),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.dashboard_layout, default_dashboard_layout())

    def test_disabled_weather_or_messages_are_not_restored_by_dashboard_layout(self):
        SystemSettings.objects.create(weather_enabled=False, messages_enabled=False)
        self.profile.dashboard_layout = self._layout(order=["weather", "quick_actions", "recent_tools", "welcome", "clock", "upcoming_events"])
        self.profile.save(update_fields=["dashboard_layout"])
        self._login()

        response = self.client.get("/home/")

        self.assertNotContains(response, 'data-widget-id="weather"')
        self.assertNotContains(response, "Nachrichten")
        self.assertContains(response, "Schnellzugriff")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
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
        self.assertFalse(settings_obj.calendar_sync_enabled)
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
        self.assertContains(response, "Der Login ist fuer Nutzer voruebergehend deaktiviert")

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

    def test_disabled_reminders_block_direct_post(self):
        SystemSettings.objects.create(calendar_reminders_enabled=False)
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/calendar/",
            {"form_name": "reminder_add", "title": "Blockierte Erinnerung"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(CalendarReminder.objects.filter(title="Blockierte Erinnerung").exists())

    def test_disabled_calendar_sync_hides_settings_forms_and_blocks_direct_post(self):
        SystemSettings.objects.create(calendar_sync_enabled=False)
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/settings/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="form_name" value="calendar_source_add"')
        self.assertContains(response, "Kalendersynchronisierung pausiert")

        response = self.client.post(
            "/settings/",
            {
                "form_name": "calendar_source_add",
                "new-name": "Privat",
                "new-ical_url": "https://calendar.google.com/calendar/ical/example/basic.ics",
                "new-color": "blue",
                "new-enabled": "on",
            },
        )

        self.assertRedirects(response, "/home/")
        self.assertFalse(CalendarSource.objects.filter(user=self.user, name="Privat").exists())

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


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class NotesTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner@example.com", email="owner@example.com", password="secret-12345", first_name="Owner"
        )
        self.reader = User.objects.create_user(
            username="reader@example.com", email="reader@example.com", password="secret-12345", first_name="Reader"
        )
        self.editor = User.objects.create_user(
            username="editor@example.com", email="editor@example.com", password="secret-12345", first_name="Editor"
        )
        Profile.objects.create(user=self.owner, display_name="Owner")
        Profile.objects.create(user=self.reader, display_name="Reader")
        Profile.objects.create(user=self.editor, display_name="Editor")
        self.client.login(username="owner@example.com", password="secret-12345")

    def create_note(self, title="Projektidee", client=None):
        response = (client or self.client).post(
            "/notes/api/create/",
            data=json.dumps({"title": title}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return Note.objects.get(pk=response.json()["note"]["id"])

    def create_folder(self, name="Projekte", parent=None, client=None):
        response = (client or self.client).post(
            "/notes/api/folders/",
            data=json.dumps({"name": name, "parent_id": parent.id if parent else None}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return NoteFolder.objects.get(pk=response.json()["folder"]["id"])

    def save_note(self, note, *, text="Erster Inhalt", revision=None, client=None):
        return (client or self.client).patch(
            f"/notes/api/{note.id}/",
            data=json.dumps(
                {
                    "title": note.title,
                    "document": note_document(text),
                    "base_revision": revision or note.revision,
                }
            ),
            content_type="application/json",
        )

    def test_notes_require_login_and_render_editor(self):
        self.client.logout()
        self.assertRedirects(self.client.get("/notes/"), "/login/?next=/notes/")
        self.client.login(username="owner@example.com", password="secret-12345")
        note = self.create_note()
        response = self.client.get(f"/notes/{note.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Textformatierung")
        self.assertContains(response, "Versionsverlauf")
        self.assertContains(response, "Projektidee")
        self.assertContains(response, "data-table-dialog-open")
        self.assertContains(response, "data-table-dialog")
        self.assertContains(response, "Tabellenwerkzeuge")
        self.assertContains(response, f'data-note-card="{note.id}"')
        self.assertContains(response, "data-note-card-title")
        self.assertContains(response, "data-note-title", count=1)
        self.assertContains(response, "data-note-card-name")
        self.assertContains(response, 'class="note-list-card-link"')
        self.assertNotContains(response, "data-note-card-preview")
        self.assertNotContains(response, "data-note-card-updated")
        self.assertNotContains(response, "data-note-card-tags")
        self.assertContains(response, "data-note-context-menu")
        self.assertContains(response, "data-folder-context-menu")
        self.assertContains(response, "data-note-context-action=\"rename\"")
        self.assertContains(response, "data-folder-context-action=\"create\"")

    def test_notes_overview_does_not_automatically_open_first_note(self):
        note = self.create_note()
        response = self.client.get("/notes/")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["selected_note_data"])
        self.assertContains(response, "Dein Platz für Gedanken")
        self.assertContains(response, '<a class="notes-mobile-back" href="/notes/"', count=0)

        detail = self.client.get(f"/notes/{note.id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.context["selected_note_data"]["id"], note.id)
        self.assertContains(detail, '<a class="notes-mobile-back" href="/notes/"')

    def test_note_save_derives_plain_text_and_search(self):
        note = self.create_note()
        response = self.save_note(note, text="Mondlicht Planung")
        self.assertEqual(response.status_code, 200, response.content)
        saved_note = response.json()["note"]
        self.assertEqual(saved_note["preview"], "Mondlicht Planung")
        self.assertTrue(saved_note["updated_at"])
        note.refresh_from_db()
        self.assertEqual(note.plain_text, "Mondlicht Planung")
        self.assertEqual(note.revision, 2)
        response = self.client.get("/notes/?q=Mondlicht")
        self.assertContains(response, "Projektidee")

    def test_note_title_change_persists_when_note_is_reopened(self):
        note = self.create_note()
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps(
                {
                    "title": "Neuer dauerhafter Titel",
                    "document": note_document("Titeltest"),
                    "base_revision": note.revision,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["note"]["title"], "Neuer dauerhafter Titel")

        note.refresh_from_db()
        self.assertEqual(note.title, "Neuer dauerhafter Titel")
        reopened = self.client.get(f"/notes/{note.id}/")
        self.assertEqual(reopened.context["selected_note_data"]["title"], "Neuer dauerhafter Titel")
        self.assertContains(reopened, 'value="Neuer dauerhafter Titel"')

    def test_note_templates_pass_document_validation(self):
        for factory in NOTE_TEMPLATES.values():
            validate_note_document(factory())

    def test_create_note_with_template_populates_plain_text(self):
        response = self.client.post(
            "/notes/api/create/",
            data=json.dumps({"title": "Sprint-Meeting", "template": "meeting"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        note = Note.objects.get(pk=response.json()["note"]["id"])
        self.assertIn("Teilnehmer", note.plain_text)
        self.assertIn("Agenda", note.plain_text)
        self.assertIn("Aufgaben", note.plain_text)

    def test_create_note_with_unknown_template_is_rejected(self):
        response = self.client.post(
            "/notes/api/create/",
            data=json.dumps({"title": "Sprint-Meeting", "template": "does-not-exist"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_save_note_as_custom_template_strips_hazards_and_reuses_structure(self):
        note = self.create_note("Vorlagen-Quelle")
        note.document = {
            "type": "doc",
            "content": [
                {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Agenda"}]},
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "Wichtig",
                            "marks": [
                                {"type": "bold"},
                                {"type": "commentThread", "attrs": {"threadId": str(uuid.uuid4())}},
                            ],
                        },
                        {"type": "mention", "attrs": {"userId": self.editor.id, "label": "Editor"}},
                    ],
                },
                {"type": "noteImage", "attrs": {"attachmentId": str(uuid.uuid4()), "alt": "Bild", "width": 400}},
                {
                    "type": "bulletList",
                    "content": [
                        {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Punkt"}]}]},
                    ],
                },
            ],
        }
        note.plain_text = "Agenda Wichtig Punkt"
        note.save(update_fields=["document", "plain_text"])

        response = self.client.post(
            "/notes/api/templates/",
            data=json.dumps({"note_id": note.id, "name": "Mein Meeting"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        template_id = response.json()["template"]["id"]
        self.assertEqual(response.json()["template"]["name"], "Mein Meeting")

        template = NoteTemplate.objects.get(pk=template_id, owner=self.owner)
        document_json = json.dumps(template.document)
        self.assertNotIn("noteImage", document_json)
        self.assertNotIn("mention", document_json)
        self.assertNotIn("commentThread", document_json)
        self.assertIn("@Editor", document_json)

        create_response = self.client.post(
            "/notes/api/create/",
            data=json.dumps({"title": "Aus Vorlage", "custom_template_id": template_id}),
            content_type="application/json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.content)
        created_note = Note.objects.get(pk=create_response.json()["note"]["id"])
        self.assertIn("Agenda", created_note.plain_text)
        self.assertIn("@Editor", created_note.plain_text)
        self.assertIn("Punkt", created_note.plain_text)

    def test_custom_template_name_must_be_unique_per_owner(self):
        note = self.create_note()
        first = self.client.post(
            "/notes/api/templates/",
            data=json.dumps({"note_id": note.id, "name": "Duplikat"}),
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 201, first.content)
        second = self.client.post(
            "/notes/api/templates/",
            data=json.dumps({"note_id": note.id, "name": "Duplikat"}),
            content_type="application/json",
        )
        self.assertEqual(second.status_code, 400)

    def test_custom_template_deletion_is_owner_scoped(self):
        note = self.create_note()
        response = self.client.post(
            "/notes/api/templates/",
            data=json.dumps({"note_id": note.id, "name": "Meine Vorlage"}),
            content_type="application/json",
        )
        template_id = response.json()["template"]["id"]
        editor_client = Client()
        editor_client.login(username="editor@example.com", password="secret-12345")
        denied = editor_client.delete(f"/notes/api/templates/{template_id}/")
        self.assertEqual(denied.status_code, 404)
        self.assertTrue(NoteTemplate.objects.filter(pk=template_id).exists())
        allowed = self.client.delete(f"/notes/api/templates/{template_id}/")
        self.assertEqual(allowed.status_code, 200)
        self.assertFalse(NoteTemplate.objects.filter(pk=template_id).exists())

    def test_create_note_with_missing_custom_template_returns_404(self):
        response = self.client.post(
            "/notes/api/create/",
            data=json.dumps({"title": "Fehlt", "custom_template_id": 999999}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_custom_template_limit_is_enforced(self):
        note = self.create_note()
        NoteTemplate.objects.bulk_create(
            [NoteTemplate(owner=self.owner, name=f"Vorlage {i}", document=empty_note_document()) for i in range(30)]
        )
        response = self.client.post(
            "/notes/api/templates/",
            data=json.dumps({"note_id": note.id, "name": "Zu viel"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_nested_folders_render_notes_in_the_sidebar_tree(self):
        projects = self.create_folder("Projekte")
        lunora = self.create_folder("Lunora", parent=projects)
        response = self.client.post(
            "/notes/api/create/",
            data=json.dumps({"title": "Ordnernotiz", "folder_id": lunora.id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        note = Note.objects.get(pk=response.json()["note"]["id"])
        self.assertEqual(response.json()["note"]["folder_id"], lunora.id)
        self.assertEqual(NoteUserState.objects.get(note=note, user=self.owner).folder, lunora)

        page = self.client.get(f"/notes/{note.id}/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f'data-note-folder="{projects.id}"')
        self.assertContains(page, f'data-note-folder="{lunora.id}"')
        self.assertContains(page, f'data-note-card="{note.id}"')
        self.assertContains(page, "Ordnernotiz")
        self.assertContains(page, "Neuer Unterordner")

    def test_folder_names_are_unique_within_the_same_parent(self):
        self.create_folder("Projekte")
        response = self.client.post(
            "/notes/api/folders/",
            data=json.dumps({"name": "projekte"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(NoteFolder.objects.filter(owner=self.owner).count(), 1)

    def test_shared_note_folder_assignment_is_personal(self):
        note = self.create_note()
        NoteShare.objects.create(note=note, user=self.reader, role=NoteShare.ROLE_READER)
        owner_folder = self.create_folder("Privat")
        reader_client = Client()
        reader_client.login(username="reader@example.com", password="secret-12345")
        reader_folder = self.create_folder("Geteilt", client=reader_client)

        response = reader_client.patch(
            f"/notes/api/{note.id}/folder/",
            data=json.dumps({"folder_id": reader_folder.id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["note"]["folder_id"], reader_folder.id)
        self.assertEqual(NoteUserState.objects.get(note=note, user=self.reader).folder, reader_folder)
        self.assertIsNone(NoteUserState.objects.get(note=note, user=self.owner).folder)

        denied = reader_client.patch(
            f"/notes/api/{note.id}/folder/",
            data=json.dumps({"folder_id": owner_folder.id}),
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 404)

    def test_deleting_folder_preserves_notes_and_reparents_children(self):
        parent = self.create_folder("Arbeit")
        child = self.create_folder("Lunora", parent=parent)
        note = self.create_note()
        state = NoteUserState.objects.get(note=note, user=self.owner)
        state.folder = parent
        state.save(update_fields=["folder"])

        response = self.client.delete(f"/notes/api/folders/{parent.id}/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(NoteFolder.objects.filter(pk=parent.id).exists())
        child.refresh_from_db()
        state.refresh_from_db()
        self.assertIsNone(child.parent)
        self.assertIsNone(state.folder)
        self.assertTrue(Note.objects.filter(pk=note.id).exists())

    def test_moving_and_duplicating_note_keeps_folder_context(self):
        folder = self.create_folder("Ideen")
        note = self.create_note()
        moved = self.client.patch(
            f"/notes/api/{note.id}/folder/",
            data=json.dumps({"folder_id": folder.id}),
            content_type="application/json",
        )
        self.assertEqual(moved.status_code, 200, moved.content)

        duplicate = self.client.post(
            f"/notes/api/{note.id}/actions/",
            data=json.dumps({"action": "duplicate"}),
            content_type="application/json",
        )
        self.assertEqual(duplicate.status_code, 201, duplicate.content)
        duplicate_note = Note.objects.get(pk=duplicate.json()["note"]["id"])
        self.assertEqual(NoteUserState.objects.get(note=duplicate_note, user=self.owner).folder, folder)

    def test_drag_move_reorders_notes_before_and_after_each_other(self):
        first = self.create_note("Erste")
        second = self.create_note("Zweite")
        third = self.create_note("Dritte")

        response = self.client.patch(
            "/notes/api/tree/move/",
            data=json.dumps(
                {
                    "item_type": "note",
                    "item_id": third.id,
                    "placement": "before",
                    "target_type": "note",
                    "target_id": first.id,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        ordered_ids = list(
            NoteUserState.objects.filter(user=self.owner, folder__isnull=True)
            .order_by("position")
            .values_list("note_id", flat=True)
        )
        self.assertEqual(ordered_ids, [third.id, first.id, second.id])

        response = self.client.patch(
            "/notes/api/tree/move/",
            data=json.dumps(
                {
                    "item_type": "note",
                    "item_id": third.id,
                    "placement": "after",
                    "target_type": "note",
                    "target_id": second.id,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        ordered_ids = list(
            NoteUserState.objects.filter(user=self.owner, folder__isnull=True)
            .order_by("position")
            .values_list("note_id", flat=True)
        )
        self.assertEqual(ordered_ids, [first.id, second.id, third.id])

    def test_drag_move_places_note_at_bottom_of_folder(self):
        folder = self.create_folder("Projekte")
        existing = self.create_note("Vorhanden")
        moved = self.create_note("Verschoben")
        existing_state = NoteUserState.objects.get(note=existing, user=self.owner)
        existing_state.folder = folder
        existing_state.position = 1000
        existing_state.save(update_fields=["folder", "position"])

        response = self.client.patch(
            "/notes/api/tree/move/",
            data=json.dumps(
                {
                    "item_type": "note",
                    "item_id": moved.id,
                    "placement": "inside",
                    "target_type": "folder",
                    "target_id": folder.id,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        moved_state = NoteUserState.objects.get(note=moved, user=self.owner)
        existing_state.refresh_from_db()
        self.assertEqual(moved_state.folder, folder)
        self.assertGreater(moved_state.position, existing_state.position)

    def test_drag_move_prevents_folder_cycles(self):
        parent = self.create_folder("Eltern")
        child = self.create_folder("Kind", parent=parent)

        response = self.client.patch(
            "/notes/api/tree/move/",
            data=json.dumps(
                {
                    "item_type": "folder",
                    "item_id": parent.id,
                    "placement": "inside",
                    "target_type": "folder",
                    "target_id": child.id,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        parent.refresh_from_db()
        child.refresh_from_db()
        self.assertIsNone(parent.parent)
        self.assertEqual(child.parent, parent)

    def test_drag_move_renders_persistent_tree_metadata(self):
        folder = self.create_folder("Ablage")
        note = self.create_note("Ziehbar")
        response = self.client.get(f"/notes/{note.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "custom")
        self.assertContains(response, "data-root-drop-target")
        self.assertContains(response, 'data-item-type="note"')
        self.assertContains(response, 'data-item-type="folder"')
        self.assertContains(response, f'data-item-id="{folder.id}"')
        self.assertContains(response, 'data-tree-draggable="true"')

    def test_stale_revision_returns_conflict_without_overwriting(self):
        note = self.create_note()
        first = self.save_note(note, text="Serverstand", revision=1)
        self.assertEqual(first.status_code, 200)
        stale = self.save_note(note, text="Veralteter Stand", revision=1)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"], "revision_conflict")
        note.refresh_from_db()
        self.assertEqual(note.plain_text, "Serverstand")

    def test_share_roles_and_personal_pin_are_enforced(self):
        note = self.create_note()
        NoteShare.objects.create(note=note, user=self.reader, role=NoteShare.ROLE_READER)
        NoteShare.objects.create(note=note, user=self.editor, role=NoteShare.ROLE_EDITOR)
        reader_client = Client()
        editor_client = Client()
        reader_client.login(username="reader@example.com", password="secret-12345")
        editor_client.login(username="editor@example.com", password="secret-12345")

        denied = self.save_note(note, client=reader_client)
        self.assertEqual(denied.status_code, 403)
        pin = reader_client.post(
            f"/notes/api/{note.id}/actions/", data=json.dumps({"action": "pin"}), content_type="application/json"
        )
        self.assertEqual(pin.status_code, 200)
        self.assertTrue(NoteUserState.objects.get(note=note, user=self.reader).is_pinned)
        self.assertFalse(NoteUserState.objects.get(note=note, user=self.owner).is_pinned)

        changed = self.save_note(note, text="Vom Bearbeiter", client=editor_client)
        self.assertEqual(changed.status_code, 200, changed.content)
        note.refresh_from_db()
        self.assertEqual(note.plain_text, "Vom Bearbeiter")

    def test_only_owner_can_manage_shares_and_trash(self):
        note = self.create_note()
        NoteShare.objects.create(note=note, user=self.editor, role=NoteShare.ROLE_EDITOR)
        editor_client = Client()
        editor_client.login(username="editor@example.com", password="secret-12345")
        denied = editor_client.post(
            f"/notes/api/{note.id}/actions/", data=json.dumps({"action": "trash"}), content_type="application/json"
        )
        self.assertEqual(denied.status_code, 403)
        denied = editor_client.post(
            f"/notes/api/{note.id}/shares/",
            data=json.dumps({"user_id": self.reader.id, "role": "reader"}),
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 403)

        trashed = self.client.post(
            f"/notes/api/{note.id}/actions/", data=json.dumps({"action": "trash"}), content_type="application/json"
        )
        self.assertEqual(trashed.status_code, 200)
        self.assertEqual(editor_client.get(f"/notes/api/{note.id}/").status_code, 404)
        restored = self.client.post(
            f"/notes/api/{note.id}/actions/", data=json.dumps({"action": "restore"}), content_type="application/json"
        )
        self.assertEqual(restored.status_code, 200)

    def bulk_action(self, note_ids, action, *, folder_id=None, client=None):
        payload = {"note_ids": note_ids, "action": action}
        if folder_id is not None or action == "move_folder":
            payload["folder_id"] = folder_id
        return (client or self.client).post(
            "/notes/api/bulk-action/", data=json.dumps(payload), content_type="application/json"
        )

    def test_bulk_action_pins_and_archives_multiple_notes(self):
        first = self.create_note("Erste Notiz")
        second = self.create_note("Zweite Notiz")

        response = self.bulk_action([first.id, second.id], "pin")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(sorted(response.json()["updated_ids"]), sorted([first.id, second.id]))
        self.assertEqual(response.json()["skipped_ids"], [])
        self.assertTrue(NoteUserState.objects.get(note=first, user=self.owner).is_pinned)
        self.assertTrue(NoteUserState.objects.get(note=second, user=self.owner).is_pinned)

        response = self.bulk_action([first.id, second.id], "archive")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(NoteUserState.objects.get(note=first, user=self.owner).is_archived)
        self.assertTrue(NoteUserState.objects.get(note=second, user=self.owner).is_archived)

    def test_bulk_action_skips_notes_the_user_is_not_allowed_to_trash(self):
        owned = self.create_note("Eigene Notiz")
        editor_client = Client()
        editor_client.login(username="editor@example.com", password="secret-12345")
        foreign_note = self.create_note("Fremde Notiz", client=editor_client)
        NoteShare.objects.create(note=foreign_note, user=self.owner, role=NoteShare.ROLE_EDITOR)

        response = self.bulk_action([owned.id, foreign_note.id], "trash")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["updated_ids"], [owned.id])
        self.assertEqual(response.json()["skipped_ids"], [foreign_note.id])
        owned.refresh_from_db()
        foreign_note.refresh_from_db()
        self.assertIsNotNone(owned.deleted_at)
        self.assertIsNone(foreign_note.deleted_at)

    def test_bulk_move_notes_to_folder(self):
        folder = self.create_folder("Archiv")
        first = self.create_note("Erste Notiz")
        second = self.create_note("Zweite Notiz")

        response = self.bulk_action([first.id, second.id], "move_folder", folder_id=folder.id)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(sorted(response.json()["updated_ids"]), sorted([first.id, second.id]))
        self.assertEqual(NoteUserState.objects.get(note=first, user=self.owner).folder, folder)
        self.assertEqual(NoteUserState.objects.get(note=second, user=self.owner).folder, folder)

        missing_folder = self.bulk_action([first.id], "move_folder", folder_id=999999)
        self.assertEqual(missing_folder.status_code, 404)

    def test_bulk_action_validates_note_ids_and_action(self):
        note = self.create_note()
        empty = self.bulk_action([], "pin")
        self.assertEqual(empty.status_code, 400)
        unknown_action = self.bulk_action([note.id], "levitate")
        self.assertEqual(unknown_action.status_code, 400)
        bad_id = self.client.post(
            "/notes/api/bulk-action/",
            data=json.dumps({"note_ids": ["not-a-number"], "action": "pin"}),
            content_type="application/json",
        )
        self.assertEqual(bad_id.status_code, 400)

    def test_version_history_and_restore_create_new_revision(self):
        note = self.create_note()
        saved = self.save_note(note, text="Neue Fassung")
        self.assertEqual(saved.status_code, 200)
        version = NoteVersion.objects.get(note=note)
        note.refresh_from_db()
        response = self.client.post(
            f"/notes/api/{note.id}/versions/{version.id}/restore/",
            data=json.dumps({"base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        note.refresh_from_db()
        self.assertEqual(note.plain_text, "")
        self.assertEqual(note.revision, 3)
        self.assertEqual(note.versions.count(), 2)

    def test_version_history_is_limited_to_100_entries_and_90_days(self):
        note = self.create_note()
        for revision in range(105):
            NoteVersion.objects.create(
                note=note,
                created_by=self.owner,
                source_revision=revision + 1,
                title=f"Version {revision + 1}",
                document=note_document(str(revision + 1)),
            )
        oldest = note.versions.order_by("created_at").first()
        NoteVersion.objects.filter(pk=oldest.pk).update(created_at=timezone.now() - timedelta(days=91))
        prune_note_versions(note)
        self.assertEqual(note.versions.count(), 100)
        self.assertFalse(NoteVersion.objects.filter(pk=oldest.pk).exists())

    def test_tiptap_table_attributes_are_accepted(self):
        note = self.create_note()
        table_document = {
            "type": "doc",
            "content": [
                {
                    "type": "table",
                    "content": [
                        {
                            "type": "tableRow",
                            "content": [
                                {
                                    "type": "tableHeader",
                                    "attrs": {"colspan": 1, "rowspan": 1, "colwidth": None, "align": None},
                                    "content": [{"type": "paragraph", "attrs": {"textAlign": None}}],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": table_document, "base_revision": 1}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_private_image_upload_requires_note_access(self):
        note = self.create_note()
        with TemporaryDirectory() as private_root, override_settings(PRIVATE_MEDIA_ROOT=private_root):
            upload = SimpleUploadedFile("moon.png", PNG_1X1_BYTES, content_type="image/png")
            response = self.client.post(
                f"/notes/api/{note.id}/attachments/", {"kind": "image", "file": upload}
            )
            self.assertEqual(response.status_code, 201, response.content)
            attachment = NoteAttachment.objects.get(note=note)
            allowed = self.client.get(f"/notes/attachments/{attachment.file_id}/inline/")
            self.assertEqual(allowed.status_code, 200)
            allowed.close()
            stranger_client = Client()
            stranger_client.login(username="reader@example.com", password="secret-12345")
            self.assertEqual(
                stranger_client.get(f"/notes/attachments/{attachment.file_id}/inline/").status_code,
                404,
            )

    def test_invalid_editor_json_and_unsafe_links_are_rejected(self):
        note = self.create_note()
        unsafe = {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "X", "marks": [{"type": "link", "attrs": {"href": "javascript:alert(1)"}}]}]}],
        }
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": unsafe, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        note.refresh_from_db()
        self.assertEqual(note.revision, 1)

    def test_tiptap_link_attributes_are_accepted(self):
        note = self.create_note()
        linked_document = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "YouTube",
                            "marks": [
                                {
                                    "type": "link",
                                    "attrs": {
                                        "href": "youtube.com",
                                        "target": "_blank",
                                        "rel": "noopener noreferrer",
                                        "class": None,
                                        "title": None,
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": linked_document, "base_revision": 1}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        saved_document = response.json()["note"]["document"]
        self.assertEqual(saved_document["content"][0]["content"][0]["marks"][0]["attrs"]["href"], "https://youtube.com")

    def test_pdf_export_preserves_access_control_and_returns_pdf(self):
        note = self.create_note("PDF Beispiel")
        note.document = {
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 1, "textAlign": "center"},
                    "content": [{"type": "text", "text": "Gestaltete Überschrift"}],
                },
                {
                    "type": "paragraph",
                    "attrs": {"textAlign": "justify"},
                    "content": [
                        {"type": "text", "text": "Fett", "marks": [{"type": "bold"}]},
                        {"type": "text", "text": " und farbig", "marks": [{"type": "textStyle", "attrs": {"color": "#a67c52", "fontSize": "18px", "lineHeight": "1.5"}}]},
                    ],
                },
                {
                    "type": "bulletList",
                    "content": [
                        {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Listenpunkt"}]}]},
                    ],
                },
                {
                    "type": "table",
                    "content": [
                        {
                            "type": "tableRow",
                            "content": [
                                {"type": "tableHeader", "attrs": {"colspan": 1, "rowspan": 1}, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Spalte"}]}]},
                                {"type": "tableHeader", "attrs": {"colspan": 1, "rowspan": 1}, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Wert"}]}]},
                            ],
                        },
                        {
                            "type": "tableRow",
                            "content": [
                                {"type": "tableCell", "attrs": {"colspan": 1, "rowspan": 1}, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "A"}]}]},
                                {"type": "tableCell", "attrs": {"colspan": 1, "rowspan": 1}, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "1"}]}]},
                            ],
                        },
                    ],
                },
            ],
        }
        note.plain_text = "Gestaltete Überschrift Fett und farbig Listenpunkt Spalte Wert A 1"
        note.save(update_fields=["document", "plain_text"])

        response = self.client.get(f"/notes/{note.id}/export/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(".pdf", response["Content-Disposition"])
        self.assertEqual(response["Cache-Control"], "private, no-store")
        pdf_bytes = b"".join(response.streaming_content)
        response.close()
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertIn(b"%%EOF", pdf_bytes[-1024:])
        self.assertGreater(len(pdf_bytes), 1500)

        NoteShare.objects.create(note=note, user=self.reader, role=NoteShare.ROLE_READER)
        reader_client = Client()
        reader_client.login(username="reader@example.com", password="secret-12345")
        reader_response = reader_client.get(f"/notes/{note.id}/export/pdf/")
        self.assertEqual(reader_response.status_code, 200)
        reader_response.close()

        stranger_client = Client()
        stranger_client.login(username="editor@example.com", password="secret-12345")
        self.assertEqual(stranger_client.get(f"/notes/{note.id}/export/pdf/").status_code, 404)
        self.assertRedirects(Client().get(f"/notes/{note.id}/export/pdf/"), f"/login/?next=/notes/{note.id}/export/pdf/")

    def test_markdown_export_preserves_access_control_and_renders_structure(self):
        note = self.create_note("Markdown Beispiel")
        note.document = {
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 2},
                    "content": [{"type": "text", "text": "Überschrift"}],
                },
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Fett", "marks": [{"type": "bold"}]},
                        {"type": "text", "text": " und *sternchen*"},
                    ],
                },
                {
                    "type": "bulletList",
                    "content": [
                        {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Listenpunkt"}]}]},
                    ],
                },
                {
                    "type": "codeBlock",
                    "attrs": {"language": "python"},
                    "content": [{"type": "text", "text": "print('hi')"}],
                },
                {
                    "type": "table",
                    "content": [
                        {
                            "type": "tableRow",
                            "content": [
                                {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Spalte"}]}]},
                                {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Wert"}]}]},
                            ],
                        },
                        {
                            "type": "tableRow",
                            "content": [
                                {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "A | B"}]}]},
                                {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "1"}]}]},
                            ],
                        },
                    ],
                },
            ],
        }
        note.plain_text = "Überschrift Fett und sternchen Listenpunkt print hi Spalte Wert A B 1"
        note.save(update_fields=["document", "plain_text"])

        response = self.client.get(f"/notes/{note.id}/export/markdown/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/markdown; charset=utf-8")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(".md", response["Content-Disposition"])
        self.assertEqual(response["Cache-Control"], "private, no-store")
        body = b"".join(response.streaming_content).decode("utf-8")
        response.close()

        self.assertIn("# Markdown Beispiel", body)
        self.assertIn("## Überschrift", body)
        self.assertIn("**Fett**", body)
        self.assertIn("\\*sternchen\\*", body)
        self.assertIn("- Listenpunkt", body)
        self.assertIn("```python", body)
        self.assertIn("print('hi')", body)
        self.assertIn("| Spalte | Wert |", body)
        self.assertIn("| --- | --- |", body)
        self.assertIn("A \\| B", body)

        NoteShare.objects.create(note=note, user=self.reader, role=NoteShare.ROLE_READER)
        reader_client = Client()
        reader_client.login(username="reader@example.com", password="secret-12345")
        reader_response = reader_client.get(f"/notes/{note.id}/export/markdown/")
        self.assertEqual(reader_response.status_code, 200)
        reader_response.close()

        stranger_client = Client()
        stranger_client.login(username="editor@example.com", password="secret-12345")
        self.assertEqual(stranger_client.get(f"/notes/{note.id}/export/markdown/").status_code, 404)
        self.assertRedirects(
            Client().get(f"/notes/{note.id}/export/markdown/"), f"/login/?next=/notes/{note.id}/export/markdown/"
        )

    def test_shortcut_conflicts_are_rejected_and_valid_overrides_persist(self):
        invalid = self.client.patch(
            "/notes/api/shortcuts/",
            data=json.dumps({"shortcuts": {"save": "Mod+S", "bold": "Mod+S"}}),
            content_type="application/json",
        )
        self.assertEqual(invalid.status_code, 400)
        valid = self.client.patch(
            "/notes/api/shortcuts/",
            data=json.dumps({"shortcuts": {"save": "Alt+S", "bold": ""}}),
            content_type="application/json",
        )
        self.assertEqual(valid.status_code, 200, valid.content)
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.note_shortcuts, {"save": "Alt+S", "bold": ""})

    def test_expired_trash_is_purged_with_private_files(self):
        note = self.create_note()
        note.deleted_at = timezone.now() - timedelta(days=31)
        note.save(update_fields=["deleted_at"])
        self.assertEqual(purge_expired_notes(), 1)
        self.assertFalse(Note.objects.filter(pk=note.id).exists())

    def test_disabled_notes_feature_blocks_page_and_api(self):
        note = self.create_note()
        self.save_note(note, text="Erster Inhalt")
        note.refresh_from_db()
        NoteShare.objects.create(note=note, user=self.reader, role=NoteShare.ROLE_READER)
        version = NoteVersion.objects.create(
            note=note,
            created_by=self.owner,
            source_revision=note.revision,
            title=note.title,
            document=note.document,
        )

        with TemporaryDirectory() as private_root, override_settings(PRIVATE_MEDIA_ROOT=private_root):
            upload = SimpleUploadedFile("moon.png", PNG_1X1_BYTES, content_type="image/png")
            attachment_response = self.client.post(
                f"/notes/api/{note.id}/attachments/", {"kind": "image", "file": upload}
            )
            self.assertEqual(attachment_response.status_code, 201, attachment_response.content)
            attachment = NoteAttachment.objects.get(note=note)

            SystemSettings.objects.update_or_create(pk=1, defaults={"notes_enabled": False})

            self.assertEqual(self.client.get("/notes/").status_code, 503)
            self.assertEqual(self.client.get(f"/notes/{note.id}/export/pdf/").status_code, 503)
            self.assertEqual(self.client.get(f"/notes/{note.id}/export/markdown/").status_code, 503)
            response = self.client.post("/notes/api/create/", data="{}", content_type="application/json")
            self.assertEqual(response.status_code, 503)
            self.assertFalse(response.json()["ok"])
            self.assertEqual(self.client.get(f"/notes/api/{note.id}/").status_code, 503)
            self.assertEqual(
                self.client.post(
                    f"/notes/api/{note.id}/actions/", data="{}", content_type="application/json"
                ).status_code,
                503,
            )
            self.assertEqual(
                self.client.post(
                    "/notes/api/bulk-action/",
                    data=json.dumps({"note_ids": [note.id], "action": "pin"}),
                    content_type="application/json",
                ).status_code,
                503,
            )
            self.assertEqual(
                self.client.post(
                    "/notes/api/templates/",
                    data=json.dumps({"note_id": note.id, "name": "Vorlage"}),
                    content_type="application/json",
                ).status_code,
                503,
            )
            self.assertEqual(self.client.get(f"/notes/api/{note.id}/shares/").status_code, 503)
            self.assertEqual(
                self.client.delete(f"/notes/api/{note.id}/shares/{self.reader.id}/").status_code, 503
            )
            self.assertEqual(self.client.get("/notes/api/share-candidates/?q=re").status_code, 503)
            self.assertEqual(
                self.client.post(
                    f"/notes/api/{note.id}/attachments/",
                    {"kind": "image", "file": SimpleUploadedFile("moon2.png", PNG_1X1_BYTES, content_type="image/png")},
                ).status_code,
                503,
            )
            self.assertEqual(self.client.get(f"/notes/attachments/{attachment.file_id}/inline/").status_code, 503)
            self.assertEqual(self.client.get(f"/notes/api/{note.id}/versions/").status_code, 503)
            self.assertEqual(
                self.client.post(
                    f"/notes/api/{note.id}/versions/{version.id}/restore/",
                    data=json.dumps({"base_revision": note.revision}),
                    content_type="application/json",
                ).status_code,
                503,
            )
            self.assertEqual(self.client.get("/notes/api/shortcuts/").status_code, 503)
            self.assertEqual(
                self.client.patch(
                    "/notes/api/shortcuts/",
                    data=json.dumps({"shortcuts": {}}),
                    content_type="application/json",
                ).status_code,
                503,
            )
            self.assertEqual(self.client.get(f"/notes/api/{note.id}/mention-candidates/?q=re").status_code, 503)
            self.assertEqual(self.client.get(f"/notes/api/{note.id}/comments/").status_code, 503)
            self.assertEqual(
                self.client.post(
                    f"/notes/api/{note.id}/comments/",
                    data=json.dumps({"thread_id": str(uuid.uuid4()), "body": "Hallo"}),
                    content_type="application/json",
                ).status_code,
                503,
            )
            self.assertEqual(
                self.client.post(
                    f"/notes/api/{note.id}/comments/{uuid.uuid4()}/",
                    data=json.dumps({"action": "reply", "body": "Hallo"}),
                    content_type="application/json",
                ).status_code,
                503,
            )

    def test_code_block_language_is_validated(self):
        note = self.create_note()
        document = {
            "type": "doc",
            "content": [
                {
                    "type": "codeBlock",
                    "attrs": {"language": "python"},
                    "content": [{"type": "text", "text": "print('hi')"}],
                }
            ],
        }
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

        note.refresh_from_db()
        document["content"][0]["attrs"]["language"] = "cobol"
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_math_nodes_are_validated_and_rendered_in_exports(self):
        note = self.create_note()
        document = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Satz des Pythagoras: "},
                        {"type": "mathInline", "attrs": {"latex": "a^2 + b^2 = c^2"}},
                    ],
                },
                {"type": "mathBlock", "attrs": {"latex": "\\frac{1}{2} g t^2"}},
            ],
        }
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        note.refresh_from_db()

        markdown_response = self.client.get(f"/notes/{note.id}/export/markdown/")
        self.assertEqual(markdown_response.status_code, 200)
        markdown_text = b"".join(markdown_response.streaming_content).decode("utf-8")
        markdown_response.close()
        self.assertIn("$a^2 + b^2 = c^2$", markdown_text)
        self.assertIn("$$\\frac{1}{2} g t^2$$", markdown_text)

        pdf_response = self.client.get(f"/notes/{note.id}/export/pdf/")
        self.assertEqual(pdf_response.status_code, 200)

        document["content"][1]["attrs"]["latex"] = ""
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

        note.refresh_from_db()
        document["content"][1]["attrs"]["latex"] = "x" * 4001
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_superscript_and_subscript_marks_are_validated_and_rendered_in_exports(self):
        note = self.create_note()
        document = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "x"},
                        {"type": "text", "text": "2", "marks": [{"type": "superscript"}]},
                        {"type": "text", "text": " und H"},
                        {"type": "text", "text": "2", "marks": [{"type": "subscript"}]},
                        {"type": "text", "text": "O"},
                    ],
                },
            ],
        }
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        note.refresh_from_db()

        markdown_response = self.client.get(f"/notes/{note.id}/export/markdown/")
        self.assertEqual(markdown_response.status_code, 200)
        markdown_text = b"".join(markdown_response.streaming_content).decode("utf-8")
        markdown_response.close()
        self.assertIn("<sup>2</sup>", markdown_text)
        self.assertIn("<sub>2</sub>", markdown_text)

        pdf_response = self.client.get(f"/notes/{note.id}/export/pdf/")
        self.assertEqual(pdf_response.status_code, 200)

        document["content"][0]["content"][1]["marks"] = [{"type": "superscript", "attrs": {"bogus": True}}]
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def mention_document(self, user, label, text="Hallo "):
        return {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": text},
                        {"type": "mention", "attrs": {"userId": user.id, "label": label}},
                    ],
                }
            ],
        }

    def test_mentioning_user_with_access_is_allowed_and_notifies(self):
        note = self.create_note()
        NoteShare.objects.create(note=note, user=self.reader, role=NoteShare.ROLE_READER)
        document = self.mention_document(self.reader, "Reader")

        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

        note.refresh_from_db()
        self.assertIn("@Reader", note.plain_text)
        notification = NoteActivityNotification.objects.get(note=note, recipient=self.reader)
        self.assertEqual(notification.kind, NoteActivityNotification.KIND_MENTION)
        self.assertEqual(notification.actor, self.owner)

        # Saving again without a new mention must not create a duplicate notification.
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(NoteActivityNotification.objects.filter(note=note, recipient=self.reader).count(), 1)

    def test_mentioning_user_without_access_is_rejected(self):
        note = self.create_note()
        outsider = User.objects.create_user(username="outsider@example.com", email="outsider@example.com", password="secret-12345")
        document = self.mention_document(outsider, "Outsider")

        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(NoteActivityNotification.objects.filter(note=note).exists())

    def test_mention_candidates_only_include_users_with_access(self):
        note = self.create_note()
        NoteShare.objects.create(note=note, user=self.reader, role=NoteShare.ROLE_READER)

        response = self.client.get(f"/notes/api/{note.id}/mention-candidates/")
        self.assertEqual(response.status_code, 200)
        names = {user["name"] for user in response.json()["users"]}
        self.assertEqual(names, {"Reader"})

    def test_comment_thread_full_lifecycle(self):
        note = self.create_note()
        NoteShare.objects.create(note=note, user=self.reader, role=NoteShare.ROLE_READER)
        thread_id = str(uuid.uuid4())

        response = self.client.post(
            f"/notes/api/{note.id}/comments/",
            data=json.dumps({"thread_id": thread_id, "anchor_text": "Erster Inhalt", "body": "Was meinst du hiermit?"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        threads = response.json()["threads"]
        self.assertEqual(len(threads), 1)
        self.assertEqual(threads[0]["comments"][0]["body"], "Was meinst du hiermit?")

        notification = NoteActivityNotification.objects.get(note=note, recipient=self.reader)
        self.assertEqual(notification.kind, NoteActivityNotification.KIND_COMMENT)

        document = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Erster Inhalt", "marks": [{"type": "commentThread", "attrs": {"threadId": thread_id}}]}],
                }
            ],
        }
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

        self.client.logout()
        self.client.login(username="reader@example.com", password="secret-12345")
        response = self.client.post(
            f"/notes/api/{note.id}/comments/{thread_id}/",
            data=json.dumps({"action": "reply", "body": "Nur ein Hinweis."}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.json()["threads"][0]["comments"]), 2)
        self.assertTrue(NoteActivityNotification.objects.filter(note=note, recipient=self.owner, kind=NoteActivityNotification.KIND_COMMENT).exists())

        response = self.client.post(
            f"/notes/api/{note.id}/comments/{thread_id}/",
            data=json.dumps({"action": "resolve"}),
            content_type="application/json",
        )
        self.assertTrue(response.json()["threads"][0]["is_resolved"])

        response = self.client.delete(f"/notes/api/{note.id}/comments/{thread_id}/")
        self.assertEqual(response.status_code, 403)

        self.client.logout()
        self.client.login(username="owner@example.com", password="secret-12345")
        response = self.client.delete(f"/notes/api/{note.id}/comments/{thread_id}/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(NoteCommentThread.objects.filter(note=note).exists())

    def test_saving_document_with_unknown_comment_thread_is_rejected(self):
        note = self.create_note()
        document = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Erster Inhalt", "marks": [{"type": "commentThread", "attrs": {"threadId": str(uuid.uuid4())}}]}],
                }
            ],
        }
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_duplicating_note_strips_comment_marks(self):
        note = self.create_note()
        thread_id = str(uuid.uuid4())
        self.client.post(
            f"/notes/api/{note.id}/comments/",
            data=json.dumps({"thread_id": thread_id, "anchor_text": "Erster Inhalt", "body": "Kommentar"}),
            content_type="application/json",
        )
        document = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Erster Inhalt", "marks": [{"type": "commentThread", "attrs": {"threadId": thread_id}}]}],
                }
            ],
        }
        self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )

        response = self.client.post(
            f"/notes/api/{note.id}/actions/",
            data=json.dumps({"action": "duplicate"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        duplicate = Note.objects.get(pk=response.json()["note"]["id"])
        self.assertNotIn("commentThread", json.dumps(duplicate.document))

    def note_link_document(self, target_note, label, text="Siehe "):
        return {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": text},
                        {"type": "noteLink", "attrs": {"noteId": target_note.id, "label": label}},
                    ],
                }
            ],
        }

    def test_linking_to_accessible_note_creates_notelink_and_plain_text(self):
        source = self.create_note(title="Notiz A")
        target = self.create_note(title="Notiz B")
        document = self.note_link_document(target, "Notiz B")

        response = self.client.patch(
            f"/notes/api/{source.id}/",
            data=json.dumps({"title": source.title, "document": document, "base_revision": source.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

        source.refresh_from_db()
        self.assertIn("[[Notiz B]]", source.plain_text)
        self.assertTrue(NoteLink.objects.filter(source_note=source, target_note=target).exists())

        # Saving again without the link removes the stale NoteLink row.
        response = self.client.patch(
            f"/notes/api/{source.id}/",
            data=json.dumps(
                {"title": source.title, "document": note_document("Kein Link mehr"), "base_revision": source.revision}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(NoteLink.objects.filter(source_note=source, target_note=target).exists())

    def test_linking_to_inaccessible_note_is_rejected(self):
        source = self.create_note(title="Notiz A")
        editor_client = Client()
        editor_client.login(username="editor@example.com", password="secret-12345")
        outsider_note = Note.objects.create(owner=self.editor, title="Fremde Notiz")
        document = self.note_link_document(outsider_note, "Fremde Notiz")

        response = self.client.patch(
            f"/notes/api/{source.id}/",
            data=json.dumps({"title": source.title, "document": document, "base_revision": source.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(NoteLink.objects.filter(source_note=source).exists())

    def test_backlinks_are_filtered_by_viewer_access(self):
        note_a = self.create_note(title="Privat A")
        note_b = self.create_note(title="Geteilt B")
        NoteShare.objects.create(note=note_b, user=self.reader, role=NoteShare.ROLE_READER)
        document = self.note_link_document(note_b, "Geteilt B")
        response = self.client.patch(
            f"/notes/api/{note_a.id}/",
            data=json.dumps({"title": note_a.title, "document": document, "base_revision": note_a.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

        reader_client = Client()
        reader_client.login(username="reader@example.com", password="secret-12345")
        response = reader_client.get(f"/notes/api/{note_b.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["note"]["backlinks"], [])

        NoteShare.objects.create(note=note_a, user=self.reader, role=NoteShare.ROLE_READER)
        response = reader_client.get(f"/notes/api/{note_b.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["note"]["backlinks"], [{"id": note_a.id, "title": "Privat A"}])

    def test_link_candidates_only_include_accessible_notes(self):
        source = self.create_note(title="Notiz A")
        own_other = self.create_note(title="Notiz B")
        outsider_note = Note.objects.create(owner=self.editor, title="Fremde Notiz")

        response = self.client.get(f"/notes/api/{source.id}/link-candidates/")
        self.assertEqual(response.status_code, 200)
        candidates = response.json()["notes"]
        self.assertEqual({item["title"] for item in candidates}, {"Notiz B"})
        ids = {item["id"] for item in candidates}
        self.assertIn(own_other.id, ids)
        self.assertNotIn(source.id, ids)
        self.assertNotIn(outsider_note.id, ids)

    def test_duplicating_note_carries_forward_note_links(self):
        source = self.create_note(title="Notiz A")
        target = self.create_note(title="Notiz B")
        document = self.note_link_document(target, "Notiz B")
        self.client.patch(
            f"/notes/api/{source.id}/",
            data=json.dumps({"title": source.title, "document": document, "base_revision": source.revision}),
            content_type="application/json",
        )

        response = self.client.post(
            f"/notes/api/{source.id}/actions/",
            data=json.dumps({"action": "duplicate"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        duplicate = Note.objects.get(pk=response.json()["note"]["id"])
        self.assertTrue(NoteLink.objects.filter(source_note=duplicate, target_note=target).exists())

    def test_restoring_version_resyncs_note_links(self):
        source = self.create_note(title="Notiz A")
        target = self.create_note(title="Notiz B")
        document = self.note_link_document(target, "Notiz B")
        self.client.patch(
            f"/notes/api/{source.id}/",
            data=json.dumps({"title": source.title, "document": document, "base_revision": source.revision}),
            content_type="application/json",
        )
        source.refresh_from_db()
        self.assertTrue(NoteLink.objects.filter(source_note=source, target_note=target).exists())

        version = NoteVersion.objects.get(note=source)
        response = self.client.post(
            f"/notes/api/{source.id}/versions/{version.id}/restore/",
            data=json.dumps({"base_revision": source.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(NoteLink.objects.filter(source_note=source, target_note=target).exists())


class VacationPlannerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="planner@example.com", email="planner@example.com", password="secret-12345")
        Profile.objects.create(user=self.user, display_name="Planner")
        self.other = User.objects.create_user(username="other-planner@example.com", email="other-planner@example.com", password="secret-12345")
        Profile.objects.create(user=self.other, display_name="Other")
        self.client.login(username="planner@example.com", password="secret-12345")
        self.vacation_year = VacationYear.objects.create(
            user=self.user,
            year=2026,
            allowance_days=Decimal("3.0"),
            subdivision="NW",
        )

    def test_half_holiday_reduces_required_vacation_days(self):
        CustomHoliday.objects.create(
            vacation_year=self.vacation_year,
            date=date(2026, 1, 6),
            name="Halber Testfeiertag",
            day_value=Decimal("0.5"),
        )

        calculation = calculate_period(self.user, date(2026, 1, 5), date(2026, 1, 7))

        self.assertEqual(calculation["calendar_days"], 3)
        self.assertEqual(calculation["weekend_days"], 0)
        self.assertEqual(calculation["holiday_count"], 1)
        self.assertEqual(calculation["required_days"], Decimal("2.5"))

    def test_annual_summary_counts_overlapping_vacation_dates_once(self):
        CustomHoliday.objects.create(
            vacation_year=self.vacation_year,
            date=date(2026, 1, 6),
            name="Halber Testfeiertag",
            day_value=Decimal("0.5"),
        )
        VacationPeriod.objects.create(user=self.user, name=VacationPeriod.TARIFURLAUB, start_date=date(2026, 1, 5), end_date=date(2026, 1, 7))
        VacationPeriod.objects.create(user=self.user, name=VacationPeriod.SONDERURLAUB, start_date=date(2026, 1, 6), end_date=date(2026, 1, 8))

        summary = annual_summary(self.user, 2026)

        self.assertEqual(summary["planned_days"], Decimal("3.5"))
        self.assertEqual(summary["remaining_days"], Decimal("-0.5"))
        self.assertTrue(summary["is_overbooked"])

    def test_preview_reports_overlaps_and_missing_years(self):
        VacationPeriod.objects.create(user=self.user, name=VacationPeriod.TARIFURLAUB, start_date=date(2026, 1, 6), end_date=date(2026, 1, 8))

        response = self.client.post(
            "/vacation-planner/preview/",
            data=json.dumps({"start_date": "2026-01-05", "end_date": "2027-01-03"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["missing_years"], [2027])
        self.assertEqual(data["overlaps"][0]["name"], "Tarifurlaub")

    def test_page_requires_login_and_feature_flag(self):
        self.client.logout()
        response = self.client.get("/vacation-planner/")
        self.assertEqual(response.status_code, 302)

        self.client.login(username="planner@example.com", password="secret-12345")
        SystemSettings.objects.create(vacation_planner_enabled=False)
        response = self.client.get("/vacation-planner/?year=2026")
        self.assertEqual(response.status_code, 503)

    def test_periods_are_filtered_per_user(self):
        VacationPeriod.objects.create(user=self.user, name=VacationPeriod.TARIFURLAUB, start_date=date(2026, 2, 2), end_date=date(2026, 2, 3), notes="Eigener Hinweis")
        VacationPeriod.objects.create(user=self.other, name=VacationPeriod.TARIFURLAUB, start_date=date(2026, 2, 2), end_date=date(2026, 2, 3), notes="Fremder Hinweis")

        response = self.client.get("/vacation-planner/?year=2026&month=2")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertContains(response, "Eigener Hinweis")
        self.assertNotContains(response, "Fremder Hinweis")

    def test_public_holiday_import_command_is_idempotent(self):
        stale = OfficialHoliday.objects.create(
            subdivision="NW",
            date=date(2026, 6, 1),
            name="Alter Feiertag",
            day_value=Decimal("1.0"),
            active=True,
        )
        output = StringIO()
        call_command("import_public_holidays", from_year=2026, to_year=2026, subdivision="NW", stdout=output)
        first_count = OfficialHoliday.objects.filter(subdivision="NW", date__year=2026).count()
        call_command("import_public_holidays", from_year=2026, to_year=2026, subdivision="NW", stdout=StringIO())

        stale.refresh_from_db()
        self.assertGreater(first_count, 0)
        self.assertFalse(stale.active)
        self.assertEqual(OfficialHoliday.objects.filter(subdivision="NW", date__year=2026).count(), first_count)
