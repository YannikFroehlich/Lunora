import hashlib
import json
import mimetypes
import os
import re
import sys
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from email.message import Message
from io import StringIO
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from app.forms import CalendarSourceForm, ProfileForm
from app.models import (
    CalendarEvent,
    CalendarEventAttendee,
    CalendarReminder,
    CalendarSource,
    ChatMessage,
    ChatMessageAttachment,
    ChatMessageReaction,
    Conversation,
    ConversationMember,
    CustomHoliday,
    HolidayOverride,
    Note,
    NoteActivityNotification,
    NoteAttachment,
    NoteCommentThread,
    NoteFolder,
    NoteLink,
    NoteShare,
    NoteTemplate,
    NoteUserState,
    NoteVersion,
    NoteViewerPresence,
    NotificationPreference,
    OfficialHoliday,
    Profile,
    SystemSettings,
    Task,
    TaskLabel,
    TaskList,
    UserNotification,
    VacationPeriod,
    VacationYear,
    WeatherLocation,
    WebPushDelivery,
    WebPushSubscription,
    WeeklySummaryDelivery,
)
from app.services.calendar_service import fetch_ical, parse_ical_events
from app.services.calendar_sync_queue import queue_calendar_sources
from app.services.dashboard import DASHBOARD_WIDGET_IDS, default_dashboard_layout, normalize_dashboard_layout
from app.services.image_uploads import PROFILE_IMAGE_MAX_BYTES
from app.services.note_content import NOTE_TEMPLATES, empty_note_document, validate_note_document
from app.services.note_search import build_snippet, highlight_text, parse_search_query, search_notes
from app.services.notes import accessible_notes, prune_note_versions, purge_expired_notes, share_note
from app.services.notifications import (
    send_due_reminder_emails,
    send_due_task_reminder_emails,
    send_new_invitation_emails,
    send_note_activity_emails,
    send_pending_user_notification_emails,
    send_weekly_summaries,
)
from app.services.scheduled_tasks import run_scheduled_tasks, sync_due_calendars
from app.services.tasks import dashboard_today_tasks, toggle_task
from app.services.vacation_planner import (
    annual_summary,
    calculate_period,
    decimal_label,
    effective_holidays_for_year,
    generated_public_holidays,
    import_public_holidays,
    month_calendar,
    month_summary,
)
from app.services.weather_service import (
    WEATHER_MAP_LAYERS,
    _build_weather_alert,
    _format_weather_description,
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
from app.services.web_push import (
    materialize_web_push_weather_alerts,
    register_web_push_subscription,
    send_pending_web_push_notifications,
)
from app.templatetags.static_versioning import versioned_static
from app.view_models import (
    _dashboard_greeting,
    _dashboard_moment_icon,
    _dashboard_moment_label,
    _dashboard_tool_shortcuts,
)
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


class DashboardGreetingTests(SimpleTestCase):
    def test_dashboard_greeting_follows_local_hour(self):
        examples = {
            4: "Gute Nacht",
            5: "Guten Morgen",
            11: "Guten Tag",
            17: "Guten Abend",
            22: "Gute Nacht",
        }

        for hour, expected in examples.items():
            with self.subTest(hour=hour):
                now = datetime(2026, 8, 26, hour, 0, tzinfo=ZoneInfo("Europe/Berlin"))
                self.assertEqual(_dashboard_greeting(now), expected)

    def test_dashboard_moment_label_follows_local_hour(self):
        examples = {
            4: "Nachtruhe",
            5: "Ruhiger Start",
            11: "Fokussierter Tag",
            17: "Ruhiger Abend",
            22: "Nachtruhe",
        }

        for hour, expected in examples.items():
            with self.subTest(hour=hour):
                now = datetime(2026, 8, 26, hour, 0, tzinfo=ZoneInfo("Europe/Berlin"))
                self.assertEqual(_dashboard_moment_label(now), expected)

    def test_dashboard_moment_icon_follows_local_hour(self):
        examples = {
            4: "fa-regular fa-moon",
            5: "fa-regular fa-sun",
            17: "fa-solid fa-cloud-sun",
            22: "fa-regular fa-moon",
        }

        for hour, expected in examples.items():
            with self.subTest(hour=hour):
                now = datetime(2026, 8, 26, hour, 0, tzinfo=ZoneInfo("Europe/Berlin"))
                self.assertEqual(_dashboard_moment_icon(now), expected)

    def test_dashboard_shortcuts_use_correct_german_spelling(self):
        shortcuts = _dashboard_tool_shortcuts(
            [],
            0,
            {"today": {"city": "Bünde"}},
            0,
            {"weather": True, "messages": True, "notes": True, "vacation_planner": True},
        )

        subtitles = {item["title"]: item["subtitle"] for item in shortcuts}
        self.assertEqual(subtitles["Kalender"], "Kalender öffnen")
        self.assertEqual(subtitles["Nachrichten"], "Inbox öffnen")
        self.assertEqual(subtitles["Einstellungen"], "Profil & Präferenzen")


class WeatherDescriptionTests(SimpleTestCase):
    def test_weather_descriptions_follow_german_capitalization(self):
        examples = {
            "ein paar wolken": "Ein paar Wolken",
            "leichter regen": "Leichter Regen",
            "gewitter mit schnee": "Gewitter mit Schnee",
            "mäßig bewölkt": "Mäßig bewölkt",
        }

        for description, expected in examples.items():
            with self.subTest(description=description):
                self.assertEqual(_format_weather_description(description), expected)


class StaticVersioningTests(SimpleTestCase):
    def test_javascript_assets_use_module_compatible_content_type(self):
        self.assertEqual(mimetypes.guess_type("notes.js")[0], "application/javascript")

    def test_versioned_static_uses_file_content_hash(self):
        static_path = BASE_DIR / "app" / "static" / "css" / "base.css"
        expected_version = hashlib.sha256(static_path.read_bytes()).hexdigest()[:12]

        self.assertEqual(
            versioned_static("css/base.css"),
            f"/static/css/base.css?v={expected_version}",
        )

    def test_nginx_immutably_caches_only_versioned_static_assets(self):
        nginx_config = (BASE_DIR / "deploy" / "nginx-lunora.conf").read_text(encoding="utf-8")

        self.assertIn("map $uri $lunora_static_path_cache_control", nginx_config)
        self.assertIn(
            "~^/static/js/bundles/chunks/[a-z0-9-]+-[A-Za-z0-9_-]{8,}\\.js$",
            nginx_config,
        )
        self.assertIn("map $arg_v $lunora_static_cache_control", nginx_config)
        self.assertIn('default "public, max-age=31536000, immutable";', nginx_config)
        self.assertIn('default "public, max-age=3600";', nginx_config)
        self.assertIn('""      $lunora_static_path_cache_control;', nginx_config)
        self.assertIn(
            "add_header Cache-Control $lunora_static_cache_control always;",
            nginx_config,
        )
        self.assertIn('add_header Cache-Control "public, max-age=3600" always;', nginx_config)

    def test_css_subresources_are_explicitly_versioned(self):
        base_css = (BASE_DIR / "app" / "static" / "css" / "base.css").read_text(encoding="utf-8")
        fontawesome_css = (
            BASE_DIR / "app" / "static" / "vendor" / "fontawesome" / "css" / "all.min.css"
        ).read_text(encoding="utf-8")

        self.assertIn("inter-latin-variable.woff2?v=inter-1", base_css)
        self.assertIn("lunora_background.webp?v=brand-3", base_css)
        self.assertIn("fa-solid-900.woff2?v=fontawesome-7.2.0", fontawesome_css)
        self.assertIn("fa-regular-400.woff2?v=fontawesome-7.2.0", fontawesome_css)
        self.assertIn("fa-brands-400.woff2?v=fontawesome-7.2.0", fontawesome_css)

    def test_notes_editor_uses_split_es_module_bundle(self):
        bundle_dir = BASE_DIR / "app" / "static" / "js" / "bundles"
        entry_path = bundle_dir / "notes.js"
        entry_source = entry_path.read_text(encoding="utf-8")
        chunk_paths = list((bundle_dir / "chunks").glob("*.js"))
        chunk_names = [path.name for path in chunk_paths]

        self.assertLess(entry_path.stat().st_size, 100_000)
        self.assertTrue(any(name.startswith("editor-core-") for name in chunk_names))
        self.assertTrue(any(name.startswith("editor-extensions-") for name in chunk_names))
        self.assertTrue(any(name.startswith("syntax-highlighting-") for name in chunk_names))
        self.assertTrue(any(name.startswith("math-renderer-") for name in chunk_names))
        self.assertLess(max(path.stat().st_size for path in chunk_paths), 500_000)
        self.assertRegex(
            entry_source,
            r'import\("\./chunks/math-renderer-[A-Za-z0-9_-]+\.js"\)',
        )

        notes_template = (BASE_DIR / "app" / "templates" / "app" / "notes.html").read_text(encoding="utf-8")
        self.assertIn(
            '<script src="{% versioned_static \'js/bundles/notes.js\' %}" type="module"></script>',
            notes_template,
        )


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PwaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="pwa@example.com",
            email="pwa@example.com",
            password="secret-12345",
        )
        Profile.objects.create(user=self.user, display_name="PWA")

    def test_service_worker_is_public_and_root_scoped(self):
        response = self.client.get("/service-worker.js")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("application/javascript"))
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertContains(response, 'const OFFLINE_URL = "/offline/";')
        self.assertContains(response, 'request.mode === "navigate"')
        self.assertContains(response, 'requestUrl.pathname.startsWith("/static/")')
        self.assertContains(response, "caches.match(request);")
        self.assertNotContains(response, "ignoreSearch")
        self.assertRegex(
            response.content.decode("utf-8"),
            r'"/static/css/base\.css\?v=[0-9a-f]{12}"',
        )

    def test_service_worker_does_not_precache_personal_pages_or_media(self):
        response = self.client.get("/service-worker.js")
        content = response.content.decode("utf-8")

        self.assertNotIn('"/home/"', content)
        self.assertNotIn("/notes/", content)
        self.assertNotIn("/messages/", content)
        self.assertNotIn("/calendar/", content)
        self.assertNotIn("/media/", content)
        self.assertNotIn("/private_media/", content)

    def test_service_worker_caches_navigated_pages_for_offline_use(self):
        response = self.client.get("/service-worker.js")
        content = response.content.decode("utf-8")

        self.assertIn("const PAGES_CACHE = ", content)
        self.assertIn("async function servePage(request)", content)
        self.assertIn("caches.match(request, { cacheName: PAGES_CACHE })", content)
        self.assertIn("networkResponse.ok", content)

    def test_service_worker_clears_page_cache_on_logout(self):
        response = self.client.get("/service-worker.js")
        content = response.content.decode("utf-8")

        self.assertIn('const LOGOUT_URL = "/logout/";', content)
        self.assertIn("requestUrl.pathname === LOGOUT_URL", content)
        self.assertIn("caches.delete(PAGES_CACHE)", content)

    def test_service_worker_handles_web_push_and_notification_clicks(self):
        response = self.client.get("/service-worker.js")
        content = response.content.decode("utf-8")

        self.assertIn('self.addEventListener("push"', content)
        self.assertIn('self.addEventListener("notificationclick"', content)
        self.assertIn("showNotification", content)
        self.assertIn("clients.openWindow", content)

    def test_offline_page_is_public_and_neutral(self):
        response = self.client.get("/offline/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Du bist offline")
        self.assertContains(response, "data-offline-retry")
        self.assertNotContains(response, "Hauptnavigation")
        self.assertEqual(response["X-Robots-Tag"], "noindex, noarchive")

    def test_manifest_contains_install_metadata_and_shortcuts(self):
        manifest_path = BASE_DIR / "app" / "static" / "site.webmanifest"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["id"], "/")
        self.assertEqual(manifest["start_url"], "/home/")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertTrue(any("maskable" in icon.get("purpose", "") for icon in manifest["icons"]))
        self.assertEqual(
            [shortcut["short_name"] for shortcut in manifest["shortcuts"]],
            ["Dashboard", "Notizen", "Kalender"],
        )

    def test_base_and_settings_expose_pwa_controls(self):
        login_response = self.client.get("/login/")

        self.assertContains(login_response, 'data-service-worker-url="/service-worker.js"')
        self.assertContains(login_response, "site.webmanifest")

        self.client.login(username="pwa@example.com", password="secret-12345")
        settings_response = self.client.get("/settings/")

        self.assertContains(settings_response, "data-pwa-install-panel")
        self.assertContains(settings_response, "data-pwa-install")
        self.assertContains(settings_response, "App installieren")


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
    def test_base_template_self_hosts_fonts_without_blocking_icon_css(self):
        response = self.client.get("/login/")
        content = response.content.decode()
        icon_stylesheet = versioned_static("vendor/fontawesome/css/all.min.css")

        self.assertNotIn("fonts.googleapis.com", content)
        self.assertNotIn("cdn.jsdelivr.net", content)
        self.assertIn("/static/vendor/inter/inter-latin-variable.woff2", content)
        self.assertIn(f'rel="preload" href="{icon_stylesheet}" as="style"', content)
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
        self.assertContains(response, "data-settings-tab=", count=6)
        self.assertContains(response, 'data-settings-panel="appearance"')
        self.assertContains(response, 'data-settings-panel="profile"', count=2)
        self.assertContains(response, 'data-settings-panel="calendar"')
        self.assertContains(response, 'data-settings-panel="notifications"')
        self.assertContains(response, 'data-settings-panel="weather"')
        self.assertContains(response, 'data-settings-panel="app"')
        self.assertContains(response, "Sprache &amp; Region speichern")
        self.assertContains(response, 'class="calendar-input-pill"', count=2)
        self.assertContains(response, 'class="source-toggle"')
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

    def test_calendar_color_choices_have_accessible_names(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira")
        CalendarSource.objects.create(
            user=user,
            name="Arbeit",
            ical_url="https://calendar.google.com/calendar/ical/accessibility/private/basic.ics",
        )
        self.client.force_login(user)

        response = self.client.get("/settings/")

        for label in ("Blau", "Grün", "Rot", "Sand", "Violett"):
            with self.subTest(label=label):
                self.assertContains(response, f'aria-label="{label}"', count=2)

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

    def test_calendar_source_can_be_added_and_queued_from_settings(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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

        self.assertEqual(response["Location"], "/settings/?next=%2Fhome%2F#settings-calendar")
        source = CalendarSource.objects.get(user=user)
        self.assertEqual(source.name, "Arbeit")
        self.assertEqual(
            source.ical_url, "https://calendar.google.com/calendar/ical/settings/private/basic.ics"
        )
        self.assertEqual(source.color, "green")
        self.assertTrue(source.is_visible)
        self.assertTrue(source.enabled)
        self.assertIsNotNone(source.sync_requested_at)
        fetch_calendar.assert_not_called()

    def test_calendar_source_is_kept_while_first_sync_is_queued(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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

        self.assertEqual(response["Location"], "/settings/?next=%2Fhome%2F#settings-calendar")
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
                form = CalendarSourceForm(
                    data={"name": "Privat", "ical_url": url, "color": "blue", "enabled": "on"}
                )

                self.assertFalse(form.is_valid())
                self.assertIn("ical_url", form.errors)

    def test_calendar_source_form_rejects_duplicate_urls_for_user(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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
        mira = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        lukas = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
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

        self.assertEqual(response["Location"], "/settings/?next=%2Fhome%2F#settings-calendar")
        self.assertEqual(CalendarSource.objects.get(user=mira).ical_url, private_url)
        self.assertEqual(
            CalendarSource.objects.get(user=lukas).ical_url,
            "https://calendar.google.com/calendar/ical/lukas/private/basic.ics",
        )

    def test_calendar_source_update_clears_events_when_url_changes(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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

        self.assertEqual(response["Location"], "/settings/?next=%2Fhome%2F#settings-calendar")
        source.refresh_from_db()
        self.assertEqual(source.name, "Neu")
        self.assertEqual(source.color, "red")
        self.assertEqual(source.ical_url, "https://calendar.google.com/calendar/ical/new/private/basic.ics")
        self.assertFalse(CalendarEvent.objects.filter(source=source, external_id="old-event").exists())
        self.assertIsNotNone(source.sync_requested_at)
        fetch_calendar.assert_not_called()

    def test_disabling_calendar_source_clears_pending_sync(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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

        self.assertEqual(response["Location"], "/settings/?next=%2Fhome%2F#settings-calendar")
        self.assertFalse(CalendarSource.objects.filter(pk=source.id).exists())
        self.assertFalse(CalendarEvent.objects.filter(external_id="delete-event").exists())

    def test_calendar_page_does_not_render_calendar_source_form(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/calendar/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="form_name" value="calendar_source_add"')
        self.assertNotContains(response, "Google Kalender-Link")
        self.assertNotContains(response, "Hinzufügen")

    def test_calendar_empty_states_offer_direct_actions(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/calendar/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-calendar-empty-action="connect"')
        self.assertContains(response, 'data-calendar-empty-action="connect-upcoming"')
        self.assertContains(response, 'data-calendar-empty-action="event-today"')
        self.assertContains(response, 'data-calendar-empty-action="event-upcoming"')
        self.assertContains(response, "Kalender verbinden", count=2)

    def test_calendar_upcoming_empty_state_respects_existing_sources(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira")
        CalendarSource.objects.create(
            user=user,
            name="Arbeit",
            ical_url="https://calendar.google.com/calendar/ical/example/private/basic.ics",
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/calendar/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Deine sichtbaren Kalender haben aktuell keine kommenden Termine.")
        self.assertNotContains(response, 'data-calendar-empty-action="connect-upcoming"')
        self.assertContains(response, 'data-calendar-empty-action="event-upcoming"')

    def test_calendar_page_get_does_not_sync_calendar_source(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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

    def test_calendar_event_delete_ignores_synced_events(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira")
        source = CalendarSource.objects.create(
            user=user, name="Google Kalender", ical_url="https://example.com/cal.ics"
        )
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
                "attendees": [str(invitee.id)],
            },
        )

        self.assertRedirects(response, "/calendar/")
        event.refresh_from_db()
        self.assertEqual(event.title, "Neuer Titel")
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

    def test_calendar_event_edit_ignores_synced_events(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Mira")
        source = CalendarSource.objects.create(
            user=user, name="Google Kalender", ical_url="https://example.com/cal.ics"
        )
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

    def test_calendar_sync_request_is_queued_and_visible(self):
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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
        start_at = timezone.localtime(timezone.now() + timedelta(days=2)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
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
        mira = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        lukas = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
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
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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
        user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
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


class TaskTests(TestCase):
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
        )

    def _login(self):
        self.client.login(username="mira@example.com", password="secret-12345")

    def test_disabled_tasks_returns_503(self):
        SystemSettings.objects.create(tasks_enabled=False)
        self._login()

        response = self.client.get("/tasks/")

        self.assertEqual(response.status_code, 503)

    def test_disabled_tasks_blocks_direct_post(self):
        SystemSettings.objects.create(tasks_enabled=False)
        self._login()

        response = self.client.post(
            "/tasks/",
            {"form_name": "task_add", "title": "Blockierte Aufgabe"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(Task.objects.filter(title="Blockierte Aufgabe").exists())

    def test_task_can_be_added_toggled_and_deleted(self):
        self._login()

        response = self.client.post(
            "/tasks/",
            {"form_name": "task_add", "title": "Wäsche waschen"},
        )

        self.assertRedirects(response, "/tasks/")
        task = Task.objects.get(user=self.user)
        self.assertEqual(task.title, "Wäsche waschen")
        self.assertFalse(task.is_done)

        response = self.client.post(
            "/tasks/",
            {"form_name": "task_toggle", "task_id": str(task.id), "is_done": "on"},
        )

        self.assertRedirects(response, "/tasks/")
        task.refresh_from_db()
        self.assertTrue(task.is_done)

        response = self.client.post(
            "/tasks/",
            {"form_name": "task_delete", "task_id": str(task.id)},
        )

        self.assertRedirects(response, "/tasks/")
        self.assertFalse(Task.objects.filter(pk=task.id).exists())

    def test_task_can_store_due_date(self):
        self._login()
        due_at = timezone.localtime(timezone.now() + timedelta(days=1)).replace(second=0, microsecond=0)

        response = self.client.post(
            "/tasks/",
            {
                "form_name": "task_add",
                "title": "Hausaufgaben abgeben",
                "due_at": due_at.strftime("%Y-%m-%dT%H:%M"),
            },
        )

        self.assertRedirects(response, "/tasks/")
        task = Task.objects.get(user=self.user)
        self.assertEqual(
            timezone.localtime(task.due_at).strftime("%Y-%m-%dT%H:%M"),
            due_at.strftime("%Y-%m-%dT%H:%M"),
        )

        response = self.client.get("/tasks/")

        self.assertContains(response, "Hausaufgaben abgeben")
        self.assertContains(response, "Morgen")

    def test_task_page_exposes_filter_counts_and_statuses(self):
        now = timezone.now()
        Task.objects.create(user=self.user, title="Ohne Termin")
        Task.objects.create(user=self.user, title="Zu spät", due_at=now - timedelta(hours=1))
        Task.objects.create(user=self.user, title="Fertig", is_done=True)
        self._login()

        response = self.client.get("/tasks/")

        self.assertEqual(
            response.context["task_counts"],
            {"all": 3, "open": 2, "done": 1, "overdue": 1, "today": 1, "upcoming": 0},
        )
        self.assertContains(response, 'data-task-state="overdue"')
        self.assertContains(response, "Überfällig")
        self.assertContains(response, 'data-task-filter="done"')

    def test_user_cannot_toggle_or_delete_another_users_task(self):
        other = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        Profile.objects.create(user=other, display_name="Lukas")
        task = Task.objects.create(user=self.user, title="Privat")
        self.client.login(username="lukas@example.com", password="secret-12345")

        self.client.post("/tasks/", {"form_name": "task_toggle", "task_id": str(task.id), "is_done": "on"})
        task.refresh_from_db()
        self.assertFalse(task.is_done)

        self.client.post("/tasks/", {"form_name": "task_delete", "task_id": str(task.id)})
        self.assertTrue(Task.objects.filter(pk=task.id).exists())

        response = self.client.get("/tasks/")
        self.assertNotContains(response, "Privat")

    def test_task_edit_updates_fields(self):
        self._login()
        task_list = TaskList.objects.create(owner=self.user, name="Projekt")
        label = TaskLabel.objects.create(owner=self.user, name="Wichtig")
        task = Task.objects.create(user=self.user, title="Alter Titel")
        due_at = timezone.localtime(timezone.now() + timedelta(days=2)).replace(second=0, microsecond=0)

        response = self.client.post(
            "/tasks/",
            {
                "form_name": "task_edit",
                "task_id": str(task.id),
                "title": "Neuer Titel",
                "due_at": due_at.strftime("%Y-%m-%dT%H:%M"),
                "task_list": str(task_list.id),
                "priority": "high",
                "recurrence_rule": "WEEKLY",
                "labels": [str(label.id)],
            },
        )

        self.assertRedirects(response, "/tasks/")
        task.refresh_from_db()
        self.assertEqual(task.title, "Neuer Titel")
        self.assertEqual(
            timezone.localtime(task.due_at).strftime("%Y-%m-%dT%H:%M"),
            due_at.strftime("%Y-%m-%dT%H:%M"),
        )
        self.assertEqual(task.task_list_id, task_list.id)
        self.assertEqual(task.priority, "high")
        self.assertEqual(task.recurrence_rule, "WEEKLY")
        self.assertEqual(list(task.labels.all()), [label])

    def test_task_edit_cannot_target_another_users_task(self):
        other = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        Profile.objects.create(user=other, display_name="Lukas")
        task = Task.objects.create(user=other, title="Fremde Aufgabe")
        self._login()

        self.client.post(
            "/tasks/",
            {"form_name": "task_edit", "task_id": str(task.id), "title": "Übernommen"},
        )

        task.refresh_from_db()
        self.assertEqual(task.title, "Fremde Aufgabe")

    def test_task_edit_rejects_blank_title(self):
        self._login()
        task = Task.objects.create(user=self.user, title="Bleibt gleich")

        self.client.post(
            "/tasks/",
            {"form_name": "task_edit", "task_id": str(task.id), "title": ""},
        )

        task.refresh_from_db()
        self.assertEqual(task.title, "Bleibt gleich")

    def test_task_reorder_updates_positions(self):
        self._login()
        first = Task.objects.create(user=self.user, title="Erstens")
        second = Task.objects.create(user=self.user, title="Zweitens")
        third = Task.objects.create(user=self.user, title="Drittens")

        response = self.client.post(
            "/tasks/",
            {
                "form_name": "task_reorder",
                "task_id": str(third.id),
                "target_id": str(first.id),
                "placement": "before",
            },
        )

        self.assertRedirects(response, "/tasks/?sort=manual")
        ordered_ids = list(
            Task.objects.filter(user=self.user).order_by("position", "id").values_list("id", flat=True)
        )
        self.assertEqual(ordered_ids, [third.id, first.id, second.id])

    def test_task_reorder_rejects_cross_parent_move(self):
        self._login()
        parent_a = Task.objects.create(user=self.user, title="Eltern A")
        parent_b = Task.objects.create(user=self.user, title="Eltern B")
        subtask = Task.objects.create(user=self.user, title="Unteraufgabe", parent=parent_a)

        self.client.post(
            "/tasks/",
            {
                "form_name": "task_reorder",
                "task_id": str(subtask.id),
                "target_id": str(parent_b.id),
                "placement": "after",
            },
        )

        subtask.refresh_from_db()
        self.assertEqual(subtask.parent_id, parent_a.id)

    def test_task_reorder_cannot_target_another_users_task(self):
        other = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        Profile.objects.create(user=other, display_name="Lukas")
        mine = Task.objects.create(user=self.user, title="Meine Aufgabe", position=1000)
        theirs = Task.objects.create(user=other, title="Fremde Aufgabe", position=2000)
        self._login()

        self.client.post(
            "/tasks/",
            {
                "form_name": "task_reorder",
                "task_id": str(mine.id),
                "target_id": str(theirs.id),
                "placement": "before",
            },
        )

        mine.refresh_from_db()
        theirs.refresh_from_db()
        self.assertEqual(mine.position, 1000)
        self.assertEqual(theirs.position, 2000)

    def test_task_list_reorder_updates_positions(self):
        self._login()
        first = TaskList.objects.create(owner=self.user, name="Erste")
        second = TaskList.objects.create(owner=self.user, name="Zweite")
        third = TaskList.objects.create(owner=self.user, name="Dritte")

        response = self.client.post(
            "/tasks/",
            {
                "form_name": "task_list_reorder",
                "task_list_id": str(third.id),
                "target_id": str(first.id),
                "placement": "before",
            },
        )

        self.assertRedirects(response, "/tasks/")
        ordered_ids = list(
            TaskList.objects.filter(owner=self.user).order_by("position", "id").values_list("id", flat=True)
        )
        self.assertEqual(ordered_ids, [third.id, first.id, second.id])

    def test_manual_sort_orders_tasks_by_position(self):
        self._login()
        now = timezone.now()
        # due_at order would put "Später fällig" first; position order should reverse that.
        Task.objects.create(
            user=self.user, title="Früher fällig", due_at=now + timedelta(days=1), position=2000
        )
        Task.objects.create(
            user=self.user, title="Später fällig", due_at=now + timedelta(days=5), position=1000
        )

        response = self.client.get("/tasks/?sort=manual")

        titles = [item["title"] for item in response.context["tasks"]]
        self.assertEqual(titles, ["Später fällig", "Früher fällig"])

    def test_due_task_reminder_email_is_sent_only_once(self):
        task = Task.objects.create(
            user=self.user,
            title="Rechnung bezahlen",
            due_at=timezone.now() - timedelta(minutes=1),
        )

        first_result = send_due_task_reminder_emails()
        second_result = send_due_task_reminder_emails()

        self.assertEqual(first_result, {"sent": 1, "failed": 0})
        self.assertEqual(second_result, {"sent": 0, "failed": 0})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Rechnung bezahlen", mail.outbox[0].subject)
        task.refresh_from_db()
        self.assertIsNotNone(task.email_notified_at)

    def test_task_email_category_can_be_disabled(self):
        task = Task.objects.create(
            user=self.user,
            title="Nicht per E-Mail senden",
            due_at=timezone.now() - timedelta(minutes=1),
        )
        NotificationPreference.objects.create(
            user=self.user,
            category=NotificationPreference.CATEGORY_TASKS,
            email_enabled=False,
        )

        result = send_due_task_reminder_emails()

        self.assertEqual(result, {"sent": 0, "failed": 0})
        self.assertEqual(len(mail.outbox), 0)
        task.refresh_from_db()
        self.assertIsNone(task.email_notified_at)

    def test_task_desktop_notification_claim_is_one_time(self):
        task = Task.objects.create(
            user=self.user,
            title="Präsentation vorbereiten",
            due_at=timezone.now() - timedelta(minutes=1),
        )
        self.client.force_login(self.user)

        first_response = self.client.post("/notifications/claim/")
        second_response = self.client.post("/notifications/claim/")

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(first_response.json()["notifications"][0]["title"], "Präsentation vorbereiten")
        self.assertEqual(first_response.json()["notifications"][0]["url"], "/tasks/")
        self.assertEqual(second_response.json(), {"notifications": []})
        task.refresh_from_db()
        self.assertIsNotNone(task.desktop_notified_at)

    def test_dashboard_widget_appears_when_enabled(self):
        self._login()
        Task.objects.create(user=self.user, title="Offene Aufgabe")

        response = self.client.get("/home/")

        self.assertContains(response, "Offene Aufgabe")
        self.assertContains(response, "Aufgaben")

    def test_dashboard_widget_and_nav_tile_hidden_when_disabled(self):
        SystemSettings.objects.create(tasks_enabled=False)
        self._login()
        Task.objects.create(user=self.user, title="Offene Aufgabe")

        response = self.client.get("/home/")

        self.assertNotContains(response, "Offene Aufgabe")

    def test_scheduled_tasks_send_task_reminder_emails_when_enabled(self):
        Task.objects.create(
            user=self.user,
            title="Rechnung bezahlen",
            due_at=timezone.now() - timedelta(minutes=1),
        )

        result = run_scheduled_tasks(now=timezone.now())

        self.assertEqual(result["task_reminder_emails"], {"sent": 1, "failed": 0})

    def test_scheduled_tasks_skip_task_reminder_emails_when_disabled(self):
        SystemSettings.objects.create(tasks_enabled=False)
        Task.objects.create(
            user=self.user,
            title="Rechnung bezahlen",
            due_at=timezone.now() - timedelta(minutes=1),
        )

        result = run_scheduled_tasks(now=timezone.now())

        self.assertEqual(result["task_reminder_emails"], {"sent": 0, "failed": 0, "disabled": True})

    def test_task_list_crud_and_task_falls_back_to_inbox_when_list_deleted(self):
        self._login()

        self.client.post("/tasks/", {"form_name": "task_list_add", "name": "Projekt Alpha", "color": "blue"})
        task_list = TaskList.objects.get(owner=self.user, name="Projekt Alpha")

        response = self.client.post(
            "/tasks/",
            {"form_name": "task_add", "title": "Kickoff vorbereiten", "task_list": str(task_list.id)},
        )
        self.assertRedirects(response, "/tasks/")
        task = Task.objects.get(title="Kickoff vorbereiten")
        self.assertEqual(task.task_list_id, task_list.id)

        self.client.post(
            "/tasks/",
            {"form_name": "task_list_rename", "task_list_id": str(task_list.id), "name": "Projekt Beta"},
        )
        task_list.refresh_from_db()
        self.assertEqual(task_list.name, "Projekt Beta")

        self.client.post("/tasks/", {"form_name": "task_list_delete", "task_list_id": str(task_list.id)})

        self.assertFalse(TaskList.objects.filter(pk=task_list.id).exists())
        task.refresh_from_db()
        self.assertIsNone(task.task_list_id)

    def test_task_label_create_assign_and_delete(self):
        self._login()
        self.client.post("/tasks/", {"form_name": "task_label_add", "name": "Dringend", "color": "red"})
        label = TaskLabel.objects.get(owner=self.user, name="Dringend")

        response = self.client.post(
            "/tasks/",
            {"form_name": "task_add", "title": "Kunde anrufen", "labels": [str(label.id)]},
        )
        self.assertRedirects(response, "/tasks/")
        task = Task.objects.get(title="Kunde anrufen")
        self.assertIn(label, task.labels.all())

        self.client.post("/tasks/", {"form_name": "task_label_delete", "task_label_id": str(label.id)})

        self.assertFalse(TaskLabel.objects.filter(pk=label.id).exists())
        self.assertEqual(task.labels.count(), 0)

    def test_subtask_creation_and_one_level_depth_enforcement(self):
        self._login()
        parent = Task.objects.create(user=self.user, title="Umzug organisieren")

        response = self.client.post(
            "/tasks/",
            {"form_name": "task_add", "title": "Umzugswagen mieten", "parent": str(parent.id)},
        )
        self.assertRedirects(response, "/tasks/")
        subtask = Task.objects.get(title="Umzugswagen mieten")
        self.assertEqual(subtask.parent_id, parent.id)

        # The parent picker only lists top-level tasks, so a subtask can never be chosen
        # as a parent itself -- this caps nesting at one level.
        response = self.client.post(
            "/tasks/",
            {"form_name": "task_add", "title": "Noch tiefer", "parent": str(subtask.id)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Task.objects.filter(title="Noch tiefer").exists())

    def test_subtasks_cascade_delete_with_parent(self):
        parent = Task.objects.create(user=self.user, title="Elternaufgabe")
        Task.objects.create(user=self.user, title="Kindaufgabe", parent=parent)
        self._login()

        self.client.post("/tasks/", {"form_name": "task_delete", "task_id": str(parent.id)})

        self.assertFalse(Task.objects.filter(title="Elternaufgabe").exists())
        self.assertFalse(Task.objects.filter(title="Kindaufgabe").exists())

    def test_completing_recurring_task_creates_next_occurrence(self):
        due_at = timezone.now().replace(microsecond=0)
        task = Task.objects.create(
            user=self.user, title="Müll rausbringen", due_at=due_at, recurrence_rule="WEEKLY"
        )

        result = toggle_task(self.user, task.id, True)

        self.assertIsNotNone(result)
        task.refresh_from_db()
        self.assertTrue(task.is_done)
        siblings = Task.objects.filter(user=self.user, title="Müll rausbringen").exclude(pk=task.id)
        self.assertEqual(siblings.count(), 1)
        next_task = siblings.first()
        self.assertFalse(next_task.is_done)
        self.assertEqual(next_task.due_at, due_at + timedelta(weeks=1))
        self.assertIsNone(next_task.email_notified_at)
        self.assertIsNone(next_task.desktop_notified_at)

    def test_completing_non_recurring_task_does_not_duplicate(self):
        task = Task.objects.create(user=self.user, title="Einmalig")

        toggle_task(self.user, task.id, True)

        self.assertEqual(Task.objects.filter(title="Einmalig").count(), 1)

    def test_task_counts_include_today_and_upcoming_buckets(self):
        now = timezone.now()
        Task.objects.create(user=self.user, title="Heute fällig", due_at=now + timedelta(hours=2))
        Task.objects.create(user=self.user, title="Nächste Woche", due_at=now + timedelta(days=3))
        Task.objects.create(user=self.user, title="Weit weg", due_at=now + timedelta(days=30))
        self._login()

        response = self.client.get("/tasks/")

        self.assertEqual(response.context["task_counts"]["today"], 1)
        self.assertEqual(response.context["task_counts"]["upcoming"], 1)

    def test_user_cannot_manage_another_users_task_list_or_label(self):
        other = User.objects.create_user(
            username="lukas2@example.com", email="lukas2@example.com", password="secret-12345"
        )
        Profile.objects.create(user=other, display_name="Lukas2")
        task_list = TaskList.objects.create(owner=self.user, name="Privatliste")
        label = TaskLabel.objects.create(owner=self.user, name="Privatlabel")
        self.client.login(username="lukas2@example.com", password="secret-12345")

        self.client.post("/tasks/", {"form_name": "task_list_delete", "task_list_id": str(task_list.id)})
        self.client.post("/tasks/", {"form_name": "task_label_delete", "task_label_id": str(label.id)})

        self.assertTrue(TaskList.objects.filter(pk=task_list.id).exists())
        self.assertTrue(TaskLabel.objects.filter(pk=label.id).exists())

    def test_dashboard_today_tasks_includes_overdue_and_today_but_not_upcoming(self):
        now = timezone.now()
        Task.objects.create(user=self.user, title="Überfällig", due_at=now - timedelta(days=1))
        Task.objects.create(user=self.user, title="Heute fällig", due_at=now + timedelta(hours=2))
        Task.objects.create(user=self.user, title="Nächste Woche", due_at=now + timedelta(days=3))
        Task.objects.create(user=self.user, title="Ohne Fälligkeit")
        Task.objects.create(user=self.user, title="Erledigt", due_at=now, is_done=True)

        result = dashboard_today_tasks(self.user, now)

        titles = {item["title"] for item in result}
        self.assertEqual(titles, {"Überfällig", "Heute fällig"})

    def test_task_toggle_redirects_to_safe_return_to_url(self):
        task = Task.objects.create(user=self.user, title="Vom Dashboard erledigen")
        self._login()

        response = self.client.post(
            "/tasks/",
            {"form_name": "task_toggle", "task_id": str(task.id), "is_done": "on", "return_to": "/home/"},
        )

        self.assertRedirects(response, "/home/")
        task.refresh_from_db()
        self.assertTrue(task.is_done)

    def test_task_toggle_ignores_unsafe_return_to_url(self):
        task = Task.objects.create(user=self.user, title="Sicherheitscheck")
        self._login()

        response = self.client.post(
            "/tasks/",
            {
                "form_name": "task_toggle",
                "task_id": str(task.id),
                "is_done": "on",
                "return_to": "https://evil.example/",
            },
        )

        self.assertRedirects(response, "/tasks/")


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
            with patch(
                "app.services.calendar_service._ICAL_OPENER.open", return_value=FakeIcalResponse()
            ) as opener:
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
        organizer = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
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
        actor = User.objects.create_user(
            username="anna@example.com", email="anna@example.com", password="secret-12345"
        )
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
        self.assertTrue(
            WeeklySummaryDelivery.objects.filter(user=self.user, week_start=monday.date()).exists()
        )

    def test_automation_command_runs_one_cycle_by_default(self):
        result = {
            "calendar_sync": {"synced": 1, "failed": 0, "skipped": 2},
            "reminder_emails": {"sent": 1, "failed": 0},
            "weekly_summaries": {"sent": 1, "failed": 0, "skipped": 0},
        }
        output = StringIO()

        with patch(
            "app.management.commands.run_automations.run_scheduled_tasks", return_value=result
        ) as run_tasks:
            call_command("run_automations", stdout=output)

        run_tasks.assert_called_once_with()
        self.assertIn("Kalender: 1 synchronisiert", output.getvalue())


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class NotificationCenterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com",
            email="mira@example.com",
            password="secret-12345",
        )
        Profile.objects.create(user=self.user, display_name="Mira")
        self.other = User.objects.create_user(
            username="lukas@example.com",
            email="lukas@example.com",
            password="secret-12345",
        )
        Profile.objects.create(user=self.other, display_name="Lukas")

    def test_notification_center_requires_login(self):
        response = self.client.get("/notifications/")

        self.assertRedirects(response, "/login/?next=/notifications/")

    def test_due_tasks_and_reminders_are_materialized_once_and_appear_in_badge(self):
        now = timezone.now()
        Task.objects.create(user=self.user, title="Präsentation", due_at=now - timedelta(minutes=2))
        CalendarReminder.objects.create(
            user=self.user, title="Arzt anrufen", due_at=now - timedelta(minutes=1)
        )
        self.client.force_login(self.user)

        first_response = self.client.get("/notifications/?status=all")
        second_response = self.client.get("/notifications/?status=all")

        self.assertEqual(first_response.status_code, 200)
        self.assertContains(first_response, "Aufgabe fällig: Präsentation")
        self.assertContains(first_response, "Erinnerung fällig: Arzt anrufen")
        self.assertContains(first_response, "2 ungelesen")
        self.assertEqual(first_response.context["unread_notification_count"], 2)
        self.assertEqual(second_response.context["total_notification_count"], 2)
        self.assertEqual(UserNotification.objects.filter(recipient=self.user).count(), 2)

    def test_existing_invitations_and_note_activity_are_backfilled(self):
        event = CalendarEvent.objects.create(
            user=self.other,
            title="Projektmeeting",
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, hours=1),
        )
        invitation = CalendarEventAttendee.objects.create(
            event=event,
            user=self.user,
            invited_by=self.other,
        )
        note = Note.objects.create(owner=self.other, title="Ideen")
        share = NoteShare.objects.create(note=note, user=self.user, role=NoteShare.ROLE_READER)
        activity = NoteActivityNotification.objects.create(
            note=note,
            recipient=self.user,
            actor=self.other,
            kind=NoteActivityNotification.KIND_MENTION,
            excerpt="Hallo @Mira",
        )
        self.client.force_login(self.user)

        response = self.client.get("/notifications/?status=all")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Einladung: Projektmeeting")
        self.assertContains(response, "Lukas hat dich erwähnt")
        self.assertContains(response, "Lukas hat eine Notiz mit dir geteilt")
        self.assertTrue(
            UserNotification.objects.filter(
                recipient=self.user,
                source_key=f"event-invitation:{invitation.id}",
            ).exists()
        )
        self.assertTrue(
            UserNotification.objects.filter(
                recipient=self.user,
                source_key=f"note-activity:{activity.id}",
            ).exists()
        )
        self.assertTrue(
            UserNotification.objects.filter(
                recipient=self.user,
                source_key=f"note-share:{share.id}",
            ).exists()
        )

    def test_new_note_share_creates_inbox_notification_immediately(self):
        note = Note.objects.create(owner=self.other, title="Roadmap")

        share = share_note(self.other, note.id, self.user.id, NoteShare.ROLE_EDITOR)
        share_note(self.other, note.id, self.user.id, NoteShare.ROLE_READER)

        notification = UserNotification.objects.get(
            recipient=self.user,
            source_key=f"note-share:{share.id}",
        )
        self.assertEqual(notification.kind, UserNotification.KIND_NOTE_SHARE)
        self.assertEqual(notification.actor, self.other)
        self.assertEqual(notification.url, f"/notes/{note.id}/")
        self.assertEqual(UserNotification.objects.filter(recipient=self.user).count(), 1)

    def test_opening_and_toggling_notification_are_user_scoped(self):
        notification = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_TASK_DUE,
            title="Aufgabe fällig",
            url="/tasks/",
            source_key="task:1001",
        )
        other_notification = UserNotification.objects.create(
            recipient=self.other,
            kind=UserNotification.KIND_TASK_DUE,
            title="Privat",
            url="/tasks/",
            source_key="task:1002",
        )
        self.client.force_login(self.user)

        response = self.client.post(f"/notifications/{notification.id}/open/")

        self.assertRedirects(response, "/tasks/")
        notification.refresh_from_db()
        self.assertIsNotNone(notification.read_at)

        response = self.client.post(f"/notifications/{notification.id}/toggle-read/", {"status": "all"})
        self.assertRedirects(response, "/notifications/?status=all")
        notification.refresh_from_db()
        self.assertIsNone(notification.read_at)

        response = self.client.post(f"/notifications/{other_notification.id}/toggle-read/")
        self.assertEqual(response.status_code, 404)

    def test_toggle_read_redirects_to_safe_return_to_url(self):
        notification = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_TASK_DUE,
            title="Aufgabe fällig",
            url="/tasks/",
            source_key="task:2001",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            f"/notifications/{notification.id}/toggle-read/",
            {"return_to": "/home/"},
        )

        self.assertRedirects(response, "/home/")
        notification.refresh_from_db()
        self.assertIsNotNone(notification.read_at)

    def test_toggle_read_ignores_unsafe_return_to_url(self):
        notification = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_TASK_DUE,
            title="Aufgabe fällig",
            url="/tasks/",
            source_key="task:2002",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            f"/notifications/{notification.id}/toggle-read/",
            {"return_to": "https://evil.example/"},
        )

        self.assertRedirects(response, "/notifications/?status=unread")

    def test_filters_and_mark_all_read(self):
        unread = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_NOTE_COMMENT,
            title="Neuer Kommentar",
            source_key="note-activity:1001",
        )
        UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_EVENT_INVITATION,
            title="Gelesene Einladung",
            source_key="event-invitation:1002",
            read_at=timezone.now(),
        )
        self.client.force_login(self.user)

        unread_response = self.client.get("/notifications/")
        all_response = self.client.get("/notifications/?status=all")

        self.assertContains(unread_response, "Neuer Kommentar")
        self.assertNotContains(unread_response, "Gelesene Einladung")
        self.assertContains(all_response, "Neuer Kommentar")
        self.assertContains(all_response, "Gelesene Einladung")

        response = self.client.post("/notifications/mark-all-read/")

        self.assertRedirects(response, "/notifications/?status=all")
        unread.refresh_from_db()
        self.assertIsNotNone(unread.read_at)
        self.assertFalse(UserNotification.objects.filter(recipient=self.user, read_at__isnull=True).exists())

    def test_inbox_category_preference_hides_items_badge_and_bulk_read(self):
        hidden = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_TASK_DUE,
            title="Verborgene Aufgabe",
            source_key="task:hidden-category",
        )
        visible = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_NOTE_COMMENT,
            title="Sichtbarer Kommentar",
            source_key="note-comment:visible-category",
        )
        NotificationPreference.objects.create(
            user=self.user,
            category=NotificationPreference.CATEGORY_TASKS,
            inbox_enabled=False,
        )
        self.client.force_login(self.user)

        response = self.client.get("/notifications/?status=all")

        self.assertNotContains(response, "Verborgene Aufgabe")
        self.assertContains(response, "Sichtbarer Kommentar")
        self.assertEqual(response.context["unread_notification_count"], 1)
        self.assertEqual(response.context["total_notification_count"], 1)

        self.client.post("/notifications/mark-all-read/")
        hidden.refresh_from_db()
        visible.refresh_from_db()
        self.assertIsNone(hidden.read_at)
        self.assertIsNotNone(visible.read_at)

    def test_inbox_backed_email_is_sent_once_and_respects_category(self):
        self.user.profile.notify_email = True
        self.user.profile.save(update_fields=["notify_email"])
        sent_notification = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_NOTE_SHARE,
            title="Neue Notizfreigabe",
            body="Lukas hat Roadmap mit dir geteilt.",
            source_key="note-share:email-enabled",
        )

        first_result = send_pending_user_notification_emails()
        second_result = send_pending_user_notification_emails()

        self.assertEqual(first_result, {"sent": 1, "failed": 0})
        self.assertEqual(second_result, {"sent": 0, "failed": 0})
        self.assertEqual(len(mail.outbox), 1)
        sent_notification.refresh_from_db()
        self.assertIsNotNone(sent_notification.email_notified_at)

        NotificationPreference.objects.create(
            user=self.user,
            category=NotificationPreference.CATEGORY_WEATHER,
            email_enabled=False,
        )
        muted_notification = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_WEATHER_ALERT,
            title="Gewitterwarnung",
            source_key="weather-alert:email-disabled",
        )

        muted_result = send_pending_user_notification_emails()

        self.assertEqual(muted_result, {"sent": 0, "failed": 0})
        self.assertEqual(len(mail.outbox), 1)
        muted_notification.refresh_from_db()
        self.assertIsNone(muted_notification.email_notified_at)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    WEB_PUSH_ENABLED=True,
    WEB_PUSH_VAPID_PUBLIC_KEY="B" * 87,
    WEB_PUSH_VAPID_PRIVATE_KEY="test-private-key",
    WEB_PUSH_VAPID_SUBJECT="mailto:push@example.com",
    WEB_PUSH_ALLOWED_ENDPOINT_HOSTS=[
        "fcm.googleapis.com",
        "updates.push.services.mozilla.com",
        "notify.windows.com",
        "push.apple.com",
    ],
    WEB_PUSH_TTL_SECONDS=600,
    WEB_PUSH_TIMEOUT_SECONDS=10,
    WEB_PUSH_MAX_ATTEMPTS=5,
)
class WebPushTests(TestCase):
    endpoint = "https://fcm.googleapis.com/fcm/send/test-device-token"
    subscription_payload = {
        "endpoint": endpoint,
        "keys": {"p256dh": "A" * 87, "auth": "B" * 22},
    }

    def setUp(self):
        self.user = User.objects.create_user(
            username="push@example.com",
            email="push@example.com",
            password="secret-12345",
        )
        Profile.objects.create(
            user=self.user,
            display_name="Push",
            notify_desktop=True,
        )
        self.other = User.objects.create_user(
            username="other-push@example.com",
            email="other-push@example.com",
            password="secret-12345",
        )
        Profile.objects.create(user=self.other, display_name="Other Push", notify_desktop=True)

    def create_subscription(self, user=None):
        subscription, _created = register_web_push_subscription(
            user or self.user,
            self.subscription_payload,
            user_agent="Test Browser",
        )
        return subscription

    def test_subscription_endpoint_requires_login_and_stores_current_device(self):
        anonymous_response = self.client.post(
            "/notifications/push-subscription/",
            data=json.dumps(self.subscription_payload),
            content_type="application/json",
        )
        self.assertRedirects(
            anonymous_response,
            "/login/?next=/notifications/push-subscription/",
        )

        self.client.force_login(self.user)
        response = self.client.post(
            "/notifications/push-subscription/",
            data=json.dumps(self.subscription_payload),
            content_type="application/json",
            HTTP_USER_AGENT="Test Browser 1.0",
        )

        self.assertEqual(response.status_code, 201)
        subscription = WebPushSubscription.objects.get(user=self.user)
        self.assertEqual(subscription.endpoint, self.endpoint)
        self.assertEqual(subscription.user_agent, "Test Browser 1.0")
        self.assertNotEqual(subscription.endpoint_hash, self.endpoint)

    def test_subscription_endpoint_rejects_untrusted_push_host(self):
        self.client.force_login(self.user)
        payload = {
            **self.subscription_payload,
            "endpoint": "https://127.0.0.1/internal-service",
        }

        response = self.client.post(
            "/notifications/push-subscription/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(WebPushSubscription.objects.exists())

    @override_settings(WEB_PUSH_ENABLED=False)
    def test_subscription_endpoint_reports_missing_server_configuration(self):
        self.client.force_login(self.user)

        response = self.client.post(
            "/notifications/push-subscription/",
            data=json.dumps(self.subscription_payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("nicht eingerichtet", response.json()["error"])

    def test_settings_exposes_public_key_and_device_activation(self):
        self.client.force_login(self.user)

        response = self.client.get("/settings/")

        self.assertContains(response, f'data-web-push-public-key="{"B" * 87}"')
        self.assertContains(response, "Auf diesem Gerät aktivieren")
        self.assertNotContains(response, "test-private-key")

    def test_subscription_delete_is_user_scoped(self):
        self.create_subscription(self.other)
        self.client.force_login(self.user)

        response = self.client.delete(
            "/notifications/push-subscription/",
            data=json.dumps({"endpoint": self.endpoint}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["removed"])
        self.assertTrue(WebPushSubscription.objects.filter(user=self.other).exists())

    def test_pending_notification_is_sent_once_per_registered_device(self):
        subscription = self.create_subscription()
        notification = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_TASK_DUE,
            title="Aufgabe fällig: Rechnung",
            body="Fällig heute um 12:00 Uhr",
            url="/tasks/",
            source_key="task:push-1",
        )

        with patch("app.services.web_push.webpush") as send_push:
            first_result = send_pending_web_push_notifications()
            second_result = send_pending_web_push_notifications()

        self.assertEqual(first_result, {"sent": 1, "failed": 0, "removed": 0, "queued": 1, "deferred": 0})
        self.assertEqual(second_result, {"sent": 0, "failed": 0, "removed": 0, "queued": 0, "deferred": 0})
        send_push.assert_called_once()
        payload = json.loads(send_push.call_args.kwargs["data"])
        self.assertEqual(payload["title"], notification.title)
        self.assertEqual(payload["url"], "/tasks/")
        self.assertEqual(send_push.call_args.kwargs["ttl"], 600)
        delivery = WebPushDelivery.objects.get(subscription=subscription, notification=notification)
        self.assertIsNotNone(delivery.delivered_at)
        self.assertEqual(delivery.attempt_count, 1)
        subscription.refresh_from_db()
        self.assertIsNotNone(subscription.last_success_at)

    def test_notifications_created_before_device_registration_are_not_backfilled(self):
        UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_NOTE_SHARE,
            title="Alte Freigabe",
            source_key="note-share:old",
        )
        self.create_subscription()

        with patch("app.services.web_push.webpush") as send_push:
            result = send_pending_web_push_notifications()

        self.assertEqual(result["queued"], 0)
        send_push.assert_not_called()

    def test_expired_subscription_is_removed_after_gone_response(self):
        subscription = self.create_subscription()
        UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_EVENT_INVITATION,
            title="Einladung",
            source_key="event-invitation:push-1",
        )
        response = SimpleNamespace(status_code=410)

        from pywebpush import WebPushException

        with patch(
            "app.services.web_push.webpush",
            side_effect=WebPushException("Gone", response=response),
        ):
            result = send_pending_web_push_notifications()

        self.assertEqual(result["removed"], 1)
        self.assertFalse(WebPushSubscription.objects.filter(pk=subscription.pk).exists())
        self.assertFalse(WebPushDelivery.objects.exists())

    def test_transient_delivery_failure_remains_retryable(self):
        subscription = self.create_subscription()
        notification = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_NOTE_COMMENT,
            title="Neuer Kommentar",
            source_key="note-comment:push-1",
        )
        response = SimpleNamespace(status_code=503)

        from pywebpush import WebPushException

        with patch(
            "app.services.web_push.webpush",
            side_effect=WebPushException("Unavailable", response=response),
        ):
            result = send_pending_web_push_notifications()

        self.assertEqual(result["failed"], 1)
        delivery = WebPushDelivery.objects.get(subscription=subscription, notification=notification)
        self.assertEqual(delivery.attempt_count, 1)
        self.assertEqual(delivery.last_status_code, 503)
        self.assertIsNone(delivery.delivered_at)
        subscription.refresh_from_db()
        self.assertEqual(subscription.failure_count, 1)

    def test_category_preference_prevents_web_push_delivery(self):
        self.create_subscription()
        NotificationPreference.objects.create(
            user=self.user,
            category=NotificationPreference.CATEGORY_TASKS,
            web_push_enabled=False,
        )
        UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_TASK_DUE,
            title="Stille Aufgabe",
            source_key="task:push-category-disabled",
        )

        with patch("app.services.web_push.webpush") as send_push:
            result = send_pending_web_push_notifications()

        self.assertEqual(result["queued"], 0)
        self.assertEqual(result["sent"], 0)
        send_push.assert_not_called()
        self.assertFalse(WebPushDelivery.objects.exists())

    def test_quiet_hours_defer_web_push_until_the_window_ends(self):
        subscription = self.create_subscription()
        profile = self.user.profile
        profile.timezone_name = "UTC"
        profile.notification_quiet_hours_enabled = True
        profile.notification_quiet_start = time(22, 0)
        profile.notification_quiet_end = time(7, 0)
        profile.save(
            update_fields=[
                "timezone_name",
                "notification_quiet_hours_enabled",
                "notification_quiet_start",
                "notification_quiet_end",
            ]
        )
        notification = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_NOTE_COMMENT,
            title="Später zustellen",
            source_key="note-comment:quiet-hours",
        )
        during_quiet_hours = datetime(2026, 8, 28, 23, 0, tzinfo=ZoneInfo("UTC"))
        after_quiet_hours = datetime(2026, 8, 29, 8, 0, tzinfo=ZoneInfo("UTC"))

        with patch("app.services.web_push.webpush") as send_push:
            deferred_result = send_pending_web_push_notifications(now=during_quiet_hours)
            sent_result = send_pending_web_push_notifications(now=after_quiet_hours)

        self.assertEqual(deferred_result["deferred"], 1)
        self.assertEqual(deferred_result["sent"], 0)
        self.assertEqual(sent_result["sent"], 1)
        send_push.assert_called_once()
        delivery = WebPushDelivery.objects.get(subscription=subscription, notification=notification)
        self.assertEqual(delivery.delivered_at, after_quiet_hours)

    def test_test_push_endpoint_sends_to_current_users_device(self):
        self.create_subscription()
        NotificationPreference.objects.create(
            user=self.user,
            category=NotificationPreference.CATEGORY_TASKS,
            web_push_enabled=False,
        )
        profile = self.user.profile
        profile.notification_quiet_hours_enabled = True
        profile.notification_quiet_start = time(0, 0)
        profile.notification_quiet_end = time(23, 59)
        profile.save(
            update_fields=[
                "notification_quiet_hours_enabled",
                "notification_quiet_start",
                "notification_quiet_end",
            ]
        )
        self.client.force_login(self.user)

        with patch("app.services.web_push.webpush") as send_push:
            response = self.client.post(
                "/notifications/push-test/",
                data=json.dumps({"endpoint": self.endpoint}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        send_push.assert_called_once()
        payload = json.loads(send_push.call_args.kwargs["data"])
        self.assertEqual(payload["title"], "Lunora-Testbenachrichtigung")

    def test_test_push_endpoint_is_user_scoped(self):
        self.create_subscription(self.other)
        self.client.force_login(self.user)

        with patch("app.services.web_push.webpush") as send_push:
            response = self.client.post(
                "/notifications/push-test/",
                data=json.dumps({"endpoint": self.endpoint}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 404)
        send_push.assert_not_called()

    def test_weather_alerts_are_checked_once_per_user_with_multiple_devices(self):
        self.create_subscription()
        second_payload = {
            **self.subscription_payload,
            "endpoint": "https://updates.push.services.mozilla.com/wpush/v2/second-device",
        }
        register_web_push_subscription(self.user, second_payload)
        WeatherLocation.objects.create(
            user=self.user,
            query="Berlin,de",
            name="Berlin",
            label="Berlin",
        )

        with patch(
            "app.services.notifications.materialize_due_weather_alerts", return_value=[{"id": "alert"}]
        ) as claim:
            result = materialize_web_push_weather_alerts()

        self.assertEqual(result, {"created": 1, "failed": 0})
        claim.assert_called_once()

    def test_weather_alert_detection_supports_inbox_only_preferences(self):
        profile = self.user.profile
        profile.notify_desktop = False
        profile.notify_email = False
        profile.save(update_fields=["notify_desktop", "notify_email"])
        NotificationPreference.objects.create(
            user=self.user,
            category=NotificationPreference.CATEGORY_WEATHER,
            inbox_enabled=True,
            email_enabled=False,
            web_push_enabled=False,
        )
        WeatherLocation.objects.create(
            user=self.user,
            query="Berlin,de",
            name="Berlin",
            label="Berlin",
        )

        with patch(
            "app.services.notifications.materialize_due_weather_alerts", return_value=[{"id": "alert"}]
        ) as detect:
            result = materialize_web_push_weather_alerts()

        self.assertEqual(result, {"created": 1, "failed": 0})
        detect.assert_called_once_with(self.user, now=None)


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
        self.assertContains(response, "data-hourly-carousel")
        self.assertContains(response, "data-hourly-scroll")
        self.assertContains(response, "data-hourly-previous")
        self.assertContains(response, "data-hourly-next")
        self.assertContains(response, "Frühere Stunden anzeigen")
        self.assertContains(response, "Spätere Stunden anzeigen")
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

    def test_dashboard_weather_requires_login(self):
        response = self.client.get("/home/weather/")

        self.assertRedirects(response, "/login/?next=/home/weather/")

    def test_dashboard_weather_returns_personalized_summary(self):
        self.client.login(username="map@example.com", password="secret-12345")
        weather_context = {
            "current": {
                "city": "Berlin",
                "temperature": 18,
                "feels_like": 17,
                "description": "Leicht bewölkt",
                "icon": "fa-cloud-sun",
            },
            "daily_forecast": [
                {
                    "day": "Donnerstag",
                    "high": 21,
                    "low": 12,
                    "rain": 25,
                    "description": "Bewölkt",
                    "icon": "fa-cloud",
                }
            ],
        }

        with patch(
            "app.views.weather_views.get_weather_context",
            return_value=weather_context,
        ) as weather_lookup:
            response = self.client.get("/home/weather/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "weather": {
                    "today": {
                        "city": "Berlin",
                        "temperature": 18,
                        "feels_like": 17,
                        "description": "Leicht bewölkt",
                        "icon": "fa-cloud-sun",
                    },
                    "tomorrow": {
                        "day": "Donnerstag",
                        "high": 21,
                        "low": 12,
                        "rain": 25,
                        "description": "Bewölkt",
                        "icon": "fa-cloud",
                    },
                },
            },
        )
        self.assertEqual(response["Cache-Control"], "private, no-store")
        weather_lookup.assert_called_once_with({}, user=self.user)

    def test_dashboard_weather_respects_feature_flag(self):
        SystemSettings.objects.create(weather_enabled=False)
        self.client.login(username="map@example.com", password="secret-12345")

        with patch("app.views.weather_views.get_weather_context") as weather_lookup:
            response = self.client.get("/home/weather/")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["ok"])
        weather_lookup.assert_not_called()

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
        with self.assertRaisesMessage(ValueError, "Ungültige Wetterkarten-Ebene"):
            fetch_weather_map_tile(7, 67, 43, layer="snow")

        for coordinates in [(0, 0, 0), (11, 0, 0), (7, 128, 43), (7, 67, 128)]:
            with self.subTest(coordinates=coordinates):
                with self.assertRaisesMessage(ValueError, "Ungültige Wetterkarten-Kachel"):
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

        with patch(
            "app.services.weather_service.urlopen", return_value=FakeWeatherResponse(payload)
        ) as mocked_urlopen:
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

        windy = _build_weather_alert(
            {"main": {"temp": 20}, "wind": {"speed": 20}}, base_forecast, {"main": "Clear"}
        )
        self.assertEqual(windy["kind"], "wind")

        rainy = _build_weather_alert(base_current, {"list": [{"pop": 0.9}]}, {"main": "Rain"})
        self.assertEqual(rainy["kind"], "rain")

        hot = _build_weather_alert(
            {"main": {"temp": 36}, "wind": {"speed": 0}}, base_forecast, {"main": "Clear"}
        )
        self.assertEqual(hot["kind"], "heat")

        cold = _build_weather_alert(
            {"main": {"temp": -12}, "wind": {"speed": 0}}, base_forecast, {"main": "Clear"}
        )
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
        location, created = save_weather_location(
            self.user, name="Berlin", lat=52.52, lon=13.405, details="DE", label="Berlin, DE"
        )
        self.assertTrue(created)
        self.assertTrue(location.is_default)

        duplicate, created_again = save_weather_location(
            self.user, name="Berlin", lat=52.5203, lon=13.4048, details="DE", label="Berlin, DE"
        )
        self.assertFalse(created_again)
        self.assertEqual(duplicate.pk, location.pk)
        self.assertEqual(list_weather_locations(self.user), [location])

    def test_save_weather_location_enforces_cap(self):
        for index in range(8):
            save_weather_location(
                self.user, name=f"Ort {index}", lat=10 + index, lon=10 + index, details="", label=""
            )

        with self.assertRaises(ValueError):
            save_weather_location(self.user, name="Ort 9", lat=50, lon=50, details="", label="")

    def test_delete_weather_location_promotes_next_default(self):
        first, _ = save_weather_location(
            self.user, name="Berlin", lat=52.52, lon=13.405, details="", label="Berlin"
        )
        second, _ = save_weather_location(
            self.user, name="Hamburg", lat=53.55, lon=9.99, details="", label="Hamburg"
        )

        delete_weather_location(self.user, first.pk)

        second.refresh_from_db()
        self.assertTrue(second.is_default)
        self.assertEqual(list(WeatherLocation.objects.filter(user=self.user)), [second])

    def test_set_default_weather_location_switches_default(self):
        first, _ = save_weather_location(
            self.user, name="Berlin", lat=52.52, lon=13.405, details="", label="Berlin"
        )
        second, _ = save_weather_location(
            self.user, name="Hamburg", lat=53.55, lon=9.99, details="", label="Hamburg"
        )

        set_default_weather_location(self.user, second.pk)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertTrue(second.is_default)

    @override_settings(WEATHER_API_KEY="")
    def test_location_from_request_prefers_default_weather_location(self):
        save_weather_location(
            self.user, name="Hamburg", lat=53.55, lon=9.99, details="DE", label="Hamburg, DE"
        )

        context = get_weather_context({}, user=self.user)

        self.assertEqual(context["current"]["city"], "Hamburg")
        self.assertEqual(context["current"]["label"], "Standardort")

    def test_weather_page_can_save_and_manage_locations(self):
        self.client.login(username="map@example.com", password="secret-12345")

        save_response = self.client.post(
            "/weather/",
            {
                "form_name": "location_save",
                "name": "Berlin",
                "lat": "52.52",
                "lon": "13.405",
                "details": "DE",
                "label": "Berlin, DE",
            },
        )
        self.assertRedirects(save_response, "/weather/")
        location = WeatherLocation.objects.get(user=self.user, name="Berlin")
        self.assertTrue(location.is_default)

        second_response = self.client.post(
            "/weather/",
            {
                "form_name": "location_save",
                "name": "Hamburg",
                "lat": "53.55",
                "lon": "9.99",
                "details": "DE",
                "label": "Hamburg, DE",
            },
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

    def test_messages_empty_state_offers_direct_start_action(self):
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/messages/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-new-chat-card")
        self.assertContains(response, "data-open-new-chat", count=2)
        self.assertContains(response, "Neue Unterhaltung starten", count=2)
        self.assertNotContains(response, "Starte links")
        self.assertNotContains(response, "Starte oben")

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
        self.assertFalse(
            ConversationMember.objects.get(conversation=conversation, user=self.mira).unread_count()
        )

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
        self.assertTrue(
            ConversationMember.objects.get(conversation=conversation, user=self.mira).unread_count()
        )

    def test_message_search_matches_older_message_bodies(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        ChatMessage.objects.create(conversation=conversation, sender=self.lukas, body="Projekt Alpha")
        ChatMessage.objects.create(
            conversation=conversation, sender=self.lukas, body="Normale letzte Nachricht"
        )
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
        self.assertFalse(
            ConversationMember.objects.filter(conversation=conversation, user=self.lukas).exists()
        )

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
        original = ChatMessage.objects.create(
            conversation=conversation, sender=self.lukas, body="Erste Nachricht"
        )

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
        ChatMessage.objects.create(
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
        sender = User.objects.create_user(
            username="sender@example.com", email="sender@example.com", password="secret-12345"
        )
        recipient = User.objects.create_user(
            username="recipient@example.com", email="recipient@example.com", password="secret-12345"
        )
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
        sender = User.objects.create_user(
            username="sender-live@example.com", email="sender-live@example.com", password="secret-12345"
        )
        recipient = User.objects.create_user(
            username="recipient-live@example.com", email="recipient-live@example.com", password="secret-12345"
        )
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
        sender = User.objects.create_user(
            username="sender-block@example.com", email="sender-block@example.com", password="secret-12345"
        )
        recipient = User.objects.create_user(
            username="recipient-block@example.com",
            email="recipient-block@example.com",
            password="secret-12345",
        )
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
        user = User.objects.create_user(
            username="overview-live@example.com", email="overview-live@example.com", password="secret-12345"
        )
        Profile.objects.create(user=user, display_name="Overview Live")

        self.client.login(username="overview-live@example.com", password="secret-12345")
        response = self.client.get("/messages/live/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertIn("contact_list_html", response.json())
        self.assertIn("overview_html", response.json())


class GlobalSearchTests(TestCase):
    def setUp(self):
        self.mira = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        self.lukas = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        Profile.objects.create(user=self.mira, display_name="Mira")
        Profile.objects.create(user=self.lukas, display_name="Lukas")

    @staticmethod
    def rendered_title(result):
        """Reassemble a note title from its highlight segments."""
        return "".join(part["text"] for part in result["title_segments"])

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
        # The matching part of a note title is wrapped in a highlight, so assert on
        # the context rather than on a contiguous title string.
        self.assertEqual(
            [self.rendered_title(item) for item in response.context["notes_results"]], ["Raketenstart planen"]
        )
        self.assertContains(response, "Rakete ist startklar")
        self.assertContains(response, "Raketenstart")

    def test_global_search_empty_query_shows_no_results(self):
        Note.objects.create(owner=self.mira, title="Irgendeine Notiz")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/search/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Wonach suchst du?")
        self.assertContains(response, "Notizen öffnen")
        self.assertContains(response, "Nachrichten öffnen")
        self.assertContains(response, "Kalender öffnen")
        self.assertNotContains(response, "Irgendeine Notiz")

    def test_global_search_no_results_offers_reset_and_shortcuts(self):
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/search/?q=Unauffindbar")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["has_search_results"])
        self.assertContains(response, "Keine Treffer für")
        self.assertContains(response, "Unauffindbar")
        self.assertContains(response, "Suche zurücksetzen")
        self.assertContains(response, "Notizen öffnen")
        self.assertContains(response, "Nachrichten öffnen")
        self.assertContains(response, "Kalender öffnen")

    def test_global_search_does_not_leak_other_users_private_data(self):
        Note.objects.create(owner=self.lukas, title="Raketengeheimnis")
        conversation = Conversation.objects.create(created_by=self.lukas)
        other_user = User.objects.create_user(
            username="anna@example.com", email="anna@example.com", password="secret-12345"
        )
        Profile.objects.create(user=other_user, display_name="Anna")
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        ConversationMember.objects.create(conversation=conversation, user=other_user)
        ChatMessage.objects.create(
            conversation=conversation, sender=self.lukas, body="Rakete geheime Unterhaltung"
        )
        CalendarEvent.objects.create(
            user=self.lukas,
            title="Rakete privater Termin",
            start_at=timezone.now() + timedelta(days=5),
            end_at=timezone.now() + timedelta(days=5, hours=1),
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/search/?q=Rakete")

        self.assertEqual(response.status_code, 200)
        # Highlighting splits a title mid-string, so a plain assertNotContains on the
        # title would pass even if the note leaked. Assert on the context instead.
        self.assertEqual(response.context["notes_results"], [])
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
        self.assertEqual(response.context["notes_results"], [])
        self.assertContains(response, "Raketenstart")


class NoteSearchQueryParserTests(SimpleTestCase):
    def test_bare_words_become_required_terms(self):
        parsed = parse_search_query("Rakete Zündung")
        self.assertEqual(parsed.terms, ("Rakete", "Zündung"))
        self.assertEqual(parsed.phrases, ())
        self.assertEqual(parsed.exclusions, ())

    def test_quoted_input_becomes_a_phrase_and_dash_prefix_excludes(self):
        parsed = parse_search_query('"kalter Start" rakete -geheim')
        self.assertEqual(parsed.terms, ("rakete",))
        self.assertEqual(parsed.phrases, ("kalter Start",))
        self.assertEqual(parsed.exclusions, ("geheim",))

    def test_punctuation_splits_a_token_into_separate_terms(self):
        self.assertEqual(parse_search_query("start,zündung;test").terms, ("start", "zündung", "test"))

    def test_single_quoted_word_stays_a_term_rather_than_a_phrase(self):
        parsed = parse_search_query('"rakete"')
        self.assertEqual(parsed.terms, ("rakete",))
        self.assertEqual(parsed.phrases, ())

    def test_duplicate_terms_are_collapsed_and_term_count_is_capped(self):
        self.assertEqual(parse_search_query("rakete rakete").terms, ("rakete",))
        parsed = parse_search_query(" ".join(f"wort{index}" for index in range(30)))
        self.assertEqual(len(parsed.terms), 12)

    def test_blank_query_is_falsy_and_exclusion_only_query_has_no_terms(self):
        self.assertFalse(parse_search_query("   "))
        parsed = parse_search_query("-geheim")
        self.assertFalse(parsed)
        self.assertEqual(parsed.exclusions, ("geheim",))


class NoteSearchSnippetTests(SimpleTestCase):
    def test_snippet_marks_every_match_and_keeps_surrounding_text(self):
        parsed = parse_search_query("Zündung")
        segments = build_snippet("Vor dem Start muss die Zündung geprüft werden.", parsed)
        self.assertEqual([part["text"] for part in segments if part["match"]], ["Zündung"])
        self.assertEqual(
            "".join(part["text"] for part in segments), "Vor dem Start muss die Zündung geprüft werden."
        )

    def test_snippet_matches_case_insensitively_and_inside_compounds(self):
        parsed = parse_search_query("rakete")
        segments = build_snippet("Der Raketenstart ist geplant.", parsed)
        self.assertEqual([part["text"] for part in segments if part["match"]], ["Rakete"])

    def test_snippet_without_a_match_falls_back_to_truncated_text(self):
        segments = build_snippet("Kein Treffer hier drin", parse_search_query("rakete"))
        self.assertEqual(segments, [{"text": "Kein Treffer hier drin", "match": False}])

    def test_snippet_windows_around_a_late_match_instead_of_the_text_start(self):
        body = ("Fülltext " * 60) + "Zündschlüssel am Ende"
        segments = build_snippet("Anfangswort " + body, parse_search_query("Zündschlüssel"))
        rendered = "".join(part["text"] for part in segments)
        self.assertTrue(rendered.startswith("… "))
        self.assertIn("Zündschlüssel", rendered)
        self.assertNotIn("Anfangswort", rendered)

    def test_empty_text_yields_no_segments(self):
        self.assertEqual(build_snippet("", parse_search_query("rakete")), [])

    def test_highlight_text_marks_a_match_without_windowing(self):
        segments = highlight_text("Raketenstart planen", parse_search_query("rakete"))
        self.assertEqual(
            segments,
            [{"text": "Rakete", "match": True}, {"text": "nstart planen", "match": False}],
        )

    def test_highlight_text_keeps_a_long_title_intact(self):
        title = "Ein sehr langer Titel " * 8 + "mit Rakete am Ende"
        rendered = "".join(part["text"] for part in highlight_text(title, parse_search_query("rakete")))
        self.assertEqual(rendered, title)
        self.assertNotIn("…", rendered)

    def test_highlight_text_without_a_match_returns_one_plain_segment(self):
        self.assertEqual(
            highlight_text("Einkaufsliste", parse_search_query("rakete")),
            [{"text": "Einkaufsliste", "match": False}],
        )


class NoteSearchRankingTests(TestCase):
    def setUp(self):
        self.mira = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=self.mira, display_name="Mira")

    def search(self, query):
        return list(
            search_notes(accessible_notes(self.mira), query).order_by("-search_rank", "-updated_at", "-id")
        )

    def test_multiple_terms_are_combined_with_and(self):
        both = Note.objects.create(owner=self.mira, title="Start", plain_text="Rakete und Zündung geprüft")
        Note.objects.create(owner=self.mira, title="Nur Rakete", plain_text="ohne das zweite Wort")

        self.assertEqual(self.search("Rakete Zündung"), [both])

    def test_title_match_ranks_above_body_only_match(self):
        body_hit = Note.objects.create(
            owner=self.mira, title="Wochenplan", plain_text="Die Rakete ist fertig"
        )
        title_hit = Note.objects.create(owner=self.mira, title="Rakete", plain_text="ohne Treffer im Text")

        self.assertEqual(self.search("Rakete"), [title_hit, body_hit])

    def test_phrase_requires_adjacent_words(self):
        adjacent = Note.objects.create(
            owner=self.mira, title="Ablauf", plain_text="Ein kalter Start am Morgen"
        )
        Note.objects.create(owner=self.mira, title="Ablauf B", plain_text="Start war kalter als erwartet")

        self.assertEqual(self.search('"kalter Start"'), [adjacent])

    def test_excluded_term_removes_a_matching_note(self):
        keep = Note.objects.create(owner=self.mira, title="Rakete", plain_text="öffentlich")
        Note.objects.create(owner=self.mira, title="Rakete", plain_text="streng geheim")

        self.assertEqual(self.search("Rakete -geheim"), [keep])

    def test_german_compounds_match_as_prefix_and_infix(self):
        note = Note.objects.create(
            owner=self.mira, title="Raketenstart planen", plain_text="Zündfolge prüfen"
        )

        self.assertEqual(self.search("Rakete"), [note])
        self.assertEqual(self.search("start"), [note])

    def test_search_does_not_return_notes_the_user_cannot_access(self):
        other = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        Note.objects.create(owner=other, title="Raketengeheimnis", plain_text="privat")

        self.assertEqual(self.search("Rakete"), [])

    def test_empty_query_annotates_a_neutral_rank_without_filtering(self):
        Note.objects.create(owner=self.mira, title="Irgendeine Notiz")

        results = search_notes(accessible_notes(self.mira), "")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].search_rank, 0.0)


class NotePostgresSearchSqlTests(SimpleTestCase):
    """Compile the production search path without needing a PostgreSQL server.

    The suite runs on SQLite, so ``search_notes`` normally never takes its
    PostgreSQL branch. Building the SQL against a real PostgreSQL backend proves
    the tsquery/tsvector expressions are constructed correctly.
    """

    def compile_search(self, query):
        from django.db.backends.postgresql.base import DatabaseWrapper

        connection = DatabaseWrapper(
            {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": "lunora",
                "USER": "lunora",
                "PASSWORD": "",
                "HOST": "127.0.0.1",
                "PORT": "5432",
                "OPTIONS": {},
                "CONN_MAX_AGE": 0,
                "CONN_HEALTH_CHECKS": False,
                "AUTOCOMMIT": True,
                "ATOMIC_REQUESTS": False,
                "TIME_ZONE": None,
                "TEST": {"CHARSET": None, "COLLATION": None, "NAME": None, "MIRROR": None},
            },
            "postgres-sql-check",
        )
        self.assertEqual(connection.vendor, "postgresql")
        with patch("app.services.note_search.connection", connection):
            queryset = search_notes(accessible_notes(User(pk=1, username="mira@example.com")), query)
        sql, params = queryset.query.get_compiler(connection=connection).as_sql()
        return sql, [value for value in params if isinstance(value, str)]

    def test_terms_compile_to_a_prefix_tsquery_ranked_with_ts_rank(self):
        sql, params = self.compile_search("rakete")

        self.assertIn("to_tsquery", sql)
        self.assertIn("ts_rank", sql)
        self.assertIn("rakete:*", params)
        self.assertIn("german", params)

    def test_phrases_compile_to_phraseto_tsquery(self):
        sql, params = self.compile_search('"kalter Start"')

        self.assertIn("phraseto_tsquery", sql)
        self.assertIn("kalter Start", params)

    def test_substring_fallback_is_kept_alongside_the_tsquery(self):
        _sql, params = self.compile_search("rakete")

        self.assertIn("%rakete%", params)

    def test_terms_that_are_not_bare_words_are_dropped_from_the_raw_tsquery(self):
        # search_type="raw" is handed to to_tsquery(); a stray operator would be a
        # database error at query time, so the parser must never emit one.
        _sql, params = self.compile_search("rakete & !start")

        self.assertNotIn("&", "".join(value for value in params if ":*" in value))
        self.assertIn("rakete:*", params)
        self.assertIn("start:*", params)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class NoteSearchViewTests(TestCase):
    def setUp(self):
        self.mira = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=self.mira, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

    def test_notes_page_ranks_by_relevance_when_searching(self):
        body_hit = Note.objects.create(
            owner=self.mira, title="Wochenplan", plain_text="Die Rakete ist fertig"
        )
        title_hit = Note.objects.create(owner=self.mira, title="Rakete", plain_text="ohne Treffer im Text")

        response = self.client.get("/notes/?q=Rakete")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "relevance")
        self.assertEqual([item["id"] for item in response.context["note_items"]], [title_hit.id, body_hit.id])

    def test_notes_page_keeps_custom_order_without_a_query(self):
        Note.objects.create(owner=self.mira, title="Wochenplan")

        response = self.client.get("/notes/")

        self.assertEqual(response.context["current_sort"], "custom")

    def test_notes_page_honours_an_explicit_sort_while_searching(self):
        Note.objects.create(owner=self.mira, title="Bravo Rakete")
        Note.objects.create(owner=self.mira, title="Alpha Rakete")

        response = self.client.get("/notes/?q=Rakete&sort=title")

        self.assertEqual(response.context["current_sort"], "title")
        self.assertEqual(
            [item["title"] for item in response.context["note_items"]], ["Alpha Rakete", "Bravo Rakete"]
        )

    def test_notes_page_falls_back_to_custom_order_for_relevance_without_a_query(self):
        Note.objects.create(owner=self.mira, title="Wochenplan")

        response = self.client.get("/notes/?sort=relevance")

        self.assertEqual(response.context["current_sort"], "custom")

    def test_notes_page_applies_multi_term_and_search(self):
        Note.objects.create(owner=self.mira, title="Start", plain_text="Rakete und Zündung geprüft")
        Note.objects.create(owner=self.mira, title="Nur Rakete", plain_text="ohne das zweite Wort")

        response = self.client.get("/notes/?q=Rakete+Z%C3%BCndung")

        self.assertEqual([item["title"] for item in response.context["note_items"]], ["Start"])

    def test_global_search_highlights_the_matching_fragment(self):
        Note.objects.create(
            owner=self.mira, title="Wochenplan", plain_text="Vor dem Start muss die Zündung geprüft werden"
        )

        response = self.client.get("/search/?q=Z%C3%BCndung")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<mark class="search-hit">Zündung</mark>', html=False)

    def test_global_search_highlights_the_matching_part_of_the_title(self):
        Note.objects.create(owner=self.mira, title="Raketenstart planen", plain_text="Zündfolge prüfen")

        response = self.client.get("/search/?q=Rakete")

        self.assertContains(response, '<mark class="search-hit">Rakete</mark>nstart planen', html=False)

    def test_global_search_falls_back_to_a_placeholder_title(self):
        Note.objects.create(owner=self.mira, title="", plain_text="Rakete im Text")

        response = self.client.get("/search/?q=Rakete")

        self.assertContains(response, "Unbenannte Notiz")

    def test_global_search_escapes_note_content_in_the_snippet(self):
        Note.objects.create(
            owner=self.mira, title="Wochenplan", plain_text="<script>alert(1)</script> Zündung"
        )

        response = self.client.get("/search/?q=Z%C3%BCndung")

        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, "&lt;script&gt;")


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

    def _layout(self, *, order=None, hidden=None, wide=None):
        return {
            "version": 2,
            "order": order or list(DASHBOARD_WIDGET_IDS),
            "hidden": hidden or [],
            "wide": wide or [],
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

    @override_settings(WEATHER_API_KEY="test-key")
    def test_home_defers_weather_provider_requests_until_after_render(self):
        self._login()

        with patch("app.services.weather_service.urlopen") as provider_request:
            response = self.client.get("/home/")

        self.assertEqual(response.status_code, 200)
        provider_request.assert_not_called()
        self.assertContains(response, 'data-dashboard-weather-url="/home/weather/"')
        self.assertContains(response, "Wetter wird geladen …")
        self.assertContains(response, "Vorhersage wird geladen …")

    def test_home_renders_saved_order(self):
        order = [
            "clock",
            "welcome",
            "quick_actions",
            "recent_tools",
            "upcoming_events",
            "weather",
            "tasks",
            "notifications",
            "stats",
        ]
        self.profile.dashboard_layout = self._layout(order=order)
        self.profile.save(update_fields=["dashboard_layout"])
        self._login()

        response = self.client.get("/home/")

        self.assertEqual(
            [widget["id"] for widget in response.context["dashboard_widgets"]],
            order,
        )

    def test_home_renders_wide_widgets_with_span_class(self):
        self.profile.dashboard_layout = self._layout(wide=["weather", "clock"])
        self.profile.save(update_fields=["dashboard_layout"])
        self._login()

        response = self.client.get("/home/")

        widgets_by_id = {widget["id"]: widget for widget in response.context["dashboard_widgets"]}
        self.assertTrue(widgets_by_id["weather"]["wide"])
        self.assertTrue(widgets_by_id["clock"]["wide"])
        self.assertFalse(widgets_by_id["welcome"]["wide"])
        self.assertContains(response, "dashboard-widget--wide")
        self.assertContains(response, 'aria-pressed="true"')

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
            "wide": ["removed", "clock", "clock"],
        }

        normalized = normalize_dashboard_layout(broken_layout)

        self.assertEqual(normalized["version"], 2)
        self.assertEqual(normalized["order"][:2], ["clock", "welcome"])
        self.assertEqual(set(normalized["order"]), set(DASHBOARD_WIDGET_IDS))
        self.assertEqual(normalized["hidden"], ["weather"])
        self.assertEqual(normalized["wide"], ["clock"])

    def test_normalization_defaults_wide_for_layouts_saved_before_the_feature_existed(self):
        pre_existing_layout = {
            "version": 1,
            "order": list(DASHBOARD_WIDGET_IDS),
            "hidden": ["weather"],
        }

        normalized = normalize_dashboard_layout(pre_existing_layout)

        self.assertEqual(normalized["wide"], [])
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
        other = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        other_profile = Profile.objects.create(user=other, display_name="Lukas")
        layout = self._layout(
            order=[
                "recent_tools",
                "quick_actions",
                "upcoming_events",
                "weather",
                "clock",
                "welcome",
                "tasks",
                "notifications",
                "stats",
            ],
            hidden=["clock", "weather"],
            wide=["recent_tools", "weather"],
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
            {"version": 2, "order": ["welcome"], "hidden": [], "wide": []},
            self._layout(
                order=["welcome", "welcome", "clock", "weather", "upcoming_events", "quick_actions"]
            ),
            self._layout(order=["welcome", "clock", "weather", "upcoming_events", "quick_actions", "bogus"]),
            {"version": 2, "order": list(DASHBOARD_WIDGET_IDS), "hidden": "weather", "wide": []},
            {"version": 2, "order": list(DASHBOARD_WIDGET_IDS), "hidden": [], "wide": "weather"},
            self._layout(wide=["welcome", "welcome"]),
            self._layout(wide=["bogus"]),
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
        self.profile.dashboard_layout = self._layout(
            order=["weather", "quick_actions", "recent_tools", "welcome", "clock", "upcoming_events"]
        )
        self.profile.save(update_fields=["dashboard_layout"])
        self._login()

        response = self.client.get("/home/")

        self.assertNotContains(response, 'data-widget-id="weather"')
        self.assertNotContains(response, "Nachrichten")
        self.assertContains(response, "Schnellzugriff")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class DashboardNotificationWidgetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com",
            email="mira@example.com",
            password="secret-12345",
        )
        Profile.objects.create(user=self.user, display_name="Mira")

    def _login(self):
        self.client.login(username="mira@example.com", password="secret-12345")

    def test_dashboard_shows_latest_unread_notifications_and_todays_tasks(self):
        now = timezone.now()
        UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_NOTE_SHARE,
            title="Notiz geteilt",
            source_key="note-share:9001",
        )
        UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_NOTE_SHARE,
            title="Bereits gelesen",
            source_key="note-share:9002",
            read_at=now,
        )
        Task.objects.create(user=self.user, title="Heute fällig", due_at=now + timedelta(hours=1))
        Task.objects.create(user=self.user, title="Nächste Woche", due_at=now + timedelta(days=3))
        self._login()

        response = self.client.get("/home/")

        self.assertContains(response, "Notiz geteilt")
        self.assertNotContains(response, "Bereits gelesen")
        self.assertContains(response, "Heute fällig")
        self.assertEqual(len(response.context["dashboard_notifications"]), 1)
        self.assertEqual(len(response.context["dashboard_today_tasks"]), 1)
        self.assertEqual(response.context["dashboard_today_tasks"][0]["title"], "Heute fällig")

    def test_dashboard_does_not_materialize_due_notification_sources(self):
        now = timezone.now()
        Task.objects.create(user=self.user, title="Überfällige Aufgabe", due_at=now - timedelta(minutes=2))
        CalendarReminder.objects.create(
            user=self.user,
            title="Überfällige Erinnerung",
            due_at=now - timedelta(minutes=1),
        )
        self._login()

        response = self.client.get("/home/")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserNotification.objects.filter(recipient=self.user).exists())
        self.assertEqual(response.context["unread_notification_count"], 0)
        self.assertEqual(response.context["dashboard_notifications"], [])

    def test_dashboard_skips_hidden_notification_widget_queries(self):
        layout = default_dashboard_layout()
        layout["hidden"] = ["notifications"]
        self.user.profile.dashboard_layout = layout
        self.user.profile.save(update_fields=["dashboard_layout"])
        self._login()

        with patch("app.view_models.dashboard_latest_notifications") as latest_notifications:
            response = self.client.get("/home/")

        self.assertEqual(response.status_code, 200)
        latest_notifications.assert_not_called()
        self.assertEqual(response.context["dashboard_notifications"], [])
        self.assertEqual(response.context["dashboard_today_tasks"], [])

    def test_dashboard_hides_task_section_when_tasks_disabled(self):
        SystemSettings.objects.create(tasks_enabled=False)
        self._login()

        response = self.client.get("/home/")

        self.assertContains(response, "Neueste Hinweise")
        self.assertNotContains(response, "Heutige Aufgaben")
        self.assertFalse(response.context["dashboard_tasks_enabled"])


class DashboardStatsWidgetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com",
            email="mira@example.com",
            password="secret-12345",
        )
        Profile.objects.create(user=self.user, display_name="Mira")

    def _login(self):
        self.client.login(username="mira@example.com", password="secret-12345")

    def test_dashboard_shows_stat_tiles_for_enabled_features(self):
        now = timezone.now()
        Task.objects.create(user=self.user, title="Erledigt", is_done=True)
        Task.objects.create(user=self.user, title="Offen")
        Note.objects.create(owner=self.user, title="Diese Woche bearbeitet")
        CalendarEvent.objects.create(
            user=self.user,
            title="Bald",
            start_at=now + timedelta(days=2),
            end_at=now + timedelta(days=2, hours=1),
        )
        VacationYear.objects.create(user=self.user, year=now.year, allowance_days=Decimal("30"))
        self._login()

        response = self.client.get("/home/")

        self.assertContains(response, "Statistik")
        dashboard_stats = response.context["dashboard_stats"]
        week_by_label = {tile["label"]: tile["value"] for tile in dashboard_stats["week"]}
        self.assertEqual(week_by_label["Erledigt (Woche)"], "1")
        self.assertEqual(week_by_label["Offene Aufgaben"], "1")
        self.assertEqual(week_by_label["Notizen (Woche)"], "1")
        self.assertEqual(week_by_label["Termine (Woche)"], "1")
        self.assertEqual(week_by_label[f"Resturlaub {now.year}"], "30 Tage")

        month_by_label = {tile["label"]: tile["value"] for tile in dashboard_stats["month"]}
        self.assertEqual(month_by_label["Erledigt (Monat)"], "1")
        self.assertEqual(month_by_label["Offene Aufgaben"], "1")
        self.assertEqual(month_by_label["Notizen (Monat)"], "1")
        self.assertEqual(month_by_label["Termine (Monat)"], "1")
        self.assertEqual(month_by_label[f"Resturlaub {now.year}"], "30 Tage")

    def test_dashboard_omits_tiles_for_disabled_features_and_unconfigured_vacation_year(self):
        SystemSettings.objects.create(tasks_enabled=False, notes_enabled=False)
        self._login()

        response = self.client.get("/home/")

        for period, done_label, notes_label, events_label in (
            ("week", "Erledigt (Woche)", "Notizen (Woche)", "Termine (Woche)"),
            ("month", "Erledigt (Monat)", "Notizen (Monat)", "Termine (Monat)"),
        ):
            labels = {tile["label"] for tile in response.context["dashboard_stats"][period]}
            self.assertNotIn(done_label, labels)
            self.assertNotIn("Offene Aufgaben", labels)
            self.assertNotIn(notes_label, labels)
            self.assertIn(events_label, labels)
            self.assertFalse(any(label.startswith("Resturlaub") for label in labels))

    def test_dashboard_stat_tiles_distinguish_week_and_month_activity(self):
        now = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        if week_start <= month_start:
            self.skipTest("Wochenstart fällt mit dem Monatsstart zusammen.")

        earlier_this_month = week_start - timedelta(days=1)
        task = Task.objects.create(user=self.user, title="Letzte Woche erledigt", is_done=True)
        Task.objects.filter(pk=task.pk).update(updated_at=earlier_this_month)
        self._login()

        response = self.client.get("/home/")

        dashboard_stats = response.context["dashboard_stats"]
        week_value = next(
            tile["value"] for tile in dashboard_stats["week"] if tile["label"] == "Erledigt (Woche)"
        )
        month_value = next(
            tile["value"] for tile in dashboard_stats["month"] if tile["label"] == "Erledigt (Monat)"
        )
        self.assertEqual(week_value, "0")
        self.assertEqual(month_value, "1")

    def test_dashboard_skips_hidden_stats_widget_queries(self):
        layout = default_dashboard_layout()
        layout["hidden"] = ["stats"]
        self.user.profile.dashboard_layout = layout
        self.user.profile.save(update_fields=["dashboard_layout"])
        self._login()

        with patch("app.view_models._dashboard_stats") as dashboard_stats:
            response = self.client.get("/home/")

        self.assertEqual(response.status_code, 200)
        dashboard_stats.assert_not_called()
        self.assertEqual(response.context["dashboard_stats"], {})


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

        self.assertEqual(response["Location"], "/settings/?next=%2Fhome%2F#settings-calendar")
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
            username="owner@example.com",
            email="owner@example.com",
            password="secret-12345",
            first_name="Owner",
        )
        self.reader = User.objects.create_user(
            username="reader@example.com",
            email="reader@example.com",
            password="secret-12345",
            first_name="Reader",
        )
        self.editor = User.objects.create_user(
            username="editor@example.com",
            email="editor@example.com",
            password="secret-12345",
            first_name="Editor",
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
        self.assertContains(response, "data-editor-context-menu")
        self.assertContains(response, "data-table-only")
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
        self.assertContains(response, 'data-note-context-action="rename"')
        self.assertContains(response, 'data-folder-context-action="create"')

    def test_notes_overview_does_not_automatically_open_first_note(self):
        note = self.create_note()
        response = self.client.get("/notes/")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["selected_note_data"])
        # Notes exist but none is selected, so the prompt is "pick one" rather
        # than "create your first note" — see test_notes_overview_empty_state_*.
        self.assertContains(response, "Wähle eine Notiz aus")
        self.assertContains(response, '<a class="notes-mobile-back" href="/notes/"', count=0)

        detail = self.client.get(f"/notes/{note.id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.context["selected_note_data"]["id"], note.id)
        self.assertContains(detail, '<a class="notes-mobile-back" href="/notes/"')

    def test_notes_overview_empty_state_invites_first_note_when_none_exist(self):
        response = self.client.get("/notes/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dein Platz für Gedanken")
        self.assertNotContains(response, "Wähle eine Notiz aus")

    def test_notes_overview_empty_state_reflects_the_current_filter_not_the_whole_account(self):
        self.create_note()

        response = self.client.get("/notes/?status=pinned")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["note_items"], [])
        # The account isn't empty, but nothing matches "Pins", so note_items is
        # empty here too — the main pane falls back to the create-a-note prompt.
        # The sidebar's own "Keine Notizen gefunden" card is what actually
        # communicates "no match" for this case.
        self.assertContains(response, "Dein Platz für Gedanken")

    def test_notes_filter_form_action_points_at_the_open_note_so_search_keeps_it_selected(self):
        note = self.create_note()

        response = self.client.get(f"/notes/{note.id}/?status=archived")

        self.assertContains(response, f'action="/notes/{note.id}/"')

    def test_notes_filter_form_action_points_at_the_overview_without_a_selected_note(self):
        response = self.client.get("/notes/")

        self.assertContains(response, 'action="/notes/"')

    def test_notes_filter_form_carries_a_hidden_status_field_for_implicit_submission(self):
        # Pressing Enter in the search field implicitly submits via the form's first
        # submit control. Without a hidden field holding the current status, that
        # control used to be the "Alle" tab button, silently resetting any other
        # active status filter whenever a search was submitted this way.
        response = self.client.get("/notes/?status=archived")

        self.assertContains(response, '<input type="hidden" name="status" value="archived">')

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
                {
                    "type": "noteImage",
                    "attrs": {"attachmentId": str(uuid.uuid4()), "alt": "Bild", "width": 400},
                },
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {"type": "paragraph", "content": [{"type": "text", "text": "Punkt"}]}
                            ],
                        },
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
            [
                NoteTemplate(owner=self.owner, name=f"Vorlage {i}", document=empty_note_document())
                for i in range(30)
            ]
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
            f"/notes/api/{note.id}/actions/",
            data=json.dumps({"action": "pin"}),
            content_type="application/json",
        )
        self.assertEqual(pin.status_code, 200)
        self.assertTrue(NoteUserState.objects.get(note=note, user=self.reader).is_pinned)
        self.assertFalse(NoteUserState.objects.get(note=note, user=self.owner).is_pinned)

        changed = self.save_note(note, text="Vom Bearbeiter", client=editor_client)
        self.assertEqual(changed.status_code, 200, changed.content)
        note.refresh_from_db()
        self.assertEqual(note.plain_text, "Vom Bearbeiter")

    def test_note_style_is_personal_and_validated(self):
        note = self.create_note()
        NoteShare.objects.create(note=note, user=self.reader, role=NoteShare.ROLE_READER)
        reader_client = Client()
        reader_client.login(username="reader@example.com", password="secret-12345")

        styled = reader_client.post(
            f"/notes/api/{note.id}/actions/",
            data=json.dumps({"action": "style", "color": "violet", "icon": "rocket"}),
            content_type="application/json",
        )
        self.assertEqual(styled.status_code, 200, styled.content)
        self.assertEqual(styled.json()["note"]["color"], "violet")
        self.assertEqual(styled.json()["note"]["icon"], "rocket")
        reader_state = NoteUserState.objects.get(note=note, user=self.reader)
        self.assertEqual(reader_state.color, "violet")
        self.assertEqual(reader_state.icon, "rocket")
        owner_state, _created = NoteUserState.objects.get_or_create(note=note, user=self.owner)
        self.assertEqual(owner_state.color, "")
        self.assertEqual(owner_state.icon, "")

        invalid = self.client.post(
            f"/notes/api/{note.id}/actions/",
            data=json.dumps({"action": "style", "color": "gold", "icon": ""}),
            content_type="application/json",
        )
        self.assertEqual(invalid.status_code, 400)

    def test_only_owner_can_manage_shares_and_trash(self):
        note = self.create_note()
        NoteShare.objects.create(note=note, user=self.editor, role=NoteShare.ROLE_EDITOR)
        editor_client = Client()
        editor_client.login(username="editor@example.com", password="secret-12345")
        denied = editor_client.post(
            f"/notes/api/{note.id}/actions/",
            data=json.dumps({"action": "trash"}),
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 403)
        denied = editor_client.post(
            f"/notes/api/{note.id}/shares/",
            data=json.dumps({"user_id": self.reader.id, "role": "reader"}),
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 403)

        trashed = self.client.post(
            f"/notes/api/{note.id}/actions/",
            data=json.dumps({"action": "trash"}),
            content_type="application/json",
        )
        self.assertEqual(trashed.status_code, 200)
        self.assertEqual(editor_client.get(f"/notes/api/{note.id}/").status_code, 404)
        restored = self.client.post(
            f"/notes/api/{note.id}/actions/",
            data=json.dumps({"action": "restore"}),
            content_type="application/json",
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

    def test_version_diff_reports_word_level_changes(self):
        note = self.create_note()
        saved = self.save_note(note, text="Erster Inhalt")
        self.assertEqual(saved.status_code, 200)
        version = NoteVersion.objects.get(note=note)
        response = self.client.get(f"/notes/api/{note.id}/versions/{version.id}/diff/")
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["added_words"], 2)
        self.assertEqual(payload["removed_words"], 0)
        non_empty_segments = [segment for segment in payload["segments"] if segment["text"].strip()]
        self.assertEqual(non_empty_segments, [{"type": "insert", "text": "Erster Inhalt"}])

    def test_version_diff_is_visible_to_read_only_share(self):
        note = self.create_note()
        self.save_note(note, text="Erster Inhalt")
        version = NoteVersion.objects.get(note=note)
        NoteShare.objects.create(note=note, user=self.reader, role=NoteShare.ROLE_READER)
        reader_client = Client()
        reader_client.login(username="reader@example.com", password="secret-12345")
        response = reader_client.get(f"/notes/api/{note.id}/versions/{version.id}/diff/")
        self.assertEqual(response.status_code, 200, response.content)

    def test_version_diff_missing_version_returns_404(self):
        note = self.create_note()
        response = self.client.get(f"/notes/api/{note.id}/versions/999999/diff/")
        self.assertEqual(response.status_code, 404)

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

    def test_tiptap_table_rejects_oversized_grid(self):
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
                                    "type": "tableCell",
                                    "attrs": {"colspan": 1, "rowspan": 1, "colwidth": None, "align": None},
                                    "content": [{"type": "paragraph", "attrs": {"textAlign": None}}],
                                }
                            ],
                        }
                        for _ in range(21)
                    ],
                }
            ],
        }
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": table_document, "base_revision": 1}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)

    def test_tiptap_table_rejects_invalid_cell_colspan(self):
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
                                    "type": "tableCell",
                                    "attrs": {"colspan": 21, "rowspan": 1, "colwidth": None, "align": None},
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
        self.assertEqual(response.status_code, 400, response.content)

    def test_tiptap_table_rejects_invalid_colwidth(self):
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
                                    "type": "tableCell",
                                    "attrs": {"colspan": 1, "rowspan": 1, "colwidth": [10], "align": None},
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
        self.assertEqual(response.status_code, 400, response.content)

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
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "X",
                            "marks": [{"type": "link", "attrs": {"href": "javascript:alert(1)"}}],
                        }
                    ],
                }
            ],
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
        self.assertEqual(
            saved_document["content"][0]["content"][0]["marks"][0]["attrs"]["href"], "https://youtube.com"
        )

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
                        {
                            "type": "text",
                            "text": " und farbig",
                            "marks": [
                                {
                                    "type": "textStyle",
                                    "attrs": {"color": "#a67c52", "fontSize": "18px", "lineHeight": "1.5"},
                                }
                            ],
                        },
                    ],
                },
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {"type": "paragraph", "content": [{"type": "text", "text": "Listenpunkt"}]}
                            ],
                        },
                    ],
                },
                {
                    "type": "table",
                    "content": [
                        {
                            "type": "tableRow",
                            "content": [
                                {
                                    "type": "tableHeader",
                                    "attrs": {"colspan": 1, "rowspan": 1},
                                    "content": [
                                        {"type": "paragraph", "content": [{"type": "text", "text": "Spalte"}]}
                                    ],
                                },
                                {
                                    "type": "tableHeader",
                                    "attrs": {"colspan": 1, "rowspan": 1},
                                    "content": [
                                        {"type": "paragraph", "content": [{"type": "text", "text": "Wert"}]}
                                    ],
                                },
                            ],
                        },
                        {
                            "type": "tableRow",
                            "content": [
                                {
                                    "type": "tableCell",
                                    "attrs": {"colspan": 1, "rowspan": 1},
                                    "content": [
                                        {"type": "paragraph", "content": [{"type": "text", "text": "A"}]}
                                    ],
                                },
                                {
                                    "type": "tableCell",
                                    "attrs": {"colspan": 1, "rowspan": 1},
                                    "content": [
                                        {"type": "paragraph", "content": [{"type": "text", "text": "1"}]}
                                    ],
                                },
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
        self.assertRedirects(
            Client().get(f"/notes/{note.id}/export/pdf/"), f"/login/?next=/notes/{note.id}/export/pdf/"
        )

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
                        {
                            "type": "listItem",
                            "content": [
                                {"type": "paragraph", "content": [{"type": "text", "text": "Listenpunkt"}]}
                            ],
                        },
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
                                {
                                    "type": "tableHeader",
                                    "content": [
                                        {"type": "paragraph", "content": [{"type": "text", "text": "Spalte"}]}
                                    ],
                                },
                                {
                                    "type": "tableHeader",
                                    "content": [
                                        {"type": "paragraph", "content": [{"type": "text", "text": "Wert"}]}
                                    ],
                                },
                            ],
                        },
                        {
                            "type": "tableRow",
                            "content": [
                                {
                                    "type": "tableCell",
                                    "content": [
                                        {"type": "paragraph", "content": [{"type": "text", "text": "A | B"}]}
                                    ],
                                },
                                {
                                    "type": "tableCell",
                                    "content": [
                                        {"type": "paragraph", "content": [{"type": "text", "text": "1"}]}
                                    ],
                                },
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
            Client().get(f"/notes/{note.id}/export/markdown/"),
            f"/login/?next=/notes/{note.id}/export/markdown/",
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
                    {
                        "kind": "image",
                        "file": SimpleUploadedFile("moon2.png", PNG_1X1_BYTES, content_type="image/png"),
                    },
                ).status_code,
                503,
            )
            self.assertEqual(
                self.client.get(f"/notes/attachments/{attachment.file_id}/inline/").status_code, 503
            )
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
            self.assertEqual(
                self.client.get(f"/notes/api/{note.id}/mention-candidates/?q=re").status_code, 503
            )
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
        self.assertTrue(
            UserNotification.objects.filter(
                recipient=self.reader,
                source_key=f"note-activity:{notification.id}",
                kind=UserNotification.KIND_NOTE_MENTION,
            ).exists()
        )

        # Saving again without a new mention must not create a duplicate notification.
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(NoteActivityNotification.objects.filter(note=note, recipient=self.reader).count(), 1)
        self.assertEqual(UserNotification.objects.filter(recipient=self.reader).count(), 1)

    def test_mentioning_user_without_access_is_rejected(self):
        note = self.create_note()
        outsider = User.objects.create_user(
            username="outsider@example.com", email="outsider@example.com", password="secret-12345"
        )
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
            data=json.dumps(
                {"thread_id": thread_id, "anchor_text": "Erster Inhalt", "body": "Was meinst du hiermit?"}
            ),
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
                    "content": [
                        {
                            "type": "text",
                            "text": "Erster Inhalt",
                            "marks": [{"type": "commentThread", "attrs": {"threadId": thread_id}}],
                        }
                    ],
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
        self.assertTrue(
            NoteActivityNotification.objects.filter(
                note=note, recipient=self.owner, kind=NoteActivityNotification.KIND_COMMENT
            ).exists()
        )

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
                    "content": [
                        {
                            "type": "text",
                            "text": "Erster Inhalt",
                            "marks": [{"type": "commentThread", "attrs": {"threadId": str(uuid.uuid4())}}],
                        }
                    ],
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
                    "content": [
                        {
                            "type": "text",
                            "text": "Erster Inhalt",
                            "marks": [{"type": "commentThread", "attrs": {"threadId": thread_id}}],
                        }
                    ],
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
                {
                    "title": source.title,
                    "document": note_document("Kein Link mehr"),
                    "base_revision": source.revision,
                }
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


class NotePresenceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="presence-owner@example.com",
            email="presence-owner@example.com",
            password="secret-12345",
            first_name="Owner",
        )
        self.reader = User.objects.create_user(
            username="presence-reader@example.com",
            email="presence-reader@example.com",
            password="secret-12345",
            first_name="Reader",
        )
        self.outsider = User.objects.create_user(
            username="presence-outsider@example.com",
            email="presence-outsider@example.com",
            password="secret-12345",
            first_name="Outsider",
        )
        Profile.objects.create(user=self.owner, display_name="Owner")
        Profile.objects.create(user=self.reader, display_name="Reader")
        Profile.objects.create(user=self.outsider, display_name="Outsider")
        self.note = Note.objects.create(owner=self.owner, title="Geteilte Notiz")
        NoteShare.objects.create(note=self.note, user=self.reader, role=NoteShare.ROLE_READER)
        self.client.login(username="presence-owner@example.com", password="secret-12345")
        self.reader_client = Client()
        self.reader_client.login(username="presence-reader@example.com", password="secret-12345")

    def presence_url(self, *, leave=False):
        url = f"/notes/api/{self.note.id}/presence/"
        return f"{url}?leave=1" if leave else url

    def test_ping_returns_other_active_viewers_only(self):
        response = self.client.post(self.presence_url())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["viewers"], [])
        self.assertTrue(NoteViewerPresence.objects.filter(note=self.note, user=self.owner).exists())

        # Owner is already present, so the reader's own ping must show the owner
        # but never itself.
        response = self.reader_client.post(self.presence_url())
        self.assertEqual(response.status_code, 200, response.content)
        viewers = response.json()["viewers"]
        self.assertEqual(len(viewers), 1)
        self.assertEqual(viewers[0]["user_id"], self.owner.id)
        self.assertEqual(viewers[0]["name"], "Owner")

        response = self.client.post(self.presence_url())
        viewers = response.json()["viewers"]
        self.assertEqual(len(viewers), 1)
        self.assertEqual(viewers[0]["user_id"], self.reader.id)
        self.assertEqual(viewers[0]["name"], "Reader")

    def test_ping_reports_current_note_revision(self):
        response = self.client.post(self.presence_url())
        self.assertEqual(response.json()["revision"], self.note.revision)

        save_response = self.client.patch(
            f"/notes/api/{self.note.id}/",
            data=json.dumps(
                {"title": self.note.title, "document": note_document("Neuer Inhalt"), "base_revision": self.note.revision}
            ),
            content_type="application/json",
        )
        self.assertEqual(save_response.status_code, 200, save_response.content)
        new_revision = save_response.json()["note"]["revision"]
        self.assertGreater(new_revision, self.note.revision)

        response = self.reader_client.post(self.presence_url())
        self.assertEqual(response.json()["revision"], new_revision)

    def test_leave_removes_presence_immediately(self):
        self.client.post(self.presence_url())
        self.reader_client.post(self.presence_url())
        self.assertEqual(NoteViewerPresence.objects.filter(note=self.note).count(), 2)

        response = self.client.post(self.presence_url(leave=True))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(NoteViewerPresence.objects.filter(note=self.note, user=self.owner).exists())

        viewers = self.reader_client.post(self.presence_url()).json()["viewers"]
        self.assertEqual(viewers, [])

    def test_expired_presence_is_excluded_and_cleaned_up(self):
        NoteViewerPresence.objects.create(
            note=self.note, user=self.reader, present_until=timezone.now() - timedelta(seconds=5)
        )

        response = self.client.post(self.presence_url())
        self.assertEqual(response.json()["viewers"], [])
        self.assertFalse(NoteViewerPresence.objects.filter(note=self.note, user=self.reader).exists())

    def test_ping_requires_note_access(self):
        outsider_client = Client()
        outsider_client.login(username="presence-outsider@example.com", password="secret-12345")

        response = outsider_client.post(self.presence_url())

        self.assertEqual(response.status_code, 404)
        self.assertFalse(NoteViewerPresence.objects.filter(note=self.note, user=self.outsider).exists())

    def test_revoked_access_excludes_stale_row_from_viewer_list(self):
        self.reader_client.post(self.presence_url())
        NoteShare.objects.filter(note=self.note, user=self.reader).delete()

        response = self.client.post(self.presence_url())

        self.assertEqual(response.json()["viewers"], [])

    def test_editor_cannot_trash_note(self):
        editor = User.objects.create_user(
            username="presence-editor@example.com",
            email="presence-editor@example.com",
            password="secret-12345",
            first_name="Editor",
        )
        Profile.objects.create(user=editor, display_name="Editor")
        NoteShare.objects.create(note=self.note, user=editor, role=NoteShare.ROLE_EDITOR)
        editor_client = Client()
        editor_client.login(username="presence-editor@example.com", password="secret-12345")

        response = editor_client.post(
            f"/notes/api/{self.note.id}/actions/",
            data=json.dumps({"action": "trash"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.note.refresh_from_db()
        self.assertIsNone(self.note.deleted_at)

    def test_ping_returns_404_for_reader_after_note_is_trashed(self):
        trash_response = self.client.post(
            f"/notes/api/{self.note.id}/actions/",
            data=json.dumps({"action": "trash"}),
            content_type="application/json",
        )
        self.assertEqual(trash_response.status_code, 200, trash_response.content)

        response = self.reader_client.post(self.presence_url())

        self.assertEqual(response.status_code, 404)

    def test_ping_still_works_for_owner_after_trashing_own_note(self):
        self.client.post(
            f"/notes/api/{self.note.id}/actions/",
            data=json.dumps({"action": "trash"}),
            content_type="application/json",
        )

        response = self.client.post(self.presence_url())

        self.assertEqual(response.status_code, 200, response.content)

    def test_ping_returns_404_after_note_is_permanently_deleted(self):
        purge_response = self.client.post(
            f"/notes/api/{self.note.id}/actions/",
            data=json.dumps({"action": "purge"}),
            content_type="application/json",
        )
        self.assertEqual(purge_response.status_code, 200, purge_response.content)

        response = self.reader_client.post(self.presence_url())

        self.assertEqual(response.status_code, 404)

    def test_ping_returns_404_for_reader_after_share_is_removed(self):
        NoteShare.objects.filter(note=self.note, user=self.reader).delete()

        response = self.reader_client.post(self.presence_url())

        self.assertEqual(response.status_code, 404)

    def test_presence_disabled_when_notes_feature_off(self):
        SystemSettings.objects.create(notes_enabled=False)

        response = self.client.post(self.presence_url())

        self.assertEqual(response.status_code, 503)


class VacationPlannerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="planner@example.com", email="planner@example.com", password="secret-12345"
        )
        Profile.objects.create(user=self.user, display_name="Planner")
        self.other = User.objects.create_user(
            username="other-planner@example.com", email="other-planner@example.com", password="secret-12345"
        )
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
        VacationPeriod.objects.create(
            user=self.user,
            name=VacationPeriod.TARIFURLAUB,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 7),
        )
        VacationPeriod.objects.create(
            user=self.user,
            name=VacationPeriod.SONDERURLAUB,
            start_date=date(2026, 1, 6),
            end_date=date(2026, 1, 8),
        )

        summary = annual_summary(self.user, 2026)

        self.assertEqual(summary["planned_days"], Decimal("3.5"))
        self.assertEqual(summary["remaining_days"], Decimal("-0.5"))
        self.assertTrue(summary["is_overbooked"])

    def test_preview_reports_overlaps_and_missing_years(self):
        VacationPeriod.objects.create(
            user=self.user,
            name=VacationPeriod.TARIFURLAUB,
            start_date=date(2026, 1, 6),
            end_date=date(2026, 1, 8),
        )

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
        VacationPeriod.objects.create(
            user=self.user,
            name=VacationPeriod.TARIFURLAUB,
            start_date=date(2026, 2, 2),
            end_date=date(2026, 2, 3),
            notes="Eigener Hinweis",
        )
        VacationPeriod.objects.create(
            user=self.other,
            name=VacationPeriod.TARIFURLAUB,
            start_date=date(2026, 2, 2),
            end_date=date(2026, 2, 3),
            notes="Fremder Hinweis",
        )

        response = self.client.get("/vacation-planner/?year=2026&month=2")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertContains(response, "Eigener Hinweis")
        self.assertNotContains(response, "Fremder Hinweis")

    def test_empty_planner_with_saved_year_offers_vacation_action(self):
        response = self.client.get("/vacation-planner/?year=2026")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertContains(response, 'id="vacation-period-editor"')
        self.assertContains(response, 'data-vacation-focus="period"')
        self.assertContains(response, "Urlaub planen")
        self.assertNotContains(response, "Jahr zuerst einrichten")

    def test_empty_planner_without_year_points_to_year_setup(self):
        self.vacation_year.delete()

        response = self.client.get("/vacation-planner/?year=2027")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertContains(response, 'id="vacation-year-settings"')
        self.assertContains(response, 'data-vacation-focus="year"', count=3)
        self.assertContains(response, "Jahr zuerst einrichten")
        self.assertContains(response, "Jahr einrichten")
        self.assertNotContains(response, 'data-vacation-focus="period"')

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
        call_command(
            "import_public_holidays", from_year=2026, to_year=2026, subdivision="NW", stdout=StringIO()
        )

        stale.refresh_from_db()
        self.assertGreater(first_count, 0)
        self.assertFalse(stale.active)
        self.assertEqual(
            OfficialHoliday.objects.filter(subdivision="NW", date__year=2026).count(), first_count
        )

    def test_decimal_label_formats_whole_and_half_values(self):
        self.assertEqual(decimal_label(Decimal("1.0")), "1")
        self.assertEqual(decimal_label(Decimal("0.5")), "0,5")
        self.assertEqual(decimal_label(Decimal("2.5")), "2,5")
        self.assertEqual(decimal_label(Decimal("0.0")), "0")

    def test_fallback_holidays_used_when_holidays_package_unavailable(self):
        with patch.dict(sys.modules, {"holidays": None}):
            holidays_list = list(generated_public_holidays(2026, "NW"))

        self.assertTrue(all(holiday["source"] == "fallback" for holiday in holidays_list))
        names = {holiday["name"] for holiday in holidays_list}
        self.assertIn("Neujahr", names)
        self.assertIn("Fronleichnam", names)
        self.assertIn("Allerheiligen", names)
        self.assertNotIn("Mariä Himmelfahrt", names)

    def test_fallback_holidays_are_subdivision_specific(self):
        with patch.dict(sys.modules, {"holidays": None}):
            bavaria_names = {holiday["name"] for holiday in generated_public_holidays(2026, "BY")}
            saxony_names = {holiday["name"] for holiday in generated_public_holidays(2026, "SN")}

        self.assertIn("Mariä Himmelfahrt", bavaria_names)
        self.assertIn("Buß- und Bettag", saxony_names)
        self.assertNotIn("Buß- und Bettag", bavaria_names)

    def test_fallback_easter_based_holidays_match_holidays_package(self):
        easter_based_names = {
            "Karfreitag",
            "Ostermontag",
            "Christi Himmelfahrt",
            "Pfingstmontag",
            "Fronleichnam",
        }
        for year in (2026, 2027, 2028):
            real_dates = {
                holiday["name"]: holiday["date"]
                for holiday in generated_public_holidays(year, "NW")
                if holiday["name"] in easter_based_names
            }
            with patch.dict(sys.modules, {"holidays": None}):
                fallback_dates = {
                    holiday["name"]: holiday["date"]
                    for holiday in generated_public_holidays(year, "NW")
                    if holiday["name"] in easter_based_names
                }
            self.assertEqual(set(real_dates), easter_based_names, year)
            self.assertEqual(fallback_dates, real_dates, year)

    def test_import_public_holidays_covers_multiple_years_and_subdivisions(self):
        imported = import_public_holidays(2026, 2027, subdivisions=["NW", "BY"])

        self.assertGreater(imported, 0)
        for subdivision in ("NW", "BY"):
            for year in (2026, 2027):
                self.assertTrue(
                    OfficialHoliday.objects.filter(
                        subdivision=subdivision, date__year=year, active=True
                    ).exists(),
                    f"{subdivision} {year}",
                )
        self.assertTrue(
            OfficialHoliday.objects.filter(
                subdivision="BY", name="Heilige Drei Könige", date__year=2026
            ).exists()
        )
        self.assertFalse(
            OfficialHoliday.objects.filter(
                subdivision="NW", name="Heilige Drei Könige", date__year=2026
            ).exists()
        )

    def test_calculate_period_splits_required_days_across_year_boundary(self):
        VacationYear.objects.create(
            user=self.user, year=2027, allowance_days=Decimal("25.0"), subdivision="NW"
        )

        calculation = calculate_period(self.user, date(2026, 12, 30), date(2027, 1, 2))

        self.assertEqual(calculation["calendar_days"], 4)
        self.assertEqual(calculation["missing_years"], [])
        per_year = {row["year"]: row for row in calculation["per_year"]}
        self.assertEqual(set(per_year), {2026, 2027})
        self.assertEqual(per_year[2026]["calendar_days"], 2)
        self.assertEqual(per_year[2026]["weekend_days"], 0)
        self.assertEqual(per_year[2026]["required_days"], Decimal("2.0"))
        self.assertEqual(per_year[2027]["calendar_days"], 2)
        self.assertEqual(per_year[2027]["weekend_days"], 1)
        self.assertEqual(per_year[2027]["holiday_count"], 1)
        self.assertEqual(per_year[2027]["required_days"], Decimal("0.0"))
        self.assertEqual(calculation["required_days"], Decimal("2.0"))

    def test_holiday_override_reduces_required_days_and_renames_holiday(self):
        official_holiday = OfficialHoliday.objects.create(
            subdivision="NW",
            date=date(2026, 6, 4),
            name="Fronleichnam",
            day_value=Decimal("1.0"),
            active=True,
        )
        HolidayOverride.objects.create(
            vacation_year=self.vacation_year,
            official_holiday=official_holiday,
            name="Firmenfeiertag",
            day_value=Decimal("0.5"),
        )

        holidays = effective_holidays_for_year(self.vacation_year)
        self.assertEqual(holidays[date(2026, 6, 4)].day_value, Decimal("0.5"))
        self.assertEqual(holidays[date(2026, 6, 4)].names, ("Firmenfeiertag",))

        calculation = calculate_period(self.user, date(2026, 6, 4), date(2026, 6, 4))
        self.assertEqual(calculation["required_days"], Decimal("0.5"))

    def test_holiday_override_can_fully_disable_a_holiday(self):
        official_holiday = OfficialHoliday.objects.create(
            subdivision="NW",
            date=date(2026, 6, 4),
            name="Fronleichnam",
            day_value=Decimal("1.0"),
            active=True,
        )
        HolidayOverride.objects.create(
            vacation_year=self.vacation_year, official_holiday=official_holiday, day_value=Decimal("0.0")
        )

        holidays = effective_holidays_for_year(self.vacation_year)
        self.assertNotIn(date(2026, 6, 4), holidays)

        calculation = calculate_period(self.user, date(2026, 6, 4), date(2026, 6, 4))
        self.assertEqual(calculation["required_days"], Decimal("1.0"))
        self.assertEqual(calculation["holiday_count"], 0)

    def test_month_summary_and_month_calendar_reflect_period_and_holiday(self):
        OfficialHoliday.objects.create(
            subdivision="NW",
            date=date(2026, 6, 4),
            name="Fronleichnam",
            day_value=Decimal("1.0"),
            active=True,
        )
        VacationPeriod.objects.create(
            user=self.user,
            name=VacationPeriod.TARIFURLAUB,
            start_date=date(2026, 6, 8),
            end_date=date(2026, 6, 9),
        )

        summary = month_summary(self.user, 2026, 6)
        self.assertEqual(summary["period_count"], 1)
        self.assertEqual(summary["planned_days"], Decimal("2.0"))
        self.assertEqual(summary["holiday_count"], 1)

        calendar_model = month_calendar(self.user, 2026, 6)
        day_cell = next(
            cell for week in calendar_model["rows"] for cell in week if cell["date"] == date(2026, 6, 4)
        )
        self.assertIsNotNone(day_cell["holiday"])
        self.assertEqual(day_cell["holiday"].names, ("Fronleichnam",))

    def test_vacation_year_save_view_creates_and_updates_allowance(self):
        self.vacation_year.delete()

        response = self.client.post(
            "/vacation-planner/year/",
            {"year": "2027", "allowance_days": "25.5", "subdivision": "BY"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        vacation_year = VacationYear.objects.get(user=self.user, year=2027)
        self.assertEqual(vacation_year.allowance_days, Decimal("25.5"))
        self.assertEqual(vacation_year.subdivision, "BY")
        self.assertContains(response, "Urlaubsjahr gespeichert.")

    def test_vacation_year_save_view_rejects_non_half_step_allowance(self):
        response = self.client.post(
            "/vacation-planner/year/",
            {"year": "2026", "allowance_days": "25.3", "subdivision": "NW"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.vacation_year.refresh_from_db()
        self.assertEqual(self.vacation_year.allowance_days, Decimal("3.0"))
        self.assertContains(response, "Das Urlaubsjahr konnte nicht gespeichert werden.")

    def test_vacation_year_save_view_rejects_negative_allowance(self):
        response = self.client.post(
            "/vacation-planner/year/",
            {"year": "2026", "allowance_days": "-5", "subdivision": "NW"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.vacation_year.refresh_from_db()
        self.assertEqual(self.vacation_year.allowance_days, Decimal("3.0"))
        self.assertContains(response, "Das Urlaubsjahr konnte nicht gespeichert werden.")

    def test_custom_holiday_save_view_creates_half_day_holiday(self):
        response = self.client.post(
            "/vacation-planner/holiday/save/",
            {"year": "2026", "date": "2026-08-14", "name": "Betriebsruhe", "is_half_day": "on"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        holiday = CustomHoliday.objects.get(vacation_year=self.vacation_year, name="Betriebsruhe")
        self.assertEqual(holiday.date, date(2026, 8, 14))
        self.assertEqual(holiday.day_value, Decimal("0.5"))
        self.assertContains(response, "Feiertag gespeichert.")

    def test_custom_holiday_save_view_rejects_date_outside_selected_year(self):
        response = self.client.post(
            "/vacation-planner/holiday/save/",
            {"year": "2026", "date": "2027-01-02", "name": "Falsches Jahr"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(CustomHoliday.objects.filter(name="Falsches Jahr").exists())
        self.assertContains(response, "Eigene Feiertage müssen im gewählten Jahr liegen.")

    def test_custom_holiday_delete_view_only_removes_own_vacation_year_holiday(self):
        other_year = VacationYear.objects.create(
            user=self.other, year=2026, allowance_days=Decimal("20.0"), subdivision="NW"
        )
        own_holiday = CustomHoliday.objects.create(
            vacation_year=self.vacation_year, date=date(2026, 8, 14), name="Eigen", day_value=Decimal("1.0")
        )
        other_holiday = CustomHoliday.objects.create(
            vacation_year=other_year, date=date(2026, 8, 14), name="Fremd", day_value=Decimal("1.0")
        )

        response = self.client.post(
            "/vacation-planner/holiday/delete/",
            {"year": "2026", "holiday_id": other_holiday.id},
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(CustomHoliday.objects.filter(pk=own_holiday.id).exists())
        self.assertTrue(CustomHoliday.objects.filter(pk=other_holiday.id).exists())

    def test_official_holiday_override_save_and_reset(self):
        official_holiday = OfficialHoliday.objects.create(
            subdivision="NW",
            date=date(2026, 6, 4),
            name="Fronleichnam",
            day_value=Decimal("1.0"),
            active=True,
        )

        response = self.client.post(
            "/vacation-planner/official-holiday/save/",
            {
                "year": "2026",
                "official_holiday_id": official_holiday.id,
                "name": "Firmenfeiertag",
                "day_value": "0.5",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        override = HolidayOverride.objects.get(
            vacation_year=self.vacation_year, official_holiday=official_holiday
        )
        self.assertEqual(override.day_value, Decimal("0.5"))
        self.assertEqual(override.name, "Firmenfeiertag")
        self.assertContains(response, "Feiertag angepasst.")

        response = self.client.post(
            "/vacation-planner/official-holiday/reset/",
            {"year": "2026", "official_holiday_id": official_holiday.id},
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(
            HolidayOverride.objects.filter(
                vacation_year=self.vacation_year, official_holiday=official_holiday
            ).exists()
        )
        self.assertContains(response, "Feiertag zurückgesetzt.")

    def test_official_holiday_override_save_rejects_mismatched_subdivision(self):
        official_holiday = OfficialHoliday.objects.create(
            subdivision="BY",
            date=date(2026, 6, 4),
            name="Fronleichnam (Bayern)",
            day_value=Decimal("1.0"),
            active=True,
        )

        response = self.client.post(
            "/vacation-planner/official-holiday/save/",
            {"year": "2026", "official_holiday_id": official_holiday.id, "name": "", "day_value": "0.0"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(HolidayOverride.objects.filter(official_holiday=official_holiday).exists())
        self.assertContains(response, "Der Feiertag konnte nicht gefunden werden.")

    def test_vacation_period_delete_view_ignores_other_users_period(self):
        own_period = VacationPeriod.objects.create(
            user=self.user,
            name=VacationPeriod.TARIFURLAUB,
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 3),
        )
        other_period = VacationPeriod.objects.create(
            user=self.other,
            name=VacationPeriod.TARIFURLAUB,
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 3),
        )

        response = self.client.post(
            "/vacation-planner/period/delete/", {"year": "2026", "period_id": other_period.id}, follow=True
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(VacationPeriod.objects.filter(pk=own_period.id).exists())
        self.assertTrue(VacationPeriod.objects.filter(pk=other_period.id).exists())

    def test_vacation_period_save_view_blocks_when_target_year_not_configured(self):
        response = self.client.post(
            "/vacation-planner/period/save/",
            {
                "year": "2026",
                "name": VacationPeriod.TARIFURLAUB,
                "start_date": "2027-01-04",
                "end_date": "2027-01-05",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(VacationPeriod.objects.filter(user=self.user, start_date=date(2027, 1, 4)).exists())
        self.assertContains(response, "Bitte bestätige zuerst die Urlaubsjahre: 2027.")

    def test_vacation_period_save_view_blocks_overlapping_period(self):
        VacationPeriod.objects.create(
            user=self.user,
            name=VacationPeriod.TARIFURLAUB,
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 4),
        )

        response = self.client.post(
            "/vacation-planner/period/save/",
            {
                "year": "2026",
                "name": VacationPeriod.SONDERURLAUB,
                "start_date": "2026-03-03",
                "end_date": "2026-03-05",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(VacationPeriod.objects.filter(user=self.user, start_date=date(2026, 3, 3)).exists())
        self.assertContains(response, "Der Zeitraum überschneidet sich mit: Tarifurlaub.")

    def test_vacation_period_save_view_updates_existing_period(self):
        period = VacationPeriod.objects.create(
            user=self.user,
            name=VacationPeriod.TARIFURLAUB,
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 3),
        )

        response = self.client.post(
            "/vacation-planner/period/save/",
            {
                "year": "2026",
                "period_id": period.id,
                "name": VacationPeriod.SONDERURLAUB,
                "start_date": "2026-03-02",
                "end_date": "2026-03-04",
                "notes": "Verlängert",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        period.refresh_from_db()
        self.assertEqual(period.name, VacationPeriod.SONDERURLAUB)
        self.assertEqual(period.end_date, date(2026, 3, 4))
        self.assertEqual(period.notes, "Verlängert")
