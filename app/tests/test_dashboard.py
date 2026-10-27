import json
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from app.models import (
    CalendarEvent,
    CalendarReminder,
    Note,
    Profile,
    SystemSettings,
    Task,
    UserNotification,
    VacationYear,
)
from app.services.dashboard import DASHBOARD_WIDGET_IDS, default_dashboard_layout, normalize_dashboard_layout
from app.services.user_preferences import get_user_zoneinfo


class DashboardCustomizationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com",
            email="mira@example.com",
            password="secret-12345",
        )
        self.profile = Profile.objects.create(user=self.user, display_name="Mira")

    def _login(self):
        self.client.login(username="mira@example.com", password="secret-12345")

    def _layout(self, *, order=None, hidden=None, wide=None):
        return {
            "version": 2,
            "order": order or list(DASHBOARD_WIDGET_IDS),
            "hidden": hidden or [],
            "wide": wide or [],
        }

    def test_home_uses_default_layout_and_creates_missing_profile(self):
        self.profile.delete()
        self._login()

        response = self.client.get("/home/")

        self.assertEqual(response.status_code, 200)
        profile = Profile.objects.get(user=self.user)
        self.assertEqual(profile.dashboard_layout, default_dashboard_layout())
        self.assertEqual(
            [widget["id"] for widget in response.context["dashboard_widgets"]],
            list(DASHBOARD_WIDGET_IDS),
        )

    @override_settings(WEATHER_API_KEY="test-key")
    def test_home_defers_weather_provider_requests_until_after_render(self):
        self._login()

        with patch("app.services.weather_service.urlopen") as provider_request:
            response = self.client.get("/home/")

        self.assertEqual(response.status_code, 200)
        provider_request.assert_not_called()
        self.assertContains(response, 'data-dashboard-weather-url="/home/weather/"')
        self.assertContains(response, "Wetter wird geladen …")
        self.assertContains(response, "Vorhersage wird geladen …")

    def test_home_renders_saved_order(self):
        order = [
            "clock",
            "welcome",
            "quick_actions",
            "recent_tools",
            "upcoming_events",
            "weather",
            "tasks",
            "notifications",
            "stats",
        ]
        self.profile.dashboard_layout = self._layout(order=order)
        self.profile.save(update_fields=["dashboard_layout"])
        self._login()

        response = self.client.get("/home/")

        self.assertEqual(
            [widget["id"] for widget in response.context["dashboard_widgets"]],
            order,
        )

    def test_home_renders_wide_widgets_with_span_class(self):
        self.profile.dashboard_layout = self._layout(wide=["weather", "clock"])
        self.profile.save(update_fields=["dashboard_layout"])
        self._login()

        response = self.client.get("/home/")

        widgets_by_id = {widget["id"]: widget for widget in response.context["dashboard_widgets"]}
        self.assertTrue(widgets_by_id["weather"]["wide"])
        self.assertTrue(widgets_by_id["clock"]["wide"])
        self.assertFalse(widgets_by_id["welcome"]["wide"])
        self.assertContains(response, "dashboard-widget--wide")
        self.assertContains(response, 'aria-pressed="true"')

    def test_home_keeps_hidden_widgets_for_edit_mode_and_shows_empty_state(self):
        self.profile.dashboard_layout = self._layout(hidden=list(DASHBOARD_WIDGET_IDS))
        self.profile.save(update_fields=["dashboard_layout"])
        self._login()

        response = self.client.get("/home/")

        self.assertFalse(response.context["dashboard_visible_widgets"])
        self.assertContains(response, "Dein Dashboard ist leer")
        self.assertContains(response, 'data-widget-id="welcome"')
        self.assertContains(response, "hidden")

    def test_normalization_adds_missing_widgets_and_drops_obsolete_entries(self):
        broken_layout = {
            "version": 1,
            "order": ["clock", "unknown", "clock", "welcome"],
            "hidden": ["removed", "weather", "weather"],
            "wide": ["removed", "clock", "clock"],
        }

        normalized = normalize_dashboard_layout(broken_layout)

        self.assertEqual(normalized["version"], 2)
        self.assertEqual(normalized["order"][:2], ["clock", "welcome"])
        self.assertEqual(set(normalized["order"]), set(DASHBOARD_WIDGET_IDS))
        self.assertEqual(normalized["hidden"], ["weather"])
        self.assertEqual(normalized["wide"], ["clock"])

    def test_normalization_defaults_wide_for_layouts_saved_before_the_feature_existed(self):
        pre_existing_layout = {
            "version": 1,
            "order": list(DASHBOARD_WIDGET_IDS),
            "hidden": ["weather"],
        }

        normalized = normalize_dashboard_layout(pre_existing_layout)

        self.assertEqual(normalized["wide"], [])
        self.assertEqual(normalized["hidden"], ["weather"])

    def test_saved_hidden_layout_is_ignored_when_customization_is_disabled(self):
        SystemSettings.objects.create(dashboard_customization_enabled=False)
        self.profile.dashboard_layout = self._layout(hidden=list(DASHBOARD_WIDGET_IDS))
        self.profile.save(update_fields=["dashboard_layout"])
        self._login()

        response = self.client.get("/home/")

        self.assertNotContains(response, "Dashboard anpassen")
        self.assertContains(response, "Willkommen zurück")
        self.assertContains(response, "Nächste Termine")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.dashboard_layout["hidden"], list(DASHBOARD_WIDGET_IDS))

    def test_dashboard_layout_api_requires_login(self):
        response = self.client.patch(
            "/home/dashboard-layout/",
            data=json.dumps(default_dashboard_layout()),
            content_type="application/json",
        )

        self.assertRedirects(response, "/login/?next=/home/dashboard-layout/")

    def test_dashboard_layout_api_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(username="mira@example.com", password="secret-12345")

        response = csrf_client.patch(
            "/home/dashboard-layout/",
            data=json.dumps(default_dashboard_layout()),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

        csrf_client.get("/home/")
        token = csrf_client.cookies["csrftoken"].value
        response = csrf_client.patch(
            "/home/dashboard-layout/",
            data=json.dumps(default_dashboard_layout()),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 200)

    def test_dashboard_layout_api_saves_valid_layout_for_current_user_only(self):
        other = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        other_profile = Profile.objects.create(user=other, display_name="Lukas")
        layout = self._layout(
            order=[
                "recent_tools",
                "quick_actions",
                "upcoming_events",
                "weather",
                "clock",
                "welcome",
                "tasks",
                "notifications",
                "stats",
            ],
            hidden=["clock", "weather"],
            wide=["recent_tools", "weather"],
        )
        self._login()

        response = self.client.patch(
            "/home/dashboard-layout/",
            data=json.dumps(layout),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "layout": layout})
        self.profile.refresh_from_db()
        other_profile.refresh_from_db()
        self.assertEqual(self.profile.dashboard_layout, layout)
        self.assertEqual(other_profile.dashboard_layout, default_dashboard_layout())

    def test_dashboard_layout_api_rejects_invalid_layouts_without_saving(self):
        original_layout = self.profile.dashboard_layout
        invalid_layouts = [
            {"version": 2, "order": ["welcome"], "hidden": [], "wide": []},
            self._layout(
                order=["welcome", "welcome", "clock", "weather", "upcoming_events", "quick_actions"]
            ),
            self._layout(order=["welcome", "clock", "weather", "upcoming_events", "quick_actions", "bogus"]),
            {"version": 2, "order": list(DASHBOARD_WIDGET_IDS), "hidden": "weather", "wide": []},
            {"version": 2, "order": list(DASHBOARD_WIDGET_IDS), "hidden": [], "wide": "weather"},
            self._layout(wide=["welcome", "welcome"]),
            self._layout(wide=["bogus"]),
            ["welcome", "clock"],
        ]
        self._login()

        for layout in invalid_layouts:
            response = self.client.patch(
                "/home/dashboard-layout/",
                data=json.dumps(layout),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 400)
            self.profile.refresh_from_db()
            self.assertEqual(self.profile.dashboard_layout, original_layout)

    def test_dashboard_layout_api_respects_feature_flag(self):
        SystemSettings.objects.create(dashboard_customization_enabled=False)
        layout = self._layout(hidden=["welcome"])
        self._login()

        response = self.client.patch(
            "/home/dashboard-layout/",
            data=json.dumps(layout),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.dashboard_layout, default_dashboard_layout())

    def test_disabled_weather_or_messages_are_not_restored_by_dashboard_layout(self):
        SystemSettings.objects.create(weather_enabled=False, messages_enabled=False)
        self.profile.dashboard_layout = self._layout(
            order=["weather", "quick_actions", "recent_tools", "welcome", "clock", "upcoming_events"]
        )
        self.profile.save(update_fields=["dashboard_layout"])
        self._login()

        response = self.client.get("/home/")

        self.assertNotContains(response, 'data-widget-id="weather"')
        self.assertNotContains(response, "Nachrichten")
        self.assertContains(response, "Schnellzugriff")


class DashboardNotificationWidgetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com",
            email="mira@example.com",
            password="secret-12345",
        )
        Profile.objects.create(user=self.user, display_name="Mira")

    def _login(self):
        self.client.login(username="mira@example.com", password="secret-12345")

    def test_dashboard_shows_latest_unread_notifications_and_todays_tasks(self):
        now = timezone.now()
        UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_NOTE_SHARE,
            title="Notiz geteilt",
            source_key="note-share:9001",
        )
        UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_NOTE_SHARE,
            title="Bereits gelesen",
            source_key="note-share:9002",
            read_at=now,
        )
        Task.objects.create(user=self.user, title="Heute fällig", due_at=now + timedelta(hours=1))
        Task.objects.create(user=self.user, title="Nächste Woche", due_at=now + timedelta(days=3))
        self._login()

        response = self.client.get("/home/")

        self.assertContains(response, "Notiz geteilt")
        self.assertNotContains(response, "Bereits gelesen")
        self.assertContains(response, "Heute fällig")
        self.assertEqual(len(response.context["dashboard_notifications"]), 1)
        self.assertEqual(len(response.context["dashboard_today_tasks"]), 1)
        self.assertEqual(response.context["dashboard_today_tasks"][0]["title"], "Heute fällig")

    def test_dashboard_does_not_materialize_due_notification_sources(self):
        now = timezone.now()
        Task.objects.create(user=self.user, title="Überfällige Aufgabe", due_at=now - timedelta(minutes=2))
        CalendarReminder.objects.create(
            user=self.user,
            title="Überfällige Erinnerung",
            due_at=now - timedelta(minutes=1),
        )
        self._login()

        response = self.client.get("/home/")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserNotification.objects.filter(recipient=self.user).exists())
        self.assertEqual(response.context["unread_notification_count"], 0)
        self.assertEqual(response.context["dashboard_notifications"], [])

    def test_dashboard_skips_hidden_notification_widget_queries(self):
        layout = default_dashboard_layout()
        layout["hidden"] = ["notifications"]
        self.user.profile.dashboard_layout = layout
        self.user.profile.save(update_fields=["dashboard_layout"])
        self._login()

        with patch("app.view_models.dashboard_latest_notifications") as latest_notifications:
            response = self.client.get("/home/")

        self.assertEqual(response.status_code, 200)
        latest_notifications.assert_not_called()
        self.assertEqual(response.context["dashboard_notifications"], [])
        self.assertEqual(response.context["dashboard_today_tasks"], [])

    def test_dashboard_hides_task_section_when_tasks_disabled(self):
        SystemSettings.objects.create(tasks_enabled=False)
        self._login()

        response = self.client.get("/home/")

        self.assertContains(response, "Neueste Hinweise")
        self.assertNotContains(response, "Heutige Aufgaben")
        self.assertFalse(response.context["dashboard_tasks_enabled"])


class DashboardStatsWidgetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com",
            email="mira@example.com",
            password="secret-12345",
        )
        Profile.objects.create(user=self.user, display_name="Mira")

    def _login(self):
        self.client.login(username="mira@example.com", password="secret-12345")

    def test_dashboard_shows_stat_tiles_for_enabled_features(self):
        # Wednesday mid-month at local midday, so the local year/week/month match what the view computes.
        now = datetime(2026, 6, 17, 12, 0, tzinfo=get_user_zoneinfo(self.user))
        with patch("django.utils.timezone.now", return_value=now):
            Task.objects.create(user=self.user, title="Erledigt", is_done=True)
            Task.objects.create(user=self.user, title="Offen")
            Note.objects.create(owner=self.user, title="Diese Woche bearbeitet")
            CalendarEvent.objects.create(
                user=self.user,
                title="Bald",
                start_at=now + timedelta(days=2),
                end_at=now + timedelta(days=2, hours=1),
            )
            VacationYear.objects.create(user=self.user, year=now.year, allowance_days=Decimal("30"))
            self._login()

            response = self.client.get("/home/")

        self.assertContains(response, "Statistik")
        dashboard_stats = response.context["dashboard_stats"]
        week_by_label = {tile["label"]: tile["value"] for tile in dashboard_stats["week"]}
        self.assertEqual(week_by_label["Erledigt (Woche)"], "1")
        self.assertEqual(week_by_label["Offene Aufgaben"], "1")
        self.assertEqual(week_by_label["Notizen (Woche)"], "1")
        self.assertEqual(week_by_label["Termine (Woche)"], "1")
        self.assertEqual(week_by_label[f"Resturlaub {now.year}"], "30 Tage")

        month_by_label = {tile["label"]: tile["value"] for tile in dashboard_stats["month"]}
        self.assertEqual(month_by_label["Erledigt (Monat)"], "1")
        self.assertEqual(month_by_label["Offene Aufgaben"], "1")
        self.assertEqual(month_by_label["Notizen (Monat)"], "1")
        self.assertEqual(month_by_label["Termine (Monat)"], "1")
        self.assertEqual(month_by_label[f"Resturlaub {now.year}"], "30 Tage")

    def test_dashboard_omits_tiles_for_disabled_features_and_unconfigured_vacation_year(self):
        SystemSettings.objects.create(tasks_enabled=False, notes_enabled=False)
        self._login()

        response = self.client.get("/home/")

        for period, done_label, notes_label, events_label in (
            ("week", "Erledigt (Woche)", "Notizen (Woche)", "Termine (Woche)"),
            ("month", "Erledigt (Monat)", "Notizen (Monat)", "Termine (Monat)"),
        ):
            labels = {tile["label"] for tile in response.context["dashboard_stats"][period]}
            self.assertNotIn(done_label, labels)
            self.assertNotIn("Offene Aufgaben", labels)
            self.assertNotIn(notes_label, labels)
            self.assertIn(events_label, labels)
            self.assertFalse(any(label.startswith("Resturlaub") for label in labels))

    def test_dashboard_stat_tiles_distinguish_week_and_month_activity(self):
        # Wednesday mid-month at local midday: week starts Mon 15th, month on the 1st.
        now = datetime(2026, 6, 17, 12, 0, tzinfo=get_user_zoneinfo(self.user))
        earlier_this_month = datetime(2026, 6, 10, 12, 0, tzinfo=now.tzinfo)
        task = Task.objects.create(user=self.user, title="Letzte Woche erledigt", is_done=True)
        Task.objects.filter(pk=task.pk).update(updated_at=earlier_this_month)
        self._login()

        with patch("django.utils.timezone.now", return_value=now):
            response = self.client.get("/home/")

        dashboard_stats = response.context["dashboard_stats"]
        week_value = next(
            tile["value"] for tile in dashboard_stats["week"] if tile["label"] == "Erledigt (Woche)"
        )
        month_value = next(
            tile["value"] for tile in dashboard_stats["month"] if tile["label"] == "Erledigt (Monat)"
        )
        self.assertEqual(week_value, "0")
        self.assertEqual(month_value, "1")

    def test_dashboard_skips_hidden_stats_widget_queries(self):
        layout = default_dashboard_layout()
        layout["hidden"] = ["stats"]
        self.user.profile.dashboard_layout = layout
        self.user.profile.save(update_fields=["dashboard_layout"])
        self._login()

        with patch("app.view_models._dashboard_stats") as dashboard_stats:
            response = self.client.get("/home/")

        self.assertEqual(response.status_code, 200)
        dashboard_stats.assert_not_called()
        self.assertEqual(response.context["dashboard_stats"], {})
