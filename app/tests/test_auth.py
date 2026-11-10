from importlib import import_module
from unittest.mock import patch
from urllib.error import URLError

from django.apps import apps
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase, override_settings

from app.models import Note, NoteAttachment, Profile, Task, UserNotification, UserSession
from app.services import two_factor
from app.services.notifications import send_pending_user_notification_emails
from app.services.user_sessions import device_label
from app.tests.helpers import FakeJsonResponse


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

        with self.assertLogs("app.services.turnstile", "WARNING") as logs:
            self.assertFalse(verify_registration_token("valid-token"))
        self.assertIn("attacker.example", logs.output[0])

    @patch("app.services.turnstile.urlopen")
    def test_logs_cloudflare_error_codes_on_rejection(self, mocked_urlopen):
        from app.services.turnstile import verify_registration_token

        mocked_urlopen.return_value = FakeJsonResponse(
            {"success": False, "error-codes": ["timeout-or-duplicate"]}
        )

        with self.assertLogs("app.services.turnstile", "WARNING") as logs:
            self.assertFalse(verify_registration_token("valid-token"))
        self.assertIn("timeout-or-duplicate", logs.output[0])

    def test_logs_missing_token(self):
        from app.services.turnstile import verify_registration_token

        with self.assertLogs("app.services.turnstile", "WARNING"):
            self.assertFalse(verify_registration_token(""))

    @patch("app.services.turnstile.urlopen", side_effect=URLError("offline"))
    def test_fails_closed_when_siteverify_is_unavailable(self, _mocked_urlopen):
        from app.services.turnstile import verify_registration_token

        with self.assertLogs("app.services.turnstile", "WARNING"):
            self.assertFalse(verify_registration_token("valid-token"))


class PasswordResetLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        User.objects.create_user("mira", "mira@example.com", "pw")

    def request_reset(self, email, **extra):
        return self.client.post("/password-reset/", {"email": email}, **extra)

    def test_address_gets_three_mails_and_a_fourth_request_looks_the_same(self):
        for _ in range(3):
            self.assertRedirects(self.request_reset("mira@example.com"), "/password-reset/done/")
        self.assertEqual(len(mail.outbox), 3)

        with self.assertLogs("app.views.auth_views", level="WARNING"):
            response = self.request_reset("mira@example.com")

        self.assertRedirects(response, "/password-reset/done/")
        self.assertEqual(len(mail.outbox), 3)

    def test_spelling_of_the_address_does_not_reset_its_limit(self):
        for email in ("mira@example.com", "MIRA@example.com", "Mira@Example.com"):
            self.request_reset(email)
        with self.assertLogs("app.views.auth_views", level="WARNING"):
            self.request_reset("mira@EXAMPLE.com")
        self.assertEqual(len(mail.outbox), 3)

    def test_unknown_address_gets_the_same_answer_past_its_limit(self):
        with self.assertLogs("app.views.auth_views", level="WARNING"):
            responses = [self.request_reset("nobody@example.com") for _ in range(4)]
        for response in responses:
            self.assertRedirects(response, "/password-reset/done/")
        self.assertEqual(len(mail.outbox), 0)

    def test_one_address_at_its_limit_leaves_others_alone(self):
        User.objects.create_user("lukas", "lukas@example.com", "pw")
        with self.assertLogs("app.views.auth_views", level="WARNING"):
            for _ in range(4):
                self.request_reset("mira@example.com")
        self.request_reset("lukas@example.com")
        self.assertEqual(mail.outbox[-1].to, ["lukas@example.com"])

    def test_one_client_is_stopped_after_ten_requests_another_is_not(self):
        for number in range(11):
            User.objects.create_user(f"user{number}", f"user{number}@example.com", "pw")
        for number in range(10):
            self.request_reset(f"user{number}@example.com", REMOTE_ADDR="203.0.113.1")
        self.assertEqual(len(mail.outbox), 10)

        with self.assertLogs("app.views.auth_views", level="WARNING"):
            self.request_reset("user10@example.com", REMOTE_ADDR="203.0.113.1")
        self.assertEqual(len(mail.outbox), 10)

        self.request_reset("user10@example.com", REMOTE_ADDR="203.0.113.2")
        self.assertEqual(len(mail.outbox), 11)

    def test_visitors_without_a_known_address_do_not_share_a_counter(self):
        # Behind Nginx's Unix socket REMOTE_ADDR is empty unless LUNORA_CLIENT_IP_HEADER is set.
        for number in range(11):
            User.objects.create_user(f"user{number}", f"user{number}@example.com", "pw")
            self.request_reset(f"user{number}@example.com", REMOTE_ADDR="")
        self.assertEqual(len(mail.outbox), 11)

    def test_address_is_not_stored_in_the_cache_key(self):
        with patch("app.services.throttle.cache") as mocked_cache:
            mocked_cache.add.return_value = True
            self.request_reset("mira@example.com")
        keys = [call.args[0] for call in mocked_cache.add.call_args_list]
        self.assertTrue(keys)
        self.assertFalse(any("mira" in key for key in keys))


class LoginClientLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_failures_across_accounts_lock_that_client_only(self):
        User.objects.create_user(username="mira", email="mira@example.com", password="secret-12345")
        correct = {"username": "mira@example.com", "password": "secret-12345"}

        with patch("app.forms.LOGIN_CLIENT_ATTEMPT_LIMIT", 3):
            for number in range(3):
                self.client.post(
                    "/login/",
                    {"username": f"nobody{number}@example.com", "password": "falsch"},
                    REMOTE_ADDR="203.0.113.7",
                )
            locked = self.client.post("/login/", correct, REMOTE_ADDR="203.0.113.7")
            elsewhere = self.client.post("/login/", correct, REMOTE_ADDR="203.0.113.8")

        self.assertContains(locked, "Zu viele fehlgeschlagene Anmeldeversuche")
        self.assertFalse(locked.wsgi_request.user.is_authenticated)
        self.assertEqual(elsewhere.status_code, 302)

    def test_count_hit_counts_atomically_and_restarts_after_expiry(self):
        from app.services.throttle import count_hit

        self.assertEqual([count_hit("zaehler", 60) for _ in range(3)], [1, 2, 3])
        with patch("app.services.throttle.cache") as mocked_cache:
            mocked_cache.add.return_value = False
            mocked_cache.incr.side_effect = ValueError
            self.assertEqual(count_hit("zaehler", 60), 1)


class TwoFactorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="lea", email="lea@example.com", password="pw-12345!")
        self.profile = Profile.objects.create(user=self.user, display_name="Lea")

    def test_digits_of_other_scripts_are_a_wrong_code_not_an_error(self):
        self.assertIsNone(two_factor.matching_step(two_factor.generate_secret(), "١٢٣٤٥٦"))

    def _enable(self):
        secret = two_factor.generate_secret()
        codes = two_factor.enable(
            self.profile, secret, two_factor.totp_code(secret, two_factor.current_step())
        )
        # Forget the setup step so the current code can be used for a login.
        self.profile.totp_last_step = None
        self.profile.save(update_fields=["totp_last_step"])
        return secret, codes

    def _password_step(self, next_url=""):
        url = f"/login/?next={next_url}" if next_url else "/login/"
        return self.client.post(url, {"username": "lea@example.com", "password": "pw-12345!"})

    def test_totp_matches_rfc_6238_test_vector(self):
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # base32 of "12345678901234567890"
        self.assertEqual(two_factor.totp_code(secret, 1), "287082")
        self.assertEqual(two_factor.matching_step(secret, "287 082", now=59), 1)
        self.assertIsNone(two_factor.matching_step(secret, "287082", now=59, after_step=1))
        self.assertIsNone(two_factor.matching_step(secret, "abcdef", now=59))

    def test_qr_code_is_a_compact_svg(self):
        svg = two_factor.qr_code_svg(two_factor.provisioning_uri(self.user, two_factor.generate_secret()))
        self.assertTrue(svg.startswith("<svg"))
        self.assertLess(len(svg), 30_000)

    def test_setup_in_settings_requires_a_valid_code_and_shows_recovery_codes_once(self):
        self.client.login(username="lea", password="pw-12345!")
        self.client.post("/settings/", {"form_name": "two_factor_setup"})
        secret = self.client.session["two_factor_setup_secret"]
        response = self.client.get("/settings/")
        self.assertContains(response, secret)
        self.assertContains(response, "<svg")

        self.client.post("/settings/", {"form_name": "two_factor_enable", "code": "000000"})
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.totp_enabled_at)

        code = two_factor.totp_code(secret, two_factor.current_step())
        response = self.client.post(
            "/settings/", {"form_name": "two_factor_enable", "code": code}, follow=True
        )
        self.profile.refresh_from_db()
        self.assertIsNotNone(self.profile.totp_enabled_at)
        self.assertEqual(len(response.context["two_factor_new_codes"]), two_factor.RECOVERY_CODE_COUNT)
        self.assertEqual(len(self.profile.totp_recovery_codes), two_factor.RECOVERY_CODE_COUNT)
        self.assertNotIn(response.context["two_factor_new_codes"][0], self.profile.totp_recovery_codes)
        self.assertIsNone(self.client.get("/settings/").context["two_factor_new_codes"])

    def test_login_without_two_factor_is_unchanged(self):
        response = self._password_step()
        self.assertRedirects(response, "/home/", fetch_redirect_response=False)
        self.assertIn("_auth_user_id", self.client.session)

    def test_login_requires_code_and_rejects_replay(self):
        secret, _codes = self._enable()

        response = self._password_step(next_url="/notes/")
        self.assertRedirects(response, "/login/zwei-faktor/")
        self.assertNotIn("_auth_user_id", self.client.session)

        code = two_factor.totp_code(secret, two_factor.current_step())
        response = self.client.post("/login/zwei-faktor/", {"code": code})
        self.assertRedirects(response, "/notes/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))

        self.client.logout()
        self._password_step()
        response = self.client.post("/login/zwei-faktor/", {"code": code})
        self.assertContains(response, "bereits verwendet")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_recovery_code_works_exactly_once(self):
        _secret, codes = self._enable()

        self._password_step()
        self.client.post("/login/zwei-faktor/", {"code": codes[0].upper()})
        self.assertIn("_auth_user_id", self.client.session)
        self.profile.refresh_from_db()
        self.assertEqual(len(self.profile.totp_recovery_codes), two_factor.RECOVERY_CODE_COUNT - 1)

        self.client.logout()
        self._password_step()
        self.client.post("/login/zwei-faktor/", {"code": codes[0]})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_wrong_codes_lock_the_account_across_login_attempts(self):
        secret, _codes = self._enable()
        for _ in range(5):
            self._password_step()
            self.client.post("/login/zwei-faktor/", {"code": "000000"})

        self._password_step()
        code = two_factor.totp_code(secret, two_factor.current_step())
        response = self.client.post("/login/zwei-faktor/", {"code": code})

        self.assertContains(response, "Zu viele falsche Codes")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_attempt_is_counted_before_the_code_is_checked(self):
        # Parallel requests must see each other's attempts, so the count can't wait for the check.
        self._enable()
        self._password_step()
        key = f"two-factor-attempts:{self.user.pk}"
        seen = []

        def verify(_profile, _code):
            seen.append(cache.get(key))
            return False

        with patch("app.views.auth_views.two_factor.verify_login_code", side_effect=verify):
            self.client.post("/login/zwei-faktor/", {"code": "123456"})

        self.assertEqual(seen, [1])

    def test_wrong_code_warns_the_owner_once_per_window_even_with_email_off(self):
        self._enable()
        self.profile.notify_email = False
        self.profile.save(update_fields=["notify_email"])
        for _ in range(2):
            self._password_step()
            self.client.post("/login/zwei-faktor/", {"code": "abcdef"})

        alerts = UserNotification.objects.filter(
            recipient=self.user, kind=UserNotification.KIND_SECURITY_ALERT
        )
        self.assertEqual(alerts.count(), 1)
        send_pending_user_notification_emails(include_note_shares=False, include_weather=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("falschem Bestätigungscode", mail.outbox[0].subject)

        viewer = Client()
        viewer.force_login(self.user)
        self.assertContains(viewer.get("/notifications/"), "falschem Bestätigungscode")

    def test_enabling_signs_out_other_devices(self):
        phone = Client()
        phone.force_login(self.user)
        phone.get("/home/")
        self.client.force_login(self.user)
        self.client.get("/home/")
        self.client.post("/settings/", {"form_name": "two_factor_setup"})
        secret = self.client.session["two_factor_setup_secret"]

        code = two_factor.totp_code(secret, two_factor.current_step())
        self.client.post("/settings/", {"form_name": "two_factor_enable", "code": code})

        self.assertRedirects(phone.get("/home/"), "/login/?next=/home/")
        self.assertEqual(self.client.get("/home/").status_code, 200)

    def test_code_page_without_password_step_redirects_to_login(self):
        self.assertRedirects(self.client.get("/login/zwei-faktor/"), "/login/")

    def test_admin_login_goes_through_lunora_login(self):
        response = self.client.get("/admin/login/?next=/admin/")
        self.assertRedirects(response, "/login/?next=/admin/", fetch_redirect_response=False)

    def test_disable_and_new_recovery_codes_require_password(self):
        _secret, codes = self._enable()
        self.client.force_login(self.user)

        self.client.post("/settings/", {"form_name": "two_factor_disable", "password": "falsch"})
        self.profile.refresh_from_db()
        self.assertIsNotNone(self.profile.totp_enabled_at)

        response = self.client.post(
            "/settings/", {"form_name": "two_factor_recovery_codes", "password": "pw-12345!"}, follow=True
        )
        self.assertNotIn(codes[0], response.context["two_factor_new_codes"])

        self.client.post("/settings/", {"form_name": "two_factor_disable", "password": "pw-12345!"})
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.totp_enabled_at)
        self.assertEqual((self.profile.totp_secret, self.profile.totp_recovery_codes), ("", []))


FIREFOX_WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0"
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)


class DeviceSessionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="lea", email="lea@example.com", password="pw-12345!")
        Profile.objects.create(user=self.user, display_name="Lea")
        self.laptop = Client(HTTP_USER_AGENT=FIREFOX_WINDOWS)
        self.phone = Client(HTTP_USER_AGENT=SAFARI_IPHONE)
        for client in (self.laptop, self.phone):
            client.post("/login/", {"username": "lea", "password": "pw-12345!"})

    def test_device_label_names_browser_and_system(self):
        self.assertEqual(device_label(FIREFOX_WINDOWS), "Firefox unter Windows")
        self.assertEqual(device_label(SAFARI_IPHONE), "Safari unter iPhone")
        self.assertEqual(
            device_label(
                "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/140.0 Safari/537.36 Edg/140.0"
            ),
            "Edge unter Windows",
        )
        self.assertEqual(device_label(""), "Unbekanntes Gerät")

    def test_settings_list_signed_in_devices_and_mark_current_one(self):
        devices = self.laptop.get("/settings/").context["device_sessions"]

        self.assertEqual(
            sorted((device["label"], device["is_current"]) for device in devices),
            [("Firefox unter Windows", True), ("Safari unter iPhone", False)],
        )

    def test_revoking_a_device_signs_it_out_but_not_the_current_one(self):
        devices = self.laptop.get("/settings/").context["device_sessions"]
        phone = next(device for device in devices if not device["is_current"])
        current = next(device for device in devices if device["is_current"])

        self.laptop.post("/settings/", {"form_name": "session_revoke", "session_id": current["id"]})
        self.laptop.post("/settings/", {"form_name": "session_revoke", "session_id": phone["id"]})

        self.assertRedirects(self.phone.get("/home/"), "/login/?next=/home/")
        self.assertEqual(self.laptop.get("/home/").status_code, 200)
        self.assertEqual(UserSession.objects.filter(user=self.user).count(), 1)

    def test_revoke_others_ends_every_other_session_of_the_user_only(self):
        tablet = Client()
        tablet.force_login(self.user)
        tablet.get("/home/")
        other = User.objects.create_user(username="tom", email="tom@example.com", password="pw-12345!")
        Profile.objects.create(user=other, display_name="Tom")
        stranger = Client()
        stranger.post("/login/", {"username": "tom", "password": "pw-12345!"})

        self.laptop.post("/settings/", {"form_name": "session_revoke_others"})

        self.assertRedirects(self.phone.get("/home/"), "/login/?next=/home/")
        self.assertRedirects(tablet.get("/home/"), "/login/?next=/home/")
        self.assertEqual(self.laptop.get("/home/").status_code, 200)
        self.assertEqual(stranger.get("/home/").status_code, 200)

    def test_backfill_migration_tracks_signed_in_sessions_that_have_no_row(self):
        tracked_keys = {self.laptop.session.session_key, self.phone.session.session_key}
        UserSession.objects.all().delete()
        Client().get("/login/")  # an anonymous session must stay untracked

        import_module("app.migrations.0063_track_existing_sessions").track_existing_sessions(apps, None)

        self.assertEqual(
            set(UserSession.objects.filter(user=self.user).values_list("session_key", flat=True)),
            tracked_keys,
        )
        self.assertEqual(UserSession.objects.count(), 2)

    def test_login_request_itself_is_tracked(self):
        self.assertEqual(
            set(UserSession.objects.filter(user=self.user).values_list("session_key", flat=True)),
            {self.laptop.session.session_key, self.phone.session.session_key},
        )

    def test_other_users_devices_cannot_be_revoked(self):
        other = User.objects.create_user(username="tom", email="tom@example.com", password="pw-12345!")
        other_client = Client()
        other_client.post("/login/", {"username": "tom", "password": "pw-12345!"})
        other_row = UserSession.objects.get(user=other)

        self.laptop.post("/settings/", {"form_name": "session_revoke", "session_id": other_row.pk})

        self.assertEqual(other_client.get("/home/").status_code, 200)

    def test_logged_out_sessions_disappear_from_the_list(self):
        self.phone.post("/logout/")

        devices = self.laptop.get("/settings/").context["device_sessions"]

        self.assertEqual([device["label"] for device in devices], ["Firefox unter Windows"])


class AccountDeletionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="lea", email="lea@example.com", password="pw-12345!")
        self.tom = User.objects.create_user(username="tom", email="tom@example.com", password="pw-12345!")
        Profile.objects.create(user=self.user, display_name="Lea")
        self.client.login(username="lea", password="pw-12345!")

    def test_wrong_password_keeps_the_account(self):
        response = self.client.post(
            "/settings/", {"form_name": "account_delete", "password": "falsch"}, follow=True
        )

        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.assertIn("Passwort ist falsch", str(list(response.context["messages"])[0]))

    def test_deletion_removes_user_data_and_files_and_signs_out(self):
        note = Note.objects.create(owner=self.user, title="Privat")
        attachment = NoteAttachment.objects.create(
            note=note,
            uploaded_by=self.user,
            file=SimpleUploadedFile("geheim.txt", b"geheim"),
            kind=NoteAttachment.KIND_FILE,
            original_name="geheim.txt",
            content_type="text/plain",
            size=6,
        )
        file_name = attachment.file.name
        storage = attachment.file.storage
        Task.objects.create(user=self.user, title="Hausaufgaben")
        toms_note = Note.objects.create(owner=self.tom, title="Toms Notiz")

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/settings/", {"form_name": "account_delete", "password": "pw-12345!"}
            )

        self.assertRedirects(response, "/login/", fetch_redirect_response=False)
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(Note.objects.filter(pk=note.pk).exists())
        self.assertFalse(Task.objects.filter(title="Hausaufgaben").exists())
        self.assertFalse(storage.exists(file_name))
        self.assertTrue(Note.objects.filter(pk=toms_note.pk).exists())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_superusers_cannot_delete_themselves_here(self):
        admin = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="pw-12345!"
        )
        self.client.force_login(admin)

        response = self.client.get("/settings/")
        self.assertContains(response, "Administratorkonten können hier nicht gelöscht werden")
        self.client.post("/settings/", {"form_name": "account_delete", "password": "pw-12345!"})

        self.assertTrue(User.objects.filter(pk=admin.pk).exists())
