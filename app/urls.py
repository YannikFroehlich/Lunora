from django.contrib.auth.views import LogoutView
from django.urls import path
from django.views.generic import RedirectView

import app.views as view

login_view = view.LunoraLoginView.as_view()

urlpatterns = [
    path("", RedirectView.as_view(url="/home/", permanent=False)),
    path("login/", login_view, name="login"),
    path("accounts/login/", login_view, name="accounts_login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("register/", view.register, name="register"),
    path("administration/", view.administration, name="administration"),
    path("home/", view.home, name="home"),
    path("settings/", view.settings, name="settings"),
    path("weather/", view.weather, name="weather"),
    path("weather/point/", view.weather_point, name="weather_point"),
    path("weather/suggest/", view.weather_suggestions, name="weather_suggestions"),
    path(
        "weather/map/<str:layer>/<int:z>/<int:x>/<int:y>.png",
        view.weather_map_tile,
        name="weather_map_tile",
    ),
    path("calendar/", view.calendar, name="calendar"),
    path("messages/", view.messages, name="messages"),
    path("messages/live/", view.messages_live_updates, name="messages_live_updates"),
    path("messages/<int:conversation_id>/live/", view.messages_live_updates, name="messages_live_detail_updates"),
    path("messages/<int:conversation_id>/", view.messages, name="messages_detail"),
]
