from django.urls import path
from django.views.generic import RedirectView

import app.views as view

urlpatterns = [
    path("", RedirectView.as_view(url="/home/", permanent=False)),
    path("home/", view.home, name="home"),
    path("settings/", view.settings, name="settings"),
    path("weather/", view.weather, name="weather"),
    path("weather/suggest/", view.weather_suggestions, name="weather_suggestions"),
    path("calendar/", view.calendar, name="calendar"),
    path("messages/", view.messages, name="messages"),
]
