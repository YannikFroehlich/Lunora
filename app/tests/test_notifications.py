import json
from datetime import datetime, time, timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from app.models import (
    CalendarEvent,
    CalendarEventAttendee,
    CalendarReminder,
    Note,
    NoteActivityNotification,
    NoteShare,
    NotificationPreference,
    Profile,
    Task,
    UserNotification,
    WeatherLocation,
    WebPushDelivery,
    WebPushSubscription,
    WeeklySummaryDelivery,
)
from app.services.notes import share_note
from app.services.notifications import (
    send_due_reminder_emails,
    send_new_invitation_emails,
    send_note_activity_emails,
    send_pending_user_notification_emails,
    send_weekly_summaries,
)
from app.services.web_push import (
    materialize_web_push_weather_alerts,
    register_web_push_subscription,
    send_pending_web_push_notifications,
)
from app.tests.helpers import FakeWeatherResponse


@override_settings(
    DEFAULT_FROM_EMAIL="Lunora <noreply@example.test>",
    LUNORA_WEEKLY_SUMMARY_HOUR=8,
)
class ScheduledAutomationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com",
            email="mira@example.com",
            password="secret-12345",
        )
        self.profile = Profile.objects.create(
            user=self.user,
            display_name="Mira",
            notify_email=True,
            notify_reminders=True,
            notify_desktop=True,
            weekly_summary=True,
            timezone_name="Europe/Berlin",
        )

    def test_due_reminder_email_is_sent_only_once(self):
        reminder = CalendarReminder.objects.create(
            user=self.user,
            title="Rechnung bezahlen",
            due_at=timezone.now() - timedelta(minutes=1),
        )

        first_result = send_due_reminder_emails()
        second_result = send_due_reminder_emails()

        self.assertEqual(first_result, {"sent": 1, "failed": 0})
        self.assertEqual(second_result, {"sent": 0, "failed": 0})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Rechnung bezahlen", mail.outbox[0].subject)
        reminder.refresh_from_db()
        self.assertIsNotNone(reminder.email_notified_at)

    def test_desktop_notification_claim_is_preference_scoped_and_one_time(self):
        reminder = CalendarReminder.objects.create(
            user=self.user,
            title="Präsentation starten",
            due_at=timezone.now() - timedelta(minutes=1),
        )
        self.client.force_login(self.user)

        first_response = self.client.post("/notifications/claim/")
        second_response = self.client.post("/notifications/claim/")

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(first_response.json()["notifications"][0]["title"], "Präsentation starten")
        self.assertEqual(second_response.json(), {"notifications": []})
        reminder.refresh_from_db()
        self.assertIsNotNone(reminder.desktop_notified_at)

    def test_event_invitation_email_and_desktop_claim_are_sent_only_once(self):
        organizer = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        Profile.objects.create(user=organizer, display_name="Lukas")
        event = CalendarEvent.objects.create(
            user=organizer,
            title="Projektmeeting",
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, hours=1),
        )
        CalendarEventAttendee.objects.create(event=event, user=self.user, invited_by=organizer)

        first_email_result = send_new_invitation_emails()
        second_email_result = send_new_invitation_emails()
        self.assertEqual(first_email_result, {"sent": 1, "failed": 0})
        self.assertEqual(second_email_result, {"sent": 0, "failed": 0})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Projektmeeting", mail.outbox[0].subject)

        self.client.force_login(self.user)
        first_response = self.client.post("/notifications/claim/")
        second_response = self.client.post("/notifications/claim/")
        self.assertEqual(first_response.json()["notifications"][0]["title"], "Einladung: Projektmeeting")
        self.assertEqual(second_response.json(), {"notifications": []})

    def test_note_activity_email_and_desktop_claim_are_sent_only_once(self):
        actor = User.objects.create_user(
            username="anna@example.com", email="anna@example.com", password="secret-12345"
        )
        Profile.objects.create(user=actor, display_name="Anna")
        note = Note.objects.create(owner=actor, title="Ideen")
        NoteActivityNotification.objects.create(
            note=note,
            recipient=self.user,
            actor=actor,
            kind=NoteActivityNotification.KIND_MENTION,
            excerpt="Hallo @Mira",
        )

        first_email_result = send_note_activity_emails()
        second_email_result = send_note_activity_emails()
        self.assertEqual(first_email_result, {"sent": 1, "failed": 0})
        self.assertEqual(second_email_result, {"sent": 0, "failed": 0})
        self.assertEqual(len(mail.outbox), 1)

        self.client.force_login(self.user)
        first_response = self.client.post("/notifications/claim/")
        second_response = self.client.post("/notifications/claim/")
        self.assertEqual(first_response.json()["notifications"][0]["title"], "Anna hat dich erwähnt")
        self.assertEqual(second_response.json(), {"notifications": []})

    @override_settings(WEATHER_API_KEY="test-key", WEATHER_CACHE_SECONDS=0)
    def test_weather_alert_desktop_claim_respects_cooldown(self):
        location = WeatherLocation.objects.create(
            user=self.user, name="Berlin", lat=52.52, lon=13.405, label="Berlin, DE", is_default=True
        )
        current_payload = {
            "weather": [{"main": "Thunderstorm", "description": "Gewitter"}],
            "main": {"temp": 22, "feels_like": 22},
            "wind": {"speed": 3},
        }
        forecast_payload = {"list": [{"pop": 0.2}]}
        self.client.force_login(self.user)

        with patch(
            "app.services.weather_service.urlopen",
            side_effect=[
                FakeWeatherResponse(current_payload),
                FakeWeatherResponse(forecast_payload),
                FakeWeatherResponse(current_payload),
                FakeWeatherResponse(forecast_payload),
            ],
        ):
            first_response = self.client.post("/notifications/claim/")
            second_response = self.client.post("/notifications/claim/")

        self.assertEqual(first_response.json()["notifications"][0]["title"], "Gewitterwarnung")
        self.assertEqual(second_response.json(), {"notifications": []})
        location.refresh_from_db()
        self.assertEqual(location.last_alert_kind, "storm")
        self.assertIsNotNone(location.last_alert_notified_at)

    def test_weekly_summary_is_sent_once_on_monday(self):
        monday = timezone.make_aware(datetime(2026, 8, 10, 9, 0), ZoneInfo("Europe/Berlin"))
        CalendarEvent.objects.create(
            user=self.user,
            title="Team Sync",
            start_at=monday + timedelta(days=1),
            end_at=monday + timedelta(days=1, hours=1),
        )
        CalendarReminder.objects.create(user=self.user, title="Agenda vorbereiten")

        first_result = send_weekly_summaries(now=monday)
        second_result = send_weekly_summaries(now=monday)

        self.assertEqual(first_result["sent"], 1)
        self.assertEqual(second_result["sent"], 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Team Sync", mail.outbox[0].body)
        self.assertIn("Agenda vorbereiten", mail.outbox[0].body)
        self.assertTrue(
            WeeklySummaryDelivery.objects.filter(user=self.user, week_start=monday.date()).exists()
        )

    def test_automation_command_runs_one_cycle_by_default(self):
        result = {
            "reminder_emails": {"sent": 1, "failed": 0},
            "weekly_summaries": {"sent": 1, "failed": 0, "skipped": 0},
        }
        output = StringIO()

        with patch(
            "app.management.commands.run_automations.run_scheduled_tasks", return_value=result
        ) as run_tasks:
            call_command("run_automations", stdout=output)

        run_tasks.assert_called_once_with()
        self.assertIn("Erinnerungen: 1 gesendet", output.getvalue())


class NotificationCenterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com",
            email="mira@example.com",
            password="secret-12345",
        )
        Profile.objects.create(user=self.user, display_name="Mira")
        self.other = User.objects.create_user(
            username="lukas@example.com",
            email="lukas@example.com",
            password="secret-12345",
        )
        Profile.objects.create(user=self.other, display_name="Lukas")

    def test_notification_center_requires_login(self):
        response = self.client.get("/notifications/")

        self.assertRedirects(response, "/login/?next=/notifications/")

    def test_due_tasks_and_reminders_are_materialized_once_and_appear_in_badge(self):
        now = timezone.now()
        Task.objects.create(user=self.user, title="Präsentation", due_at=now - timedelta(minutes=2))
        CalendarReminder.objects.create(
            user=self.user, title="Arzt anrufen", due_at=now - timedelta(minutes=1)
        )
        self.client.force_login(self.user)

        first_response = self.client.get("/notifications/?status=all")
        second_response = self.client.get("/notifications/?status=all")

        self.assertEqual(first_response.status_code, 200)
        self.assertContains(first_response, "Aufgabe fällig: Präsentation")
        self.assertContains(first_response, "Erinnerung fällig: Arzt anrufen")
        self.assertContains(first_response, "2 ungelesen")
        self.assertEqual(first_response.context["unread_notification_count"], 2)
        self.assertEqual(second_response.context["total_notification_count"], 2)
        self.assertEqual(UserNotification.objects.filter(recipient=self.user).count(), 2)

    def test_existing_invitations_and_note_activity_are_backfilled(self):
        event = CalendarEvent.objects.create(
            user=self.other,
            title="Projektmeeting",
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, hours=1),
        )
        invitation = CalendarEventAttendee.objects.create(
            event=event,
            user=self.user,
            invited_by=self.other,
        )
        note = Note.objects.create(owner=self.other, title="Ideen")
        share = NoteShare.objects.create(note=note, user=self.user, role=NoteShare.ROLE_READER)
        activity = NoteActivityNotification.objects.create(
            note=note,
            recipient=self.user,
            actor=self.other,
            kind=NoteActivityNotification.KIND_MENTION,
            excerpt="Hallo @Mira",
        )
        self.client.force_login(self.user)

        response = self.client.get("/notifications/?status=all")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Einladung: Projektmeeting")
        self.assertContains(response, "Lukas hat dich erwähnt")
        self.assertContains(response, "Lukas hat eine Notiz mit dir geteilt")
        self.assertTrue(
            UserNotification.objects.filter(
                recipient=self.user,
                source_key=f"event-invitation:{invitation.id}",
            ).exists()
        )
        self.assertTrue(
            UserNotification.objects.filter(
                recipient=self.user,
                source_key=f"note-activity:{activity.id}",
            ).exists()
        )
        self.assertTrue(
            UserNotification.objects.filter(
                recipient=self.user,
                source_key=f"note-share:{share.id}",
            ).exists()
        )

    def test_new_note_share_creates_inbox_notification_immediately(self):
        note = Note.objects.create(owner=self.other, title="Roadmap")

        share = share_note(self.other, note.id, self.user.id, NoteShare.ROLE_EDITOR)
        share_note(self.other, note.id, self.user.id, NoteShare.ROLE_READER)

        notification = UserNotification.objects.get(
            recipient=self.user,
            source_key=f"note-share:{share.id}",
        )
        self.assertEqual(notification.kind, UserNotification.KIND_NOTE_SHARE)
        self.assertEqual(notification.actor, self.other)
        self.assertEqual(notification.url, f"/notes/{note.id}/")
        self.assertEqual(UserNotification.objects.filter(recipient=self.user).count(), 1)

    def test_opening_and_toggling_notification_are_user_scoped(self):
        notification = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_TASK_DUE,
            title="Aufgabe fällig",
            url="/tasks/",
            source_key="task:1001",
        )
        other_notification = UserNotification.objects.create(
            recipient=self.other,
            kind=UserNotification.KIND_TASK_DUE,
            title="Privat",
            url="/tasks/",
            source_key="task:1002",
        )
        self.client.force_login(self.user)

        response = self.client.post(f"/notifications/{notification.id}/open/")

        self.assertRedirects(response, "/tasks/")
        notification.refresh_from_db()
        self.assertIsNotNone(notification.read_at)

        response = self.client.post(f"/notifications/{notification.id}/toggle-read/", {"status": "all"})
        self.assertRedirects(response, "/notifications/?status=all")
        notification.refresh_from_db()
        self.assertIsNone(notification.read_at)

        response = self.client.post(f"/notifications/{other_notification.id}/toggle-read/")
        self.assertEqual(response.status_code, 404)

    def test_toggle_read_redirects_to_safe_return_to_url(self):
        notification = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_TASK_DUE,
            title="Aufgabe fällig",
            url="/tasks/",
            source_key="task:2001",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            f"/notifications/{notification.id}/toggle-read/",
            {"return_to": "/home/"},
        )

        self.assertRedirects(response, "/home/")
        notification.refresh_from_db()
        self.assertIsNotNone(notification.read_at)

    def test_toggle_read_ignores_unsafe_return_to_url(self):
        notification = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_TASK_DUE,
            title="Aufgabe fällig",
            url="/tasks/",
            source_key="task:2002",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            f"/notifications/{notification.id}/toggle-read/",
            {"return_to": "https://evil.example/"},
        )

        self.assertRedirects(response, "/notifications/?status=unread")

    def test_filters_and_mark_all_read(self):
        unread = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_NOTE_COMMENT,
            title="Neuer Kommentar",
            source_key="note-activity:1001",
        )
        UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_EVENT_INVITATION,
            title="Gelesene Einladung",
            source_key="event-invitation:1002",
            read_at=timezone.now(),
        )
        self.client.force_login(self.user)

        unread_response = self.client.get("/notifications/")
        all_response = self.client.get("/notifications/?status=all")

        self.assertContains(unread_response, "Neuer Kommentar")
        self.assertNotContains(unread_response, "Gelesene Einladung")
        self.assertContains(all_response, "Neuer Kommentar")
        self.assertContains(all_response, "Gelesene Einladung")

        response = self.client.post("/notifications/mark-all-read/")

        self.assertRedirects(response, "/notifications/?status=all")
        unread.refresh_from_db()
        self.assertIsNotNone(unread.read_at)
        self.assertFalse(UserNotification.objects.filter(recipient=self.user, read_at__isnull=True).exists())

    def test_inbox_category_preference_hides_items_badge_and_bulk_read(self):
        hidden = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_TASK_DUE,
            title="Verborgene Aufgabe",
            source_key="task:hidden-category",
        )
        visible = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_NOTE_COMMENT,
            title="Sichtbarer Kommentar",
            source_key="note-comment:visible-category",
        )
        NotificationPreference.objects.create(
            user=self.user,
            category=NotificationPreference.CATEGORY_TASKS,
            inbox_enabled=False,
        )
        self.client.force_login(self.user)

        response = self.client.get("/notifications/?status=all")

        self.assertNotContains(response, "Verborgene Aufgabe")
        self.assertContains(response, "Sichtbarer Kommentar")
        self.assertEqual(response.context["unread_notification_count"], 1)
        self.assertEqual(response.context["total_notification_count"], 1)

        self.client.post("/notifications/mark-all-read/")
        hidden.refresh_from_db()
        visible.refresh_from_db()
        self.assertIsNone(hidden.read_at)
        self.assertIsNotNone(visible.read_at)

    def test_inbox_backed_email_is_sent_once_and_respects_category(self):
        self.user.profile.notify_email = True
        self.user.profile.save(update_fields=["notify_email"])
        sent_notification = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_NOTE_SHARE,
            title="Neue Notizfreigabe",
            body="Lukas hat Roadmap mit dir geteilt.",
            source_key="note-share:email-enabled",
        )

        first_result = send_pending_user_notification_emails()
        second_result = send_pending_user_notification_emails()

        self.assertEqual(first_result, {"sent": 1, "failed": 0})
        self.assertEqual(second_result, {"sent": 0, "failed": 0})
        self.assertEqual(len(mail.outbox), 1)
        sent_notification.refresh_from_db()
        self.assertIsNotNone(sent_notification.email_notified_at)

        NotificationPreference.objects.create(
            user=self.user,
            category=NotificationPreference.CATEGORY_WEATHER,
            email_enabled=False,
        )
        muted_notification = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_WEATHER_ALERT,
            title="Gewitterwarnung",
            source_key="weather-alert:email-disabled",
        )

        muted_result = send_pending_user_notification_emails()

        self.assertEqual(muted_result, {"sent": 0, "failed": 0})
        self.assertEqual(len(mail.outbox), 1)
        muted_notification.refresh_from_db()
        self.assertIsNone(muted_notification.email_notified_at)


@override_settings(
    WEB_PUSH_ENABLED=True,
    WEB_PUSH_VAPID_PUBLIC_KEY="B" * 87,
    WEB_PUSH_VAPID_PRIVATE_KEY="test-private-key",
    WEB_PUSH_VAPID_SUBJECT="mailto:push@example.com",
    WEB_PUSH_ALLOWED_ENDPOINT_HOSTS=[
        "fcm.googleapis.com",
        "updates.push.services.mozilla.com",
        "notify.windows.com",
        "push.apple.com",
    ],
    WEB_PUSH_TTL_SECONDS=600,
    WEB_PUSH_TIMEOUT_SECONDS=10,
    WEB_PUSH_MAX_ATTEMPTS=5,
)
class WebPushTests(TestCase):
    endpoint = "https://fcm.googleapis.com/fcm/send/test-device-token"
    subscription_payload = {
        "endpoint": endpoint,
        "keys": {"p256dh": "A" * 87, "auth": "B" * 22},
    }

    def setUp(self):
        self.user = User.objects.create_user(
            username="push@example.com",
            email="push@example.com",
            password="secret-12345",
        )
        Profile.objects.create(
            user=self.user,
            display_name="Push",
            notify_desktop=True,
        )
        self.other = User.objects.create_user(
            username="other-push@example.com",
            email="other-push@example.com",
            password="secret-12345",
        )
        Profile.objects.create(user=self.other, display_name="Other Push", notify_desktop=True)

    def create_subscription(self, user=None):
        subscription, _created = register_web_push_subscription(
            user or self.user,
            self.subscription_payload,
            user_agent="Test Browser",
        )
        return subscription

    def test_subscription_endpoint_requires_login_and_stores_current_device(self):
        anonymous_response = self.client.post(
            "/notifications/push-subscription/",
            data=json.dumps(self.subscription_payload),
            content_type="application/json",
        )
        self.assertRedirects(
            anonymous_response,
            "/login/?next=/notifications/push-subscription/",
        )

        self.client.force_login(self.user)
        response = self.client.post(
            "/notifications/push-subscription/",
            data=json.dumps(self.subscription_payload),
            content_type="application/json",
            HTTP_USER_AGENT="Test Browser 1.0",
        )

        self.assertEqual(response.status_code, 201)
        subscription = WebPushSubscription.objects.get(user=self.user)
        self.assertEqual(subscription.endpoint, self.endpoint)
        self.assertEqual(subscription.user_agent, "Test Browser 1.0")
        self.assertNotEqual(subscription.endpoint_hash, self.endpoint)

    def test_subscription_endpoint_rejects_untrusted_push_host(self):
        self.client.force_login(self.user)
        payload = {
            **self.subscription_payload,
            "endpoint": "https://127.0.0.1/internal-service",
        }

        response = self.client.post(
            "/notifications/push-subscription/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(WebPushSubscription.objects.exists())

    @override_settings(WEB_PUSH_ENABLED=False)
    def test_subscription_endpoint_reports_missing_server_configuration(self):
        self.client.force_login(self.user)

        response = self.client.post(
            "/notifications/push-subscription/",
            data=json.dumps(self.subscription_payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("nicht eingerichtet", response.json()["error"])

    def test_settings_exposes_public_key_and_device_activation(self):
        self.client.force_login(self.user)

        response = self.client.get("/settings/")

        self.assertContains(response, f'data-web-push-public-key="{"B" * 87}"')
        self.assertContains(response, "Auf diesem Gerät aktivieren")
        self.assertNotContains(response, "test-private-key")

    def test_subscription_delete_is_user_scoped(self):
        self.create_subscription(self.other)
        self.client.force_login(self.user)

        response = self.client.delete(
            "/notifications/push-subscription/",
            data=json.dumps({"endpoint": self.endpoint}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["removed"])
        self.assertTrue(WebPushSubscription.objects.filter(user=self.other).exists())

    def test_pending_notification_is_sent_once_per_registered_device(self):
        subscription = self.create_subscription()
        notification = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_TASK_DUE,
            title="Aufgabe fällig: Rechnung",
            body="Fällig heute um 12:00 Uhr",
            url="/tasks/",
            source_key="task:push-1",
        )

        with patch("app.services.web_push.webpush") as send_push:
            first_result = send_pending_web_push_notifications()
            second_result = send_pending_web_push_notifications()

        self.assertEqual(first_result, {"sent": 1, "failed": 0, "removed": 0, "queued": 1, "deferred": 0})
        self.assertEqual(second_result, {"sent": 0, "failed": 0, "removed": 0, "queued": 0, "deferred": 0})
        send_push.assert_called_once()
        payload = json.loads(send_push.call_args.kwargs["data"])
        self.assertEqual(payload["title"], notification.title)
        self.assertEqual(payload["url"], "/tasks/")
        self.assertEqual(send_push.call_args.kwargs["ttl"], 600)
        delivery = WebPushDelivery.objects.get(subscription=subscription, notification=notification)
        self.assertIsNotNone(delivery.delivered_at)
        self.assertEqual(delivery.attempt_count, 1)
        subscription.refresh_from_db()
        self.assertIsNotNone(subscription.last_success_at)

    def test_notifications_created_before_device_registration_are_not_backfilled(self):
        UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_NOTE_SHARE,
            title="Alte Freigabe",
            source_key="note-share:old",
        )
        self.create_subscription()

        with patch("app.services.web_push.webpush") as send_push:
            result = send_pending_web_push_notifications()

        self.assertEqual(result["queued"], 0)
        send_push.assert_not_called()

    def test_expired_subscription_is_removed_after_gone_response(self):
        subscription = self.create_subscription()
        UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_EVENT_INVITATION,
            title="Einladung",
            source_key="event-invitation:push-1",
        )
        response = SimpleNamespace(status_code=410)

        from pywebpush import WebPushException

        with patch(
            "app.services.web_push.webpush",
            side_effect=WebPushException("Gone", response=response),
        ):
            result = send_pending_web_push_notifications()

        self.assertEqual(result["removed"], 1)
        self.assertFalse(WebPushSubscription.objects.filter(pk=subscription.pk).exists())
        self.assertFalse(WebPushDelivery.objects.exists())

    def test_transient_delivery_failure_remains_retryable(self):
        subscription = self.create_subscription()
        notification = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_NOTE_COMMENT,
            title="Neuer Kommentar",
            source_key="note-comment:push-1",
        )
        response = SimpleNamespace(status_code=503)

        from pywebpush import WebPushException

        with patch(
            "app.services.web_push.webpush",
            side_effect=WebPushException("Unavailable", response=response),
        ):
            result = send_pending_web_push_notifications()

        self.assertEqual(result["failed"], 1)
        delivery = WebPushDelivery.objects.get(subscription=subscription, notification=notification)
        self.assertEqual(delivery.attempt_count, 1)
        self.assertEqual(delivery.last_status_code, 503)
        self.assertIsNone(delivery.delivered_at)
        subscription.refresh_from_db()
        self.assertEqual(subscription.failure_count, 1)

    def test_category_preference_prevents_web_push_delivery(self):
        self.create_subscription()
        NotificationPreference.objects.create(
            user=self.user,
            category=NotificationPreference.CATEGORY_TASKS,
            web_push_enabled=False,
        )
        UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_TASK_DUE,
            title="Stille Aufgabe",
            source_key="task:push-category-disabled",
        )

        with patch("app.services.web_push.webpush") as send_push:
            result = send_pending_web_push_notifications()

        self.assertEqual(result["queued"], 0)
        self.assertEqual(result["sent"], 0)
        send_push.assert_not_called()
        self.assertFalse(WebPushDelivery.objects.exists())

    def test_quiet_hours_defer_web_push_until_the_window_ends(self):
        subscription = self.create_subscription()
        profile = self.user.profile
        profile.timezone_name = "UTC"
        profile.notification_quiet_hours_enabled = True
        profile.notification_quiet_start = time(22, 0)
        profile.notification_quiet_end = time(7, 0)
        profile.save(
            update_fields=[
                "timezone_name",
                "notification_quiet_hours_enabled",
                "notification_quiet_start",
                "notification_quiet_end",
            ]
        )
        notification = UserNotification.objects.create(
            recipient=self.user,
            kind=UserNotification.KIND_NOTE_COMMENT,
            title="Später zustellen",
            source_key="note-comment:quiet-hours",
        )
        during_quiet_hours = datetime(2026, 8, 28, 23, 0, tzinfo=ZoneInfo("UTC"))
        after_quiet_hours = datetime(2026, 8, 29, 8, 0, tzinfo=ZoneInfo("UTC"))

        with patch("app.services.web_push.webpush") as send_push:
            deferred_result = send_pending_web_push_notifications(now=during_quiet_hours)
            sent_result = send_pending_web_push_notifications(now=after_quiet_hours)

        self.assertEqual(deferred_result["deferred"], 1)
        self.assertEqual(deferred_result["sent"], 0)
        self.assertEqual(sent_result["sent"], 1)
        send_push.assert_called_once()
        delivery = WebPushDelivery.objects.get(subscription=subscription, notification=notification)
        self.assertEqual(delivery.delivered_at, after_quiet_hours)

    def test_test_push_endpoint_sends_to_current_users_device(self):
        self.create_subscription()
        NotificationPreference.objects.create(
            user=self.user,
            category=NotificationPreference.CATEGORY_TASKS,
            web_push_enabled=False,
        )
        profile = self.user.profile
        profile.notification_quiet_hours_enabled = True
        profile.notification_quiet_start = time(0, 0)
        profile.notification_quiet_end = time(23, 59)
        profile.save(
            update_fields=[
                "notification_quiet_hours_enabled",
                "notification_quiet_start",
                "notification_quiet_end",
            ]
        )
        self.client.force_login(self.user)

        with patch("app.services.web_push.webpush") as send_push:
            response = self.client.post(
                "/notifications/push-test/",
                data=json.dumps({"endpoint": self.endpoint}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        send_push.assert_called_once()
        payload = json.loads(send_push.call_args.kwargs["data"])
        self.assertEqual(payload["title"], "Lunora-Testbenachrichtigung")

    def test_test_push_endpoint_is_user_scoped(self):
        self.create_subscription(self.other)
        self.client.force_login(self.user)

        with patch("app.services.web_push.webpush") as send_push:
            response = self.client.post(
                "/notifications/push-test/",
                data=json.dumps({"endpoint": self.endpoint}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 404)
        send_push.assert_not_called()

    def test_weather_alerts_are_checked_once_per_user_with_multiple_devices(self):
        self.create_subscription()
        second_payload = {
            **self.subscription_payload,
            "endpoint": "https://updates.push.services.mozilla.com/wpush/v2/second-device",
        }
        register_web_push_subscription(self.user, second_payload)
        WeatherLocation.objects.create(
            user=self.user,
            query="Berlin,de",
            name="Berlin",
            label="Berlin",
        )

        with patch(
            "app.services.notifications.materialize_due_weather_alerts", return_value=[{"id": "alert"}]
        ) as claim:
            result = materialize_web_push_weather_alerts()

        self.assertEqual(result, {"created": 1, "failed": 0})
        claim.assert_called_once()

    def test_weather_alert_detection_supports_inbox_only_preferences(self):
        profile = self.user.profile
        profile.notify_desktop = False
        profile.notify_email = False
        profile.save(update_fields=["notify_desktop", "notify_email"])
        NotificationPreference.objects.create(
            user=self.user,
            category=NotificationPreference.CATEGORY_WEATHER,
            inbox_enabled=True,
            email_enabled=False,
            web_push_enabled=False,
        )
        WeatherLocation.objects.create(
            user=self.user,
            query="Berlin,de",
            name="Berlin",
            label="Berlin",
        )

        with patch(
            "app.services.notifications.materialize_due_weather_alerts", return_value=[{"id": "alert"}]
        ) as detect:
            result = materialize_web_push_weather_alerts()

        self.assertEqual(result, {"created": 1, "failed": 0})
        detect.assert_called_once_with(self.user, now=None)
