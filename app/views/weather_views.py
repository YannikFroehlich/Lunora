from django.conf import settings
from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from app.models import Profile
from app.services.system_settings import disabled_feature_response, feature_enabled
from app.services.weather_service import (
    dashboard_weather_from_context,
    delete_weather_location,
    fetch_weather_map_tile,
    get_location_suggestions,
    get_weather_at_coordinates,
    get_weather_context,
    list_weather_locations,
    save_weather_location,
    set_default_weather_location,
)


@login_required
def weather(request):
    if not feature_enabled("weather"):
        return disabled_feature_response(request, "weather")

    if request.method == "POST" and request.POST.get("form_name") == "weather_default":
        default_city = request.POST.get("weather_default_city", "").strip()
        if default_city:
            profile, _created = Profile.objects.get_or_create(
                user=request.user,
                defaults={"display_name": request.user.first_name or request.user.get_username()},
            )
            profile.weather_default_city = default_city
            profile.save(update_fields=["weather_default_city", "updated_at"])
            django_messages.success(request, f"{default_city} als Standard-Wetterort gespeichert.")
        return redirect("weather")

    if request.method == "POST" and request.POST.get("form_name") == "location_save":
        name = request.POST.get("name", "").strip()
        try:
            _location, created = save_weather_location(
                request.user,
                name=name,
                lat=request.POST.get("lat"),
                lon=request.POST.get("lon"),
                details=request.POST.get("details", "").strip(),
                label=request.POST.get("label", "").strip(),
            )
        except ValueError as error:
            django_messages.error(request, str(error))
        else:
            if created:
                django_messages.success(request, f"{name or 'Ort'} wurde gespeichert.")
            else:
                django_messages.info(request, f"{name or 'Ort'} ist bereits gespeichert.")
        return redirect("weather")

    if request.method == "POST" and request.POST.get("form_name") == "location_delete":
        delete_weather_location(request.user, request.POST.get("location_id"))
        return redirect("weather")

    if request.method == "POST" and request.POST.get("form_name") == "location_set_default":
        set_default_weather_location(request.user, request.POST.get("location_id"))
        return redirect("weather")

    context = get_weather_context(request.GET, user=request.user)
    context["weather_locations"] = list_weather_locations(request.user)
    return render(request, "app/weather.html", context)


@login_required
def weather_suggestions(request):
    if not feature_enabled("weather"):
        return disabled_feature_response(request, "weather", json_response=True)
    query = request.GET.get("q", "")
    return JsonResponse({"results": get_location_suggestions(query)})


@login_required
@require_GET
def dashboard_weather(request):
    if not feature_enabled("weather"):
        return disabled_feature_response(request, "weather", json_response=True)

    weather_context = get_weather_context({}, user=request.user)
    response = JsonResponse(
        {"ok": True, "weather": dashboard_weather_from_context(weather_context)}
    )
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_GET
def weather_point(request):
    if not feature_enabled("weather"):
        return disabled_feature_response(request, "weather", json_response=True)

    if not settings.WEATHER_API_KEY:
        return JsonResponse(
            {
                "ok": False,
                "error": "Für die Temperaturabfrage wird ein OpenWeather-Schlüssel benötigt.",
            },
            status=503,
        )

    try:
        point_weather = get_weather_at_coordinates(
            request.GET.get("lat"),
            request.GET.get("lon"),
        )
    except ValueError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)
    except Exception:
        return JsonResponse(
            {"ok": False, "error": "Die Temperatur an diesem Ort konnte nicht geladen werden."},
            status=502,
        )

    return JsonResponse({"ok": True, "weather": point_weather})


@login_required
def weather_map_tile(request, layer, z, x, y):
    if not feature_enabled("weather"):
        return disabled_feature_response(request, "weather", json_response=True)

    try:
        tile = fetch_weather_map_tile(z, x, y, layer=layer)
    except ValueError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=404)
    except Exception:
        return JsonResponse(
            {"ok": False, "error": "Wetterebene konnte nicht geladen werden."},
            status=502,
        )

    response = HttpResponse(tile, content_type="image/png")
    response["Cache-Control"] = "public, max-age=300"
    return response
