import json
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings


logger = logging.getLogger(__name__)

TURNSTILE_SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TURNSTILE_ACTION = "register"
TURNSTILE_TOKEN_MAX_LENGTH = 2048
TURNSTILE_RESPONSE_MAX_BYTES = 64 * 1024


def verify_registration_token(token):
    """Validate a single-use Turnstile registration token, failing closed."""
    if not settings.CLOUDFLARE_TURNSTILE_REQUIRED:
        return True

    token = (token or "").strip()
    if not token or len(token) > TURNSTILE_TOKEN_MAX_LENGTH:
        return False

    payload = urlencode(
        {
            "secret": settings.CLOUDFLARE_TURNSTILE_SECRET_KEY,
            "response": token,
        }
    ).encode("utf-8")
    request = Request(
        TURNSTILE_SITEVERIFY_URL,
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Lunora Turnstile/1.0",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=settings.CLOUDFLARE_TURNSTILE_TIMEOUT) as response:
            response_body = response.read(TURNSTILE_RESPONSE_MAX_BYTES + 1)
            if len(response_body) > TURNSTILE_RESPONSE_MAX_BYTES:
                logger.warning("Turnstile returned an oversized response.")
                return False
            result = json.loads(response_body.decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        logger.warning("Turnstile validation request failed: %s", error.__class__.__name__)
        return False

    if not isinstance(result, dict):
        logger.warning("Turnstile returned an unexpected response type.")
        return False

    if not result.get("success"):
        logger.info("Turnstile rejected a registration token.")
        return False

    if result.get("action") != TURNSTILE_ACTION:
        logger.warning("Turnstile returned an unexpected action.")
        return False

    hostname = str(result.get("hostname", "")).casefold()
    return hostname == settings.CLOUDFLARE_TURNSTILE_EXPECTED_HOSTNAME.casefold()
