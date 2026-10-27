import json
import re
import zipfile
from datetime import datetime, time, timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from app.forms import ProfileForm
from app.models import (
    CalendarEvent,
    CalendarEventAttendee,
    CalendarReminder,
    ChatMessage,
    Conversation,
    ConversationMember,
    Note,
    NoteAttachment,
    NotificationPreference,
    Profile,
    SystemSettings,
    Task,
    UmlDiagram,
    UserNotification,
    VocabularyCard,
    VocabularyList,
    timezone_choices,
)
from app.services.image_uploads import PROFILE_IMAGE_MAX_BYTES
from app.templatetags.static_versioning import versioned_static
from app.tests.helpers import (
    PNG_1X1_BYTES,
)


class SettingsProfileTests(TestCase):
    def test_base_template_self_hosts_fonts_without_blocking_icon_css(self):
        response = self.client.get("/login/")
        content = response.content.decode()
        icon_stylesheet = versioned_static("vendor/fontawesome/css/all.min.css")

        self.assertNotIn("fonts.googleapis.com", content)
        self.assertNotIn("cdn.jsdelivr.net", content)
        self.assertIn("/static/vendor/inter/inter-latin-variable.woff2", content)
        self.assertIn(
            f'<link rel="stylesheet" href="{icon_stylesheet}" media="print" onload="this.media=\'all\'">',
            content,
        )
        self.assertNotIn(f'rel="preload" href="{icon_stylesheet}"', content)
        self.assertIn(f'<noscript><link rel="stylesheet" href="{icon_stylesheet}">', content)

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
        User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )

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
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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

        self.assertEqual(response["Location"], "/settings/?next=%2Fhome%2F#settings-profile")
        user.refresh_from_db()
        self.assertEqual(user.profile.display_name, "Mira Neu")
        self.assertEqual(user.first_name, "Mira Neu")

    def test_profile_form_accepts_valid_profile_image(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        profile = Profile.objects.create(user=user, display_name="Mira")
        upload = SimpleUploadedFile("avatar.png", PNG_1X1_BYTES, content_type="image/png")

        form = ProfileForm(data={"display_name": "Mira"}, files={"profile_image": upload}, instance=profile)

        self.assertTrue(form.is_valid(), form.errors)

    def test_profile_form_rejects_spoofed_profile_image(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        profile = Profile.objects.create(user=user, display_name="Mira")
        upload = SimpleUploadedFile("avatar.png", b"not really an image", content_type="image/png")

        form = ProfileForm(data={"display_name": "Mira"}, files={"profile_image": upload}, instance=profile)

        self.assertFalse(form.is_valid())
        self.assertIn("profile_image", form.errors)

    def test_profile_form_rejects_oversized_profile_image(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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
            form = ProfileForm(
                data={"display_name": "Mira"}, files={"profile_image": first_upload}, instance=profile
            )
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
            form = ProfileForm(
                data={"display_name": "Mira"}, files={"profile_image": upload}, instance=profile
            )
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
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(
            user=user,
            display_name="Mira",
            date_format="iso",
            time_format="12h",
            timezone_name="UTC",
        )
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

        self.assertEqual(response["Location"], "/settings/?next=%2Fhome%2F#settings-appearance")
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.theme, "dark")
        self.assertEqual(user.profile.accent_color, "#7f916b")
        self.assertEqual(user.profile.background_softness, 82)
        self.assertEqual(user.profile.density, "compact")
        self.assertEqual(user.profile.date_format, "iso")
        self.assertEqual(user.profile.time_format, "12h")
        self.assertEqual(user.profile.timezone_name, "UTC")

    def test_logged_in_user_can_save_region_settings(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(
            user=user,
            display_name="Mira",
            theme="dark",
            accent_color="#7f916b",
            background_softness=82,
            density="compact",
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/settings/",
            {
                "form_name": "region",
                "date_format": "iso",
                "time_format": "12h",
                "timezone_name": "UTC",
            },
        )

        self.assertEqual(response["Location"], "/settings/?next=%2Fhome%2F#settings-profile")
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.date_format, "iso")
        self.assertEqual(user.profile.time_format, "12h")
        self.assertEqual(user.profile.timezone_name, "UTC")
        self.assertEqual(user.profile.theme, "dark")
        self.assertEqual(user.profile.accent_color, "#7f916b")
        self.assertEqual(user.profile.background_softness, 82)
        self.assertEqual(user.profile.density, "compact")

    def test_logged_in_user_can_save_notification_preferences_without_changing_weather(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira", weather_default_city="Hamburg,de")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/settings/",
            {
                "form_name": "notifications",
                "notify_reminders": "on",
                "weekly_summary": "on",
                "usage_data_enabled": "on",
            },
        )

        self.assertEqual(response["Location"], "/settings/?next=%2Fhome%2F#settings-notifications")
        user.profile.refresh_from_db()
        self.assertFalse(user.profile.notify_email)
        self.assertTrue(user.profile.notify_reminders)
        self.assertFalse(user.profile.notify_desktop)
        self.assertTrue(user.profile.weekly_summary)
        self.assertTrue(user.profile.analytics_enabled)
        self.assertFalse(user.profile.usage_data_enabled)
        self.assertEqual(user.profile.weather_default_city, "Hamburg,de")

    def test_logged_in_user_can_save_weather_without_changing_notifications(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(
            user=user,
            display_name="Mira",
            notify_email=True,
            notify_reminders=False,
            notify_desktop=True,
            weekly_summary=True,
        )
        NotificationPreference.objects.create(
            user=user,
            category="calendar",
            inbox_enabled=False,
            email_enabled=False,
            web_push_enabled=True,
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/settings/",
            {
                "form_name": "weather",
                "weather_default_city": "  Berlin,de  ",
            },
        )

        self.assertEqual(response["Location"], "/settings/?next=%2Fhome%2F#settings-weather")
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.weather_default_city, "Berlin,de")
        self.assertTrue(user.profile.notify_email)
        self.assertFalse(user.profile.notify_reminders)
        self.assertTrue(user.profile.notify_desktop)
        self.assertTrue(user.profile.weekly_summary)
        category = NotificationPreference.objects.get(user=user, category="calendar")
        self.assertFalse(category.inbox_enabled)
        self.assertFalse(category.email_enabled)
        self.assertTrue(category.web_push_enabled)

    def test_invalid_appearance_settings_render_without_changing_profile(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        profile = Profile.objects.create(user=user, display_name="Mira", background_softness=55)
        self.client.force_login(user)

        response = self.client.post(
            "/settings/",
            {
                "form_name": "appearance",
                "theme": "dark",
                "accent_color": "#7f916b",
                "background_softness": "101",
                "density": "compact",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("background_softness", response.context["appearance_form"].errors)
        profile.refresh_from_db()
        self.assertEqual(profile.background_softness, 55)

    def test_invalid_region_settings_render_without_changing_profile(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        profile = Profile.objects.create(user=user, display_name="Mira", timezone_name="Europe/Berlin")
        self.client.force_login(user)

        response = self.client.post(
            "/settings/",
            {
                "form_name": "region",
                "date_format": "iso",
                "time_format": "12h",
                "timezone_name": "Mars/Olympus",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("timezone_name", response.context["region_form"].errors)
        profile.refresh_from_db()
        self.assertEqual(profile.timezone_name, "Europe/Berlin")

    def test_timezone_choices_are_offered_but_kept_out_of_migrations(self):
        # The zone list differs between platforms, so migrations must only reference the callable.
        field = Profile._meta.get_field("timezone_name")
        self.assertIs(field.deconstruct()[3]["choices"], timezone_choices)

        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira", timezone_name="America/New_York")
        self.client.force_login(user)

        response = self.client.get("/settings/")

        self.assertContains(
            response, '<option value="America/New_York" selected>America/New_York</option>', html=True
        )
        self.assertContains(response, '<option value="Europe/Berlin">Europe/Berlin</option>', html=True)

    def test_invalid_weather_settings_render_without_changing_profile(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        profile = Profile.objects.create(user=user, display_name="Mira", weather_default_city="Bünde,de")
        self.client.force_login(user)

        response = self.client.post(
            "/settings/",
            {
                "form_name": "weather",
                "weather_default_city": "x" * 121,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("weather_default_city", response.context["weather_form"].errors)
        profile.refresh_from_db()
        self.assertEqual(profile.weather_default_city, "Bünde,de")

    def test_settings_hide_unimplemented_analytics_controls(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/settings/")

        self.assertNotContains(response, 'name="analytics_enabled"')
        self.assertNotContains(response, 'name="usage_data_enabled"')
        self.assertContains(response, "Erinnerungszustellung")

    def test_settings_show_detailed_notification_controls(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira")
        self.client.force_login(user)

        response = self.client.get("/settings/")

        self.assertContains(response, 'aria-label="Zustellung nach Kategorie"')
        self.assertContains(response, 'name="notification_calendar_inbox"')
        self.assertContains(response, 'name="notification_weather_web_push"')
        self.assertContains(response, "Web-Push-Ruhezeit")
        self.assertContains(response, "Test senden")

    def test_settings_are_grouped_into_accessible_sections(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira")
        self.client.force_login(user)

        response = self.client.get("/settings/")

        self.assertContains(response, 'role="tablist"')
        self.assertContains(response, "data-settings-tab=", count=7)
        self.assertContains(response, 'data-settings-panel="appearance"')
        self.assertContains(response, 'data-settings-panel="profile"', count=2)
        self.assertContains(response, 'data-settings-panel="calendar"')
        self.assertContains(response, 'data-settings-panel="notifications"')
        self.assertContains(response, 'data-settings-panel="weather"')
        self.assertContains(response, 'data-settings-panel="app"')
        self.assertContains(response, 'data-settings-panel="security"', count=2)
        self.assertContains(response, "Sprache &amp; Region speichern")
        self.assertContains(response, 'name="form_name" value="calendar_feed_reset"')
        self.assertContains(response, 'name="form_name" value="appearance"')
        self.assertContains(response, 'name="form_name" value="profile"')
        self.assertContains(response, 'name="form_name" value="region"')
        self.assertContains(response, 'name="form_name" value="notifications"')
        self.assertContains(response, 'name="form_name" value="weather"')
        self.assertContains(response, 'class="settings-back-link" href="/home/"')
        self.assertNotContains(response, ">Abbrechen<")

    def test_settings_reject_unsafe_or_authentication_return_targets(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira")
        self.client.force_login(user)
        unsafe_targets = [
            "https://evil.example/steal",
            "/settings/",
            "/login/?next=/settings/",
            "/accounts/login/",
            "/logout/",
            "/register/",
            "/password-reset/",
            "/password-reset/done/",
            "/reset/example/token/",
            "/reset/done/",
        ]

        for target in unsafe_targets:
            with self.subTest(target=target):
                response = self.client.get("/settings/", {"next": target})

                self.assertEqual(response.context["return_to"], "/home/")
                self.assertContains(response, 'class="settings-back-link" href="/home/"')

    def test_settings_preserve_valid_return_target_across_save_redirect(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira")
        self.client.force_login(user)
        return_to = "/calendar/?view=month#week"

        response = self.client.get("/settings/", {"next": return_to})

        self.assertEqual(response.context["return_to"], return_to)
        self.assertContains(response, 'class="settings-back-link" href="/calendar/?view=month#week"')

        response = self.client.post(
            "/settings/",
            {
                "form_name": "weather",
                "return_to": return_to,
                "weather_default_city": "Berlin,de",
            },
        )

        self.assertEqual(
            response["Location"],
            "/settings/?next=%2Fcalendar%2F%3Fview%3Dmonth%23week#settings-weather",
        )

    def test_settings_ignore_login_referrer_for_back_link(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira")
        self.client.force_login(user)

        response = self.client.get(
            "/settings/",
            HTTP_REFERER="http://testserver/login/?next=/settings/",
        )

        self.assertEqual(response.context["return_to"], "/home/")

    def test_logged_in_user_can_save_detailed_notification_preferences(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira")
        self.client.force_login(user)

        response = self.client.post(
            "/settings/",
            {
                "form_name": "notifications",
                "notification_matrix_present": "1",
                "notify_email": "on",
                "notify_reminders": "on",
                "notify_desktop": "on",
                "notification_quiet_hours_enabled": "on",
                "notification_quiet_start": "21:30",
                "notification_quiet_end": "06:45",
                "notification_calendar_inbox": "on",
                "notification_calendar_email": "on",
                "notification_tasks_web_push": "on",
                "notification_notes_inbox": "on",
                "notification_notes_email": "on",
                "notification_notes_web_push": "on",
                "notification_weather_email": "on",
            },
        )

        self.assertEqual(response["Location"], "/settings/?next=%2Fhome%2F#settings-notifications")
        user.profile.refresh_from_db()
        self.assertTrue(user.profile.notification_quiet_hours_enabled)
        self.assertEqual(user.profile.notification_quiet_start, time(21, 30))
        self.assertEqual(user.profile.notification_quiet_end, time(6, 45))
        calendar = NotificationPreference.objects.get(user=user, category="calendar")
        tasks = NotificationPreference.objects.get(user=user, category="tasks")
        weather = NotificationPreference.objects.get(user=user, category="weather")
        self.assertTrue(calendar.inbox_enabled)
        self.assertTrue(calendar.email_enabled)
        self.assertFalse(calendar.web_push_enabled)
        self.assertFalse(tasks.inbox_enabled)
        self.assertFalse(tasks.email_enabled)
        self.assertTrue(tasks.web_push_enabled)
        self.assertFalse(weather.inbox_enabled)
        self.assertTrue(weather.email_enabled)
        self.assertFalse(weather.web_push_enabled)

    def test_equal_quiet_hour_boundaries_are_rejected(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira")
        self.client.force_login(user)

        response = self.client.post(
            "/settings/",
            {
                "form_name": "notifications",
                "notification_matrix_present": "1",
                "notification_quiet_hours_enabled": "on",
                "notification_quiet_start": "22:00",
                "notification_quiet_end": "22:00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Beginn und Ende der Ruhezeit müssen unterschiedlich sein.")
        user.profile.refresh_from_db()
        self.assertFalse(user.profile.notification_quiet_hours_enabled)
        self.assertFalse(NotificationPreference.objects.filter(user=user).exists())

    def test_settings_save_shows_feedback_message(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/settings/",
            {
                "form_name": "notifications",
                "notify_email": "on",
                "notify_reminders": "on",
                "notify_desktop": "on",
                "analytics_enabled": "on",
            },
            follow=True,
        )

        self.assertContains(response, "Benachrichtigungseinstellungen gespeichert.")

    def test_calendar_feed_link_can_be_created_rotated_and_disabled(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        profile = Profile.objects.create(user=user, display_name="Mira")
        self.client.force_login(user)

        response = self.client.get("/settings/")
        self.assertContains(response, 'name="form_name" value="calendar_feed_reset"')
        self.assertContains(response, "Abo-Link erstellen")

        response = self.client.post("/settings/", {"form_name": "calendar_feed_reset"})
        self.assertEqual(response["Location"], "/settings/?next=%2Fhome%2F#settings-calendar")
        profile.refresh_from_db()
        first_token = profile.calendar_feed_token
        self.assertGreaterEqual(len(first_token), 40)
        response = self.client.get("/settings/")
        self.assertContains(response, f"http://testserver/calendar/feed/{first_token}.ics")
        self.assertEqual(self.client.get(f"/calendar/feed/{first_token}.ics").status_code, 200)

        self.client.post("/settings/", {"form_name": "calendar_feed_reset"})
        profile.refresh_from_db()
        self.assertNotEqual(profile.calendar_feed_token, first_token)
        self.assertEqual(self.client.get(f"/calendar/feed/{first_token}.ics").status_code, 404)

        self.client.post("/settings/", {"form_name": "calendar_feed_disable"})
        profile.refresh_from_db()
        self.assertIsNone(profile.calendar_feed_token)

    def test_settings_feed_link_is_scoped_to_logged_in_user(self):
        mira = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        lukas = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        Profile.objects.create(user=mira, display_name="Mira", calendar_feed_token="mira-secret-token")
        Profile.objects.create(user=lukas, display_name="Lukas")
        self.client.force_login(lukas)

        response = self.client.get("/settings/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "mira-secret-token")

    def test_calendar_page_shows_categories_and_no_import_controls(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira")
        self.client.force_login(user)

        response = self.client.get("/calendar/")

        self.assertEqual(response.status_code, 200)
        for label in ("Klassenarbeit", "Test", "Referat", "Abgabe", "Sonstiges"):
            with self.subTest(label=label):
                self.assertContains(response, f"<strong>{label}</strong>")
        self.assertContains(response, "/settings/?next=/calendar/#settings-calendar")
        self.assertNotContains(response, "calendar_sync_all")
        self.assertNotContains(response, "Kalender verbinden")

    def test_calendar_empty_states_offer_direct_actions(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/calendar/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-calendar-empty-action="event-today"')
        self.assertContains(response, 'data-calendar-empty-action="event-upcoming"')

    def test_calendar_page_displays_saved_events(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira")
        start_at = timezone.make_aware(datetime(2026, 7, 8, 9, 0))
        CalendarEvent.objects.create(
            user=user,
            category=CalendarEvent.CATEGORY_EXAM,
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
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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
        self.assertEqual(event.category, CalendarEvent.CATEGORY_OTHER)
        self.assertEqual(timezone.localtime(event.start_at).strftime("%Y-%m-%d %H:%M"), "2099-08-12 10:30")
        self.assertEqual(timezone.localtime(event.end_at).strftime("%Y-%m-%d %H:%M"), "2099-08-12 11:15")
        self.assertEqual(event.location, "Praxis am Markt")

        response = self.client.get(calendar_url)

        self.assertContains(response, "Zahnarzttermin")
        self.assertContains(response, "tone-sand")
        self.assertContains(response, "<small>Sonstiges</small>", html=False)

    def test_calendar_event_with_attendee_creates_invitation_visible_to_invitee(self):
        organizer = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        invitee = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
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
        self.assertTrue(
            UserNotification.objects.filter(
                recipient=invitee,
                source_key=f"event-invitation:{attendee.id}",
                kind=UserNotification.KIND_EVENT_INVITATION,
            ).exists()
        )

        self.client.logout()
        self.client.login(username="lukas@example.com", password="secret-12345")
        response = self.client.get(calendar_url)
        self.assertContains(response, "Projektmeeting")
        self.assertContains(response, "Einladungen")

    def test_invitee_can_accept_and_decline_event_invitation(self):
        organizer = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        invitee = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
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
        organizer = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        invitee = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        outsider = User.objects.create_user(
            username="anna@example.com", email="anna@example.com", password="secret-12345"
        )
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
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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
        owner = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        outsider = User.objects.create_user(
            username="anna@example.com", email="anna@example.com", password="secret-12345"
        )
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

    def test_calendar_event_edit_updates_fields_and_attendees(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        invitee = User.objects.create_user(
            username="anna@example.com", email="anna@example.com", password="secret-12345"
        )
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
                "category": "presentation",
                "attendees": [str(invitee.id)],
            },
        )

        self.assertRedirects(response, "/calendar/")
        event.refresh_from_db()
        self.assertEqual(event.title, "Neuer Titel")
        self.assertEqual(event.category, CalendarEvent.CATEGORY_PRESENTATION)
        self.assertEqual(event.location, "Neues Büro")
        self.assertTrue(CalendarEventAttendee.objects.filter(event=event, user=invitee).exists())

    def test_calendar_event_edit_cannot_target_another_users_event(self):
        owner = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        outsider = User.objects.create_user(
            username="anna@example.com", email="anna@example.com", password="secret-12345"
        )
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

    def test_calendar_event_edit_rejects_an_end_before_its_start(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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

    def test_calendar_context_includes_due_tasks(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira")
        now = timezone.now()
        Task.objects.create(user=user, title="Heute fällig", due_at=now)
        Task.objects.create(user=user, title="Überfällig", due_at=now - timedelta(days=1))
        Task.objects.create(user=user, title="Erledigt", due_at=now, is_done=True)
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/calendar/")

        due_task_titles = {item["title"] for item in response.context["due_tasks"]}
        self.assertEqual(due_task_titles, {"Heute fällig", "Überfällig"})
        self.assertContains(response, "Fällige Aufgaben")

    def test_calendar_due_tasks_hidden_when_tasks_disabled(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira")
        Task.objects.create(user=user, title="Heute fällig", due_at=timezone.now())
        SystemSettings.objects.create(tasks_enabled=False)
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/calendar/")

        self.assertEqual(response.context["due_tasks"], [])
        self.assertNotContains(response, "Fällige Aufgaben")

    def test_home_page_shows_upcoming_calendar_events(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira")
        start_at = timezone.now() + timedelta(days=2)
        CalendarEvent.objects.create(
            user=user,
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
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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


class DataExportTests(TestCase):
    def setUp(self):
        self.lea = User.objects.create_user(username="lea", email="lea@example.com", password="pw-12345!")
        self.tom = User.objects.create_user(username="tom", email="tom@example.com", password="pw-12345!")
        Profile.objects.create(user=self.lea, display_name="Lea", calendar_feed_token="geheimer-token")

    def _download(self):
        response = self.client.get("/settings/export/")
        try:
            content = b"".join(response.streaming_content)
        finally:
            response.close()
        return response, zipfile.ZipFile(BytesIO(content))

    def test_export_requires_login(self):
        self.assertRedirects(self.client.get("/settings/export/"), "/login/?next=/settings/export/")

    def test_export_contains_own_data_and_attachments_only(self):
        note = Note.objects.create(owner=self.lea, title="Mathe", plain_text="Integrale")
        NoteAttachment.objects.create(
            note=note,
            uploaded_by=self.lea,
            file=SimpleUploadedFile("../formel.txt", b"e=mc2"),
            kind=NoteAttachment.KIND_FILE,
            original_name="../formel.txt",
            content_type="text/plain",
            size=5,
        )
        Note.objects.create(owner=self.tom, title="Toms Notiz")
        Task.objects.create(user=self.lea, title="Hausaufgaben")
        vocabulary_list = VocabularyList.objects.create(owner=self.lea, title="Unit 3")
        VocabularyCard.objects.create(vocabulary_list=vocabulary_list, term="house", translation="Haus")
        UmlDiagram.objects.create(owner=self.lea, title="Klassen")
        conversation = Conversation.objects.create(created_by=self.lea)
        ConversationMember.objects.create(conversation=conversation, user=self.lea)
        ConversationMember.objects.create(conversation=conversation, user=self.tom)
        ChatMessage.objects.create(conversation=conversation, sender=self.lea, body="Hallo Tom")
        ChatMessage.objects.create(conversation=conversation, sender=self.tom, body="Antwort von Tom")
        self.client.login(username="lea", password="pw-12345!")

        response, archive = self._download()

        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn("lunora-export-lea-", response["Content-Disposition"])
        self.assertEqual(response["Cache-Control"], "private, no-store")
        names = archive.namelist()
        self.assertIn(f"notizen/{note.pk}-mathe.md", names)
        attachment_path = next(name for name in names if name.startswith("notizen/anhaenge/"))
        self.assertTrue(attachment_path.endswith("-formel.txt"))
        self.assertNotIn("..", attachment_path)
        self.assertEqual(archive.read(attachment_path), b"e=mc2")
        self.assertTrue(any(name.startswith("uml/") for name in names))

        notes = json.loads(archive.read("notizen.json"))
        self.assertEqual([item["title"] for item in notes["notes"]], ["Mathe"])
        self.assertEqual(json.loads(archive.read("aufgaben.json"))["tasks"][0]["title"], "Hausaufgaben")
        self.assertEqual(json.loads(archive.read("vokabeln.json"))[0]["cards"][0]["term"], "house")
        messages = json.loads(archive.read("nachrichten.json"))
        self.assertEqual([item["body"] for item in messages], ["Hallo Tom"])
        account = archive.read("konto.json").decode("utf-8")
        self.assertIn('"display_name": "Lea"', account)
        self.assertNotIn("geheimer-token", account)
