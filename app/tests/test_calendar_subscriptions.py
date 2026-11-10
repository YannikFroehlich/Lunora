import json
import socket
import threading
import time
import zipfile
from datetime import timedelta
from io import BytesIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from app.models import CalendarEvent, CalendarSubscription, Profile, SystemSettings
from app.services.calendar_service import build_calendar_feed
from app.services.calendar_subscriptions import (
    MAX_CALENDAR_BYTES,
    SubscriptionError,
    fetch_calendar,
    normalize_url,
    sync_due_subscriptions,
    sync_subscription,
)
from app.services.data_export import write_user_export
from app.services.data_import import MAX_ICS_DESCRIPTION_CHARS, import_files, import_ics_events
from app.services.scheduled_tasks import run_scheduled_tasks
from app.tests.helpers import FakeHttpConnection

SECRET_URL = "https://calendar.example.com/calendar/ical/secret-token-123/basic.ics"


def ics(*events):
    body = "".join(f"BEGIN:VEVENT\r\n{event}END:VEVENT\r\n" for event in events)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n{body}END:VCALENDAR\r\n"


def vevent(uid, title, start, *, hours=1, extra=""):
    end = start + timedelta(hours=hours)
    return (
        f"UID:{uid}\r\nSUMMARY:{title}\r\nDTSTART:{start:%Y%m%dT%H%M%SZ}\r\n"
        f"DTEND:{end:%Y%m%dT%H%M%SZ}\r\n{extra}"
    )


class CalendarSubscriptionFetchTests(TestCase):
    def setUp(self):
        FakeHttpConnection.responses = []
        FakeHttpConnection.requests = []
        patcher = mock.patch.multiple(
            "app.services.safe_fetch.http.client",
            HTTPSConnection=FakeHttpConnection,
            HTTPConnection=FakeHttpConnection,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_normalize_url_accepts_webcal_and_rejects_unsafe_targets(self):
        self.assertEqual(normalize_url(" webcal://p01.icloud.com/x.ics "), "https://p01.icloud.com/x.ics")
        self.assertEqual(normalize_url(SECRET_URL), SECRET_URL)
        for url in [
            "ftp://example.com/cal.ics",
            "file:///etc/passwd",
            "https://user:pw@example.com/cal.ics",
            "https://example.com:8443/cal.ics",
            "http://localhost/cal.ics",
            "http://127.0.0.1/cal.ics",
            "http://10.0.0.5/cal.ics",
            "http://[::1]/cal.ics",
            "http://169.254.169.254/latest/meta-data",
            "http://intranet/cal.ics",
            "http://printer.local/cal.ics",
            "",
        ]:
            with self.subTest(url=url), self.assertRaises(SubscriptionError):
                normalize_url(url)

    def test_fetch_follows_public_redirect_and_returns_calendar(self):
        FakeHttpConnection.responses = [
            (302, {"Location": "https://cdn.example.org/real.ics?x=1"}, b""),
            (200, {}, ics(vevent("a", "Essen", timezone.now())).encode()),
        ]

        text = fetch_calendar("webcal://calendar.example.com/cal.ics")

        self.assertIn("SUMMARY:Essen", text)
        self.assertEqual(
            FakeHttpConnection.requests,
            [("calendar.example.com", "/cal.ics"), ("cdn.example.org", "/real.ics?x=1")],
        )

    def test_fetch_refuses_redirect_to_internal_address(self):
        FakeHttpConnection.responses = [(301, {"Location": "http://192.168.0.1/admin"}, b"")]

        with self.assertRaises(SubscriptionError):
            fetch_calendar(SECRET_URL)
        self.assertEqual(len(FakeHttpConnection.requests), 1)

    def test_fetch_errors_never_contain_the_url(self):
        cases = [
            (404, {}, b"not found"),
            (200, {"Content-Type": "text/html"}, b"<html>login</html>"),
            (200, {}, b"BEGIN:VCALENDAR\r\n" + b"X" * (MAX_CALENDAR_BYTES + 1)),
        ]
        for response in cases:
            FakeHttpConnection.responses = [response]
            with self.subTest(status=response[0]), self.assertRaises(SubscriptionError) as error:
                fetch_calendar(SECRET_URL)
            self.assertNotIn("secret-token", str(error.exception))
            self.assertNotIn("example.com", str(error.exception))


class CalendarSubscriptionConnectTests(TestCase):
    """Runs the real http.client, with DNS stubbed, to prove the connection hook is in place."""

    def test_name_resolving_to_internal_address_is_never_connected(self):
        internal = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 443))]
        with (
            mock.patch("socket.getaddrinfo", return_value=internal),
            mock.patch("socket.socket") as socket_class,
            self.assertRaises(SubscriptionError) as error,
        ):
            fetch_calendar("https://rebinding.example.com/cal.ics")

        self.assertIn("öffentlichen Server", str(error.exception))
        socket_class.assert_not_called()


class CalendarSubscriptionDeadlineTests(TestCase):
    """A real http.client against a loopback server that sends one byte at a time."""

    def fetch_from_dripping_server(self, head):
        listener = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(listener.close)

        def serve():
            client, _address = listener.accept()
            with client:
                client.recv(4096)
                try:
                    client.sendall(head)
                    # Bounded, so a regression fails the time check below instead of hanging.
                    for _byte in range(150):
                        client.sendall(b"x")
                        time.sleep(0.02)
                except OSError:
                    pass

        threading.Thread(target=serve, daemon=True).start()
        port = listener.getsockname()[1]
        started = time.monotonic()
        with (
            mock.patch("app.services.safe_fetch.FETCH_DEADLINE_SECONDS", 0.2),
            mock.patch(
                "app.services.safe_fetch._public_connection",
                lambda _address, timeout=None, _source=None: socket.create_connection(
                    ("127.0.0.1", port), timeout
                ),
            ),
            self.assertRaises(SubscriptionError),
        ):
            fetch_calendar("http://calendar.example.com/cal.ics")
        self.assertLess(time.monotonic() - started, 2)

    def test_slow_status_line_is_cut_at_the_deadline(self):
        self.fetch_from_dripping_server(b"")

    def test_slow_body_is_cut_at_the_deadline(self):
        self.fetch_from_dripping_server(b"HTTP/1.1 200 OK\r\n\r\nBEGIN:VCALENDAR\r\n")


class CalendarSubscriptionSyncTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=self.user, display_name="Mira", timezone_name="Europe/Berlin")
        self.subscription = CalendarSubscription.objects.create(
            user=self.user, name="Privat", url=SECRET_URL, color="green"
        )
        self.now = timezone.now().replace(microsecond=0)

    def sync(self, text):
        with mock.patch("app.services.calendar_subscriptions.fetch_calendar", return_value=text):
            return sync_subscription(self.subscription, now=self.now)

    def test_sync_mirrors_window_and_expands_old_series_up_to_today(self):
        years_ago = self.now - timedelta(days=3 * 365)
        text = ics(
            # Without the window, a daily series stops after 366 days and a yearly one after five years.
            vevent("yoga", "Yoga", years_ago, extra="RRULE:FREQ=DAILY\r\n"),
            vevent("bday", "Geburtstag", self.now - timedelta(days=30 * 365), extra="RRULE:FREQ=YEARLY\r\n"),
            vevent("dinner", "Abendessen", self.now + timedelta(days=2)),
            vevent("old", "Uralt", self.now - timedelta(days=400)),
        )

        self.assertTrue(self.sync(text))

        events = CalendarEvent.objects.filter(subscription=self.subscription)
        self.assertTrue(events.filter(title="Abendessen").exists())
        self.assertFalse(events.filter(title="Uralt").exists())
        self.assertTrue(
            events.filter(
                title="Yoga", start_at__gte=self.now, start_at__lt=self.now + timedelta(days=7)
            ).exists()
        )
        self.assertTrue(events.filter(title="Geburtstag", start_at__gte=self.now).exists())
        self.assertTrue(all(event.user_id == self.user.pk for event in events))
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.event_count, events.count())
        self.assertEqual(self.subscription.last_synced_at, self.now)

    def test_resync_replaces_events_and_failure_keeps_them(self):
        self.sync(ics(vevent("a", "Eins", self.now + timedelta(days=1)), vevent("b", "Zwei", self.now)))
        self.sync(ics(vevent("a", "Eins verschoben", self.now + timedelta(days=3))))

        titles = list(
            CalendarEvent.objects.filter(subscription=self.subscription).values_list("title", flat=True)
        )
        self.assertEqual(titles, ["Eins verschoben"])

        with mock.patch(
            "app.services.calendar_subscriptions.fetch_calendar",
            side_effect=SubscriptionError("Der Kalender-Server ist nicht erreichbar."),
        ):
            self.assertFalse(sync_subscription(self.subscription, now=self.now))

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.last_error, "Der Kalender-Server ist nicht erreichbar.")
        self.assertEqual(CalendarEvent.objects.filter(subscription=self.subscription).count(), 1)

    def test_due_sync_respects_interval_and_feature_flag(self):
        fetch = mock.patch(
            "app.services.calendar_subscriptions.fetch_calendar",
            return_value=ics(vevent("a", "Eins", self.now)),
        )
        with fetch as fetched:
            self.assertEqual(sync_due_subscriptions(now=self.now), {"synced": 1, "failed": 0})
            self.assertEqual(sync_due_subscriptions(now=self.now + timedelta(minutes=5))["synced"], 0)
            self.assertEqual(sync_due_subscriptions(now=self.now + timedelta(minutes=31))["synced"], 1)
            self.assertEqual(fetched.call_count, 2)

            SystemSettings.objects.update_or_create(pk=1, defaults={"calendar_subscriptions_enabled": False})
            result = run_scheduled_tasks(now=self.now + timedelta(hours=2))
            self.assertTrue(result["calendar_subscriptions"]["disabled"])
            self.assertEqual(fetched.call_count, 2)

    def hostile_calendars(self):
        start = f"DTSTART:{self.now:%Y%m%dT%H%M%SZ}\r\n"
        return [
            ics(f"UID:x\r\n{start}DURATION:P99999999999W\r\n"),
            ics(f"UID:x\r\n{start}RRULE:FREQ=DAILY;INTERVAL=99999999999\r\n"),
            ics(f"UID:x\r\n{start}RRULE:FREQ=YEARLY;INTERVAL=100000\r\n"),
            ics("UID:x\r\nDTSTART:99991231T235959Z\r\nDURATION:P2D\r\n"),
        ]

    def test_hostile_calendar_fails_alone_and_keeps_the_old_events(self):
        self.sync(ics(vevent("a", "Eins", self.now)))

        for text in self.hostile_calendars():
            CalendarSubscription.objects.filter(pk=self.subscription.pk).update(last_attempt_at=None)
            with self.subTest(text=text):
                self.assertFalse(self.sync(text))
                self.subscription.refresh_from_db()
                self.assertEqual(self.subscription.last_error, "Der Kalender konnte nicht gelesen werden.")
                self.assertEqual(self.subscription.last_attempt_at, self.now)
                self.assertEqual(CalendarEvent.objects.filter(subscription=self.subscription).count(), 1)

    def test_hostile_calendar_is_not_retried_on_every_pass(self):
        text = self.hostile_calendars()[0]
        with mock.patch("app.services.calendar_subscriptions.fetch_calendar", return_value=text):
            self.assertEqual(sync_due_subscriptions(now=self.now), {"synced": 0, "failed": 1})
            self.assertEqual(
                sync_due_subscriptions(now=self.now + timedelta(minutes=5)), {"synced": 0, "failed": 0}
            )

    def test_malformed_redirect_fails_without_showing_the_url(self):
        FakeHttpConnection.responses = [(302, {"Location": "http://[bad/"}, b"")]
        FakeHttpConnection.requests = []
        with mock.patch.multiple(
            "app.services.safe_fetch.http.client",
            HTTPSConnection=FakeHttpConnection,
            HTTPConnection=FakeHttpConnection,
        ):
            self.assertFalse(sync_subscription(self.subscription, now=self.now))

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.last_error, "Der Kalender konnte nicht gelesen werden.")

    def test_long_descriptions_are_shortened(self):
        description = "x" * (MAX_ICS_DESCRIPTION_CHARS + 1)

        self.assertTrue(self.sync(ics(vevent("a", "Eins", self.now, extra=f"DESCRIPTION:{description}\r\n"))))

        event = CalendarEvent.objects.get(subscription=self.subscription)
        self.assertEqual(len(event.description), MAX_ICS_DESCRIPTION_CHARS)

    def test_nul_characters_are_dropped(self):
        self.assertTrue(self.sync(ics(vevent("a", "Ti\x00tel", self.now))))

        self.assertTrue(CalendarEvent.objects.filter(subscription=self.subscription, title="Titel").exists())

    def test_uploaded_ics_with_impossible_dates_is_reported(self):
        upload = SimpleUploadedFile("kalender.ics", self.hostile_calendars()[0].encode())

        successes, errors = import_files(self.user, [upload], notes_enabled=True, calendar_enabled=True)

        self.assertEqual(successes, [])
        self.assertIn("ungültige Termine", errors[0])

    def test_own_feed_and_export_leave_subscribed_events_and_url_out(self):
        self.sync(ics(vevent("a", "Privattermin", self.now + timedelta(days=1))))
        CalendarEvent.objects.create(
            user=self.user, title="Eigener", start_at=self.now, end_at=self.now + timedelta(hours=1)
        )

        feed = build_calendar_feed(self.user, calendar_name="Lunora")
        self.assertIn("Eigener", feed)
        self.assertNotIn("Privattermin", feed)

        buffer = BytesIO()
        write_user_export(self.user, buffer)
        archive = zipfile.ZipFile(buffer)
        calendar = json.loads(archive.read("kalender.json"))
        self.assertEqual([event["title"] for event in calendar["events"]], ["Eigener"])
        self.assertEqual(calendar["subscriptions"][0]["name"], "Privat")
        for name in archive.namelist():
            self.assertNotIn(b"secret-token", archive.read(name))

    def test_file_import_is_not_blocked_by_subscribed_uids(self):
        text = ics(vevent("same-uid", "Doppelt", self.now + timedelta(days=1)))
        self.sync(text)

        counts = import_ics_events(self.user, text)

        self.assertEqual(counts["created"], 1)
        self.assertEqual(CalendarEvent.objects.filter(title="Doppelt").count(), 2)


class CalendarSubscriptionViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=self.user, display_name="Mira", timezone_name="Europe/Berlin")
        self.client.login(username="mira@example.com", password="secret-12345")
        self.now = timezone.now().replace(microsecond=0)
        self.text = ics(vevent("dinner", "Abendessen", self.now + timedelta(days=1)))

    def add(self, url=SECRET_URL):
        with mock.patch("app.services.calendar_subscriptions.fetch_calendar", return_value=self.text):
            return self.client.post(
                "/settings/",
                {"form_name": "calendar_subscription_add", "name": "Privat", "url": url, "color": "violet"},
                follow=True,
            )

    def test_add_fetches_right_away_and_never_renders_the_url(self):
        response = self.add()

        subscription = CalendarSubscription.objects.get(user=self.user)
        self.assertEqual(subscription.url, SECRET_URL)
        self.assertContains(response, "„Privat“ aktualisiert: 1 Termin(e).")
        self.assertContains(response, "Externe Kalender")
        self.assertNotContains(response, "secret-token")
        self.assertEqual(CalendarEvent.objects.filter(subscription=subscription).count(), 1)

    def test_invalid_url_is_rejected(self):
        response = self.add(url="http://127.0.0.1/cal.ics")

        self.assertContains(response, "öffentlichen Server")
        self.assertFalse(CalendarSubscription.objects.exists())

    def test_refresh_is_throttled_and_delete_removes_events(self):
        self.add()
        subscription = CalendarSubscription.objects.get(user=self.user)
        with mock.patch("app.services.calendar_subscriptions.fetch_calendar") as fetched:
            response = self.client.post(
                "/settings/",
                {"form_name": "calendar_subscription_refresh", "subscription_id": subscription.pk},
                follow=True,
            )
        fetched.assert_not_called()
        self.assertContains(response, "gerade erst aktualisiert")

        self.client.post(
            "/settings/", {"form_name": "calendar_subscription_delete", "subscription_id": subscription.pk}
        )

        self.assertFalse(CalendarSubscription.objects.exists())
        self.assertFalse(CalendarEvent.objects.filter(title="Abendessen").exists())

    def test_subscribed_events_show_read_only_and_cannot_be_changed(self):
        self.add()
        event = CalendarEvent.objects.get(title="Abendessen")

        response = self.client.get("/calendar/?view=month")
        upcoming = next(item for item in response.context["upcoming_events"] if item["title"] == "Abendessen")
        self.assertFalse(upcoming["can_delete"])
        self.assertEqual(upcoming["subscription_name"], "Privat")
        self.assertEqual(upcoming["tone"], "violet")
        self.assertEqual(upcoming["category"], "Privat")
        home = self.client.get("/home/")
        self.assertIn("Abendessen", [item["title"] for item in home.context["upcoming_dashboard_events"]])

        target = (self.now + timedelta(days=5)).date().isoformat()
        for data in [
            {"form_name": "calendar_event_delete", "event_id": event.pk},
            {"form_name": "calendar_event_delete_series", "recurrence_id": str(event.recurrence_id or "")},
            {"form_name": "calendar_event_move", "event_id": event.pk, "date": target},
            {"form_name": "calendar_event_edit", "event_id": event.pk, "title": "Gehackt", "date": target},
        ]:
            self.client.post("/calendar/", data)
        event.refresh_from_db()
        self.assertEqual(event.title, "Abendessen")
        self.assertEqual(event.start_at, self.now + timedelta(days=1))

        detail = self.client.get(f"/calendar/events/{event.pk}/")
        self.assertContains(detail, "Abonniert – nur lesbar")
        self.assertNotContains(detail, "Im Kalender bearbeiten")

    def test_feature_flag_hides_events_and_blocks_settings_actions(self):
        self.add()
        event = CalendarEvent.objects.get(title="Abendessen")
        SystemSettings.objects.update_or_create(pk=1, defaults={"calendar_subscriptions_enabled": False})

        response = self.client.get("/calendar/?view=month")
        self.assertNotIn("Abendessen", [item["title"] for item in response.context["upcoming_events"]])
        self.assertNotContains(self.client.get("/settings/"), "Externe Kalender")
        self.assertEqual(self.client.get(f"/calendar/events/{event.pk}/").status_code, 404)
        blocked = self.client.post("/settings/", {"form_name": "calendar_subscription_add", "name": "X"})
        self.assertEqual(blocked.status_code, 503)

    def test_calendar_panel_restyles_a_subscription_and_events_use_its_icon(self):
        self.add()
        subscription = CalendarSubscription.objects.get(user=self.user)
        other = User.objects.create_user(username="ole@example.com", email="ole@example.com", password="x")
        foreign = CalendarSubscription.objects.create(user=other, name="Fremd", url=SECRET_URL)

        page = self.client.get("/calendar/?view=month")
        self.assertContains(page, "Abonnierte Kalender")
        self.assertNotContains(page, "Farben im Kalender")
        self.assertNotContains(page, "secret-token")

        style = {"form_name": "calendar_subscription_style", "name": "Familie", "color": "green"}
        response = self.client.post(
            "/calendar/?view=month",
            {**style, "subscription_id": subscription.pk, "icon": "fa-cake-candles"},
        )
        self.assertRedirects(response, "/calendar/?view=month", fetch_redirect_response=False)
        subscription.refresh_from_db()
        self.assertEqual(
            (subscription.name, subscription.color, subscription.icon),
            ("Familie", "green", "fa-cake-candles"),
        )
        upcoming = next(
            item
            for item in self.client.get("/calendar/?view=month").context["upcoming_events"]
            if item["title"] == "Abendessen"
        )
        self.assertEqual(upcoming["subscription_icon"], "fa-cake-candles")

        # Only allowlisted icons, and only the user's own subscriptions.
        self.client.post("/calendar/", {**style, "subscription_id": subscription.pk, "icon": "fa-x onclick"})
        self.client.post("/calendar/", {**style, "subscription_id": foreign.pk, "icon": "fa-star"})
        subscription.refresh_from_db()
        foreign.refresh_from_db()
        self.assertEqual(subscription.icon, "fa-cake-candles")
        self.assertEqual((foreign.name, foreign.icon), ("Fremd", "fa-rss"))

        SystemSettings.objects.update_or_create(pk=1, defaults={"calendar_subscriptions_enabled": False})
        blocked = self.client.post(
            "/calendar/", {**style, "subscription_id": subscription.pk, "icon": "fa-star"}
        )
        self.assertEqual(blocked.status_code, 503)
        self.assertNotContains(self.client.get("/calendar/?view=month"), "Abonnierte Kalender")
