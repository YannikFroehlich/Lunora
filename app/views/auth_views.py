import hashlib
import logging
import time
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import (
    LoginView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.core.cache import cache
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render, resolve_url
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from app.forms import (
    LOGIN_ATTEMPT_LIMIT,
    LOGIN_ATTEMPT_LOCKOUT_SECONDS,
    LOGIN_CLIENT_ATTEMPT_LIMIT,
    LOGIN_LOCKED_MESSAGE,
    EmailLoginForm,
    RegistrationForm,
)
from app.models import AuditLogEntry, Profile
from app.services import audit_log, email_verification, passkeys, two_factor
from app.services.notifications import (
    materialize_failed_two_factor_notification,
    materialize_login_notification,
)
from app.services.system_settings import normal_user_login_enabled, user_can_login
from app.services.throttle import count_hit
from app.services.turnstile import verify_registration_token
from app.services.user_sessions import device_label

logger = logging.getLogger(__name__)

TWO_FACTOR_SESSION_KEY = "two_factor_pending"
TWO_FACTOR_PENDING_SECONDS = 5 * 60
PASSKEY_CHALLENGE_KEY = "passkey_challenge"  # inside the pending dict
PASSKEY_SIGN_IN_SESSION_KEY = "passkey_sign_in"
PASSWORD_RESET_EMAIL_LIMIT = 3
PASSWORD_RESET_IP_LIMIT = 10
PASSWORD_RESET_WINDOW_SECONDS = 60 * 60


KNOWN_DEVICE_COOKIE = "lunora_known_device"
KNOWN_DEVICE_SALT = "app.views.auth_views.known_device"
KNOWN_DEVICE_MAX_AGE = 400 * 24 * 60 * 60  # browsers cap cookie lifetime at 400 days
KNOWN_DEVICE_MAX_USERS = 10


def _known_device_user_ids(request):
    value = request.get_signed_cookie(KNOWN_DEVICE_COOKIE, default="", salt=KNOWN_DEVICE_SALT)
    return [part for part in value.split(",") if part.isdigit()]


def _notify_login(request, response, user):
    """Mail the user about this sign-in (through the inbox, delivered by run_automations), as their
    `login_alerts` setting asks. A signed cookie marks the browser as known for this account; it is
    unforgeable, so a sign-in elsewhere with a stolen password still counts as a new device."""
    known_ids = _known_device_user_ids(request)
    preference = (
        Profile.objects.filter(user=user).values_list("login_alerts", flat=True).first()
        or Profile.LOGIN_ALERT_NEW_DEVICE
    )
    is_new_device = str(user.pk) not in known_ids
    if preference == Profile.LOGIN_ALERT_ALWAYS or (
        preference == Profile.LOGIN_ALERT_NEW_DEVICE and is_new_device
    ):
        materialize_login_notification(
            user,
            device=device_label(request.headers.get("User-Agent", "")),
            ip_address=audit_log.client_ip(request),
        )
    known_ids = [user_id for user_id in known_ids if user_id != str(user.pk)] + [str(user.pk)]
    response.set_signed_cookie(
        KNOWN_DEVICE_COOKIE,
        ",".join(known_ids[-KNOWN_DEVICE_MAX_USERS:]),
        salt=KNOWN_DEVICE_SALT,
        max_age=KNOWN_DEVICE_MAX_AGE,
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="Lax",
    )
    return response


class LunoraLoginView(LoginView):
    template_name = "app/login.html"
    authentication_form = EmailLoginForm
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["login_disabled"] = not normal_user_login_enabled()
        return context

    def form_valid(self, form):
        user = form.get_user()
        if not two_factor.is_enabled(user):
            return _notify_login(self.request, super().form_valid(form), user)
        # Password is correct, but the session stays anonymous until the second factor is checked.
        self.request.session[TWO_FACTOR_SESSION_KEY] = {
            "user_id": user.pk,
            "backend": user.backend,
            "next": self.get_success_url(),
            "started_at": int(time.time()),
        }
        return redirect("login_two_factor")


def _two_factor_attempts_key(user_id):
    return f"two-factor-attempts:{user_id}"


def login_two_factor(request):
    pending = request.session.get(TWO_FACTOR_SESSION_KEY)
    if not pending or time.time() - pending["started_at"] > TWO_FACTOR_PENDING_SECONDS:
        request.session.pop(TWO_FACTOR_SESSION_KEY, None)
        return redirect("login")

    error = ""
    rp_id, origin = passkeys.relying_party(request)
    if request.method == "POST":
        # A passkey answer stands in for the code. Its challenge is good for one attempt only.
        posted_passkey = request.POST.get("passkey", "")
        challenge = pending.pop(PASSKEY_CHALLENGE_KEY, None) if posted_passkey else None
        request.session.modified = True
        # Throttled per account, not per session: restarting the password step must not reset it.
        # Counted before checking, so parallel requests can't all pass a not-yet-updated counter.
        attempts_key = _two_factor_attempts_key(pending["user_id"])
        if count_hit(attempts_key, LOGIN_ATTEMPT_LOCKOUT_SECONDS) > LOGIN_ATTEMPT_LIMIT:
            error = "Zu viele falsche Codes. Bitte warte einige Minuten und versuche es erneut."
        else:
            with transaction.atomic():
                profile = (
                    Profile.objects.select_for_update()
                    .select_related("user")
                    .filter(user_id=pending["user_id"], user__is_active=True)
                    .first()
                )
                verified = bool(
                    profile
                    and user_can_login(profile.user)
                    and two_factor.is_enabled(profile.user)
                    and (
                        passkeys.verify_login(
                            profile.user, posted_passkey, challenge=challenge, rp_id=rp_id, origin=origin
                        )
                        if posted_passkey
                        else two_factor.verify_login_code(profile, request.POST.get("code", ""))
                    )
                )
            if verified:
                cache.delete(attempts_key)
                del request.session[TWO_FACTOR_SESSION_KEY]
                login(request, profile.user, backend=pending["backend"])
                return _notify_login(request, redirect(pending["next"]), profile.user)
            audit_log.record(
                AuditLogEntry.ACTION_TWO_FACTOR_FAILED, request, actor=profile.user if profile else None
            )
            if profile:
                materialize_failed_two_factor_notification(profile.user)
            error = (
                "Der Passkey konnte nicht bestätigt werden."
                if posted_passkey
                else "Der Code ist ungültig oder wurde bereits verwendet."
            )

    challenge = pending.get(PASSKEY_CHALLENGE_KEY) or passkeys.new_challenge()
    passkey_options = passkeys.authentication_options(pending["user_id"], challenge, rp_id)
    if passkey_options:
        pending[PASSKEY_CHALLENGE_KEY] = challenge
        request.session.modified = True  # pending is a dict inside the session
    return render(request, "app/login_two_factor.html", {"error": error, "passkey_options": passkey_options})


def _safe_next(request, url):
    """`url` when it is a local address on this host (as LoginView checks `next`), else ''."""
    allowed = url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    )
    return url if url and allowed else ""


@require_POST
def login_passkey_options(request):
    """Start a sign-in with a passkey: a fresh challenge for this session, good for one answer.

    Only on request (the button), so the login page itself doesn't create a session per visitor.
    """
    challenge = passkeys.new_challenge()
    request.session[PASSKEY_SIGN_IN_SESSION_KEY] = {"challenge": challenge, "started_at": int(time.time())}
    return JsonResponse(passkeys.sign_in_options(challenge, passkeys.relying_party(request)[0]))


@require_POST
def login_passkey(request):
    """Sign in with a passkey instead of password and code (see passkeys.authenticate)."""
    started = request.session.pop(PASSKEY_SIGN_IN_SESSION_KEY, None)
    next_url = _safe_next(request, request.POST.get("next", ""))
    # The same per-client counter as wrong passwords, so switching method doesn't reset it.
    client_ip = audit_log.client_ip(request)
    client_key = f"login-client:{client_ip}" if client_ip else None
    if client_key and cache.get(client_key, 0) >= LOGIN_CLIENT_ATTEMPT_LIMIT:
        messages.error(request, LOGIN_LOCKED_MESSAGE)
        return redirect(_login_url(next_url))

    user = None
    if started and time.time() - started["started_at"] <= TWO_FACTOR_PENDING_SECONDS:
        rp_id, origin = passkeys.relying_party(request)
        user = passkeys.authenticate(
            request.POST.get("passkey", ""), challenge=started["challenge"], rp_id=rp_id, origin=origin
        )
    # Passkeys only count while TOTP is on (they stand in for it); the rest is what the password
    # form checks in EmailLoginForm.confirm_login_allowed.
    allowed = bool(
        user
        and user.is_active
        and user_can_login(user)
        and two_factor.is_enabled(user)
        and not email_verification.is_pending(user)
    )
    if not allowed:
        if client_key:
            count_hit(client_key, LOGIN_ATTEMPT_LOCKOUT_SECONDS)
        audit_log.record(AuditLogEntry.ACTION_LOGIN_FAILED, request, actor=user, summary="Passkey")
        messages.error(request, "Die Anmeldung mit Passkey hat nicht geklappt. Bitte versuche es erneut.")
        return redirect(_login_url(next_url))

    request.session.pop(TWO_FACTOR_SESSION_KEY, None)
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return _notify_login(request, redirect(next_url or resolve_url(settings.LOGIN_REDIRECT_URL)), user)


def _login_url(next_url):
    return f"{reverse('login')}?{urlencode({'next': next_url})}" if next_url else reverse("login")


def _counter_exceeded(key, limit):
    """Count one request against ``key``; the window starts at its first request and doesn't slide."""
    return count_hit(key, PASSWORD_RESET_WINDOW_SECONDS) > limit


def _password_reset_limit_reached(request, email):
    """Per address, so nobody's inbox gets flooded; per client, so nobody can walk through addresses."""
    email_hash = hashlib.sha256(email.strip().casefold().encode()).hexdigest()
    limited = _counter_exceeded(f"password-reset-email:{email_hash}", PASSWORD_RESET_EMAIL_LIMIT)
    # Without a known client address the counter would be shared by every visitor.
    ip_address = audit_log.client_ip(request)
    if ip_address and _counter_exceeded(f"password-reset-ip:{ip_address}", PASSWORD_RESET_IP_LIMIT):
        limited = True
    return limited


class LunoraPasswordResetView(PasswordResetView):
    template_name = "app/password_reset_form.html"
    email_template_name = "app/password_reset_email.html"
    subject_template_name = "app/password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")

    def dispatch(self, request, *args, **kwargs):
        if not normal_user_login_enabled():
            return render(request, "app/password_reset_form.html", {"reset_disabled": True}, status=503)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        # The same answer as a sent mail: a visible limit would reveal which addresses have accounts.
        if _password_reset_limit_reached(self.request, form.cleaned_data["email"]):
            logger.warning("Password reset request dropped by the rate limit")
            return redirect(self.get_success_url())
        return super().form_valid(form)


class LunoraPasswordResetDoneView(PasswordResetDoneView):
    template_name = "app/password_reset_done.html"


class LunoraPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = "app/password_reset_confirm.html"
    success_url = reverse_lazy("password_reset_complete")

    def form_valid(self, form):
        response = super().form_valid(form)
        audit_log.record(AuditLogEntry.ACTION_PASSWORD_RESET, self.request, actor=form.user)
        email_verification.mark_confirmed(form.user)
        return response


class LunoraPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "app/password_reset_complete.html"


def register(request):
    if request.user.is_authenticated:
        return redirect("home")

    if not normal_user_login_enabled():
        return render(request, "app/register.html", {"registration_disabled": True}, status=503)

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        form_is_valid = form.is_valid()
        turnstile_is_valid = verify_registration_token(request.POST.get("cf-turnstile-response"))
        if not turnstile_is_valid:
            form.add_error(
                None,
                "Die Sicherheitsprüfung ist fehlgeschlagen. Bitte versuche es erneut.",
            )
        if form_is_valid and turnstile_is_valid:
            user = form.save()
            audit_log.record(AuditLogEntry.ACTION_REGISTER, request, actor=user)
            email_verification.send_confirmation_email(request, user)
            return render(request, "app/register_confirm.html", {"state": "sent", "email": user.email})
    else:
        form = RegistrationForm()

    return render(
        request,
        "app/register.html",
        {
            "form": form,
            "turnstile_site_key": (
                settings.CLOUDFLARE_TURNSTILE_SITE_KEY if settings.CLOUDFLARE_TURNSTILE_REQUIRED else ""
            ),
        },
    )


def register_confirm(request, uidb64, token):
    user = email_verification.confirm(uidb64, token)
    if user is None:
        return render(request, "app/register_confirm.html", {"state": "invalid"}, status=400)
    email_verification.send_welcome_email(request, user)
    return render(request, "app/register_confirm.html", {"state": "confirmed"})
