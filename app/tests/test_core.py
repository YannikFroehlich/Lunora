import hashlib
import json
import mimetypes
import os
import re
from datetime import datetime
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import default_storage
from django.test import SimpleTestCase, TestCase, override_settings

from app.models import Profile, SystemSettings
from app.services.note_files import private_note_storage
from app.templatetags.static_versioning import versioned_static
from app.view_models import (
    _dashboard_greeting,
    _dashboard_moment_icon,
    _dashboard_moment_label,
    _dashboard_tool_shortcuts,
)
from lunora.settings import BASE_DIR, database_config


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

    def test_nginx_serves_root_favicon_without_reaching_django(self):
        nginx_config = (BASE_DIR / "deploy" / "nginx-lunora.conf").read_text(encoding="utf-8")

        self.assertIn("location = /favicon.ico {", nginx_config)
        self.assertIn("alias /srv/lunora/app/staticfiles/img/favicon.ico;", nginx_config)
        self.assertTrue((BASE_DIR / "app" / "static" / "img" / "favicon.ico").is_file())

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

    def test_vendored_katex_stylesheet_matches_installed_katex(self):
        # KaTeX 0.18+ prefixes its CSS classes, so a stylesheet from another version than the
        # bundled JS breaks every formula. Re-vendor with `npm run vendor:katex` after a bump.
        katex_version = json.loads((BASE_DIR / "package.json").read_text(encoding="utf-8"))["dependencies"][
            "katex"
        ]
        vendor_dir = BASE_DIR / "app" / "static" / "vendor" / "katex"
        katex_css = (vendor_dir / "katex.min.css").read_text(encoding="utf-8")
        notes_template = (BASE_DIR / "app" / "templates" / "app" / "notes.html").read_text(encoding="utf-8")
        font_urls = re.findall(r"url\(([^)]+)\)", katex_css)

        self.assertIn(f'.katex-version:after{{content:"{katex_version}"}}', katex_css)
        self.assertIn("{% versioned_static 'vendor/katex/katex.min.css' %}", notes_template)
        self.assertNotIn("cdn.jsdelivr.net", notes_template)
        self.assertTrue(font_urls)
        for font_url in font_urls:
            path, _, query = font_url.partition("?")
            self.assertEqual(query, f"v=katex-{katex_version}", font_url)
            self.assertTrue((vendor_dir / path).is_file(), font_url)

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
            r'import\(["`]\./chunks/math-renderer-[A-Za-z0-9_-]+\.js["`]\)',
        )

        notes_template = (BASE_DIR / "app" / "templates" / "app" / "notes.html").read_text(encoding="utf-8")
        self.assertIn(
            '<script src="{% versioned_static \'js/bundles/notes.js\' %}" type="module"></script>',
            notes_template,
        )


class DesignTokenTests(SimpleTestCase):
    """Guard the shared design tokens in base.css against per-page drift."""

    CSS_DIR = BASE_DIR / "app" / "static" / "css"
    # Below this size a rem font-size is UI text and must come from the --text-* scale;
    # larger values are display sizes (hero titles, clock, big numbers).
    DISPLAY_FONT_SIZE_REM = 2.2

    def css_lines(self, *, include_base=True):
        for path in sorted(self.CSS_DIR.glob("*.css")):
            if path.name == "base.css" and not include_base:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                yield f"{path.name}:{number}", line

    def test_page_stylesheets_do_not_override_the_profile_accent(self):
        offenders = [
            location
            for location, line in self.css_lines(include_base=False)
            if re.search(r"--color-accent(-strong)?\s*:", line)
        ]

        self.assertEqual(offenders, [], "Pages must follow the profile accent from base.css.")

    def test_accent_tints_are_derived_from_the_accent_variable(self):
        hard_coded_accent = re.compile(
            r"rgba?\(\s*(181,\s*150,\s*104|194,\s*162,\s*118|190,\s*148,\s*85)\b|#(c2a276|b59668|9e8055)\b",
            re.IGNORECASE,
        )
        offenders = [
            location
            for location, line in self.css_lines()
            if hard_coded_accent.search(line) and not re.match(r"\s*--color-accent:", line)
        ]

        self.assertEqual(
            offenders,
            [],
            "Use color-mix(in srgb, var(--color-accent) N%, transparent) instead of a fixed accent tone.",
        )

    def test_ui_font_sizes_use_the_type_scale(self):
        offenders = [
            location
            for location, line in self.css_lines()
            for value in re.findall(r"font-size:\s*(\d*\.?\d+)rem", line)
            if 0 < float(value) < self.DISPLAY_FONT_SIZE_REM
        ]

        self.assertEqual(offenders, [], "Use a --text-* token from base.css for UI font sizes.")


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
        self.assertEqual(config["OPTIONS"]["transaction_mode"], "IMMEDIATE")

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


class PrivateMediaStorageTests(SimpleTestCase):
    def test_storage_follows_private_media_root_override(self):
        with TemporaryDirectory() as private_root, override_settings(PRIVATE_MEDIA_ROOT=private_root):
            self.assertEqual(private_note_storage.location, os.path.abspath(private_root))

    def test_test_runner_keeps_uploads_out_of_real_media_folders(self):
        self.assertNotEqual(os.path.abspath(private_note_storage.location), str(BASE_DIR / "private_media"))
        self.assertNotEqual(os.path.abspath(default_storage.location), str(BASE_DIR / "media"))


class CommandPaletteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="lea", email="lea@example.com", password="pw-12345!")

    def test_palette_is_rendered_for_signed_in_users_only(self):
        self.assertNotContains(self.client.get("/login/"), "data-command-palette")

        self.client.force_login(self.user)
        response = self.client.get("/home/")

        self.assertContains(response, "data-command-palette")
        self.assertContains(response, 'aria-keyshortcuts="Control+K Meta+K"')
        self.assertContains(response, 'href="/settings/#settings-security"')
        self.assertNotContains(response, 'id="command-administration"')

    def test_palette_hides_disabled_features_and_shows_admin_for_superusers(self):
        SystemSettings.objects.update_or_create(
            pk=1, defaults={"notes_enabled": False, "tools_enabled": False}
        )
        admin = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="pw-12345!"
        )
        self.client.force_login(admin)

        response = self.client.get("/home/")

        self.assertNotContains(response, 'id="command-notes"')
        self.assertNotContains(response, 'id="command-vocabulary"')
        self.assertContains(response, 'id="command-tasks"')
        self.assertContains(response, 'id="command-administration"')
