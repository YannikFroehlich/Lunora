import hashlib
import json
import secrets
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase

from app.models import AuditLogEntry, Passkey, Profile, UserNotification
from app.services import passkeys, two_factor
from app.services.data_export import _account
from app.services.user_sessions import device_label

SITE = {"rp_id": "testserver", "origin": "http://testserver"}


def _cbor(value):
    """Encode the few CBOR types an authenticator sends (the service only ever decodes)."""

    def head(major, number):
        if number < 24:
            return bytes([major << 5 | number])
        if number < 256:
            return bytes([major << 5 | 24, number])
        return bytes([major << 5 | 25]) + number.to_bytes(2, "big")

    if isinstance(value, int):
        return head(0, value) if value >= 0 else head(1, -1 - value)
    if isinstance(value, bytes):
        return head(2, len(value)) + value
    if isinstance(value, str):
        return head(3, len(value.encode())) + value.encode()
    return head(5, len(value)) + b"".join(_cbor(key) + _cbor(item) for key, item in value.items())


class FakeAuthenticator:
    """A software passkey: answers the options the pages hand to passkeys.js, as a browser would."""

    def __init__(self, *, origin=SITE["origin"], rp_id=SITE["rp_id"], use_rsa=False):
        self.origin = origin
        self.rp_id = rp_id
        self.key = (
            rsa.generate_private_key(public_exponent=65537, key_size=2048)
            if use_rsa
            else ec.generate_private_key(ec.SECP256R1())
        )
        self.credential_id = secrets.token_bytes(32)
        self.counter = 0

    def _client_data(self, kind, challenge):
        return json.dumps({"type": kind, "challenge": challenge, "origin": self.origin}).encode()

    def _auth_data(self, flags, attested=b""):
        rp_hash = hashlib.sha256(self.rp_id.encode()).digest()
        return rp_hash + bytes([flags]) + self.counter.to_bytes(4, "big") + attested

    def _cose_key(self):
        numbers = self.key.public_key().public_numbers()
        if isinstance(self.key, rsa.RSAPrivateKey):
            return {1: 3, 3: -257, -1: numbers.n.to_bytes(256, "big"), -2: numbers.e.to_bytes(3, "big")}
        return {1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")}

    def _answer(self, **response):
        return json.dumps(
            {
                "id": passkeys._b64encode(self.credential_id),
                "response": {name: passkeys._b64encode(value) for name, value in response.items()},
            }
        )

    def create(self, options):
        attested = (
            bytes(16)
            + len(self.credential_id).to_bytes(2, "big")
            + self.credential_id
            + _cbor(self._cose_key())
        )
        return self._answer(
            clientDataJSON=self._client_data("webauthn.create", options["challenge"]),
            attestationObject=_cbor(
                {"fmt": "none", "attStmt": {}, "authData": self._auth_data(0x45, attested)}
            ),
        )

    def get(self, options):
        self.counter += 1
        client_data = self._client_data("webauthn.get", options["challenge"])
        auth_data = self._auth_data(0x05)
        signed = auth_data + hashlib.sha256(client_data).digest()
        if isinstance(self.key, rsa.RSAPrivateKey):
            signature = self.key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
        else:
            signature = self.key.sign(signed, ec.ECDSA(hashes.SHA256()))
        return self._answer(clientDataJSON=client_data, authenticatorData=auth_data, signature=signature)


class PasskeyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="lea", email="lea@example.com", password="pw-12345!")
        self.profile = Profile.objects.create(user=self.user, display_name="Lea")
        secret = two_factor.generate_secret()
        two_factor.enable(self.profile, secret, two_factor.totp_code(secret, two_factor.current_step()))

    def _add(self, authenticator, name="Laptop"):
        """Register through the settings page, as passkeys.js does."""
        self.client.force_login(self.user)
        options = self.client.get("/settings/").context["passkey_options"]
        return self.client.post(
            "/settings/",
            {"form_name": "passkey_add", "name": name, "credential": authenticator.create(options)},
            follow=True,
        )

    def _login_options(self):
        """Sign out, pass the password step and return the passkey options of the second step."""
        self.client.logout()
        response = self.client.post("/login/", {"username": "lea@example.com", "password": "pw-12345!"})
        self.assertRedirects(response, "/login/zwei-faktor/", fetch_redirect_response=False)
        return self.client.get("/login/zwei-faktor/").context["passkey_options"]

    def _confirm(self, answer):
        return self.client.post("/login/zwei-faktor/", {"passkey": answer})

    def test_added_passkey_confirms_the_login_instead_of_the_code(self):
        authenticator = FakeAuthenticator()
        response = self._add(authenticator)
        self.assertContains(response, "Passkey „Laptop“ hinzugefügt.")
        passkey = Passkey.objects.get(user=self.user)
        self.assertEqual(passkey.name, "Laptop")
        self.assertTrue(
            AuditLogEntry.objects.filter(action=AuditLogEntry.ACTION_PASSKEY_ADDED, actor=self.user).exists()
        )

        options = self._login_options()
        self.assertEqual(options["allowCredentials"][0]["id"], passkey.credential_id)
        self.assertEqual(options["rpId"], "testserver")
        self.assertContains(self.client.get("/login/zwei-faktor/"), "Mit Passkey bestätigen")
        response = self._confirm(authenticator.get(options))

        self.assertRedirects(response, "/home/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))
        passkey.refresh_from_db()
        self.assertEqual(passkey.sign_count, 1)
        self.assertIsNotNone(passkey.last_used_at)

    def test_code_page_offers_no_passkey_without_one(self):
        self.assertIsNone(self._login_options())
        self.assertNotContains(self.client.get("/login/zwei-faktor/"), "Mit Passkey bestätigen")

    def test_answer_cannot_be_replayed(self):
        authenticator = FakeAuthenticator()
        self._add(authenticator)
        answer = authenticator.get(self._login_options())
        self._confirm(answer)
        self.assertIn("_auth_user_id", self.client.session)

        self._login_options()
        response = self._confirm(answer)

        self.assertContains(response, "Der Passkey konnte nicht bestätigt werden.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_failed_attempt_spends_the_challenge_and_warns_the_owner(self):
        authenticator = FakeAuthenticator()
        self._add(authenticator)
        answer = json.loads(authenticator.get(self._login_options()))
        broken = json.dumps({**answer, "response": {**answer["response"], "signature": "AAAA"}})

        self._confirm(broken)
        response = self._confirm(json.dumps(answer))

        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(response, "Der Passkey konnte nicht bestätigt werden.")
        self.assertEqual(
            AuditLogEntry.objects.filter(action=AuditLogEntry.ACTION_TWO_FACTOR_FAILED).count(), 2
        )
        self.assertTrue(
            UserNotification.objects.filter(
                recipient=self.user, kind=UserNotification.KIND_SECURITY_ALERT
            ).exists()
        )

    def test_answer_made_for_another_site_is_rejected(self):
        # What a phishing page would get: the browser signs that page's own origin and RP id.
        self._add(FakeAuthenticator(origin="https://lunora.example", rp_id="lunora.example"))
        self.assertFalse(Passkey.objects.exists())

        authenticator = FakeAuthenticator()
        self._add(authenticator)
        options = self._login_options()
        for origin, rp_id in (
            ("https://lunora.example", "testserver"),
            ("http://testserver", "lunora.example"),
        ):
            authenticator.origin, authenticator.rp_id = origin, rp_id
            self._confirm(authenticator.get(options))
            self.assertNotIn("_auth_user_id", self.client.session)
            options = self.client.get("/login/zwei-faktor/").context["passkey_options"]

    def test_counter_that_does_not_advance_is_rejected(self):
        authenticator = FakeAuthenticator()
        self._add(authenticator)
        Passkey.objects.update(sign_count=5)

        self._confirm(authenticator.get(self._login_options()))

        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(Passkey.objects.get().sign_count, 5)

    def test_passkey_of_another_account_does_not_confirm_this_login(self):
        other = User.objects.create_user(username="tom", email="tom@example.com", password="pw-12345!")
        authenticator = FakeAuthenticator()
        fields = passkeys.verify_registration(
            authenticator.create({"challenge": "abc"}), challenge="abc", **SITE
        )
        Passkey.objects.create(user=other, name="Toms Schlüssel", **fields)
        self._add(FakeAuthenticator())

        self._confirm(authenticator.get(self._login_options()))

        self.assertNotIn("_auth_user_id", self.client.session)

    def test_same_credential_cannot_be_added_twice_and_the_limit_holds(self):
        authenticator = FakeAuthenticator()
        self._add(authenticator)
        response = self._add(authenticator, name="Nochmal")
        self.assertContains(response, "Der Passkey konnte nicht hinzugefügt werden.")
        self.assertEqual(Passkey.objects.count(), 1)

        with patch("app.services.passkeys.MAX_PASSKEYS", 1):
            self.assertIsNone(self.client.get("/settings/").context["passkey_options"])
            response = self.client.post(
                "/settings/",
                {"form_name": "passkey_add", "credential": FakeAuthenticator().create({"challenge": "x"})},
                follow=True,
            )
        self.assertContains(response, "Mehr als 1 Passkeys sind nicht möglich.")
        self.assertEqual(Passkey.objects.count(), 1)

    def test_registration_challenge_works_once(self):
        self.client.force_login(self.user)
        options = self.client.get("/settings/").context["passkey_options"]
        for _ in range(2):
            self.client.post(
                "/settings/",
                {"form_name": "passkey_add", "credential": FakeAuthenticator().create(options)},
            )

        # Without a typed name the device's label is used.
        self.assertEqual(Passkey.objects.get().name, device_label(""))

    def test_remove_only_touches_own_passkeys(self):
        self._add(FakeAuthenticator())
        other = User.objects.create_user(username="tom", email="tom@example.com", password="pw-12345!")
        Profile.objects.create(user=other, display_name="Tom")
        mine = Passkey.objects.get()
        intruder = Client()
        intruder.force_login(other)

        intruder.post("/settings/", {"form_name": "passkey_delete", "passkey_id": mine.pk})
        self.client.post("/settings/", {"form_name": "passkey_delete", "passkey_id": "abc"})
        self.assertTrue(Passkey.objects.filter(pk=mine.pk).exists())

        response = self.client.post(
            "/settings/", {"form_name": "passkey_delete", "passkey_id": mine.pk}, follow=True
        )
        self.assertContains(response, "Passkey „Laptop“ entfernt.")
        self.assertFalse(Passkey.objects.exists())

    def test_passkeys_need_the_code_and_go_when_it_is_switched_off(self):
        self._add(FakeAuthenticator())
        self.client.post("/settings/", {"form_name": "two_factor_disable", "password": "pw-12345!"})
        self.assertFalse(Passkey.objects.exists())

        response = self.client.get("/settings/")
        self.assertIsNone(response.context["passkey_options"])
        self.assertNotContains(response, "passkey-create-options")
        self.client.post(
            "/settings/",
            {"form_name": "passkey_add", "credential": FakeAuthenticator().create({"challenge": "x"})},
        )
        self.assertFalse(Passkey.objects.exists())

    def test_rsa_keys_work_too(self):
        authenticator = FakeAuthenticator(use_rsa=True)
        fields = passkeys.verify_registration(
            authenticator.create({"challenge": "abc"}), challenge="abc", **SITE
        )
        stored = {"public_key": fields["public_key"], "sign_count": fields["sign_count"]}

        count = passkeys.verify_assertion(
            authenticator.get({"challenge": "def"}), challenge="def", **stored, **SITE
        )

        self.assertEqual(count, 1)
        with self.assertRaises(passkeys.PasskeyError):
            passkeys.verify_assertion(
                authenticator.get({"challenge": "other"}), challenge="def", **stored, **SITE
            )

    def test_malformed_answers_are_rejected_without_an_error(self):
        answer = json.loads(FakeAuthenticator().create({"challenge": "abc"}))
        attestation = passkeys._b64decode(answer["response"]["attestationObject"])
        samples = [
            "",
            "kein json",
            "[]",
            "[" * 5000,
            json.dumps({"response": 3}),
            json.dumps({"response": {"clientDataJSON": "####", "attestationObject": 7}}),
            json.dumps(
                {
                    "response": {
                        **answer["response"],
                        "attestationObject": passkeys._b64encode(attestation[:-20]),
                    }
                }
            ),
            json.dumps({"response": {**answer["response"], "attestationObject": "v" * 40}}),
            "x" * (passkeys.MAX_POSTED_CHARS + 1),
        ]
        for sample in samples:
            with self.subTest(sample=sample[:30]):
                with self.assertRaises(passkeys.PasskeyError):
                    passkeys.verify_registration(sample, challenge="abc", **SITE)
                self.assertFalse(passkeys.verify_login(self.user, sample, challenge="abc", **SITE))

    def test_export_lists_passkeys_without_key_material(self):
        self._add(FakeAuthenticator())
        exported = json.dumps(_account(self.user, None)["profile"]["passkeys"], default=str)

        self.assertIn("Laptop", exported)
        self.assertNotIn(Passkey.objects.get().credential_id, exported)
        self.assertNotIn("PUBLIC KEY", exported)
