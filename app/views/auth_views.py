import time

from django.conf import settings
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
from django.shortcuts import redirect, render
from django.urls import reverse_lazy

from app.forms import LOGIN_ATTEMPT_LIMIT, LOGIN_ATTEMPT_LOCKOUT_SECONDS, EmailLoginForm, RegistrationForm
from app.models import Profile
from app.services import two_factor
from app.services.system_settings import normal_user_login_enabled, user_can_login
from app.services.turnstile import verify_registration_token

TWO_FACTOR_SESSION_KEY = "two_factor_pending"
TWO_FACTOR_PENDING_SECONDS = 5 * 60


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
            return super().form_valid(form)
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
    if request.method == "POST":
        # Throttled per account, not per session: restarting the password step must not reset it.
        attempts_key = _two_factor_attempts_key(pending["user_id"])
        if cache.get(attempts_key, 0) >= LOGIN_ATTEMPT_LIMIT:
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
                    and two_factor.verify_login_code(profile, request.POST.get("code", ""))
                )
            if verified:
                cache.delete(attempts_key)
                del request.session[TWO_FACTOR_SESSION_KEY]
                login(request, profile.user, backend=pending["backend"])
                return redirect(pending["next"])
            cache.set(attempts_key, cache.get(attempts_key, 0) + 1, LOGIN_ATTEMPT_LOCKOUT_SECONDS)
            error = "Der Code ist ungültig oder wurde bereits verwendet."

    return render(request, "app/login_two_factor.html", {"error": error})


class LunoraPasswordResetView(PasswordResetView):
    template_name = "app/password_reset_form.html"
    email_template_name = "app/password_reset_email.html"
    subject_template_name = "app/password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")

    def dispatch(self, request, *args, **kwargs):
        if not normal_user_login_enabled():
            return render(request, "app/password_reset_form.html", {"reset_disabled": True}, status=503)
        return super().dispatch(request, *args, **kwargs)


class LunoraPasswordResetDoneView(PasswordResetDoneView):
    template_name = "app/password_reset_done.html"


class LunoraPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = "app/password_reset_confirm.html"
    success_url = reverse_lazy("password_reset_complete")


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
            login(request, user)
            return redirect("home")
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
