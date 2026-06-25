import json
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings


def get_weather_context():
    """Return weather data from OpenWeather, or calm demo data without a key."""
    fallback = _fallback_weather_context()
    api_key = settings.WEATHER_API_KEY

    if not api_key:
        return fallback

    try:
        current = _fetch_json(
            f"{settings.WEATHER_API_BASE_URL}/weather",
            {
                "q": settings.WEATHER_DEFAULT_CITY,
                "appid": api_key,
                "units": "metric",
                "lang": "de",
            },
        )
        forecast = _fetch_json(
            f"{settings.WEATHER_API_BASE_URL}/forecast",
            {
                "q": settings.WEATHER_DEFAULT_CITY,
                "appid": api_key,
                "units": "metric",
                "lang": "de",
            },
        )
    except Exception as exc:
        fallback["api_notice"] = (
            "Wetter-API ist gerade nicht erreichbar. Demo-Daten werden angezeigt."
        )
        fallback["api_error"] = exc.__class__.__name__
        return fallback

    return _build_context_from_api(current, forecast, fallback)


def _fetch_json(endpoint, params):
    query = urlencode(params)
    request = Request(f"{endpoint}?{query}", headers={"User-Agent": "Lunora/1.0"})

    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _build_context_from_api(current, forecast, fallback):
    weather = current.get("weather", [{}])[0]
    main = current.get("main", {})
    wind = current.get("wind", {})
    sys = current.get("sys", {})

    city_name = current.get("name") or "Bünde"
    temperature = round(main.get("temp", 24))
    feels_like = round(main.get("feels_like", temperature))
    description = weather.get("description", "Teilweise bewölkt").capitalize()
    updated = datetime.fromtimestamp(current.get("dt", datetime.now().timestamp()))

    sunrise = _format_time(sys.get("sunrise"))
    sunset = _format_time(sys.get("sunset"))

    context = fallback.copy()
    context["current"] = {
        "city": city_name,
        "label": "Mein Standort",
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
                "day": when.strftime("%A"),
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


def _precipitation_percent(forecast):
    first_item = next(iter(forecast.get("list", [])), {})
    return round(first_item.get("pop", 0.1) * 100)


def _format_time(timestamp):
    if not timestamp:
        return "05:23"
    return datetime.fromtimestamp(timestamp).strftime("%H:%M")


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
    return {
        "active_page": "weather",
        "current": {
            "city": "Bünde",
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
        "daily_forecast": [
            {"day": "Samstag", "icon": "fa-cloud-sun", "description": "Teilweise bewölkt", "high": 27, "low": 16, "rain": 10},
            {"day": "Sonntag", "icon": "fa-cloud-rain", "description": "Leichter Regen", "high": 22, "low": 14, "rain": 60},
            {"day": "Montag", "icon": "fa-cloud", "description": "Bewölkt", "high": 21, "low": 13, "rain": 20},
            {"day": "Dienstag", "icon": "fa-cloud-sun", "description": "Wolkig", "high": 23, "low": 14, "rain": 20},
            {"day": "Mittwoch", "icon": "fa-sun", "description": "Sonnig", "high": 26, "low": 15, "rain": 10},
            {"day": "Donnerstag", "icon": "fa-sun", "description": "Sonnig", "high": 27, "low": 16, "rain": 10},
            {"day": "Freitag", "icon": "fa-cloud-sun", "description": "Teilweise bewölkt", "high": 25, "low": 15, "rain": 20},
        ],
        "air_quality": {"score": 28, "label": "Gut"},
        "weather_hint": "Am Nachmittag leichter Regen möglich. Vergiss nicht, einen Regenschirm mitzunehmen.",
        "api_notice": "",
    }
