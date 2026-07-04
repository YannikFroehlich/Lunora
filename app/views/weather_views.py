from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render

from app.models import Profile
from app.services.weather_service import (
    fetch_weather_radar_tile,
    get_location_suggestions,
    get_weather_context,
)


@login_required
def weather(request):
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

    return render(request, "app/weather.html", get_weather_context(request.GET, user=request.user))


@login_required
def weather_suggestions(request):
    query = request.GET.get("q", "")
    return JsonResponse({"results": get_location_suggestions(query)})


@login_required
def weather_radar_tile(request, z, x, y, layer="precipitation"):
    try:
        tile = fetch_weather_radar_tile(z, x, y, layer=layer)
    except ValueError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=404)
    except Exception:
        return JsonResponse({"ok": False, "error": "Radar konnte nicht geladen werden."}, status=502)

    response = HttpResponse(tile, content_type="image/png")
    response["Cache-Control"] = "public, max-age=300"
    return response
