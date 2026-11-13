import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from app.forms import SystemSettingsForm
from app.models import (
    AuditLogEntry,
    AutomationHeartbeat,
    CalendarEvent,
    CalendarReminder,
    Profile,
    SystemSettings,
)
from app.services import audit_log
from app.services.deployment import deployed_commit


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

    def test_administration_shows_system_health(self):
        self.client.login(username="admin@example.com", password="secret-12345")
        response = self.client.get("/administration/")
        states = {item["label"]: item["state"] for item in response.context["system_health"]["items"]}
        self.assertEqual(states, {"Datenbank": "OK", "Cache": "OK", "Automationen": "Noch nie gelaufen"})
        self.assertNotContains(response, "Letzter Automationslauf")

        AutomationHeartbeat.objects.create(pk=1, finished_at=timezone.now() - timedelta(hours=1))
        response = self.client.get("/administration/")
        self.assertEqual(response.context["system_health"]["items"][2]["state"], "Überfällig")
        self.assertContains(response, "Letzter Automationslauf")

        AutomationHeartbeat.objects.filter(pk=1).update(finished_at=timezone.now())
        response = self.client.get("/administration/")
        self.assertEqual(response.context["system_health"]["items"][2]["state"], "OK")

    @override_settings(
        MAILERS={"default": {"BACKEND": "django.core.mail.backends.smtp.EmailBackend", "OPTIONS": {}}},
        WEB_PUSH_ENABLED=False,
        WEATHER_API_KEY="secret-weather-key",
        CLOUDFLARE_TURNSTILE_REQUIRED=True,
        ADMINS=[],
    )
    def test_administration_shows_configuration_without_values(self):
        self.client.login(username="admin@example.com", password="secret-12345")
        with patch("app.services.deployment.deployed_commit", return_value="a" * 40):
            response = self.client.get("/administration/")
        states = {item["label"]: item["state"] for item in response.context["configuration"]}
        self.assertEqual(
            states,
            {
                "E-Mail-Versand": "SMTP",
                "Web Push": "Fehlt",
                "Wetterdaten": "OpenWeather",
                "Registrierungsschutz": "Turnstile",
                "Fehlermails": "Aus",
            },
        )
        self.assertNotContains(response, "secret-weather-key")
        self.assertContains(response, f'<code title="{"a" * 40}">aaaaaaa</code>', html=True)

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


class AuditLogTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin@example.com", email="admin@example.com", password="secret-12345"
        )
        self.user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=self.admin, display_name="Admin")
        self.profile = Profile.objects.create(user=self.user, display_name="Mira")

    def entries(self, action=None):
        entries = AuditLogEntry.objects.order_by("created_at", "id")
        return entries.filter(action=action) if action else entries

    def test_login_logout_and_failed_logins_are_recorded(self):
        self.client.post("/login/", {"username": "mira@example.com", "password": "falsch"})
        self.client.post("/login/", {"username": "niemand@example.com", "password": "falsch"})
        self.client.post("/login/", {"username": "mira@example.com", "password": "secret-12345"})
        self.client.post("/logout/")

        failed_known, failed_unknown = self.entries(AuditLogEntry.ACTION_LOGIN_FAILED)
        self.assertEqual(failed_known.actor, self.user)
        self.assertIsNone(failed_unknown.actor)
        self.assertEqual(failed_unknown.summary, "Unbekanntes Konto")
        # The typed identifier of an unknown account is never stored.
        self.assertFalse(AuditLogEntry.objects.filter(actor_label__contains="niemand").exists())
        login = self.entries(AuditLogEntry.ACTION_LOGIN).get()
        self.assertEqual(
            (login.actor, login.actor_label, login.ip_address), (self.user, "mira@example.com", "127.0.0.1")
        )
        self.assertTrue(self.entries(AuditLogEntry.ACTION_LOGOUT).filter(actor=self.user).exists())

    @override_settings(LUNORA_CLIENT_IP_HEADER="CF-Connecting-IP")
    def test_client_ip_header_is_used_when_configured(self):
        self.client.post(
            "/login/",
            {"username": "mira@example.com", "password": "secret-12345"},
            HTTP_CF_CONNECTING_IP="203.0.113.7",
        )

        self.assertEqual(self.entries(AuditLogEntry.ACTION_LOGIN).get().ip_address, "203.0.113.7")

    def test_invalid_ip_values_are_dropped(self):
        from django.test import RequestFactory

        from app.services.audit_log import client_ip

        request = RequestFactory().get("/", REMOTE_ADDR="kein-ip")

        self.assertIsNone(client_ip(request))

    def test_security_settings_changes_are_recorded(self):
        from app.services import two_factor

        self.client.force_login(self.user)
        self.client.post("/settings/", {"form_name": "two_factor_setup"})
        secret = self.client.session["two_factor_setup_secret"]
        self.client.post(
            "/settings/",
            {
                "form_name": "two_factor_enable",
                "code": two_factor.totp_code(secret, two_factor.current_step()),
            },
        )
        self.client.post("/settings/", {"form_name": "two_factor_recovery_codes", "password": "secret-12345"})
        self.client.post("/settings/", {"form_name": "two_factor_disable", "password": "secret-12345"})
        self.client.post("/settings/", {"form_name": "session_revoke_others"})
        from django.core.cache import cache

        cache.clear()
        self.client.get("/settings/export/").close()

        self.assertEqual(
            list(self.entries().exclude(action=AuditLogEntry.ACTION_LOGIN).values_list("action", flat=True)),
            [
                AuditLogEntry.ACTION_TWO_FACTOR_ENABLED,
                AuditLogEntry.ACTION_RECOVERY_CODES,
                AuditLogEntry.ACTION_TWO_FACTOR_DISABLED,
                AuditLogEntry.ACTION_SESSIONS_REVOKED,
                AuditLogEntry.ACTION_DATA_EXPORT,
            ],
        )
        self.assertTrue(self.entries().filter(actor=self.user).exists())

    def test_account_deletion_keeps_the_label(self):
        self.client.force_login(self.user)

        self.client.post("/settings/", {"form_name": "account_delete", "password": "secret-12345"})

        entry = self.entries(AuditLogEntry.ACTION_ACCOUNT_DELETED).get()
        self.assertIsNone(entry.actor)
        self.assertEqual(entry.actor_label, "mira@example.com")
        self.assertFalse(self.entries(AuditLogEntry.ACTION_LOGOUT).exists())

    def test_admin_actions_are_recorded_with_changed_switches(self):
        self.client.force_login(self.admin)
        current = SystemSettingsForm(instance=SystemSettings.objects.get_or_create(pk=1)[0]).initial
        data = {name: "on" for name, value in current.items() if value is True}
        data.pop("weather_enabled", None)

        self.client.post("/administration/", {"form_name": "system_settings", **data})
        self.client.post("/administration/", {"form_name": "system_settings", **data})
        self.client.post("/administration/", {"form_name": "force_logout_all"})

        changed = self.entries(AuditLogEntry.ACTION_SETTINGS_CHANGED).get()
        self.assertEqual(changed.summary, "Wetter: aus")
        self.assertEqual(changed.actor, self.admin)
        self.assertTrue(self.entries(AuditLogEntry.ACTION_FORCE_LOGOUT).exists())

    def test_administration_page_lists_and_filters_entries(self):
        audit_log.record(AuditLogEntry.ACTION_LOGIN, actor=self.user)
        audit_log.record(AuditLogEntry.ACTION_LOGIN_FAILED, actor=self.user, summary="")
        self.client.force_login(self.admin)

        response = self.client.get("/administration/")
        self.assertContains(response, "Audit-Log")
        self.assertContains(response, "Fehlgeschlagene Anmeldung")
        self.assertContains(response, 'class="is-warning"')

        warnings = self.client.get("/administration/", {"audit": "warnings"}).context["audit_log"]
        self.assertEqual([item["action"] for item in warnings["items"]], [AuditLogEntry.ACTION_LOGIN_FAILED])

    def test_only_superusers_see_the_audit_log(self):
        self.client.force_login(self.user)

        self.assertEqual(self.client.get("/administration/").status_code, 403)

    def test_old_entries_are_purged_by_the_automations(self):
        from app.services.scheduled_tasks import run_scheduled_tasks

        old = audit_log.record(AuditLogEntry.ACTION_LOGIN, actor=self.user)
        AuditLogEntry.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=181))
        audit_log.record(AuditLogEntry.ACTION_LOGIN, actor=self.user)

        self.assertEqual(run_scheduled_tasks()["audit_log"], {"purged": 1})
        self.assertEqual(AuditLogEntry.objects.count(), 1)

    def test_a_failing_write_does_not_break_the_request(self):
        with patch("app.services.audit_log.AuditLogEntry.objects.create", side_effect=RuntimeError("db")):
            with self.assertLogs("app.services.audit_log", level="ERROR"):
                self.assertIsNone(audit_log.record(AuditLogEntry.ACTION_LOGIN, actor=self.user))


class DeployedCommitTests(TestCase):
    COMMIT = "0123456789abcdef0123456789abcdef01234567"

    def git_dir(self, files):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        directory = Path(temp.name)
        for name, content in files.items():
            (directory / name).parent.mkdir(parents=True, exist_ok=True)
            (directory / name).write_text(content, encoding="ascii")
        return directory

    def test_reads_loose_packed_and_detached_heads(self):
        head_ref = "ref: refs/heads/main\n"
        loose = self.git_dir({"HEAD": head_ref, "refs/heads/main": f"{self.COMMIT}\n"})
        packed = self.git_dir(
            {"HEAD": head_ref, "packed-refs": f"# pack-refs\n{self.COMMIT} refs/heads/main\n"}
        )
        detached = self.git_dir({"HEAD": f"{self.COMMIT}\n"})
        for git_dir in (loose, packed, detached):
            self.assertEqual(deployed_commit(git_dir), self.COMMIT)

    def test_returns_none_without_a_readable_commit(self):
        self.assertIsNone(deployed_commit(self.git_dir({})))
        self.assertIsNone(deployed_commit(self.git_dir({"HEAD": "ref: refs/heads/main"})))
        self.assertIsNone(deployed_commit(self.git_dir({"HEAD": "not a commit"})))
