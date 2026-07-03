from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path
from django.views.generic import RedirectView

from app.forms import EmailLoginForm
import app.views as view

login_view = LoginView.as_view(
    template_name="app/login.html",
    authentication_form=EmailLoginForm,
    redirect_authenticated_user=True,
)

urlpatterns = [
    path("", RedirectView.as_view(url="/home/", permanent=False)),
    path("login/", login_view, name="login"),
    path("accounts/login/", login_view, name="accounts_login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("register/", view.register, name="register"),
    path("home/", view.home, name="home"),
    path("settings/", view.settings, name="settings"),
    path("weather/", view.weather, name="weather"),
    path("weather/suggest/", view.weather_suggestions, name="weather_suggestions"),
    path("weather/radar/<int:z>/<int:x>/<int:y>.png", view.weather_radar_tile, name="weather_radar_tile"),
    path("weather/radar/<str:layer>/<int:z>/<int:x>/<int:y>.png", view.weather_radar_tile, name="weather_radar_layer_tile"),
    path("calendar/", view.calendar, name="calendar"),
    path("messages/", view.messages, name="messages"),
    path("messages/live/", view.messages_live_updates, name="messages_live_updates"),
    path("messages/<int:conversation_id>/live/", view.messages_live_updates, name="messages_live_detail_updates"),
    path("messages/<int:conversation_id>/", view.messages, name="messages_detail"),
]
