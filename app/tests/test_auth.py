from unittest.mock import patch
from urllib.error import URLError

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from app.models import (
    Profile,
)
from app.services import two_factor
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

        self.assertFalse(verify_registration_token("valid-token"))

    @patch("app.services.turnstile.urlopen", side_effect=URLError("offline"))
    def test_fails_closed_when_siteverify_is_unavailable(self, _mocked_urlopen):
        from app.services.turnstile import verify_registration_token

        self.assertFalse(verify_registration_token("valid-token"))


class TwoFactorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="lea", email="lea@example.com", password="pw-12345!")
        self.profile = Profile.objects.create(user=self.user, display_name="Lea")

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
