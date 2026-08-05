import json
from datetime import datetime, timedelta
from email.message import Message
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from app.forms import CalendarSourceForm, ProfileForm
from app.models import CalendarEvent, CalendarReminder, CalendarSource, ChatMessage, ChatMessageReaction, Conversation, ConversationMember, Note, NoteAttachment, NoteShare, NoteUserState, NoteVersion, Profile, SystemSettings
from app.services.calendar_service import fetch_ical, parse_ical_events, sync_calendar_sources
from app.services.image_uploads import PROFILE_IMAGE_MAX_BYTES
from app.services.weather_service import (
    WEATHER_MAP_LAYERS,
    fetch_weather_map_tile,
    get_location_suggestions,
    get_weather_at_coordinates,
    get_weather_context,
)
from app.services.notes import prune_note_versions, purge_expired_notes
from app.views.message_views import _build_inbox_items


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
        self.assertFalse(user.profile.analytics_enabled)
        self.assertTrue(user.profile.usage_data_enabled)
        self.assertEqual(user.profile.weather_default_city, "Berlin,de")

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

    def test_calendar_source_can_be_added_from_settings(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        with patch("app.views.core_views.sync_calendar_source", return_value={"synced": True, "message": "1 Termine synchronisiert."}) as sync_source:
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
        sync_source.assert_called_once_with(source, force=True)

    def test_calendar_source_failed_first_sync_is_kept(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        with patch("app.views.core_views.sync_calendar_source", return_value={"synced": False, "message": "Link nicht erreichbar."}):
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
        self.assertTrue(CalendarSource.objects.filter(user=user, name="Familie").exists())

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

        with patch("app.views.core_views.sync_calendar_source", return_value={"synced": True, "message": "1 Termine synchronisiert."}):
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

        with patch("app.views.core_views.sync_calendar_source", return_value={"synced": True, "message": "1 Termine synchronisiert."}) as sync_source:
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
        sync_source.assert_called_once_with(source, force=True)

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

        with patch("app.views.calendar_views.sync_calendar_sources") as sync_calendar:
            response = self.client.get("/calendar/")

        self.assertEqual(response.status_code, 200)
        sync_calendar.assert_not_called()

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

    def test_calendar_sync_result_is_visible(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        CalendarSource.objects.create(
            user=user,
            ical_url="https://calendar.google.com/calendar/ical/example/private/basic.ics",
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        with patch("app.views.calendar_views.sync_calendar_sources", return_value={"synced": True, "message": "2 Kalender synchronisiert."}):
            response = self.client.post(
                "/calendar/",
                {"form_name": "calendar_sync_all"},
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2 Kalender synchronisiert.")

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

    def test_sync_all_processes_hidden_sources_and_skips_disabled_sources(self):
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

        with patch("app.services.calendar_service.sync_calendar_source", return_value={"synced": True, "message": "1 Termine synchronisiert."}) as sync_source:
            result = sync_calendar_sources([hidden_source, disabled_source], force=True)

        self.assertTrue(result["synced"])
        sync_source.assert_called_once_with(hidden_source, force=True)

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

    def test_overview_live_updates_return_contact_list(self):
        user = User.objects.create_user(username="overview-live@example.com", email="overview-live@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Overview Live")

        self.client.login(username="overview-live@example.com", password="secret-12345")
        response = self.client.get("/messages/live/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertIn("contact_list_html", response.json())
        self.assertIn("overview_html", response.json())


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

    def create_note(self, title="Projektidee"):
        response = self.client.post(
            "/notes/api/create/",
            data=json.dumps({"title": title}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return Note.objects.get(pk=response.json()["note"]["id"])

    def save_note(self, note, *, text="Erster Inhalt", tags=None, revision=None, client=None):
        return (client or self.client).patch(
            f"/notes/api/{note.id}/",
            data=json.dumps(
                {
                    "title": note.title,
                    "document": note_document(text),
                    "tags": tags or [],
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
        self.assertContains(response, "data-note-card-preview")
        self.assertContains(response, "data-note-card-updated")
        self.assertContains(response, "data-note-card-tags")

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

    def test_note_save_derives_plain_text_tags_and_search(self):
        note = self.create_note()
        response = self.save_note(note, text="Mondlicht Planung", tags=["Projekt", "projekt", "Wichtig"])
        self.assertEqual(response.status_code, 200, response.content)
        saved_note = response.json()["note"]
        self.assertEqual(saved_note["preview"], "Mondlicht Planung")
        self.assertEqual(saved_note["tags"], ["Projekt", "Wichtig"])
        self.assertTrue(saved_note["updated_at"])
        note.refresh_from_db()
        self.assertEqual(note.plain_text, "Mondlicht Planung")
        self.assertEqual(note.revision, 2)
        self.assertEqual(list(note.tags.values_list("normalized_name", flat=True)), ["projekt", "wichtig"])
        response = self.client.get("/notes/?q=Mondlicht")
        self.assertContains(response, "Projektidee")
        response = self.client.get("/notes/?tag=projekt")
        self.assertContains(response, "Projektidee")

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
            data=json.dumps({"title": note.title, "document": table_document, "tags": [], "base_revision": 1}),
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
            data=json.dumps({"title": note.title, "document": unsafe, "tags": [], "base_revision": note.revision}),
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
            data=json.dumps({"title": note.title, "document": linked_document, "tags": [], "base_revision": 1}),
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
        self.assertGreater(len(pdf_bytes), 2000)

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
        settings_obj = SystemSettings.objects.create(notes_enabled=False)
        self.assertEqual(self.client.get("/notes/").status_code, 503)
        response = self.client.post("/notes/api/create/", data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["ok"])
