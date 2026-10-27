from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from app.models import (
    Profile,
    SystemSettings,
    WeatherLocation,
)
from app.services.weather_service import (
    WEATHER_MAP_LAYERS,
    _build_weather_alert,
    _format_weather_description,
    delete_weather_location,
    fetch_weather_map_tile,
    get_location_suggestions,
    get_weather_alert_for_location,
    get_weather_at_coordinates,
    get_weather_context,
    list_weather_locations,
    save_weather_location,
    set_default_weather_location,
)
from app.tests.helpers import FakeWeatherResponse, FakeWeatherTileResponse


class WeatherDescriptionTests(SimpleTestCase):
    def test_weather_descriptions_follow_german_capitalization(self):
        examples = {
            "ein paar wolken": "Ein paar Wolken",
            "leichter regen": "Leichter Regen",
            "gewitter mit schnee": "Gewitter mit Schnee",
            "mäßig bewölkt": "Mäßig bewölkt",
        }

        for description, expected in examples.items():
            with self.subTest(description=description):
                self.assertEqual(_format_weather_description(description), expected)


class WeatherMapTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="map@example.com",
            email="map@example.com",
            password="secret-12345",
            first_name="Map",
        )
        Profile.objects.create(user=self.user, display_name="Map")

    @override_settings(WEATHER_API_KEY="")
    def test_weather_page_renders_interactive_weather_map(self):
        self.client.login(username="map@example.com", password="secret-12345")

        response = self.client.get("/weather/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-weather-map")
        self.assertContains(response, "data-weather-map-canvas")
        self.assertContains(response, "data-weather-map-reset")
        self.assertContains(response, "data-weather-map-fullscreen")
        self.assertContains(response, "data-hourly-carousel")
        self.assertContains(response, "data-hourly-scroll")
        self.assertContains(response, "data-hourly-previous")
        self.assertContains(response, "data-hourly-next")
        self.assertContains(response, "Frühere Stunden anzeigen")
        self.assertContains(response, "Spätere Stunden anzeigen")
        self.assertContains(response, 'data-weather-map-point-url="/weather/point/"')
        self.assertContains(response, "per Klick die Temperatur eines Ortes abrufen")
        self.assertContains(response, "Keine Einfärbung bedeutet aktuell kein Niederschlag.")
        for layer in WEATHER_MAP_LAYERS:
            self.assertContains(response, f'data-weather-map-layer="{layer}"')
        self.assertContains(response, 'aria-disabled="true"', count=5)

    @override_settings(WEATHER_API_KEY="")
    def test_weather_map_tile_requires_api_key(self):
        self.client.login(username="map@example.com", password="secret-12345")

        response = self.client.get("/weather/map/temperature/7/67/43.png")

        self.assertEqual(response.status_code, 404)

    @override_settings(WEATHER_API_KEY="test-key")
    def test_weather_map_tile_proxies_png(self):
        self.client.login(username="map@example.com", password="secret-12345")

        with patch("app.views.weather_views.fetch_weather_map_tile", return_value=b"png-bytes") as fetch_tile:
            response = self.client.get("/weather/map/wind/7/67/43.png")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertIn("max-age=300", response["Cache-Control"])
        self.assertEqual(response.content, b"png-bytes")
        fetch_tile.assert_called_once_with(7, 67, 43, layer="wind")

    def test_weather_map_tile_requires_login(self):
        response = self.client.get("/weather/map/temperature/7/67/43.png")

        self.assertRedirects(
            response,
            "/login/?next=/weather/map/temperature/7/67/43.png",
        )

    def test_weather_point_requires_login(self):
        response = self.client.get("/weather/point/")

        self.assertRedirects(response, "/login/?next=/weather/point/")

    def test_dashboard_weather_requires_login(self):
        response = self.client.get("/home/weather/")

        self.assertRedirects(response, "/login/?next=/home/weather/")

    def test_dashboard_weather_returns_personalized_summary(self):
        self.client.login(username="map@example.com", password="secret-12345")
        weather_context = {
            "current": {
                "city": "Berlin",
                "temperature": 18,
                "feels_like": 17,
                "description": "Leicht bewölkt",
                "icon": "fa-cloud-sun",
            },
            "daily_forecast": [
                {
                    "day": "Donnerstag",
                    "high": 21,
                    "low": 12,
                    "rain": 25,
                    "description": "Bewölkt",
                    "icon": "fa-cloud",
                }
            ],
        }

        with patch(
            "app.views.weather_views.get_weather_context",
            return_value=weather_context,
        ) as weather_lookup:
            response = self.client.get("/home/weather/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "weather": {
                    "today": {
                        "city": "Berlin",
                        "temperature": 18,
                        "feels_like": 17,
                        "description": "Leicht bewölkt",
                        "icon": "fa-cloud-sun",
                    },
                    "tomorrow": {
                        "day": "Donnerstag",
                        "high": 21,
                        "low": 12,
                        "rain": 25,
                        "description": "Bewölkt",
                        "icon": "fa-cloud",
                    },
                },
            },
        )
        self.assertEqual(response["Cache-Control"], "private, no-store")
        weather_lookup.assert_called_once_with({}, user=self.user)

    def test_dashboard_weather_respects_feature_flag(self):
        SystemSettings.objects.create(weather_enabled=False)
        self.client.login(username="map@example.com", password="secret-12345")

        with patch("app.views.weather_views.get_weather_context") as weather_lookup:
            response = self.client.get("/home/weather/")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["ok"])
        weather_lookup.assert_not_called()

    @override_settings(WEATHER_API_KEY="")
    def test_weather_point_requires_api_key(self):
        self.client.login(username="map@example.com", password="secret-12345")

        response = self.client.get("/weather/point/?lat=52.52&lon=13.405")

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["ok"])

    @override_settings(WEATHER_API_KEY="test-key")
    def test_weather_point_returns_current_temperature(self):
        self.client.login(username="map@example.com", password="secret-12345")
        weather = {
            "location": "Berlin, DE",
            "latitude": 52.52,
            "longitude": 13.405,
            "temperature": 18.4,
            "feels_like": 17.9,
            "description": "Leicht bewölkt",
        }

        with patch(
            "app.views.weather_views.get_weather_at_coordinates",
            return_value=weather,
        ) as point_lookup:
            response = self.client.get("/weather/point/?lat=52.52&lon=13.405")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "weather": weather})
        point_lookup.assert_called_once_with("52.52", "13.405")

    @override_settings(WEATHER_API_KEY="test-key")
    def test_weather_point_rejects_invalid_coordinates(self):
        self.client.login(username="map@example.com", password="secret-12345")

        for latitude, longitude in [("91", "8"), ("52", "181"), ("x", "8"), ("NaN", "8")]:
            with self.subTest(latitude=latitude, longitude=longitude):
                response = self.client.get(
                    "/weather/point/",
                    {"lat": latitude, "lon": longitude},
                )
                self.assertEqual(response.status_code, 400)

    @override_settings(
        WEATHER_API_KEY="test-key",
        WEATHER_API_BASE_URL="https://weather.example.test/data/2.5",
    )
    def test_weather_point_service_maps_provider_response(self):
        provider_response = {
            "name": "Berlin",
            "sys": {"country": "DE"},
            "main": {"temp": 18.44, "feels_like": 17.86},
            "weather": [{"description": "leicht bewölkt"}],
        }

        with patch(
            "app.services.weather_service._fetch_json",
            return_value=provider_response,
        ) as fetch_json:
            weather = get_weather_at_coordinates("52.52", "13.405")

        self.assertEqual(weather["location"], "Berlin, DE")
        self.assertEqual(weather["temperature"], 18.4)
        self.assertEqual(weather["feels_like"], 17.9)
        self.assertEqual(weather["description"], "Leicht bewölkt")
        fetch_json.assert_called_once_with(
            "https://weather.example.test/data/2.5/weather",
            {
                "lat": 52.52,
                "lon": 13.405,
                "appid": "test-key",
                "units": "metric",
                "lang": "de",
            },
        )

    @override_settings(
        WEATHER_API_KEY="test-key",
        WEATHER_TILE_BASE_URL="https://tiles.example.test/map",
    )
    def test_weather_map_service_maps_all_supported_layers(self):
        expected_layers = {
            "temperature": "temp_new",
            "precipitation": "precipitation_new",
            "clouds": "clouds_new",
            "wind": "wind_new",
            "pressure": "pressure_new",
        }

        for layer, provider_layer in expected_layers.items():
            with self.subTest(layer=layer):
                with patch(
                    "app.services.weather_service.urlopen",
                    return_value=FakeWeatherTileResponse(),
                ) as mocked_urlopen:
                    tile = fetch_weather_map_tile(7, 67, 43, layer=layer)

                self.assertEqual(tile, b"png-bytes")
                request = mocked_urlopen.call_args.args[0]
                self.assertIn(f"/map/{provider_layer}/7/67/43.png", request.full_url)
                self.assertIn("appid=test-key", request.full_url)

    @override_settings(WEATHER_API_KEY="test-key")
    def test_weather_map_service_rejects_invalid_layer_and_coordinates(self):
        with self.assertRaisesMessage(ValueError, "Ungültige Wetterkarten-Ebene"):
            fetch_weather_map_tile(7, 67, 43, layer="snow")

        for coordinates in [(0, 0, 0), (11, 0, 0), (7, 128, 43), (7, 67, 128)]:
            with self.subTest(coordinates=coordinates):
                with self.assertRaisesMessage(ValueError, "Ungültige Wetterkarten-Kachel"):
                    fetch_weather_map_tile(*coordinates, layer="temperature")

    @override_settings(WEATHER_API_KEY="")
    def test_weather_forecast_summary_uses_daily_forecast_values(self):
        context = get_weather_context({})

        self.assertEqual(context["forecast_summary"]["average_high"], "24°")
        self.assertEqual(context["forecast_summary"]["rain_days"], "1")
        self.assertEqual(context["forecast_summary"]["trend"], "Nass")

    @override_settings(WEATHER_API_KEY="")
    def test_weather_context_uses_profile_default_city(self):
        self.user.profile.weather_default_city = "Berlin,de"
        self.user.profile.save(update_fields=["weather_default_city"])

        context = get_weather_context({}, user=self.user)

        self.assertEqual(context["current"]["city"], "Berlin")
        self.assertEqual(context["current"]["label"], "Standardort")
        self.assertEqual(context["search_query"], "")
        self.assertEqual(context["weather_map"]["location"], "Berlin")
        self.assertEqual(context["weather_map"]["default_layer"], "temperature")

    @override_settings(WEATHER_API_KEY="")
    def test_weather_page_can_save_current_place_as_default(self):
        self.client.login(username="map@example.com", password="secret-12345")

        response = self.client.post(
            "/weather/",
            {
                "form_name": "weather_default",
                "weather_default_city": "Berlin",
            },
        )

        self.assertRedirects(response, "/weather/")
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.weather_default_city, "Berlin")

    @override_settings(WEATHER_API_KEY="test-key", WEATHER_CACHE_SECONDS=600)
    def test_location_suggestions_cache_api_responses(self):
        cache.clear()
        payload = [{"name": "Berlin", "state": "Berlin", "country": "DE", "lat": 52.52, "lon": 13.405}]

        with patch(
            "app.services.weather_service.urlopen", return_value=FakeWeatherResponse(payload)
        ) as mocked_urlopen:
            first = get_location_suggestions("Berlin")
            second = get_location_suggestions("Berlin")

        self.assertEqual(first, second)
        self.assertEqual(first[0]["name"], "Berlin")
        self.assertEqual(mocked_urlopen.call_count, 1)

    @override_settings(WEATHER_API_KEY="")
    def test_weather_page_renders_calculated_forecast_summary(self):
        self.client.login(username="map@example.com", password="secret-12345")

        response = self.client.get("/weather/")

        self.assertContains(response, "24°")
        self.assertContains(response, "Regentage")
        self.assertContains(response, "Nass")
        self.assertNotContains(response, "31°")

    def test_build_weather_alert_detects_severe_conditions(self):
        base_current = {"main": {"temp": 20}, "wind": {"speed": 0}}
        base_forecast = {"list": [{"pop": 0.1}]}

        thunder = _build_weather_alert(base_current, base_forecast, {"main": "Thunderstorm"})
        self.assertEqual(thunder["kind"], "storm")

        windy = _build_weather_alert(
            {"main": {"temp": 20}, "wind": {"speed": 20}}, base_forecast, {"main": "Clear"}
        )
        self.assertEqual(windy["kind"], "wind")

        rainy = _build_weather_alert(base_current, {"list": [{"pop": 0.9}]}, {"main": "Rain"})
        self.assertEqual(rainy["kind"], "rain")

        hot = _build_weather_alert(
            {"main": {"temp": 36}, "wind": {"speed": 0}}, base_forecast, {"main": "Clear"}
        )
        self.assertEqual(hot["kind"], "heat")

        cold = _build_weather_alert(
            {"main": {"temp": -12}, "wind": {"speed": 0}}, base_forecast, {"main": "Clear"}
        )
        self.assertEqual(cold["kind"], "cold")

        self.assertIsNone(_build_weather_alert(base_current, base_forecast, {"main": "Clear"}))

    @override_settings(WEATHER_API_KEY="")
    def test_get_weather_alert_for_location_requires_api_key(self):
        self.assertIsNone(get_weather_alert_for_location({"lat": 52.5, "lon": 13.4}))

    @override_settings(WEATHER_API_KEY="test-key", WEATHER_CACHE_SECONDS=0)
    def test_get_weather_alert_for_location_maps_provider_response(self):
        current_payload = {
            "weather": [{"main": "Thunderstorm", "description": "Gewitter"}],
            "main": {"temp": 22, "feels_like": 22},
            "wind": {"speed": 3},
        }
        forecast_payload = {"list": [{"pop": 0.2}]}

        with patch(
            "app.services.weather_service.urlopen",
            side_effect=[FakeWeatherResponse(current_payload), FakeWeatherResponse(forecast_payload)],
        ):
            alert = get_weather_alert_for_location({"lat": 52.5, "lon": 13.4})

        self.assertEqual(alert["kind"], "storm")

    def test_save_weather_location_dedupes_and_sets_first_as_default(self):
        location, created = save_weather_location(
            self.user, name="Berlin", lat=52.52, lon=13.405, details="DE", label="Berlin, DE"
        )
        self.assertTrue(created)
        self.assertTrue(location.is_default)

        duplicate, created_again = save_weather_location(
            self.user, name="Berlin", lat=52.5203, lon=13.4048, details="DE", label="Berlin, DE"
        )
        self.assertFalse(created_again)
        self.assertEqual(duplicate.pk, location.pk)
        self.assertEqual(list_weather_locations(self.user), [location])

    def test_save_weather_location_enforces_cap(self):
        for index in range(8):
            save_weather_location(
                self.user, name=f"Ort {index}", lat=10 + index, lon=10 + index, details="", label=""
            )

        with self.assertRaises(ValueError):
            save_weather_location(self.user, name="Ort 9", lat=50, lon=50, details="", label="")

    def test_delete_weather_location_promotes_next_default(self):
        first, _ = save_weather_location(
            self.user, name="Berlin", lat=52.52, lon=13.405, details="", label="Berlin"
        )
        second, _ = save_weather_location(
            self.user, name="Hamburg", lat=53.55, lon=9.99, details="", label="Hamburg"
        )

        delete_weather_location(self.user, first.pk)

        second.refresh_from_db()
        self.assertTrue(second.is_default)
        self.assertEqual(list(WeatherLocation.objects.filter(user=self.user)), [second])

    def test_set_default_weather_location_switches_default(self):
        first, _ = save_weather_location(
            self.user, name="Berlin", lat=52.52, lon=13.405, details="", label="Berlin"
        )
        second, _ = save_weather_location(
            self.user, name="Hamburg", lat=53.55, lon=9.99, details="", label="Hamburg"
        )

        set_default_weather_location(self.user, second.pk)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertTrue(second.is_default)

    @override_settings(WEATHER_API_KEY="")
    def test_location_from_request_prefers_default_weather_location(self):
        save_weather_location(
            self.user, name="Hamburg", lat=53.55, lon=9.99, details="DE", label="Hamburg, DE"
        )

        context = get_weather_context({}, user=self.user)

        self.assertEqual(context["current"]["city"], "Hamburg")
        self.assertEqual(context["current"]["label"], "Standardort")

    def test_weather_page_can_save_and_manage_locations(self):
        self.client.login(username="map@example.com", password="secret-12345")

        save_response = self.client.post(
            "/weather/",
            {
                "form_name": "location_save",
                "name": "Berlin",
                "lat": "52.52",
                "lon": "13.405",
                "details": "DE",
                "label": "Berlin, DE",
            },
        )
        self.assertRedirects(save_response, "/weather/")
        location = WeatherLocation.objects.get(user=self.user, name="Berlin")
        self.assertTrue(location.is_default)

        second_response = self.client.post(
            "/weather/",
            {
                "form_name": "location_save",
                "name": "Hamburg",
                "lat": "53.55",
                "lon": "9.99",
                "details": "DE",
                "label": "Hamburg, DE",
            },
        )
        self.assertRedirects(second_response, "/weather/")
        second_location = WeatherLocation.objects.get(user=self.user, name="Hamburg")

        set_default_response = self.client.post(
            "/weather/",
            {"form_name": "location_set_default", "location_id": second_location.pk},
        )
        self.assertRedirects(set_default_response, "/weather/")
        location.refresh_from_db()
        second_location.refresh_from_db()
        self.assertFalse(location.is_default)
        self.assertTrue(second_location.is_default)

        delete_response = self.client.post(
            "/weather/",
            {"form_name": "location_delete", "location_id": location.pk},
        )
        self.assertRedirects(delete_response, "/weather/")
        self.assertFalse(WeatherLocation.objects.filter(pk=location.pk).exists())

        overview = self.client.get("/weather/")
        self.assertContains(overview, "Hamburg")
