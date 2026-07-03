from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from app.models import CalendarEvent, CalendarReminder, CalendarSource, ChatMessage, ChatMessageReaction, Conversation, ConversationMember, Profile
from app.services.calendar_service import parse_ical_events
from app.services.weather_service import get_weather_context



@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SettingsProfileTests(TestCase):
    def test_settings_page_requires_login(self):
        response = self.client.get("/settings/")

        self.assertRedirects(response, "/login/?next=/settings/")

    def test_default_django_login_path_is_supported(self):
        response = self.client.get("/accounts/login/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Anmelden")

    def test_registration_creates_user_and_profile(self):
        response = self.client.post(
            "/register/",
            {
                "name": "Mira Beispiel",
                "email": "mira@example.com",
                "password1": "sicheres-passwort-42",
                "password2": "sicheres-passwort-42",
            },
        )

        self.assertRedirects(response, "/home/")
        user = User.objects.get(username="mira@example.com")
        self.assertTrue(user.check_password("sicheres-passwort-42"))
        self.assertEqual(user.profile.display_name, "Mira Beispiel")

    def test_logged_in_user_can_update_profile(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/settings/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="form_name" value="profile"')
        self.assertContains(response, "Profil speichern")

        response = self.client.post(
            "/settings/",
            {
                "form_name": "profile",
                "display_name": "Mira Neu",
            },
        )

        self.assertRedirects(response, "/home/")
        user.refresh_from_db()
        self.assertEqual(user.profile.display_name, "Mira Neu")
        self.assertEqual(user.first_name, "Mira Neu")

    def test_logged_in_user_can_save_appearance_settings(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/settings/",
            {
                "form_name": "appearance",
                "theme": "dark",
                "accent_color": "#7f916b",
                "background_softness": "82",
                "density": "compact",
            },
        )

        self.assertRedirects(response, "/home/")
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.theme, "dark")
        self.assertEqual(user.profile.accent_color, "#7f916b")
        self.assertEqual(user.profile.background_softness, 82)
        self.assertEqual(user.profile.density, "compact")

    def test_logged_in_user_can_save_region_settings(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/settings/",
            {
                "form_name": "appearance",
                "theme": "light",
                "accent_color": "#c2a276",
                "background_softness": "55",
                "density": "comfortable",
                "date_format": "iso",
                "time_format": "12h",
                "timezone_name": "UTC",
            },
        )

        self.assertRedirects(response, "/home/")
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.date_format, "iso")
        self.assertEqual(user.profile.time_format, "12h")
        self.assertEqual(user.profile.timezone_name, "UTC")

    def test_calendar_source_can_be_saved(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        with patch("app.views.calendar_views.sync_calendar_source", return_value={"synced": True, "message": "1 Termine synchronisiert."}):
            response = self.client.post(
                "/calendar/",
                {
                    "form_name": "calendar_source",
                    "ical_url": "https://calendar.google.com/calendar/ical/example/private/basic.ics",
                    "enabled": "on",
                },
            )

        self.assertRedirects(response, "/calendar/")
        source = CalendarSource.objects.get(user=user)
        self.assertEqual(source.ical_url, "https://calendar.google.com/calendar/ical/example/private/basic.ics")
        self.assertTrue(source.enabled)

    def test_calendar_source_can_be_saved_from_settings(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/settings/",
            {
                "form_name": "calendar_source",
                "ical_url": "https://calendar.google.com/calendar/ical/settings/private/basic.ics",
                "enabled": "on",
            },
        )

        self.assertRedirects(response, "/home/")
        source = CalendarSource.objects.get(user=user)
        self.assertEqual(source.ical_url, "https://calendar.google.com/calendar/ical/settings/private/basic.ics")
        self.assertTrue(source.enabled)

    def test_settings_calendar_source_is_scoped_to_logged_in_user(self):
        mira = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        lukas = User.objects.create_user(username="lukas@example.com", email="lukas@example.com", password="secret-12345")
        Profile.objects.create(user=mira, display_name="Mira")
        Profile.objects.create(user=lukas, display_name="Lukas")
        private_url = "https://calendar.google.com/calendar/ical/mira/private/basic.ics"
        CalendarSource.objects.create(user=mira, ical_url=private_url)
        self.client.login(username="lukas@example.com", password="secret-12345")

        response = self.client.get("/settings/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="form_name" value="calendar_source"')
        self.assertNotContains(response, private_url)

        response = self.client.post(
            "/settings/",
            {
                "form_name": "calendar_source",
                "ical_url": "https://calendar.google.com/calendar/ical/lukas/private/basic.ics",
                "enabled": "on",
            },
        )

        self.assertRedirects(response, "/home/")
        self.assertEqual(CalendarSource.objects.get(user=mira).ical_url, private_url)
        self.assertEqual(
            CalendarSource.objects.get(user=lukas).ical_url,
            "https://calendar.google.com/calendar/ical/lukas/private/basic.ics",
        )

    def test_calendar_page_does_not_render_calendar_source_form(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/calendar/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="form_name" value="calendar_source"')
        self.assertNotContains(response, "Google Kalender-Link")
        self.assertNotContains(response, "Kalender speichern")

    def test_calendar_page_displays_saved_events(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        source = CalendarSource.objects.create(
            user=user,
            ical_url="https://calendar.google.com/calendar/ical/example/private/basic.ics",
        )
        start_at = timezone.make_aware(datetime(2026, 7, 8, 9, 0))
        CalendarEvent.objects.create(
            user=user,
            source=source,
            external_id="event-1",
            title="Design Review",
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        with patch("app.views.calendar_views.sync_calendar_source", return_value={"synced": False, "message": "Kalender ist aktuell."}):
            response = self.client.get("/calendar/?year=2026&month=7")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Juli 2026")
        self.assertContains(response, "Design Review")

    def test_home_page_shows_upcoming_calendar_events(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        source = CalendarSource.objects.create(
            user=user,
            ical_url="https://calendar.google.com/calendar/ical/example/private/basic.ics",
        )
        start_at = timezone.now() + timedelta(days=2)
        CalendarEvent.objects.create(
            user=user,
            source=source,
            external_id="home-event-1",
            title="Sprint Planning",
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/home/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nächste Termine")
        self.assertContains(response, "Sprint Planning")
        self.assertNotContains(response, "Meine Notizen")

    def test_calendar_reminders_can_be_added_and_completed(self):
        user = User.objects.create_user(username="mira@example.com", email="mira@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/calendar/",
            {
                "form_name": "reminder_add",
                "title": "Rechnung bezahlen",
            },
        )

        self.assertRedirects(response, "/calendar/")
        reminder = CalendarReminder.objects.get(user=user)
        self.assertEqual(reminder.title, "Rechnung bezahlen")
        self.assertFalse(reminder.is_done)

        response = self.client.post(
            "/calendar/",
            {
                "form_name": "reminder_toggle",
                "reminder_id": str(reminder.id),
                "is_done": "on",
            },
        )

        self.assertRedirects(response, "/calendar/")
        reminder.refresh_from_db()
        self.assertTrue(reminder.is_done)

    def test_ical_parser_reads_google_events_and_weekly_recurrence(self):
        ical = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:abc@example.com
SUMMARY:Team Sync
DTSTART;TZID=Europe/Berlin:20260706T090000
DTEND;TZID=Europe/Berlin:20260706T100000
RRULE:FREQ=WEEKLY;COUNT=2
END:VEVENT
END:VCALENDAR
"""
        events = parse_ical_events(
            ical,
            window_start=timezone.make_aware(datetime(2026, 7, 1)),
            window_end=timezone.make_aware(datetime(2026, 7, 31)),
        )

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].title, "Team Sync")
        self.assertEqual(events[1].start_at.day, 13)



@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class WeatherRadarTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="radar@example.com",
            email="radar@example.com",
            password="secret-12345",
            first_name="Radar",
        )
        Profile.objects.create(user=self.user, display_name="Radar")

    @override_settings(WEATHER_API_KEY="")
    def test_weather_page_renders_interactive_radar(self):
        self.client.login(username="radar@example.com", password="secret-12345")

        response = self.client.get("/weather/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-radar-map")
        self.assertContains(response, "data-radar-tile-layer")
        self.assertContains(response, "data-radar-cloud-layer")
        self.assertContains(response, "data-radar-rain-layer")
        self.assertContains(response, "data-radar-cloud-tile-url")
        self.assertContains(response, "data-radar-rain-tile-url")
        self.assertContains(response, "data-radar-fullscreen")

    @override_settings(WEATHER_API_KEY="")
    def test_weather_radar_tile_requires_api_key(self):
        self.client.login(username="radar@example.com", password="secret-12345")

        response = self.client.get("/weather/radar/7/67/43.png")

        self.assertEqual(response.status_code, 404)

    @override_settings(WEATHER_API_KEY="test-key")
    def test_weather_radar_tile_proxies_png(self):
        self.client.login(username="radar@example.com", password="secret-12345")

        with patch("app.views.weather_views.fetch_weather_radar_tile", return_value=b"png-bytes") as fetch_tile:
            response = self.client.get("/weather/radar/clouds/7/67/43.png")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertIn("max-age=300", response["Cache-Control"])
        self.assertEqual(response.content, b"png-bytes")
        fetch_tile.assert_called_once_with(7, 67, 43, layer="clouds")

    @override_settings(WEATHER_API_KEY="")
    def test_weather_forecast_summary_uses_daily_forecast_values(self):
        context = get_weather_context({})

        self.assertEqual(context["forecast_summary"]["average_high"], "24°")
        self.assertEqual(context["forecast_summary"]["rain_days"], "1")
        self.assertEqual(context["forecast_summary"]["trend"], "Nass")

    @override_settings(WEATHER_API_KEY="")
    def test_weather_page_renders_calculated_forecast_summary(self):
        self.client.login(username="radar@example.com", password="secret-12345")

        response = self.client.get("/weather/")

        self.assertContains(response, "24°")
        self.assertContains(response, "Regentage")
        self.assertContains(response, "Nass")
        self.assertNotContains(response, "31°")




@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class MessagesPageTests(TestCase):
    def setUp(self):
        self.mira = User.objects.create_user(
            username="mira@example.com",
            email="mira@example.com",
            password="secret-12345",
            first_name="Mira",
        )
        self.lukas = User.objects.create_user(
            username="lukas@example.com",
            email="lukas@example.com",
            password="secret-12345",
            first_name="Lukas",
        )
        self.anna = User.objects.create_user(
            username="anna@example.com",
            email="anna@example.com",
            password="secret-12345",
            first_name="Anna",
        )
        Profile.objects.create(user=self.mira, display_name="Mira")
        Profile.objects.create(user=self.lukas, display_name="Lukas")
        Profile.objects.create(user=self.anna, display_name="Anna")

    def test_messages_page_requires_login(self):
        response = self.client.get("/messages/")

        self.assertRedirects(response, "/login/?next=/messages/")

    def test_user_can_start_direct_conversation_and_send_message(self):
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            "/messages/",
            {
                "form_name": "start_conversation",
                "recipient": str(self.lukas.id),
                "body": "Hey Lukas!",
            },
        )

        conversation = Conversation.objects.get()
        self.assertRedirects(response, f"/messages/{conversation.id}/")
        self.assertEqual(conversation.participants.count(), 2)
        self.assertTrue(conversation.participants.filter(pk=self.mira.pk).exists())
        self.assertTrue(conversation.participants.filter(pk=self.lukas.pk).exists())
        self.assertEqual(conversation.messages.get().body, "Hey Lukas!")

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message",
                "conversation_id": str(conversation.id),
                "body": "Noch eine Nachricht",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        self.assertEqual(conversation.messages.count(), 2)
        self.assertTrue(conversation.messages.filter(body="Noch eine Nachricht", sender=self.mira).exists())

    def test_non_member_cannot_open_conversation(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        ChatMessage.objects.create(conversation=conversation, sender=self.mira, body="Privat")
        self.client.login(username="anna@example.com", password="secret-12345")

        response = self.client.get(f"/messages/{conversation.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Privat")
        self.assertContains(response, "Nachrichtenübersicht")

    def test_inbox_shows_unread_message_until_opened(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        ChatMessage.objects.create(conversation=conversation, sender=self.lukas, body="Neue Antwort")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/messages/?filter=unread")

        self.assertContains(response, "Neue Antwort")
        self.assertContains(response, "unread-badge")

        response = self.client.get(f"/messages/{conversation.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Neue Antwort")
        self.assertFalse(ConversationMember.objects.get(conversation=conversation, user=self.mira).unread_count())

    def test_messages_root_shows_overview_without_auto_opening_chat(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        ChatMessage.objects.create(conversation=conversation, sender=self.lukas, body="Neue Antwort")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/messages/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nachrichtenübersicht")
        self.assertContains(response, "Neue Nachrichten")
        self.assertContains(response, "Lukas: Neue Antwort")
        self.assertNotContains(response, 'name="form_name" value="message"')
        self.assertTrue(ConversationMember.objects.get(conversation=conversation, user=self.mira).unread_count())

    def test_user_can_react_to_message(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        message = ChatMessage.objects.create(conversation=conversation, sender=self.mira, body="Gute Idee")
        self.client.login(username="lukas@example.com", password="secret-12345")

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message_action",
                "conversation_id": str(conversation.id),
                "message_id": str(message.id),
                "action": "reaction",
                "emoji": "👍",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        reaction = ChatMessageReaction.objects.get(message=message, user=self.lukas)
        self.assertEqual(reaction.emoji, "👍")

    def test_user_can_pin_and_unpin_message(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        message = ChatMessage.objects.create(conversation=conversation, sender=self.lukas, body="Wichtig")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message_action",
                "conversation_id": str(conversation.id),
                "message_id": str(message.id),
                "action": "pin",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        message.refresh_from_db()
        self.assertTrue(message.is_pinned)
        self.assertEqual(message.pinned_by, self.mira)

        response = self.client.get(f"/messages/{conversation.id}/")
        self.assertContains(response, "Angepinnt")

    def test_own_message_can_be_deleted_with_placeholder(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        message = ChatMessage.objects.create(conversation=conversation, sender=self.mira, body="Soll weg")
        ChatMessageReaction.objects.create(message=message, user=self.lukas, emoji="❤️")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message_action",
                "conversation_id": str(conversation.id),
                "message_id": str(message.id),
                "action": "delete",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        message.refresh_from_db()
        self.assertTrue(message.is_deleted)
        self.assertEqual(message.body, "")
        self.assertFalse(message.reactions.exists())

        response = self.client.get(f"/messages/{conversation.id}/")
        self.assertContains(response, "Diese Nachricht wurde gelöscht.")
        self.assertNotContains(response, "Soll weg")

    def test_other_user_cannot_delete_foreign_message(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        message = ChatMessage.objects.create(conversation=conversation, sender=self.mira, body="Bleibt da")
        self.client.login(username="lukas@example.com", password="secret-12345")

        self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message_action",
                "conversation_id": str(conversation.id),
                "message_id": str(message.id),
                "action": "delete",
            },
        )

        message.refresh_from_db()
        self.assertFalse(message.is_deleted)
        self.assertEqual(message.body, "Bleibt da")


    def test_user_can_mute_and_unmute_conversation(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "member_action",
                "conversation_id": str(conversation.id),
                "action": "mute_8h",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        membership = ConversationMember.objects.get(conversation=conversation, user=self.mira)
        self.assertIsNotNone(membership.muted_until)
        self.assertTrue(membership.muted_until > timezone.now())

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "member_action",
                "conversation_id": str(conversation.id),
                "action": "unmute",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        membership.refresh_from_db()
        self.assertIsNone(membership.muted_until)

    def test_blocked_conversation_disables_sending_until_unblocked(self):
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "member_action",
                "conversation_id": str(conversation.id),
                "action": "block",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        membership = ConversationMember.objects.get(conversation=conversation, user=self.mira)
        self.assertTrue(membership.is_blocked)

        response = self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "message",
                "conversation_id": str(conversation.id),
                "body": "Soll nicht gesendet werden",
            },
        )

        self.assertRedirects(response, f"/messages/{conversation.id}/")
        self.assertFalse(conversation.messages.filter(body="Soll nicht gesendet werden").exists())

        self.client.post(
            f"/messages/{conversation.id}/",
            {
                "form_name": "member_action",
                "conversation_id": str(conversation.id),
                "action": "unblock",
            },
        )
        membership.refresh_from_db()
        self.assertFalse(membership.is_blocked)


class MessageReadReceiptTests(TestCase):
    def test_outgoing_message_shows_read_receipt_after_recipient_opened_chat(self):
        sender = User.objects.create_user(username="sender@example.com", email="sender@example.com", password="secret-12345")
        recipient = User.objects.create_user(username="recipient@example.com", email="recipient@example.com", password="secret-12345")
        Profile.objects.create(user=sender, display_name="Sender")
        Profile.objects.create(user=recipient, display_name="Recipient")

        conversation = Conversation.objects.create(created_by=sender)
        ConversationMember.objects.create(conversation=conversation, user=sender, last_read_at=timezone.now())
        ConversationMember.objects.create(conversation=conversation, user=recipient)

        ChatMessage.objects.create(conversation=conversation, sender=sender, body="Hallo")

        self.client.login(username="sender@example.com", password="secret-12345")
        response = self.client.get(f"/messages/{conversation.id}/")
        self.assertContains(response, "Gesendet")
        self.client.logout()

        self.client.login(username="recipient@example.com", password="secret-12345")
        self.client.get(f"/messages/{conversation.id}/")
        self.client.logout()

        self.client.login(username="sender@example.com", password="secret-12345")
        response = self.client.get(f"/messages/{conversation.id}/")
        self.assertContains(response, "Gelesen")


class MessageLiveUpdateTests(TestCase):
    def test_live_updates_return_new_messages_and_mark_chat_as_read(self):
        sender = User.objects.create_user(username="sender-live@example.com", email="sender-live@example.com", password="secret-12345")
        recipient = User.objects.create_user(username="recipient-live@example.com", email="recipient-live@example.com", password="secret-12345")
        Profile.objects.create(user=sender, display_name="Sender Live")
        Profile.objects.create(user=recipient, display_name="Recipient Live")

        conversation = Conversation.objects.create(created_by=sender)
        ConversationMember.objects.create(conversation=conversation, user=sender, last_read_at=timezone.now())
        recipient_member = ConversationMember.objects.create(conversation=conversation, user=recipient)
        ChatMessage.objects.create(conversation=conversation, sender=sender, body="Neue Live-Nachricht")

        self.client.login(username="recipient-live@example.com", password="secret-12345")
        response = self.client.get(f"/messages/{conversation.id}/live/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertIn("Neue Live-Nachricht", response.json()["message_stream_html"])

        recipient_member.refresh_from_db()
        self.assertIsNotNone(recipient_member.last_read_at)

    def test_overview_live_updates_return_contact_list(self):
        user = User.objects.create_user(username="overview-live@example.com", email="overview-live@example.com", password="secret-12345")
        Profile.objects.create(user=user, display_name="Overview Live")

        self.client.login(username="overview-live@example.com", password="secret-12345")
        response = self.client.get("/messages/live/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertIn("contact_list_html", response.json())
        self.assertIn("overview_html", response.json())
