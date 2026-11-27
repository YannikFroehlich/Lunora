import base64
import json
import time
from unittest.mock import patch
from urllib import error as urlerror
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from app.models import AuditLogEntry, Profile
from app.services.system_settings import get_system_settings
from app.tests.helpers import FakeJsonResponse
from app.views.auth_views import SSO_ID_TOKEN_SESSION_KEY, SSO_SESSION_KEY

ISSUER = "https://auth.example.test/realms/yfserver"
SSO_SETTINGS = {
    "OIDC_ISSUER": ISSUER,
    "OIDC_CLIENT_ID": "lunora",
    "OIDC_CLIENT_SECRET": "client-secret",
    "OIDC_REQUIRED_GROUP": "",
}


def fake_id_token(**overrides):
    claims = {
        "iss": ISSUER,
        "aud": "lunora",
        "exp": time.time() + 300,
        "nonce": "the-nonce",
        "sub": "kc-user-1",
        "email": "ada@example.com",
        "email_verified": True,
        "name": "Ada Lovelace",
    }
    claims.update(overrides)
    claims = {key: value for key, value in claims.items() if value is not None}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"header.{payload}.signature"


@override_settings(**SSO_SETTINGS)
class SingleSignOnTests(TestCase):
    def start_login(self, next_url="/notes/"):
        response = self.client.get(reverse("login_sso"), {"next": next_url})
        session = self.client.session
        login_session = session[SSO_SESSION_KEY]
        login_session["nonce"] = "the-nonce"
        session[SSO_SESSION_KEY] = login_session
        session.save()
        return response, login_session

    def finish_login(self, id_token=None, state=None):
        _, login_session = self.start_login()
        with patch("app.services.oidc.urlrequest.urlopen") as urlopen:
            urlopen.return_value = FakeJsonResponse({"id_token": id_token or fake_id_token()})
            response = self.client.get(
                reverse("login_sso_callback"),
                {"state": state or login_session["state"], "code": "auth-code"},
            )
        return response, urlopen

    def assert_rejected(self, response):
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def signed_in_user_id(self):
        return int(self.client.session["_auth_user_id"])

    def test_login_page_offers_sso(self):
        response = self.client.get(reverse("login"), {"next": "/notes/"})
        self.assertContains(response, reverse("login_sso") + "?next=/notes/")

    @override_settings(OIDC_CLIENT_SECRET="")
    def test_sso_is_hidden_and_unreachable_when_not_configured(self):
        self.assertNotContains(self.client.get(reverse("login")), reverse("login_sso"))
        self.assertEqual(self.client.get(reverse("login_sso")).status_code, 404)
        self.assertEqual(self.client.get(reverse("login_sso_callback")).status_code, 404)

    def test_login_redirects_to_keycloak_with_state_and_pkce(self):
        response, login_session = self.start_login()
        location = urlsplit(response["Location"])
        query = parse_qs(location.query)
        self.assertEqual(
            f"{location.scheme}://{location.netloc}{location.path}",
            f"{ISSUER}/protocol/openid-connect/auth",
        )
        self.assertEqual(query["state"], [login_session["state"]])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["redirect_uri"], ["http://testserver/login/sso/callback/"])
        self.assertEqual(login_session["next"], "/notes/")

    def test_foreign_next_url_falls_back_to_home(self):
        _, login_session = self.start_login(next_url="https://evil.example/")
        self.assertEqual(login_session["next"], "/home/")

    def test_new_user_gets_account_and_profile(self):
        response, urlopen = self.finish_login()
        self.assertRedirects(response, "/notes/", fetch_redirect_response=False)
        user = get_user_model().objects.get(email="ada@example.com")
        self.assertEqual(self.signed_in_user_id(), user.pk)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.profile.display_name, "Ada Lovelace")
        self.assertEqual(user.profile.oidc_subject, "kc-user-1")
        self.assertFalse(user.profile.email_confirmation_pending)
        self.assertIn(SSO_ID_TOKEN_SESSION_KEY, self.client.session)
        token_request = urlopen.call_args.args[0]
        sent = parse_qs(token_request.data.decode())
        self.assertEqual(sent["client_secret"], ["client-secret"])
        self.assertIn("code_verifier", sent)
        self.assertEqual(token_request.get_header("User-agent"), "Lunora")
        self.assertTrue(AuditLogEntry.objects.filter(actor=user).exists())

    def test_existing_account_is_linked_by_verified_email_and_confirmed(self):
        user = get_user_model().objects.create_user("ada@example.com", "Ada@Example.com", "pw")
        Profile.objects.create(user=user, display_name="Ada", email_confirmation_pending=True)
        self.finish_login()
        self.assertEqual(self.signed_in_user_id(), user.pk)
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.oidc_subject, "kc-user-1")
        self.assertFalse(user.profile.email_confirmation_pending)
        self.assertTrue(user.has_usable_password())

    def test_unverified_email_does_not_take_over_an_account(self):
        user = get_user_model().objects.create_user("ada@example.com", "ada@example.com", "pw")
        get_user_model().objects.create_user("kc-user-1")
        self.finish_login(fake_id_token(email_verified=False))
        self.assertNotEqual(self.signed_in_user_id(), user.pk)
        new_user = get_user_model().objects.get(pk=self.signed_in_user_id())
        self.assertEqual((new_user.username, new_user.email), ("kc-user-1-2", ""))

    def test_returning_user_is_found_by_subject(self):
        user = get_user_model().objects.create_user("renamed")
        Profile.objects.create(user=user, display_name="R", oidc_subject="kc-user-1")
        self.finish_login(fake_id_token(email="new@example.com"))
        self.assertEqual(self.signed_in_user_id(), user.pk)
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_admin_group_controls_admin_rights(self):
        self.finish_login(fake_id_token(groups=["admins"]))
        user = get_user_model().objects.get()
        self.assertTrue(user.is_staff and user.is_superuser)
        self.client.logout()
        self.finish_login(fake_id_token(groups=[]))
        user.refresh_from_db()
        self.assertFalse(user.is_staff or user.is_superuser)

    @override_settings(OIDC_REQUIRED_GROUP="dev-access")
    def test_required_group_is_enforced(self):
        response, _ = self.finish_login(fake_id_token(groups=["admins"]))
        self.assert_rejected(response)
        self.finish_login(fake_id_token(groups=["dev-access"]))
        self.assertIn("_auth_user_id", self.client.session)

    def test_disabled_normal_login_still_admits_admins_only(self):
        system_settings = get_system_settings()
        system_settings.normal_login_enabled = False
        system_settings.save()
        response, _ = self.finish_login(fake_id_token(groups=[]))
        self.assert_rejected(response)
        self.finish_login(fake_id_token(groups=["admins"]))
        self.assertIn("_auth_user_id", self.client.session)

    def test_inactive_user_is_rejected(self):
        user = get_user_model().objects.create_user("gone", is_active=False)
        Profile.objects.create(user=user, display_name="G", oidc_subject="kc-user-1")
        response, _ = self.finish_login()
        self.assert_rejected(response)

    def test_state_mismatch_is_rejected_without_token_request(self):
        response, urlopen = self.finish_login(state="forged")
        self.assert_rejected(response)
        urlopen.assert_not_called()

    def test_cancelled_sign_in_is_rejected(self):
        _, login_session = self.start_login()
        response = self.client.get(
            reverse("login_sso_callback"),
            {"state": login_session["state"], "error": "access_denied"},
        )
        self.assert_rejected(response)

    def test_invalid_id_tokens_are_rejected(self):
        for id_token in (
            fake_id_token(nonce="replayed"),
            fake_id_token(iss="https://other.example/realms/x"),
            fake_id_token(aud=["account"]),
            fake_id_token(exp=time.time() - 10),
            fake_id_token(sub=None),
            "not-a-jwt",
            "header.W10.signature",
        ):
            with self.subTest(id_token=id_token):
                response, _ = self.finish_login(id_token)
                self.assert_rejected(response)

    def test_unreachable_or_odd_token_endpoint_is_rejected(self):
        for side_effect in (urlerror.URLError("down"), [FakeJsonResponse({"access_token": "x"})]):
            with self.subTest(side_effect=side_effect):
                _, login_session = self.start_login()
                with patch("app.services.oidc.urlrequest.urlopen", side_effect=side_effect):
                    response = self.client.get(
                        reverse("login_sso_callback"),
                        {"state": login_session["state"], "code": "auth-code"},
                    )
                self.assert_rejected(response)

    def test_logout_also_ends_keycloak_session(self):
        id_token = fake_id_token()
        self.finish_login(id_token)
        response = self.client.post(reverse("logout"))
        query = parse_qs(urlsplit(response["Location"]).query)
        self.assertTrue(response["Location"].startswith(f"{ISSUER}/protocol/openid-connect/logout"))
        self.assertEqual(query["id_token_hint"], [id_token])
        self.assertEqual(query["post_logout_redirect_uri"], ["http://testserver/login/"])
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_password_logout_is_unchanged(self):
        user = get_user_model().objects.create_user("local", password="pw")
        self.client.force_login(user)
        response = self.client.post(reverse("logout"))
        self.assertFalse(response["Location"].startswith(ISSUER))
