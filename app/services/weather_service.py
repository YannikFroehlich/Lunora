import json
import math
import re
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from app.models import WeatherLocation


MAX_WEATHER_LOCATIONS = 8
_LOCATION_DEDUPE_PRECISION = 2
_WEATHER_NOUN_PATTERN = re.compile(
    r"\b(regenschauer|nieselregen|schneeregen|schneeschauer|wolken?|regen|schnee|gewitter|"
    r"nebel|dunst|rauch|staub|sand|asche|himmel|hagel|sturm|tornado)\b",
    re.IGNORECASE,
)


def _format_weather_description(description, default="Aktuelles Wetter"):
    text = (description or default).strip() or default
    text = text[0].upper() + text[1:]
    return _WEATHER_NOUN_PATTERN.sub(
        lambda match: match.group(0)[0].upper() + match.group(0)[1:],
        text,
    )


def get_weather_context(params=None, user=None):
    """Return weather data for the requested place without exposing API keys."""
    params = params or {}
    fallback = _fallback_weather_context()
    location = _location_from_request(params, user=user)
    search_query = params.get("q", "").strip()

    fallback["search_query"] = search_query

    if not settings.WEATHER_API_KEY:
        return _fallback_for_location(fallback, location, search_query)

    if location.get("query") and not location.get("lat"):
        location = _geocode_first(location["query"])
        if not location:
            fallback["api_notice"] = "Ort nicht gefunden. Demo-Daten werden angezeigt."
            return fallback

    try:
        weather_params = _weather_api_params(location)
        current = _fetch_json(f"{settings.WEATHER_API_BASE_URL}/weather", weather_params)
        forecast = _fetch_json(f"{settings.WEATHER_API_BASE_URL}/forecast", weather_params)
    except Exception as exc:
        context = _fallback_for_location(fallback, location, search_query)
        context["api_notice"] = (
            "Wetter-API ist gerade nicht erreichbar. Demo-Daten werden angezeigt."
        )
        context["api_error"] = exc.__class__.__name__
        return context

    return _build_context_from_api(current, forecast, fallback, location)


def get_location_suggestions(query, limit=5):
    """Return city suggestions from OpenWeather Geocoding or local demo data."""
    clean_query = query.strip()
    if len(clean_query) < 2:
        return []

    if not settings.WEATHER_API_KEY:
        return _fallback_location_suggestions(clean_query, limit)

    try:
        locations = _fetch_json(
            f"{settings.WEATHER_GEO_API_BASE_URL}/direct",
            {
                "q": clean_query,
                "limit": min(limit, 5),
                "appid": settings.WEATHER_API_KEY,
            },
        )
    except Exception:
        return _fallback_location_suggestions(clean_query, limit)

    return [_normalize_location(item) for item in locations[:limit]]


def get_weather_at_coordinates(lat, lon):
    """Return current weather for a point selected on the weather map."""
    latitude = _coerce_float(lat)
    longitude = _coerce_float(lon)

    if (
        latitude is None
        or longitude is None
        or not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or latitude < -90
        or latitude > 90
        or longitude < -180
        or longitude > 180
    ):
        raise ValueError("Ungültige Kartenkoordinaten.")

    if not settings.WEATHER_API_KEY:
        raise ValueError("Punktwetter ist ohne API-Schlüssel nicht verfügbar.")

    current = _fetch_json(
        f"{settings.WEATHER_API_BASE_URL}/weather",
        {
            "lat": latitude,
            "lon": longitude,
            "appid": settings.WEATHER_API_KEY,
            "units": "metric",
            "lang": "de",
        },
    )
    main = current.get("main") or {}
    weather = (current.get("weather") or [{}])[0]
    temperature = _coerce_float(main.get("temp"))

    if temperature is None or not math.isfinite(temperature):
        raise RuntimeError("Wetterdienst hat keine Temperatur geliefert.")

    feels_like = _coerce_float(main.get("feels_like"))
    if feels_like is None or not math.isfinite(feels_like):
        feels_like = temperature

    location_name = (current.get("name") or "").strip()
    country = ((current.get("sys") or {}).get("country") or "").strip()
    if location_name and country:
        location_name = f"{location_name}, {country}"
    if not location_name:
        location_name = f"{latitude:.2f}°, {longitude:.2f}°"

    description = _format_weather_description(weather.get("description"))
    return {
        "location": location_name,
        "latitude": round(latitude, 4),
        "longitude": round(longitude, 4),
        "temperature": round(temperature, 1),
        "feels_like": round(feels_like, 1),
        "description": description,
    }


def _location_from_request(params, user=None):
    lat = params.get("lat", "").strip()
    lon = params.get("lon", "").strip()
    label = params.get("label", "").strip()
    name = params.get("name", "").strip()
    details = params.get("details", "").strip()
    query = params.get("q", "").strip()

    if lat and lon:
        return {
            "lat": lat,
            "lon": lon,
            "name": name or _short_location_name(label or query),
            "details": details or _location_details_from_label(label),
            "label": label or query or "Ausgewählter Ort",
        }

    if query:
        return {"query": query, "label": query}

    default_location = _default_weather_location_dict(user)
    if default_location:
        return default_location

    return {
        "query": _weather_default_city_for(user),
        "label": "Standardort",
        "is_default": True,
    }


def _weather_default_city_for(user=None):
    if user and getattr(user, "is_authenticated", False):
        try:
            default_city = user.profile.weather_default_city.strip()
        except Exception:
            default_city = ""
        if default_city:
            return default_city
    return settings.WEATHER_DEFAULT_CITY


def _default_weather_location_dict(user):
    if not user or not getattr(user, "is_authenticated", False):
        return None
    location = WeatherLocation.objects.filter(user=user, is_default=True).first()
    if not location:
        return None
    result = weather_location_to_dict(location)
    result["is_default"] = True
    return result


def weather_location_to_dict(location):
    result = {
        "name": location.name,
        "details": location.details,
        "label": location.label or location.name or location.query,
    }
    if location.lat is not None and location.lon is not None:
        result["lat"] = location.lat
        result["lon"] = location.lon
    elif location.query:
        result["query"] = location.query
    return result


def list_weather_locations(user):
    return list(WeatherLocation.objects.filter(user=user).order_by("order", "id"))


def save_weather_location(user, *, name, lat, lon, details="", label=""):
    lat = _coerce_float(lat)
    lon = _coerce_float(lon)
    existing = list(WeatherLocation.objects.filter(user=user).order_by("order", "id"))

    if lat is not None and lon is not None:
        for location in existing:
            if (
                location.lat is not None
                and location.lon is not None
                and round(location.lat, _LOCATION_DEDUPE_PRECISION) == round(lat, _LOCATION_DEDUPE_PRECISION)
                and round(location.lon, _LOCATION_DEDUPE_PRECISION) == round(lon, _LOCATION_DEDUPE_PRECISION)
            ):
                return location, False

    if len(existing) >= MAX_WEATHER_LOCATIONS:
        raise ValueError(f"Es können höchstens {MAX_WEATHER_LOCATIONS} Orte gespeichert werden.")

    location = WeatherLocation.objects.create(
        user=user,
        name=name or "",
        lat=lat,
        lon=lon,
        details=details or "",
        label=label or name or "",
        order=len(existing),
        is_default=not existing,
    )
    return location, True


def delete_weather_location(user, location_id):
    location_id = _coerce_location_id(location_id)
    if location_id is None:
        return
    location = WeatherLocation.objects.filter(user=user, pk=location_id).first()
    if not location:
        return
    was_default = location.is_default
    location.delete()
    if was_default:
        next_location = WeatherLocation.objects.filter(user=user).order_by("order", "id").first()
        if next_location:
            next_location.is_default = True
            next_location.save(update_fields=["is_default"])


def set_default_weather_location(user, location_id):
    location_id = _coerce_location_id(location_id)
    if location_id is None:
        return
    location = WeatherLocation.objects.filter(user=user, pk=location_id).first()
    if not location:
        return
    WeatherLocation.objects.filter(user=user).exclude(pk=location.pk).update(is_default=False)
    if not location.is_default:
        location.is_default = True
        location.save(update_fields=["is_default"])


def _geocode_first(query):
    suggestions = get_location_suggestions(query, limit=1)
    if not suggestions:
        return {}
    return suggestions[0]


def _weather_api_params(location):
    params = {
        "appid": settings.WEATHER_API_KEY,
        "units": "metric",
        "lang": "de",
    }

    if location.get("lat") and location.get("lon"):
        params["lat"] = location["lat"]
        params["lon"] = location["lon"]
    else:
        params["q"] = location.get("query", settings.WEATHER_DEFAULT_CITY)

    return params


def _fetch_json(endpoint, params):
    query = urlencode(sorted(params.items()))
    request_url = f"{endpoint}?{query}"
    cache_seconds = max(0, getattr(settings, "WEATHER_CACHE_SECONDS", 600))
    cache_key = f"weather:json:{sha256(request_url.encode('utf-8')).hexdigest()}"

    if cache_seconds:
        cached = cache.get(cache_key)
        if cached is not None:
            return deepcopy(cached)

    request = Request(request_url, headers={"User-Agent": "Lunora/1.0"})

    with urlopen(request, timeout=5) as response:
        data = json.loads(response.read().decode("utf-8"))

    if cache_seconds:
        cache.set(cache_key, data, cache_seconds)
    return data


WEATHER_MAP_LAYERS = {
    "temperature": {
        "provider_layer": "temp_new",
        "label": "Temperatur",
        "icon": "fa-temperature-half",
        "unit": "°C",
        "legend_start": "Kälter",
        "legend_end": "Wärmer",
        "legend_class": "is-temperature",
        "hint": "Die Farben zeigen die aktuelle Lufttemperatur.",
        "opacity": 0.72,
    },
    "precipitation": {
        "provider_layer": "precipitation_new",
        "label": "Niederschlag",
        "icon": "fa-cloud-rain",
        "unit": "mm/h",
        "legend_start": "Leicht",
        "legend_end": "Stark",
        "legend_class": "is-precipitation",
        "hint": "Keine Einfärbung bedeutet aktuell kein Niederschlag.",
        "opacity": 0.88,
    },
    "clouds": {
        "provider_layer": "clouds_new",
        "label": "Wolken",
        "icon": "fa-cloud",
        "unit": "%",
        "legend_start": "0 %",
        "legend_end": "100 %",
        "legend_class": "is-clouds",
        "hint": "Helle Flächen zeigen eine stärkere Bewölkung.",
        "opacity": 0.82,
    },
    "wind": {
        "provider_layer": "wind_new",
        "label": "Wind",
        "icon": "fa-wind",
        "unit": "m/s",
        "legend_start": "Ruhig",
        "legend_end": "Stark",
        "legend_class": "is-wind",
        "hint": "Die Farben zeigen die aktuelle Windgeschwindigkeit.",
        "opacity": 0.78,
    },
    "pressure": {
        "provider_layer": "pressure_new",
        "label": "Luftdruck",
        "icon": "fa-gauge-high",
        "unit": "hPa",
        "legend_start": "Niedrig",
        "legend_end": "Hoch",
        "legend_class": "is-pressure",
        "hint": "Die Farben zeigen den Luftdruck auf Meereshöhe.",
        "opacity": 0.72,
    },
}


def fetch_weather_map_tile(z, x, y, layer="temperature"):
    if not settings.WEATHER_API_KEY:
        raise ValueError("Wetterkarte ist ohne API-Schlüssel nicht verfügbar.")

    layer_config = WEATHER_MAP_LAYERS.get(layer)
    if not layer_config:
        raise ValueError("Ungültige Wetterkarten-Ebene.")

    if z < 1 or z > 10:
        raise ValueError("Ungültige Wetterkarten-Kachel.")

    max_tile = 2 ** z
    if x < 0 or y < 0 or x >= max_tile or y >= max_tile:
        raise ValueError("Ungültige Wetterkarten-Kachel.")

    query = urlencode({"appid": settings.WEATHER_API_KEY})
    tile_base_url = settings.WEATHER_TILE_BASE_URL.rstrip("/")
    endpoint = f"{tile_base_url}/{layer_config['provider_layer']}/{z}/{x}/{y}.png?{query}"
    request = Request(endpoint, headers={"User-Agent": "Lunora Weather Map/1.0"})

    with urlopen(request, timeout=6) as response:
        return response.read(1_500_000)


def _normalize_location(item):
    local_names = item.get("local_names") or {}
    name = local_names.get("de") or item.get("name", "")
    state = item.get("state", "")
    country = item.get("country", "")
    details = [part for part in [state, country] if part]
    detail_label = ", ".join(details)

    return {
        "name": name,
        "state": state,
        "country": country,
        "lat": item.get("lat"),
        "lon": item.get("lon"),
        "details": detail_label,
        "label": ", ".join([name, *details]),
    }


def _build_context_from_api(current, forecast, fallback, location):
    weather = current.get("weather", [{}])[0]
    main = current.get("main", {})
    wind = current.get("wind", {})
    sys = current.get("sys", {})

    location_label = location.get("label", "")
    city_name = (
        location.get("name")
        or current.get("name")
        or _short_location_name(location_label)
        or "Bünde"
    )
    location_detail = location.get("details") or _location_details_from_label(location_label)
    temperature = round(main.get("temp", 24))
    feels_like = round(main.get("feels_like", temperature))
    description = _format_weather_description(weather.get("description"), "Teilweise bewölkt")
    updated = datetime.fromtimestamp(current.get("dt", datetime.now().timestamp()))

    sunrise = _format_time(sys.get("sunrise"))
    sunset = _format_time(sys.get("sunset"))

    context = deepcopy(fallback)
    context["search_query"] = "" if location.get("is_default") else location.get("label", "")
    context["current"] = {
        "city": city_name,
        "detail": location_detail,
        "label": "Ausgewählter Ort" if location.get("lat") else "Mein Standort",
        "temperature": temperature,
        "feels_like": feels_like,
        "description": description,
        "high": round(main.get("temp_max", temperature + 2)),
        "low": round(main.get("temp_min", temperature - 4)),
        "updated": updated.strftime("heute, %H:%M Uhr"),
        "icon": _icon_for_weather(weather.get("main", "")),
        "latitude": location.get("lat"),
        "longitude": location.get("lon"),
        "place_label": location.get("label") or city_name,
    }
    context["summary"] = [
        {
            "icon": "fa-droplet",
            "label": "Niederschlag",
            "value": f"{_precipitation_percent(forecast)} %",
            "hint": "Geringe Chance",
        },
        {
            "icon": "fa-droplet",
            "label": "Luftfeuchtigkeit",
            "value": f"{main.get('humidity', 56)} %",
            "hint": "Angenehm",
        },
        {
            "icon": "fa-wind",
            "label": "Wind",
            "value": f"{round(wind.get('speed', 4) * 3.6)} km/h",
            "hint": "W - Mäßig",
        },
        {
            "icon": "fa-sun",
            "label": "UV-Index",
            "value": "4",
            "hint": "Mäßig",
        },
        {
            "icon": "fa-gauge",
            "label": "Luftdruck",
            "value": f"{main.get('pressure', 1016)} hPa",
            "hint": "Stabil",
        },
        {
            "icon": "fa-cloud-sun",
            "label": "Sonnenaufgang / Sonnenuntergang",
            "value": f"{sunrise} / {sunset}",
            "hint": "",
        },
    ]
    context["hourly_forecast"] = _hourly_from_api(forecast) or fallback["hourly_forecast"]
    context["daily_forecast"] = _daily_from_api(forecast) or fallback["daily_forecast"]
    context["forecast_summary"] = _forecast_summary(context["daily_forecast"])
    context["weather_tip"] = _build_weather_tip(current, forecast, weather)
    context["weather_hint"] = context["weather_tip"]["text"]
    context["weather_alert"] = _build_weather_alert(current, forecast, weather)
    context["weather_map"] = _weather_map_context_for_location(location, current, city_name)
    return context


def _hourly_from_api(forecast):
    items = []
    for item in forecast.get("list", [])[:8]:
        when = datetime.fromtimestamp(item.get("dt", 0))
        weather = item.get("weather", [{}])[0]
        items.append(
            {
                "time": "Jetzt" if not items else when.strftime("%H:%M"),
                "icon": _icon_for_weather(weather.get("main", "")),
                "temperature": round(item.get("main", {}).get("temp", 24)),
                "rain": round(item.get("pop", 0.1) * 100),
            }
        )
    return items


def _daily_from_api(forecast):
    days = {}
    for item in forecast.get("list", []):
        when = datetime.fromtimestamp(item.get("dt", 0))
        key = when.date().isoformat()
        main = item.get("main", {})
        weather = item.get("weather", [{}])[0]
        day = days.setdefault(
            key,
            {
                "day": _weekday_name(when),
                "icon": _icon_for_weather(weather.get("main", "")),
                "description": _format_weather_description(weather.get("description"), "Bewölkt"),
                "high": round(main.get("temp_max", 24)),
                "low": round(main.get("temp_min", 16)),
                "rain": round(item.get("pop", 0.1) * 100),
            },
        )
        day["high"] = max(day["high"], round(main.get("temp_max", day["high"])))
        day["low"] = min(day["low"], round(main.get("temp_min", day["low"])))
        day["rain"] = max(day["rain"], round(item.get("pop", 0) * 100))

    return list(days.values())[1:7]


def _forecast_summary(daily_forecast):
    days = daily_forecast or []
    if not days:
        return {
            "average_high": "-",
            "rain_days": "0",
            "trend": "Ruhig",
        }

    highs = [day["high"] for day in days if isinstance(day.get("high"), (int, float))]
    lows = [day["low"] for day in days if isinstance(day.get("low"), (int, float))]
    rain_days = sum(1 for day in days if day.get("rain", 0) >= 40)
    average_high = round(sum(highs) / len(highs)) if highs else "-"

    first_high = highs[0] if highs else None
    last_high = highs[-1] if highs else None
    average_rain = sum(day.get("rain", 0) for day in days) / len(days)
    rainy_descriptions = ("regen", "schauer", "gewitter", "drizzle", "rain")
    has_rain_text = any(
        any(word in day.get("description", "").casefold() for word in rainy_descriptions)
        for day in days
    )

    if average_rain >= 55 or rain_days >= max(2, len(days) // 2) or has_rain_text:
        trend = "Nass"
    elif first_high is not None and last_high is not None and last_high - first_high >= 3:
        trend = "Wärmer"
    elif first_high is not None and last_high is not None and first_high - last_high >= 3:
        trend = "Kühler"
    elif lows and max(highs or [0]) >= 27:
        trend = "Warm"
    elif highs and max(highs) <= 5:
        trend = "Kalt"
    else:
        trend = "Stabil"

    return {
        "average_high": f"{average_high}°" if isinstance(average_high, int) else average_high,
        "rain_days": str(rain_days),
        "trend": trend,
    }


def _fallback_for_location(fallback, location, search_query):
    context = deepcopy(fallback)
    selected_label = location.get("label") or search_query
    display_label = (location.get("query") or selected_label) if location.get("is_default") else selected_label
    context["search_query"] = search_query

    if display_label:
        fallback_place = _fallback_place_for_label(display_label)
        context["current"]["city"] = location.get("name") or fallback_place.get("name") or _short_location_name(display_label)
        context["current"]["detail"] = (
            location.get("details")
            or fallback_place.get("details")
            or _location_details_from_label(display_label)
        )
        context["current"]["label"] = "Standardort" if location.get("is_default") else "Demo-Ort"
        context["api_notice"] = (
            "Trage OPENWEATHER_API_KEY in deiner .env ein, um echte Wetterdaten zu laden."
        )

    context["weather_map"] = _weather_map_context_for_location(
        location,
        city_name=context["current"]["city"],
    )
    return context


def _weather_map_context_for_location(location=None, current=None, city_name=""):
    location = location or {}
    current = current or {}
    coord = current.get("coord", {}) if isinstance(current, dict) else {}

    lat = _coerce_float(location.get("lat"))
    lon = _coerce_float(location.get("lon"))
    if lat is None:
        lat = _coerce_float(coord.get("lat"))
    if lon is None:
        lon = _coerce_float(coord.get("lon"))
    label = (
        location.get("name")
        or _short_location_name(location.get("query", ""))
        or _short_location_name(location.get("label", ""))
        or city_name
    )

    if lat is None or lon is None:
        fallback_place = _fallback_place_for_label(label or settings.WEATHER_DEFAULT_CITY)
        lat = fallback_place.get("lat", 52.1984)
        lon = fallback_place.get("lon", 8.5864)
        label = fallback_place.get("name", label or "Bünde")

    layers = []
    for layer_id, layer_config in WEATHER_MAP_LAYERS.items():
        layers.append(
            {
                "id": layer_id,
                **{
                    key: value
                    for key, value in layer_config.items()
                    if key != "provider_layer"
                },
            }
        )

    return {
        "center_lat": f"{lat:.4f}",
        "center_lon": f"{lon:.4f}",
        "zoom": 6,
        "location": label or "Bünde",
        "status": "Live-Ebenen" if settings.WEATHER_API_KEY else "Nur Basiskarte",
        "available": bool(settings.WEATHER_API_KEY),
        "default_layer": "temperature",
        "layers": layers,
    }


def _coerce_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_location_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fallback_place_for_label(label):
    matches = _fallback_location_suggestions(label or "", 1)
    if matches:
        return matches[0]
    return {"name": "Bünde", "lat": 52.1984, "lon": 8.5864}


def _normalize_location_search_text(value):
    return (
        (value or "")
        .casefold()
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )


def _fallback_location_suggestions(query, limit):
    places = [
        {"name": "Bünde", "state": "Nordrhein-Westfalen", "country": "DE", "lat": 52.1984, "lon": 8.5864},
        {"name": "Berlin", "state": "Berlin", "country": "DE", "lat": 52.52, "lon": 13.405},
        {"name": "Hamburg", "state": "Hamburg", "country": "DE", "lat": 53.5511, "lon": 9.9937},
        {"name": "München", "state": "Bayern", "country": "DE", "lat": 48.1372, "lon": 11.5755},
        {"name": "Köln", "state": "Nordrhein-Westfalen", "country": "DE", "lat": 50.9375, "lon": 6.9603},
        {"name": "Frankfurt am Main", "state": "Hessen", "country": "DE", "lat": 50.1109, "lon": 8.6821},
        {"name": "London", "state": "England", "country": "GB", "lat": 51.5072, "lon": -0.1276},
        {"name": "Paris", "state": "Île-de-France", "country": "FR", "lat": 48.8566, "lon": 2.3522},
    ]
    needle = _normalize_location_search_text(query)
    matches = [
        {
            **place,
            "details": ", ".join([place["state"], place["country"]]),
            "label": ", ".join([place["name"], place["state"], place["country"]]),
        }
        for place in places
        if needle in _normalize_location_search_text(place["name"])
        or _normalize_location_search_text(place["name"]) in needle
    ]
    return matches[:limit]


def _short_location_name(label):
    if not label:
        return ""
    return label.split(",", 1)[0].strip()


def _location_details_from_label(label):
    if "," not in label:
        return ""
    return label.split(",", 1)[1].strip()


def _precipitation_percent(forecast):
    first_item = next(iter(forecast.get("list", [])), {})
    return round(first_item.get("pop", 0.1) * 100)


def _build_weather_tip(current, forecast, weather):
    hour = timezone.localtime().hour
    period = _day_period(hour)
    condition = weather.get("main", "")
    description = weather.get("description", "").lower()
    rain_chance = _precipitation_percent(forecast)
    temperature = round(current.get("main", {}).get("temp", 24))
    wind_speed = round(current.get("wind", {}).get("speed", 0) * 3.6)

    if condition in {"Rain", "Drizzle", "Thunderstorm"} or rain_chance >= 60:
        return {
            "icon": "fa-umbrella",
            "kicker": f"Tipp für den {period}",
            "title": "Regen im Blick behalten",
            "text": f"Für den {period.lower()} liegt das Regenrisiko bei {rain_chance} %. Nimm lieber etwas Regenschutz mit.",
            "chips": [
                {"icon": "fa-cloud-rain", "label": "Regenschutz"},
                {"icon": "fa-shoe-prints", "label": "Trockene Wege"},
                {"icon": "fa-clock", "label": "Pufferzeit"},
            ],
        }

    if rain_chance >= 30:
        return {
            "icon": "fa-cloud-sun-rain",
            "kicker": f"Tipp für den {period}",
            "title": "Wetter bleibt wechselhaft",
            "text": f"Es bleibt meist ruhig, aber mit {rain_chance} % Regenchance lohnt sich ein kurzer Blick nach draußen.",
            "chips": [
                {"icon": "fa-cloud", "label": "Wolkencheck"},
                {"icon": "fa-bag-shopping", "label": "Leicht packen"},
                {"icon": "fa-route", "label": "Flexibel bleiben"},
            ],
        }

    if temperature >= 30:
        return {
            "icon": "fa-temperature-high",
            "kicker": f"Tipp für den {period}",
            "title": f"Warmer {period}",
            "text": f"Es bleibt trocken und warm bei etwa {temperature}°. Trinken und kurze Pausen tun heute gut.",
            "chips": [
                {"icon": "fa-bottle-water", "label": "Wasser"},
                {"icon": "fa-sun", "label": "Schatten"},
                {"icon": "fa-fan", "label": "Lüften"},
            ],
        }

    if wind_speed >= 28:
        return {
            "icon": "fa-wind",
            "kicker": f"Tipp für den {period}",
            "title": "Etwas windig draußen",
            "text": f"Der {period.lower()} bleibt trocken, aber mit rund {wind_speed} km/h spürbar windig.",
            "chips": [
                {"icon": "fa-wind", "label": "Wind beachten"},
                {"icon": "fa-shirt", "label": "Leichte Jacke"},
                {"icon": "fa-leaf", "label": "Ruhige Route"},
            ],
        }

    if condition == "Clear" or "klar" in description:
        return {
            "icon": "fa-moon" if period in {"Abend", "Nacht"} else "fa-sun",
            "kicker": f"Tipp für den {period}",
            "title": f"Klarer {period}",
            "text": "Es bleibt trocken und klar. Gute Zeit für frische Luft oder einen entspannten Abschluss.",
            "chips": [
                {"icon": "fa-person-walking", "label": "Spaziergang"},
                {"icon": "fa-house", "label": "Lüften"},
                {"icon": "fa-mug-hot", "label": "Ruhig ausklingen"},
            ],
        }

    return {
        "icon": "fa-cloud-sun",
        "kicker": f"Tipp für den {period}",
        "title": f"Ruhiger {period}",
        "text": "Es sieht stabil aus. Plane normal weiter und behalte nur die Wolkenentwicklung im Blick.",
        "chips": [
            {"icon": "fa-cloud", "label": "Stabil"},
            {"icon": "fa-calendar-check", "label": "Planbar"},
            {"icon": "fa-leaf", "label": "Ruhig"},
        ],
    }


def _build_weather_alert(current, forecast, weather):
    """Derive a heuristic severe-weather warning from already-loaded current/forecast data.

    Lunora has no access to official weather-warning feeds (that needs OpenWeather's
    separately-subscribed One Call API), so this is Lunora's own assessment from the
    current/forecast fields already fetched for the page, not an official warning.
    """
    condition = weather.get("main", "")
    rain_chance = _precipitation_percent(forecast)
    temperature = round(current.get("main", {}).get("temp", 24))
    wind_speed = round(current.get("wind", {}).get("speed", 0) * 3.6)

    if condition == "Thunderstorm":
        return {
            "kind": "storm",
            "icon": "fa-bolt",
            "title": "Gewitterwarnung",
            "text": "Aktuell werden Gewitter gemeldet. Meide freie Flächen und suche wenn möglich Schutz.",
        }

    if wind_speed >= 60:
        return {
            "kind": "wind",
            "icon": "fa-wind",
            "title": "Sturmwarnung",
            "text": f"Windgeschwindigkeiten von rund {wind_speed} km/h wurden gemeldet. Sichere loses Material und meide freie Flächen.",
        }

    if rain_chance >= 80:
        return {
            "kind": "rain",
            "icon": "fa-cloud-showers-heavy",
            "title": "Starkregenwarnung",
            "text": f"Die Regenwahrscheinlichkeit liegt bei {rain_chance} %. Rechne mit kurzfristigen Überflutungen auf Straßen und Wegen.",
        }

    if temperature >= 35:
        return {
            "kind": "heat",
            "icon": "fa-temperature-high",
            "title": "Hitzewarnung",
            "text": f"Bei rund {temperature}° besteht erhöhtes Risiko für Kreislaufbeschwerden. Trinke ausreichend und meide direkte Sonne.",
        }

    if temperature <= -10:
        return {
            "kind": "cold",
            "icon": "fa-temperature-low",
            "title": "Kältewarnung",
            "text": f"Bei rund {temperature}° besteht Erfrierungsgefahr im Freien. Kleide dich entsprechend warm.",
        }

    return None


def get_weather_alert_for_location(location):
    """Fetch current/forecast data for a saved location and return a heuristic alert, or None."""
    if not settings.WEATHER_API_KEY:
        return None

    resolved = location
    if resolved.get("query") and not resolved.get("lat"):
        resolved = _geocode_first(resolved["query"])
        if not resolved:
            return None

    try:
        params = _weather_api_params(resolved)
        current = _fetch_json(f"{settings.WEATHER_API_BASE_URL}/weather", params)
        forecast = _fetch_json(f"{settings.WEATHER_API_BASE_URL}/forecast", params)
    except Exception:
        return None

    weather = (current.get("weather") or [{}])[0]
    return _build_weather_alert(current, forecast, weather)


def _fallback_weather_tip():
    period = _day_period(timezone.localtime().hour)

    if period in {"Abend", "Nacht"}:
        return {
            "icon": "fa-moon",
            "kicker": f"Tipp für den {period}",
            "title": f"Ruhiger {period}",
            "text": "Es bleibt in den Demo-Daten trocken und ruhig. Gut für einen entspannten Abschluss.",
            "chips": [
                {"icon": "fa-house", "label": "Lüften"},
                {"icon": "fa-mug-hot", "label": "Runterkommen"},
                {"icon": "fa-cloud", "label": "Trocken"},
            ],
        }

    return {
        "icon": "fa-cloud-sun",
        "kicker": f"Tipp für den {period}",
        "title": f"Ruhiger {period}",
        "text": "Es bleibt in den Demo-Daten überwiegend trocken. Plane normal weiter.",
        "chips": [
            {"icon": "fa-person-walking", "label": "Spaziergang"},
            {"icon": "fa-house", "label": "Lüften"},
            {"icon": "fa-cloud", "label": "Trocken"},
        ],
    }


def _day_period(hour):
    if 5 <= hour < 11:
        return "Morgen"
    if 11 <= hour < 14:
        return "Mittag"
    if 14 <= hour < 18:
        return "Nachmittag"
    if 18 <= hour < 23:
        return "Abend"
    return "Nacht"


def _format_time(timestamp):
    if not timestamp:
        return "05:23"
    return datetime.fromtimestamp(timestamp).strftime("%H:%M")


def _weekday_name(value):
    names = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    return names[value.weekday()]


def _icon_for_weather(condition):
    icons = {
        "Clear": "fa-sun",
        "Clouds": "fa-cloud-sun",
        "Rain": "fa-cloud-rain",
        "Drizzle": "fa-cloud-rain",
        "Thunderstorm": "fa-cloud-bolt",
        "Snow": "fa-snowflake",
        "Mist": "fa-smog",
        "Fog": "fa-smog",
    }
    return icons.get(condition, "fa-cloud-sun")


def _fallback_weather_context():
    weather_tip = _fallback_weather_tip()
    daily_forecast = [
        {"day": "Samstag", "icon": "fa-cloud-sun", "description": "Teilweise bewölkt", "high": 27, "low": 16, "rain": 10},
        {"day": "Sonntag", "icon": "fa-cloud-rain", "description": "Leichter Regen", "high": 22, "low": 14, "rain": 60},
        {"day": "Montag", "icon": "fa-cloud", "description": "Bewölkt", "high": 21, "low": 13, "rain": 20},
        {"day": "Dienstag", "icon": "fa-cloud-sun", "description": "Wolkig", "high": 23, "low": 14, "rain": 20},
        {"day": "Mittwoch", "icon": "fa-sun", "description": "Sonnig", "high": 26, "low": 15, "rain": 10},
        {"day": "Donnerstag", "icon": "fa-sun", "description": "Sonnig", "high": 27, "low": 16, "rain": 10},
        {"day": "Freitag", "icon": "fa-cloud-sun", "description": "Teilweise bewölkt", "high": 25, "low": 15, "rain": 20},
    ]

    return {
        "active_page": "weather",
        "search_query": "",
        "current": {
            "city": "Bünde",
            "detail": "Nordrhein-Westfalen, DE",
            "label": "Mein Standort",
            "temperature": 24,
            "feels_like": 25,
            "description": "Teilweise bewölkt",
            "high": 27,
            "low": 16,
            "updated": "heute, 10:25 Uhr",
            "icon": "fa-cloud-sun",
            "latitude": None,
            "longitude": None,
            "place_label": "",
        },
        "summary": [
            {"icon": "fa-droplet", "label": "Niederschlag", "value": "10 %", "hint": "Geringe Chance"},
            {"icon": "fa-droplet", "label": "Luftfeuchtigkeit", "value": "56 %", "hint": "Angenehm"},
            {"icon": "fa-wind", "label": "Wind", "value": "14 km/h", "hint": "W - Mäßig"},
            {"icon": "fa-sun", "label": "UV-Index", "value": "4", "hint": "Mäßig"},
            {"icon": "fa-gauge", "label": "Luftdruck", "value": "1016 hPa", "hint": "Stabil"},
            {"icon": "fa-cloud-sun", "label": "Sonnenaufgang / Sonnenuntergang", "value": "05:23 / 21:21", "hint": ""},
        ],
        "hourly_forecast": [
            {"time": "Jetzt", "icon": "fa-cloud-sun", "temperature": 24, "rain": 10},
            {"time": "11:00", "icon": "fa-cloud", "temperature": 25, "rain": 10},
            {"time": "12:00", "icon": "fa-cloud-sun", "temperature": 26, "rain": 10},
            {"time": "13:00", "icon": "fa-cloud", "temperature": 26, "rain": 20},
            {"time": "14:00", "icon": "fa-cloud-rain", "temperature": 25, "rain": 30},
            {"time": "15:00", "icon": "fa-cloud-showers-heavy", "temperature": 24, "rain": 40},
            {"time": "16:00", "icon": "fa-cloud", "temperature": 23, "rain": 30},
            {"time": "17:00", "icon": "fa-cloud-sun", "temperature": 22, "rain": 20},
            {"time": "18:00", "icon": "fa-sun", "temperature": 21, "rain": 10},
        ],
        "daily_forecast": daily_forecast,
        "forecast_summary": _forecast_summary(daily_forecast),
        "air_quality": {"score": 28, "label": "Gut"},
        "weather_map": _weather_map_context_for_location(city_name="Bünde"),
        "weather_tip": weather_tip,
        "weather_hint": weather_tip["text"],
        "weather_alert": None,
        "api_notice": "",
    }
