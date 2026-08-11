from django.contrib.auth import login
from django.contrib.auth.views import (
    LoginView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.shortcuts import redirect, render
from django.urls import reverse_lazy

from app.forms import EmailLoginForm, RegistrationForm
from app.services.system_settings import normal_user_login_enabled


class LunoraLoginView(LoginView):
    template_name = "app/login.html"
    authentication_form = EmailLoginForm
    redirect_authenticated_user = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["login_disabled"] = not normal_user_login_enabled()
        return context


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
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("home")
    else:
        form = RegistrationForm()

    return render(request, "app/register.html", {"form": form})
