"""Confirming a new account's e-mail address and the mails around it (stdlib + Django only)."""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.mail import send_mail
from django.urls import reverse
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from app.models import Profile
from app.services.throttle import count_hit

logger = logging.getLogger(__name__)

RESEND_LIMIT = 3
RESEND_WINDOW_SECONDS = 60 * 60


class EmailConfirmationTokenGenerator(PasswordResetTokenGenerator):
    """Valid for PASSWORD_RESET_TIMEOUT; confirming (or changing the address) invalidates it."""

    key_salt = "app.services.email_verification.EmailConfirmationTokenGenerator"

    def _make_hash_value(self, user, timestamp):
        return f"{user.pk}{user.email}{is_pending(user)}{timestamp}"


token_generator = EmailConfirmationTokenGenerator()


def is_pending(user):
    return Profile.objects.filter(user=user, email_confirmation_pending=True).exists()


def _greeting(user):
    return f"Hallo {user.first_name}," if user.first_name else "Hallo,"


def _send(user, subject, body):
    """Mail failures must not break registration or login; the user can ask for a new link."""
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email])
    except Exception:
        logger.exception("Account e-mail to user %s failed", user.pk)
        return False
    return True


def send_confirmation_email(request, user):
    path = reverse(
        "register_confirm",
        kwargs={
            "uidb64": urlsafe_base64_encode(force_bytes(user.pk)),
            "token": token_generator.make_token(user),
        },
    )
    return _send(
        user,
        "Lunora – E-Mail-Adresse bestätigen",
        f"{_greeting(user)}\n\n"
        "bitte bestätige deine E-Mail-Adresse, damit du dich bei Lunora anmelden kannst:\n"
        f"{request.build_absolute_uri(path)}\n\n"
        "Der Link ist einige Tage gültig. Hast du kein Konto erstellt, kannst du diese E-Mail ignorieren.\n\n"
        "Dein Lunora-Team",
    )


def resend_confirmation_email(request, user):
    """Throttled per account, so a known password can't be used to flood the inbox."""
    if count_hit(f"email-confirm-resend:{user.pk}", RESEND_WINDOW_SECONDS) > RESEND_LIMIT:
        return False
    return send_confirmation_email(request, user)


def confirm(uidb64, token):
    """The confirmed user, or None for an invalid, expired or already used link."""
    try:
        user = get_user_model().objects.get(pk=force_str(urlsafe_base64_decode(uidb64)), is_active=True)
    except (ValueError, OverflowError, get_user_model().DoesNotExist):
        return None
    if not token_generator.check_token(user, token):
        return None
    if not Profile.objects.filter(user=user, email_confirmation_pending=True).update(
        email_confirmation_pending=False
    ):
        return None
    return user


def mark_confirmed(user):
    """A completed password reset proves the address as well."""
    Profile.objects.filter(user=user, email_confirmation_pending=True).update(
        email_confirmation_pending=False
    )


def send_welcome_email(request, user):
    return _send(
        user,
        "Willkommen bei Lunora",
        f"{_greeting(user)}\n\n"
        "deine E-Mail-Adresse ist bestätigt und dein Lunora-Konto ist bereit. Melde dich hier an:\n"
        f"{request.build_absolute_uri(reverse('login'))}\n\n"
        "Tipp: Unter Einstellungen → Sicherheit kannst du die Anmeldung in zwei Schritten einschalten.\n\n"
        "Dein Lunora-Team",
    )
