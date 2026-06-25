from django.http import JsonResponse
from django.shortcuts import render

from app.services.weather_service import get_location_suggestions, get_weather_context
from app.view_models import (
    get_calendar_context,
    get_dashboard_context,
    get_messages_context,
    get_settings_context,
)


def home(request):
    return render(request, "app/home.html", get_dashboard_context())


def settings(request):
    return render(request, "app/settings.html", get_settings_context())


def weather(request):
    return render(request, "app/weather.html", get_weather_context(request.GET))


def weather_suggestions(request):
    query = request.GET.get("q", "")
    return JsonResponse({"results": get_location_suggestions(query)})


def calendar(request):
    return render(request, "app/calendar.html", get_calendar_context())


def messages(request):
    return render(request, "app/messages.html", get_messages_context())
