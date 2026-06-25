from django.urls import path
from django.views.generic import RedirectView

import app.views as view

urlpatterns = [
    path("", RedirectView.as_view(url="/home/", permanent=False)),
    path('home/', view.home, name='home'),
    path('settings/', view.settings, name='settings')
]
