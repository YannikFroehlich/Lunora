"""Passkeys (WebAuthn): a second login factor next to the TOTP code, and a sign-in of their own.

Only the part of the spec Lunora needs: "none" attestation at registration (the public key is
read from the authenticator data, an attestation statement is ignored), ES256 and RS256 keys, and
the checks of WebAuthn Level 2 §7.1/§7.2 that matter without attestation: type, challenge,
origin, RP id hash, user presence, signature and signature counter. Signatures are verified with
`cryptography`; the few CBOR types an authenticator sends are decoded here.

Signing in without the password (`authenticate`) needs a discoverable passkey and user
verification (fingerprint, face or device PIN), so the passkey alone is still two factors.
"""

import base64
import hashlib
import hmac
import json
import secrets

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from django.db import transaction
from django.http.request import split_domain_port
from django.utils import timezone

from app.models import Passkey

RP_NAME = "Lunora"
MAX_PASSKEYS = 10
MAX_NAME_LENGTH = 60
MAX_POSTED_CHARS = 64 * 1024
MAX_CREDENTIAL_ID_BYTES = 1023
MIN_RSA_BITS = 2048
TIMEOUT_MS = 120_000
CBOR_MAX_DEPTH = 8
FLAG_USER_PRESENT = 0x01
FLAG_USER_VERIFIED = 0x04
FLAG_ATTESTED_DATA = 0x40
COSE_ES256 = -7
COSE_RS256 = -257
# Everything a malformed or forged answer can raise while it is taken apart and checked.
_INVALID = (
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    AttributeError,
    RecursionError,
    InvalidSignature,
    UnsupportedAlgorithm,
)


class PasskeyError(Exception):
    """The browser's answer failed a check. The text is for logs and tests, never for users."""


def _b64encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text):
    if not isinstance(text, str):
        raise PasskeyError("not base64url text")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_challenge():
    return _b64encode(secrets.token_bytes(32))


def relying_party(request):
    """(RP id, origin) for this request. WebAuthn binds every credential to the host name."""
    host = request.get_host().lower()
    return split_domain_port(host)[0], f"{request.scheme}://{host}"


def _cbor_item(data, pos, depth=0):
    """Decode one CBOR item at pos; returns (value, next position). No tags, floats or
    indefinite lengths: authenticator data and "none" attestation objects don't use them."""
    if depth > CBOR_MAX_DEPTH:
        raise PasskeyError("CBOR nested too deeply")
    major, info = data[pos] >> 5, data[pos] & 0x1F
    pos += 1
    if info < 24:
        value = info
    elif info <= 27:
        size = 1 << (info - 24)
        if pos + size > len(data):
            raise PasskeyError("CBOR cut off")
        value = int.from_bytes(data[pos : pos + size], "big")
        pos += size
    else:
        raise PasskeyError("unsupported CBOR length")
    if major == 0:
        return value, pos
    if major == 1:
        return -1 - value, pos
    if major in (2, 3):
        end = pos + value
        if end > len(data):
            raise PasskeyError("CBOR cut off")
        chunk = bytes(data[pos:end])
        return (chunk if major == 2 else chunk.decode("utf-8")), end
    if major == 4:
        items = []
        for _ in range(value):  # every item takes at least a byte, so a huge count runs dry
            item, pos = _cbor_item(data, pos, depth + 1)
            items.append(item)
        return items, pos
    if major == 5:
        mapping = {}
        for _ in range(value):
            key, pos = _cbor_item(data, pos, depth + 1)
            mapping[key], pos = _cbor_item(data, pos, depth + 1)
        return mapping, pos
    if major == 7 and info in (20, 21, 22):
        return {20: False, 21: True, 22: None}[info], pos
    raise PasskeyError("unsupported CBOR type")


def _public_key_pem(cose):
    """Turn the authenticator's COSE key into PEM, accepting only the algorithms we offered."""
    if not isinstance(cose, dict):
        raise PasskeyError("COSE key is not a map")
    key_type, algorithm = cose.get(1), cose.get(3)
    if key_type == 2 and algorithm == COSE_ES256 and cose.get(-1) == 1:
        x, y = cose[-2], cose[-3]
        if not (isinstance(x, bytes) and isinstance(y, bytes) and len(x) == len(y) == 32):
            raise PasskeyError("bad P-256 point")
        # public_key() checks that the point is on the curve.
        key = ec.EllipticCurvePublicNumbers(
            int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()
        ).public_key()
    elif key_type == 3 and algorithm == COSE_RS256:
        modulus, exponent = cose[-1], cose[-2]
        if not (isinstance(modulus, bytes) and isinstance(exponent, bytes)):
            raise PasskeyError("bad RSA key")
        key = rsa.RSAPublicNumbers(
            int.from_bytes(exponent, "big"), int.from_bytes(modulus, "big")
        ).public_key()
        if key.key_size < MIN_RSA_BITS:
            raise PasskeyError("RSA key too short")
    else:
        raise PasskeyError("unsupported key type")
    return key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")


def _posted_response(posted):
    """The `response` object of the JSON the page posted (see passkeys.js)."""
    if not isinstance(posted, str) or len(posted) > MAX_POSTED_CHARS:
        raise PasskeyError("answer missing or too large")
    response = json.loads(posted).get("response")
    if not isinstance(response, dict):
        raise PasskeyError("answer without response")
    return response


def _checked_client_data(response, expected_type, challenge, origin):
    """Check type, challenge and origin of clientDataJSON; returns its raw bytes (they are signed)."""
    raw = _b64decode(response["clientDataJSON"])
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("type") != expected_type:
        raise PasskeyError("wrong ceremony type")
    if not challenge or not hmac.compare_digest(str(data.get("challenge")).encode(), challenge.encode()):
        raise PasskeyError("wrong challenge")
    if data.get("origin") != origin or data.get("crossOrigin") is True:
        raise PasskeyError("wrong origin")
    return raw


def _checked_authenticator_data(raw, rp_id):
    """Check RP id hash and user presence; returns (flags, signature counter)."""
    if len(raw) < 37:
        raise PasskeyError("authenticator data too short")
    if raw[:32] != hashlib.sha256(rp_id.encode()).digest():
        raise PasskeyError("credential belongs to another site")
    if not raw[32] & FLAG_USER_PRESENT:
        raise PasskeyError("user was not present")
    return raw[32], int.from_bytes(raw[33:37], "big")


def verify_registration(posted, *, challenge, rp_id, origin):
    """Check a navigator.credentials.create() answer; returns the fields of the new Passkey."""
    try:
        response = _posted_response(posted)
        _checked_client_data(response, "webauthn.create", challenge, origin)
        attestation, _end = _cbor_item(_b64decode(response["attestationObject"]), 0)
        auth_data = attestation["authData"]
        if not isinstance(auth_data, bytes):
            raise PasskeyError("authenticator data missing")
        flags, sign_count = _checked_authenticator_data(auth_data, rp_id)
        if not flags & FLAG_ATTESTED_DATA or len(auth_data) < 55:
            raise PasskeyError("no credential in the answer")
        # Layout after the 37 fixed bytes: AAGUID (16), id length (2), id, COSE public key.
        id_end = 55 + int.from_bytes(auth_data[53:55], "big")
        credential_id = auth_data[55:id_end]
        if not 0 < len(credential_id) <= MAX_CREDENTIAL_ID_BYTES or id_end > len(auth_data):
            raise PasskeyError("bad credential id")
        cose_key, _end = _cbor_item(auth_data, id_end)
        return {
            "credential_id": _b64encode(credential_id),
            "public_key": _public_key_pem(cose_key),
            "sign_count": sign_count,
        }
    except _INVALID as error:
        raise PasskeyError(str(error) or type(error).__name__) from error


def verify_assertion(posted, *, public_key, sign_count, challenge, rp_id, origin, user_handle=None):
    """Check a navigator.credentials.get() answer against a stored key; returns the new counter.

    With `user_handle` (the stored user's handle) it is a sign-in without password: the answer
    must name that user and the authenticator must have verified the person.
    """
    try:
        response = _posted_response(posted)
        client_data = _checked_client_data(response, "webauthn.get", challenge, origin)
        auth_data = _b64decode(response["authenticatorData"])
        flags, new_count = _checked_authenticator_data(auth_data, rp_id)
        if user_handle is not None:
            if not flags & FLAG_USER_VERIFIED:
                raise PasskeyError("user was not verified")
            if not hmac.compare_digest(_b64decode(response.get("userHandle")), user_handle):
                raise PasskeyError("answer names another user")
        signature = _b64decode(response["signature"])
        signed = auth_data + hashlib.sha256(client_data).digest()
        key = serialization.load_pem_public_key(public_key.encode("ascii"))
        if isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
        else:
            key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        # Authenticators without a counter (synced passkeys) always send 0. With a counter, one
        # that doesn't move forward means the credential was copied.
        if (new_count or sign_count) and new_count <= sign_count:
            raise PasskeyError("signature counter went backwards")
        return new_count
    except _INVALID as error:
        raise PasskeyError(str(error) or type(error).__name__) from error


def _posted_credential_id(posted):
    """The credential id the page claims, re-encoded so it matches the stored spelling."""
    try:
        if not isinstance(posted, str) or len(posted) > MAX_POSTED_CHARS:
            raise PasskeyError("answer missing or too large")
        return _b64encode(_b64decode(json.loads(posted).get("id")))
    except _INVALID as error:
        raise PasskeyError("answer without credential id") from error


def _user_handle(user_id):
    # The user handle must not carry personal data, so not the e-mail address.
    return str(user_id).encode()


def registration_options(user, challenge, rp_id):
    """PublicKeyCredentialCreationOptions as JSON; passkeys.js turns the base64url parts into bytes."""
    return {
        "challenge": challenge,
        "rp": {"id": rp_id, "name": RP_NAME},
        "user": {
            "id": _b64encode(_user_handle(user.pk)),
            "name": user.email or user.get_username(),
            "displayName": user.first_name or user.email or user.get_username(),
        },
        "pubKeyCredParams": [
            {"type": "public-key", "alg": COSE_ES256},
            {"type": "public-key", "alg": COSE_RS256},
        ],
        "excludeCredentials": [
            {"type": "public-key", "id": credential_id}
            for credential_id in user.passkeys.values_list("credential_id", flat=True)
        ],
        "authenticatorSelection": {"residentKey": "preferred", "userVerification": "preferred"},
        "attestation": "none",
        "timeout": TIMEOUT_MS,
    }


def authentication_options(user_id, challenge, rp_id):
    """PublicKeyCredentialRequestOptions for the user's passkeys, or None when there are none."""
    credential_ids = list(Passkey.objects.filter(user_id=user_id).values_list("credential_id", flat=True))
    if not credential_ids:
        return None
    return {
        "challenge": challenge,
        "rpId": rp_id,
        "allowCredentials": [{"type": "public-key", "id": value} for value in credential_ids],
        "userVerification": "preferred",
        "timeout": TIMEOUT_MS,
    }


def sign_in_options(challenge, rp_id):
    """PublicKeyCredentialRequestOptions without allowCredentials: the browser offers every passkey
    it keeps for this site, and the answer says whose it is."""
    return {
        "challenge": challenge,
        "rpId": rp_id,
        "userVerification": "required",
        "timeout": TIMEOUT_MS,
    }


def add_passkey(user, name, posted, *, challenge, rp_id, origin):
    """Store the credential the browser just created. Raises PasskeyError when a check fails."""
    fields = verify_registration(posted, challenge=challenge, rp_id=rp_id, origin=origin)
    if Passkey.objects.filter(credential_id=fields["credential_id"]).exists():
        raise PasskeyError("credential already registered")
    name = " ".join((name or "").split())[:MAX_NAME_LENGTH] or "Passkey"
    return Passkey.objects.create(user=user, name=name, **fields)


def _use_passkey(candidates, posted, *, without_password, **checks):
    """The passkey among `candidates` that signed the answer, with its counter moved on, or None.

    The row lock keeps two answers from passing the same counter value.
    """
    try:
        with transaction.atomic():
            passkey = (
                candidates.select_for_update()
                .select_related("user")
                .filter(credential_id=_posted_credential_id(posted))
                .first()
            )
            if passkey is None:
                return None
            passkey.sign_count = verify_assertion(
                posted,
                public_key=passkey.public_key,
                sign_count=passkey.sign_count,
                user_handle=_user_handle(passkey.user_id) if without_password else None,
                **checks,
            )
            passkey.last_used_at = timezone.now()
            passkey.save(update_fields=["sign_count", "last_used_at"])
            return passkey
    except PasskeyError:
        return None


def verify_login(user, posted, *, challenge, rp_id, origin):
    """True when the posted assertion comes from one of the user's passkeys (second login step)."""
    checks = {"challenge": challenge, "rp_id": rp_id, "origin": origin}
    return (
        _use_passkey(Passkey.objects.filter(user=user), posted, without_password=False, **checks) is not None
    )


def authenticate(posted, *, challenge, rp_id, origin):
    """The user whose passkey signed this sign-in without password, or None.

    Whether that user may sign in at all is the caller's check.
    """
    checks = {"challenge": challenge, "rp_id": rp_id, "origin": origin}
    passkey = _use_passkey(Passkey.objects.all(), posted, without_password=True, **checks)
    return passkey.user if passkey else None
