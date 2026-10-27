"""TOTP two-factor login (RFC 6238, the scheme every authenticator app speaks) plus recovery codes."""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

from django.utils import timezone
from reportlab.graphics.barcode import qrencoder

ISSUER = "Lunora"
TOTP_DIGITS = 6
TOTP_PERIOD_SECONDS = 30
# Accept the neighbouring time steps too, so a slightly drifting phone clock still works.
TOTP_VALID_WINDOW = 1
RECOVERY_CODE_COUNT = 10


def generate_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def totp_code(secret, step):
    key = base64.b32decode(secret, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**TOTP_DIGITS).zfill(TOTP_DIGITS)


def current_step(now=None):
    return int((now if now is not None else time.time()) // TOTP_PERIOD_SECONDS)


def _normalize_code(code):
    return "".join((code or "").split()).replace("-", "").lower()


def matching_step(secret, code, *, now=None, after_step=None):
    """Return the time step the code belongs to, or None. Steps up to after_step count as used."""
    code = _normalize_code(code)
    if len(code) != TOTP_DIGITS or not code.isdigit():
        return None
    step = current_step(now)
    for candidate in range(step - TOTP_VALID_WINDOW, step + TOTP_VALID_WINDOW + 1):
        if after_step is not None and candidate <= after_step:
            continue
        if hmac.compare_digest(totp_code(secret, candidate), code):
            return candidate
    return None


def is_enabled(user):
    profile = getattr(user, "profile", None)
    return bool(profile and profile.totp_enabled_at and profile.totp_secret)


def provisioning_uri(user, secret):
    label = quote(f"{ISSUER}:{user.email or user.get_username()}")
    query = urlencode(
        {"secret": secret, "issuer": ISSUER, "digits": TOTP_DIGITS, "period": TOTP_PERIOD_SECONDS}
    )
    return f"otpauth://totp/{label}?{query}"


def qr_code_svg(data):
    """Render data as a compact SVG QR code (one path), using reportlab's bundled encoder."""
    qr = qrencoder.QRCode(None, qrencoder.QRErrorCorrectLevel.M)
    qr.addData(data)
    qr.make()
    size = qr.getModuleCount()
    border = 4
    cells = "".join(
        f"M{col + border} {row + border}h1v1h-1z"
        for row in range(size)
        for col in range(size)
        if qr.isDark(row, col)
    )
    total = size + 2 * border
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {total}" '
        f'shape-rendering="crispEdges" role="img" aria-label="QR-Code für die Authenticator-App">'
        f'<rect width="{total}" height="{total}" fill="#fff"/><path d="{cells}" fill="#000"/></svg>'
    )


def _hash_recovery_code(code):
    return hashlib.sha256(_normalize_code(code).encode("utf-8")).hexdigest()


def generate_recovery_codes():
    """Return (plain codes to show once, hashes to store)."""
    codes = [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(RECOVERY_CODE_COUNT)]
    return codes, [_hash_recovery_code(code) for code in codes]


def enable(profile, secret, code, *, now=None):
    """Turn 2FA on after the user proved their app produces valid codes; returns recovery codes or None."""
    step = matching_step(secret, code, now=now)
    if step is None:
        return None
    codes, hashes = generate_recovery_codes()
    profile.totp_secret = secret
    profile.totp_enabled_at = timezone.now()
    profile.totp_last_step = step
    profile.totp_recovery_codes = hashes
    profile.save(
        update_fields=[
            "totp_secret",
            "totp_enabled_at",
            "totp_last_step",
            "totp_recovery_codes",
            "updated_at",
        ]
    )
    return codes


def disable(profile):
    profile.totp_secret = ""
    profile.totp_enabled_at = None
    profile.totp_last_step = None
    profile.totp_recovery_codes = []
    profile.save(
        update_fields=[
            "totp_secret",
            "totp_enabled_at",
            "totp_last_step",
            "totp_recovery_codes",
            "updated_at",
        ]
    )


def regenerate_recovery_codes(profile):
    codes, hashes = generate_recovery_codes()
    profile.totp_recovery_codes = hashes
    profile.save(update_fields=["totp_recovery_codes", "updated_at"])
    return codes


def verify_login_code(profile, code, *, now=None):
    """Check a TOTP or recovery code at login. Used codes are consumed so they cannot be replayed.

    Callers must hold a row lock on the profile (select_for_update) so two parallel requests
    cannot both redeem the same code.
    """
    step = matching_step(profile.totp_secret, code, now=now, after_step=profile.totp_last_step)
    if step is not None:
        profile.totp_last_step = step
        profile.save(update_fields=["totp_last_step"])
        return True
    hashed = _hash_recovery_code(code)
    remaining = [item for item in profile.totp_recovery_codes if not hmac.compare_digest(item, hashed)]
    if len(remaining) < len(profile.totp_recovery_codes):
        profile.totp_recovery_codes = remaining
        profile.save(update_fields=["totp_recovery_codes"])
        return True
    return False
