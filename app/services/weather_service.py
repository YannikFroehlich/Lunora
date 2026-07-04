import json
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone


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


WEATHER_RADAR_TILE_LAYERS = {
    "clouds": "clouds_new",
    "precipitation": "precipitation_new",
}


def fetch_weather_radar_tile(z, x, y, layer="precipitation"):
    if not settings.WEATHER_API_KEY:
        raise ValueError("Wetter-Radar ist ohne API-Key nicht verfuegbar.")

    tile_layer = WEATHER_RADAR_TILE_LAYERS.get(layer)
    if not tile_layer:
        raise ValueError("Ungueltige Radar-Ebene.")

    if z < 1 or z > 10:
        raise ValueError("Ungueltige Radar-Kachel.")

    max_tile = 2 ** z
    if x < 0 or y < 0 or x >= max_tile or y >= max_tile:
        raise ValueError("Ungueltige Radar-Kachel.")

    query = urlencode({"appid": settings.WEATHER_API_KEY})
    tile_base_url = settings.WEATHER_TILE_BASE_URL.rstrip("/")
    endpoint = f"{tile_base_url}/{tile_layer}/{z}/{x}/{y}.png?{query}"
    request = Request(endpoint, headers={"User-Agent": "Lunora Radar/1.0"})

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
    description = weather.get("description", "Teilweise bewölkt").capitalize()
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
    context["radar"] = _radar_context_for_location(location, current, city_name)
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
                "description": weather.get("description", "Bewölkt").capitalize(),
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
    display_label = location.get("query") if location.get("is_default") else selected_label
    context["search_query"] = search_query

    if display_label:
        context["current"]["city"] = location.get("name") or _short_location_name(display_label)
        context["current"]["detail"] = location.get("details") or _location_details_from_label(display_label)
        context["current"]["label"] = "Standardort" if location.get("is_default") else "Demo-Ort"
        context["api_notice"] = (
            "Trage OPENWEATHER_API_KEY in deiner .env ein, um echte Wetterdaten zu laden."
        )

    context["radar"] = _radar_context_for_location(location, city_name=context["current"]["city"])
    return context


def _radar_context_for_location(location=None, current=None, city_name=""):
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
        label = fallback_place.get("name", label or "Buende")

    return {
        "center_lat": f"{lat:.4f}",
        "center_lon": f"{lon:.4f}",
        "zoom": 7,
        "location": label or "Buende",
        "status": "Live-Radar" if settings.WEATHER_API_KEY else "Demo-Vorschau",
        "has_live": bool(settings.WEATHER_API_KEY),
    }


def _coerce_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fallback_place_for_label(label):
    matches = _fallback_location_suggestions(label or "", 1)
    if matches:
        return matches[0]
    return {"name": "Buende", "lat": 52.1984, "lon": 8.5864}


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
    needle = query.casefold()
    matches = [
        {
            **place,
            "details": ", ".join([place["state"], place["country"]]),
            "label": ", ".join([place["name"], place["state"], place["country"]]),
        }
        for place in places
        if needle in place["name"].casefold()
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
        "radar": _radar_context_for_location(city_name="Bünde"),
        "weather_tip": weather_tip,
        "weather_hint": weather_tip["text"],
        "api_notice": "",
    }
