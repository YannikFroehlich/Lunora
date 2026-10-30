import importlib
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from app.models import (
    CalendarEvent,
    CalendarEventAttendee,
    Profile,
    SystemSettings,
    Task,
    VacationPeriod,
    VacationYear,
)


class CalendarFeedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        self.profile = Profile.objects.create(
            user=self.user,
            display_name="Mira",
            timezone_name="Europe/Berlin",
            calendar_feed_token="mira-feed-token",
        )
        self.feed_url = "/calendar/feed/mira-feed-token.ics"

    def create_event(self, user, title, start_at, **extra):
        return CalendarEvent.objects.create(
            user=user,
            title=title,
            start_at=start_at,
            end_at=extra.pop("end_at", start_at + timedelta(hours=1)),
            **extra,
        )

    def test_feed_is_public_with_token_and_404_for_unknown_or_inactive(self):
        response = self.client.get(self.feed_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/calendar; charset=utf-8")
        body = response.content.decode("utf-8")
        self.assertTrue(body.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"))
        self.assertTrue(body.endswith("END:VCALENDAR\r\n"))
        self.assertIn("X-WR-CALNAME:Lunora – Mira", body)

        self.assertEqual(self.client.get("/calendar/feed/wrong-token.ics").status_code, 404)

        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(self.feed_url).status_code, 404)

    def test_feed_contains_own_events_and_accepted_invitations_only(self):
        other = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        Profile.objects.create(user=other, display_name="Lukas")
        start_at = timezone.now() + timedelta(days=3)
        own = self.create_event(
            self.user, "Quartalsplanung", start_at, category=CalendarEvent.CATEGORY_MEETING
        )
        accepted = self.create_event(other, "Gemeinsames Essen", start_at)
        declined = self.create_event(other, "Abgesagtes Treffen", start_at)
        pending = self.create_event(other, "Offene Einladung", start_at)
        self.create_event(other, "Fremder Termin", start_at)
        self.create_event(self.user, "Uralt", timezone.now() - timedelta(days=400))
        CalendarEventAttendee.objects.create(
            event=accepted, user=self.user, status=CalendarEventAttendee.STATUS_ACCEPTED
        )
        CalendarEventAttendee.objects.create(
            event=declined, user=self.user, status=CalendarEventAttendee.STATUS_DECLINED
        )
        CalendarEventAttendee.objects.create(event=pending, user=self.user)

        body = self.client.get(self.feed_url).content.decode("utf-8")

        self.assertIn(f"UID:lunora-event-{own.id}\r\n", body)
        self.assertIn("SUMMARY:[Meeting] Quartalsplanung\r\n", body)
        self.assertIn("CATEGORIES:Meeting\r\n", body)
        self.assertIn("SUMMARY:Gemeinsames Essen\r\n", body)
        self.assertIn("CATEGORIES:Termin\r\n", body)
        self.assertEqual(body.count("BEGIN:VEVENT"), 2)
        for title in ("Abgesagtes Treffen", "Offene Einladung", "Fremder Termin", "Uralt"):
            self.assertNotIn(title, body)

    def test_feed_formats_times_escapes_text_and_folds_long_lines(self):
        start_at = timezone.make_aware(datetime(2099, 3, 2, 8, 15), ZoneInfo("Europe/Berlin"))
        self.create_event(
            self.user,
            "Physik; Kapitel 3, Optik",
            start_at,
            location="Raum 1\nNeubau",
            description="ä" * 60,
            category=CalendarEvent.CATEGORY_PERSONAL,
        )
        all_day_start = timezone.make_aware(datetime(2099, 3, 5), ZoneInfo("Europe/Berlin"))
        self.create_event(
            self.user,
            "Projektabgabe",
            all_day_start,
            end_at=all_day_start + timedelta(days=1),
            is_all_day=True,
            category=CalendarEvent.CATEGORY_DEADLINE,
        )

        body = self.client.get(self.feed_url).content.decode("utf-8")

        self.assertIn("DTSTART:20990302T071500Z\r\nDTEND:20990302T081500Z\r\n", body)
        self.assertIn("SUMMARY:[Privat] Physik\\; Kapitel 3\\, Optik\r\n", body)
        self.assertIn("LOCATION:Raum 1\\nNeubau\r\n", body)
        self.assertIn("DTSTART;VALUE=DATE:20990305\r\nDTEND;VALUE=DATE:20990306\r\n", body)
        self.assertIn("SUMMARY:[Deadline] Projektabgabe\r\n", body)
        for line in body.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75, line)
        unfolded = body.replace("\r\n ", "")
        self.assertIn("DESCRIPTION:" + "ä" * 60 + "\r\n", unfolded)


class CalendarVacationAndRangeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(
            user=self.user,
            display_name="Mira",
            timezone_name="Europe/Berlin",
            calendar_feed_token="mira-token",
        )
        self.client.login(username="mira@example.com", password="secret-12345")

    def _days(self, response):
        return {day["date_input"]: day for week in response.context["calendar_rows"] for day in week}

    def test_month_grid_shows_vacations_and_holidays_as_markers(self):
        VacationYear.objects.create(user=self.user, year=2026, allowance_days=Decimal("30"), subdivision="NW")
        VacationPeriod.objects.create(
            user=self.user, name="tarifurlaub", start_date=date(2026, 12, 21), end_date=date(2026, 12, 23)
        )

        days = self._days(self.client.get("/calendar/?year=2026&month=12"))

        self.assertEqual([marker["label"] for marker in days["2026-12-22"]["markers"]], ["Tarifurlaub"])
        self.assertEqual(days["2026-12-22"]["markers"][0]["tone"], "green")
        self.assertEqual(
            days["2026-12-22"]["markers"][0]["url"], "/vacation-planner/?year=2026&month=12#vacation-calendar"
        )
        self.assertEqual(days["2026-12-22"]["events"], [])
        self.assertEqual(
            days["2026-12-25"]["markers"],
            [
                {
                    "label": "Erster Weihnachtstag",
                    "tone": "red",
                    "url": "/vacation-planner/?year=2026&month=12#vacation-calendar",
                }
            ],
        )
        self.assertEqual(days["2026-12-24"]["markers"], [])

        SystemSettings.objects.update_or_create(pk=1, defaults={"vacation_planner_enabled": False})
        days = self._days(self.client.get("/calendar/?year=2026&month=12"))
        self.assertEqual(days["2026-12-22"]["markers"], [])

    def test_upcoming_lists_include_vacations_in_date_order(self):
        today = timezone.localdate(timezone=ZoneInfo("Europe/Berlin"))
        VacationPeriod.objects.create(
            user=self.user,
            name="sonderurlaub",
            start_date=today + timedelta(days=5),
            end_date=today + timedelta(days=6),
        )
        start_at = timezone.make_aware(
            datetime.combine(today + timedelta(days=9), datetime.min.time()), ZoneInfo("Europe/Berlin")
        )
        CalendarEvent.objects.create(user=self.user, title="Zahnarzt", start_at=start_at, end_at=start_at)

        calendar_items = self.client.get("/calendar/").context["upcoming_events"]
        dashboard_items = self.client.get("/home/").context["upcoming_dashboard_events"]

        self.assertEqual([item["title"] for item in calendar_items], ["Sonderurlaub", "Zahnarzt"])
        self.assertTrue(calendar_items[0]["category"].startswith("Urlaub bis "))
        self.assertEqual([item["title"] for item in dashboard_items], ["Sonderurlaub", "Zahnarzt"])
        self.assertTrue(dashboard_items[0]["time"].startswith("Urlaub bis "))
        self.assertTrue(dashboard_items[0]["url"].startswith("/vacation-planner/?year="))

    def test_feed_contains_vacations_as_all_day_events(self):
        period = VacationPeriod.objects.create(
            user=self.user,
            name="tarifurlaub",
            start_date=date(2099, 7, 1),
            end_date=date(2099, 7, 14),
            notes="Ostsee",
        )

        body = self.client.get("/calendar/feed/mira-token.ics").content.decode("utf-8")

        self.assertIn(
            f"BEGIN:VEVENT\r\nUID:lunora-vacation-{period.pk}\r\n",
            body,
        )
        self.assertIn(
            "DTSTART;VALUE=DATE:20990701\r\nDTEND;VALUE=DATE:20990715\r\nSUMMARY:Tarifurlaub\r\n", body
        )
        self.assertIn("CATEGORIES:Urlaub\r\nDESCRIPTION:Ostsee\r\n", body)

    def test_event_can_span_several_days_with_an_end_date(self):
        response = self.client.post(
            "/calendar/?year=2099&month=8",
            {
                "form_name": "calendar_event_add",
                "title": "Messe",
                "event_date": "2099-08-10",
                "end_date": "2099-08-12",
                "is_all_day": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        event = CalendarEvent.objects.get(user=self.user, title="Messe")
        berlin = ZoneInfo("Europe/Berlin")
        self.assertEqual(event.start_at.astimezone(berlin), datetime(2099, 8, 10, tzinfo=berlin))
        self.assertEqual(event.end_at.astimezone(berlin), datetime(2099, 8, 13, tzinfo=berlin))
        days = self._days(self.client.get("/calendar/?year=2099&month=8"))
        self.assertEqual(
            [len(days[f"2099-08-{day:02d}"]["events"]) for day in (9, 10, 11, 12, 13)], [0, 1, 1, 1, 0]
        )
        self.assertEqual(days["2099-08-11"]["events"][0]["edit_end_date"], "2099-08-12")

        response = self.client.post(
            "/calendar/?year=2099&month=8",
            {
                "form_name": "calendar_event_add",
                "title": "Rückwärts",
                "event_date": "2099-08-10",
                "end_date": "2099-08-09",
                "is_all_day": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("end_date", response.context["event_form"].errors)
        self.assertFalse(CalendarEvent.objects.filter(title="Rückwärts").exists())


class CalendarCategoryMigrationTests(TestCase):
    def test_school_categories_become_appointments(self):
        migration = importlib.import_module("app.migrations.0052_general_calendar_categories")
        user = User.objects.create_user(username="mira", password="secret-12345")
        start_at = timezone.now()
        for category in ("exam", "test", "presentation", "deadline", "other"):
            CalendarEvent.objects.create(
                user=user, title=category, category=category, start_at=start_at, end_at=start_at
            )

        migration.map_school_categories(django_apps, None)

        self.assertEqual(
            dict(CalendarEvent.objects.values_list("title", "category")),
            {
                "exam": "appointment",
                "test": "appointment",
                "presentation": "appointment",
                "deadline": "deadline",
                "other": "other",
            },
        )


class CalendarWeekViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=self.user, display_name="Mira", timezone_name="Europe/Berlin")
        self.client.login(username="mira@example.com", password="secret-12345")
        self.berlin = ZoneInfo("Europe/Berlin")

    def create_event(self, title, start, end, **extra):
        return CalendarEvent.objects.create(
            user=self.user,
            title=title,
            start_at=start.replace(tzinfo=self.berlin),
            end_at=end.replace(tzinfo=self.berlin),
            **extra,
        )

    def view_urls(self, response):
        return {option["key"]: option["url"] for option in response.context["calendar_view_options"]}

    def week(self, day="2099-08-12"):
        response = self.client.get(f"/calendar/?view=week&date={day}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["calendar_view"], "week")
        return response, {item["date_input"]: item for item in response.context["calendar_grid"]["days"]}

    def test_week_shows_monday_to_sunday_of_the_anchor_day_with_navigation(self):
        response, days = self.week("2099-08-12")

        self.assertEqual(list(days), [f"2099-08-{day:02d}" for day in range(10, 17)])
        self.assertEqual(
            [day["weekday"] for day in days.values()], ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
        )
        week = response.context["calendar_grid"]
        self.assertEqual(week["label"], "KW 33")
        self.assertEqual(response.context["period_label"], "KW 33")
        self.assertEqual(response.context["calendar_rows"], [])
        self.assertEqual(week["scroll_minute"], 7 * 60)
        self.assertEqual(response.context["prev_url"], "/calendar/?view=week&date=2099-08-05")
        self.assertEqual(response.context["next_url"], "/calendar/?view=week&date=2099-08-19")
        self.assertEqual(self.view_urls(response)["month"], "/calendar/?view=month&year=2099&month=8")
        self.assertTemplateUsed(response, "app/partials/calendar_time_grid.html")
        self.assertContains(response, 'aria-label="Nächste Woche"')

    def test_timed_events_are_placed_on_the_grid_and_overlaps_share_the_column(self):
        self.create_event("Standup", datetime(2099, 8, 12, 9, 0), datetime(2099, 8, 12, 10, 30))
        self.create_event("Review", datetime(2099, 8, 12, 10, 0), datetime(2099, 8, 12, 11, 0))
        self.create_event("Mittag", datetime(2099, 8, 12, 12, 0), datetime(2099, 8, 12, 12, 0))

        _response, days = self.week()

        timed = {event["label"]: event for event in days["2099-08-12"]["timed"]}
        self.assertEqual(
            {
                label: (item["start_minute"], item["duration"], item["lane"], item["lanes"])
                for label, item in timed.items()
            },
            {"Standup": (540, 90, 0, 2), "Review": (600, 60, 1, 2), "Mittag": (720, 20, 0, 1)},
        )
        self.assertEqual(timed["Standup"]["time"], "09:00 – 10:30")
        self.assertEqual(timed["Mittag"]["time"], "12:00")
        self.assertTrue(timed["Standup"]["can_delete"])
        self.assertEqual(timed["Standup"]["edit_start_time"], "09:00")
        self.assertEqual(days["2099-08-12"]["all_day"], [])

    def test_all_day_and_long_events_go_to_the_all_day_row_and_night_events_are_split(self):
        self.create_event("Messe", datetime(2099, 8, 11), datetime(2099, 8, 13), is_all_day=True)
        self.create_event("Konferenz", datetime(2099, 8, 14, 9, 0), datetime(2099, 8, 15, 18, 0))
        self.create_event("Nachtschicht", datetime(2099, 8, 12, 22, 0), datetime(2099, 8, 13, 6, 0))
        self.create_event("Bis Mitternacht", datetime(2099, 8, 10, 23, 0), datetime(2099, 8, 11, 0, 0))

        _response, days = self.week()

        self.assertEqual([event["label"] for event in days["2099-08-11"]["all_day"]], ["Messe"])
        self.assertEqual([event["label"] for event in days["2099-08-12"]["all_day"]], ["Messe"])
        self.assertEqual(days["2099-08-13"]["all_day"], [])
        self.assertEqual([event["label"] for event in days["2099-08-15"]["all_day"]], ["Konferenz"])
        night = [(event["start_minute"], event["duration"]) for event in days["2099-08-12"]["timed"]]
        self.assertEqual(night, [(22 * 60, 2 * 60)])
        night = [(event["start_minute"], event["duration"]) for event in days["2099-08-13"]["timed"]]
        self.assertEqual(night, [(0, 6 * 60)])
        self.assertEqual([event["label"] for event in days["2099-08-10"]["timed"]], ["Bis Mitternacht"])
        self.assertEqual(days["2099-08-11"]["timed"], [])

    def test_week_shows_tasks_and_today_and_falls_back_for_invalid_dates(self):
        today = timezone.localdate(timezone=self.berlin)
        due_at = timezone.make_aware(datetime.combine(today, datetime.min.time()), self.berlin) + timedelta(
            hours=15
        )
        Task.objects.create(user=self.user, title="Steuer", due_at=due_at)

        response, days = self.week("kein-datum")

        today_item = days[today.isoformat()]
        self.assertTrue(today_item["today"])
        self.assertIsNotNone(today_item["now_minute"])
        self.assertEqual([task["label"] for task in today_item["tasks"]], ["Steuer"])
        self.assertEqual(response.context["today_url"], "/calendar/?view=week")
        self.assertEqual(self.view_urls(response)["day"], f"/calendar/?view=day&date={today.isoformat()}")

    def test_month_view_stays_the_default_and_links_to_the_week(self):
        response = self.client.get("/calendar/?year=2099&month=8")

        self.assertEqual(response.context["calendar_view"], "month")
        self.assertIsNone(response.context["calendar_grid"])
        self.assertEqual(response.context["period_label"], "August 2099")
        self.assertEqual(self.view_urls(response)["week"], "/calendar/?view=week&date=2099-08-01")
        self.assertEqual(response.context["today_url"], "/calendar/?view=month")
        self.assertEqual(response.context["next_url"], "/calendar/?year=2099&month=9")
        self.assertTemplateNotUsed(response, "app/partials/calendar_time_grid.html")

    def test_posts_from_the_week_view_redirect_back_to_it(self):
        response = self.client.post(
            "/calendar/?view=week&date=2099-08-12",
            {
                "form_name": "calendar_event_add",
                "title": "Workshop",
                "event_date": "2099-08-13",
                "start_time": "14:00",
                "end_time": "16:00",
                "category": CalendarEvent.CATEGORY_MEETING,
                "repeat": "none",
            },
        )

        self.assertRedirects(response, "/calendar/?view=week&date=2099-08-12")
        _response, days = self.week()
        self.assertEqual(
            [
                (event["label"], event["start_minute"], event["duration"])
                for event in days["2099-08-13"]["timed"]
            ],
            [("Workshop", 14 * 60, 120)],
        )


class CalendarSeriesEditTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=self.user, display_name="Mira", timezone_name="Europe/Berlin")
        self.guest = User.objects.create_user(username="jonas@example.com", email="jonas@example.com")
        self.other_guest = User.objects.create_user(username="lea@example.com", email="lea@example.com")
        self.client.login(username="mira@example.com", password="secret-12345")
        self.berlin = ZoneInfo("Europe/Berlin")
        # Five Wednesdays across the end of daylight saving time (25 October 2099).
        response = self.client.post(
            "/calendar/",
            {
                "form_name": "calendar_event_add",
                "title": "Teamrunde",
                "event_date": "2099-10-07",
                "start_time": "10:00",
                "end_time": "11:00",
                "category": CalendarEvent.CATEGORY_MEETING,
                "repeat": "WEEKLY",
                "repeat_until": "2099-11-04",
                "attendees": [self.guest.id],
            },
        )
        self.assertEqual(response.status_code, 302)
        self.series = list(CalendarEvent.objects.order_by("start_at"))
        self.assertEqual(len(self.series), 5)

    def edit(self, event, scope, **changes):
        data = {
            "form_name": "calendar_event_edit",
            "event_id": event.id,
            "title": "Teamrunde",
            "event_date": event.start_at.astimezone(self.berlin).date().isoformat(),
            "start_time": "10:00",
            "end_time": "11:00",
            "category": CalendarEvent.CATEGORY_MEETING,
            "attendees": [self.guest.id],
            "scope": scope,
            **changes,
        }
        return self.client.post("/calendar/", data)

    def local(self, event):
        event.refresh_from_db()
        start = event.start_at.astimezone(self.berlin)
        end = event.end_at.astimezone(self.berlin)
        return (event.title, start.date().isoformat(), start.strftime("%H:%M"), end.strftime("%H:%M"))

    def test_only_this_occurrence_changes_and_stays_in_the_series(self):
        response = self.edit(
            self.series[1], "this", title="Sonderrunde", start_time="09:00", end_time="09:30"
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.local(self.series[1]), ("Sonderrunde", "2099-10-14", "09:00", "09:30"))
        self.assertEqual(self.local(self.series[2]), ("Teamrunde", "2099-10-21", "10:00", "11:00"))
        self.assertEqual(self.series[1].recurrence_id, self.series[0].recurrence_id)

    def test_following_occurrences_move_along_and_split_into_their_own_series(self):
        response = self.edit(
            self.series[2],
            "following",
            title="Neue Runde",
            event_date="2099-10-22",
            start_time="11:30",
            end_time="12:15",
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            [self.local(event) for event in self.series],
            [
                ("Teamrunde", "2099-10-07", "10:00", "11:00"),
                ("Teamrunde", "2099-10-14", "10:00", "11:00"),
                ("Neue Runde", "2099-10-22", "11:30", "12:15"),
                # Past the switch to winter time the local time of day stays the same.
                ("Neue Runde", "2099-10-29", "11:30", "12:15"),
                ("Neue Runde", "2099-11-05", "11:30", "12:15"),
            ],
        )
        old_series = {event.recurrence_id for event in self.series[:2]}
        new_series = {event.recurrence_id for event in self.series[2:]}
        self.assertEqual(len(old_series), 1)
        self.assertEqual(len(new_series), 1)
        self.assertNotEqual(old_series, new_series)

    def test_following_from_the_first_occurrence_keeps_the_series(self):
        recurrence_id = self.series[0].recurrence_id

        self.edit(self.series[0], "following", title="Umbenannt")

        self.assertEqual(
            set(CalendarEvent.objects.values_list("title", "recurrence_id")), {("Umbenannt", recurrence_id)}
        )

    def test_whole_series_takes_new_attendees_and_keeps_existing_answers(self):
        CalendarEventAttendee.objects.filter(event=self.series[0], user=self.guest).update(
            status=CalendarEventAttendee.STATUS_ACCEPTED
        )

        self.edit(self.series[3], "all", attendees=[self.guest.id, self.other_guest.id], location="Raum 2")

        self.assertEqual(set(CalendarEvent.objects.values_list("location", flat=True)), {"Raum 2"})
        self.assertEqual(CalendarEventAttendee.objects.filter(user=self.other_guest).count(), 5)
        self.assertEqual(
            CalendarEventAttendee.objects.get(event=self.series[0], user=self.guest).status,
            CalendarEventAttendee.STATUS_ACCEPTED,
        )

        self.edit(self.series[3], "all", attendees=[self.other_guest.id])

        self.assertFalse(CalendarEventAttendee.objects.filter(user=self.guest).exists())

    def test_delete_honours_the_series_scope(self):
        response = self.client.post(
            "/calendar/",
            {
                "form_name": ["calendar_event_edit", "calendar_event_delete"],
                "event_id": self.series[3].id,
                "scope": "following",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            list(CalendarEvent.objects.order_by("start_at").values_list("id", flat=True)),
            [event.id for event in self.series[:3]],
        )

        self.client.post(
            "/calendar/",
            {"form_name": "calendar_event_delete", "event_id": self.series[1].id, "scope": "this"},
        )
        self.assertEqual(CalendarEvent.objects.count(), 2)

    def test_other_users_cannot_edit_or_delete_the_series(self):
        intruder = User.objects.create_user(username="eve@example.com", password="secret-12345")
        self.client.force_login(intruder)

        self.edit(self.series[0], "all", title="Gekapert")
        self.client.post(
            "/calendar/",
            {"form_name": "calendar_event_delete", "event_id": self.series[0].id, "scope": "all"},
        )

        self.assertEqual(CalendarEvent.objects.filter(title="Teamrunde").count(), 5)

    def test_edit_dialog_offers_the_scope_only_for_series(self):
        single = CalendarEvent.objects.create(
            user=self.user,
            title="Einzeltermin",
            start_at=datetime(2099, 10, 8, 9, tzinfo=self.berlin),
            end_at=datetime(2099, 10, 8, 10, tzinfo=self.berlin),
        )

        response = self.client.get("/calendar/?year=2099&month=10")

        events = {
            item["event_id"]: item for item in response.context["upcoming_events"] if "event_id" in item
        }
        self.assertTrue(events[self.series[0].id]["recurrence_id"])
        self.assertIsNone(events[single.id]["recurrence_id"])
        self.assertContains(response, 'data-edit-series="true"')
        self.assertContains(response, 'data-edit-series="false"')
        self.assertContains(response, 'name="scope" value="following"')


class CalendarDayAndThreeDayViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        self.profile = Profile.objects.create(
            user=self.user, display_name="Mira", timezone_name="Europe/Berlin"
        )
        self.client.login(username="mira@example.com", password="secret-12345")
        self.berlin = ZoneInfo("Europe/Berlin")

    def test_three_days_start_at_the_anchor_and_reach_into_the_next_month(self):
        start = datetime(2099, 9, 1, 10, 0, tzinfo=self.berlin)
        CalendarEvent.objects.create(
            user=self.user, title="Kickoff", start_at=start, end_at=start + timedelta(hours=1)
        )

        response = self.client.get("/calendar/?view=three_days&date=2099-08-31")

        grid = response.context["calendar_grid"]
        self.assertEqual(
            [day["date_input"] for day in grid["days"]], ["2099-08-31", "2099-09-01", "2099-09-02"]
        )
        self.assertEqual(grid["day_count"], 3)
        self.assertEqual(grid["label"], "3 Tage")
        self.assertEqual([event["label"] for event in grid["days"][1]["timed"]], ["Kickoff"])
        # The stats stay with the anchor day's month.
        self.assertEqual(response.context["month_label"], "August 2099")
        self.assertEqual(response.context["prev_url"], "/calendar/?view=three_days&date=2099-08-28")
        self.assertEqual(response.context["next_url"], "/calendar/?view=three_days&date=2099-09-03")
        self.assertContains(response, "--week-days: 3")

    def test_day_view_shows_one_day_named_by_its_weekday(self):
        response = self.client.get("/calendar/?view=day&date=2099-08-12")

        grid = response.context["calendar_grid"]
        self.assertEqual([day["date_input"] for day in grid["days"]], ["2099-08-12"])
        self.assertEqual(grid["label"], "Mittwoch")
        self.assertEqual(grid["range_label"], "12.08.2099")
        self.assertEqual(response.context["next_url"], "/calendar/?view=day&date=2099-08-13")
        self.assertContains(response, 'aria-label="Nächster Tag"')

    def test_the_chosen_view_is_remembered_for_the_plain_calendar_page(self):
        self.assertEqual(self.client.get("/calendar/").context["calendar_view"], "month")

        self.client.get("/calendar/?view=three_days&date=2099-08-12")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.calendar_view, "three_days")
        self.assertEqual(self.client.get("/calendar/").context["calendar_view"], "three_days")
        # A month link without a view still shows that month.
        self.assertEqual(self.client.get("/calendar/?year=2099&month=8").context["calendar_view"], "month")

        self.client.get("/calendar/?view=month&year=2099&month=8")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.calendar_view, "month")
        self.client.get("/calendar/?view=unbekannt")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.calendar_view, "month")


class CalendarEventMoveTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=self.user, display_name="Mira", timezone_name="Europe/Berlin")
        self.client.login(username="mira@example.com", password="secret-12345")
        self.berlin = ZoneInfo("Europe/Berlin")
        self.url = "/calendar/?view=week&date=2099-08-12"

    def create_event(self, title, start, end, **extra):
        return CalendarEvent.objects.create(
            user=self.user,
            title=title,
            start_at=start.replace(tzinfo=self.berlin),
            end_at=end.replace(tzinfo=self.berlin),
            **extra,
        )

    def move(self, event, **fields):
        return self.client.post(
            self.url, {"form_name": "calendar_event_move", "event_id": event.pk, **fields}
        )

    def local(self, event):
        event.refresh_from_db()
        return event.start_at.astimezone(self.berlin).replace(tzinfo=None), event.end_at.astimezone(
            self.berlin
        ).replace(tzinfo=None)

    def test_dropping_on_a_day_keeps_time_and_length_and_on_the_grid_sets_the_time(self):
        event = self.create_event("Review", datetime(2099, 8, 12, 9, 30), datetime(2099, 8, 12, 11, 0))

        response = self.move(event, date="2099-08-14")

        self.assertRedirects(response, self.url)
        self.assertEqual(self.local(event), (datetime(2099, 8, 14, 9, 30), datetime(2099, 8, 14, 11, 0)))

        self.move(event, date="2099-08-15", start_minute=23 * 60)
        self.assertEqual(self.local(event), (datetime(2099, 8, 15, 23, 0), datetime(2099, 8, 16, 0, 30)))

    def test_moves_keep_the_wall_clock_time_across_daylight_saving_changes(self):
        event = self.create_event(
            "Sprechstunde", datetime(2099, 10, 21, 10, 0), datetime(2099, 10, 21, 11, 0)
        )

        self.move(event, date="2099-10-28")

        self.assertEqual(self.local(event), (datetime(2099, 10, 28, 10, 0), datetime(2099, 10, 28, 11, 0)))

    def test_all_day_events_only_change_their_days(self):
        event = self.create_event("Messe", datetime(2099, 8, 10), datetime(2099, 8, 13), is_all_day=True)

        self.move(event, date="2099-08-17", start_minute=600)

        self.assertEqual(self.local(event), (datetime(2099, 8, 17), datetime(2099, 8, 20)))
        self.move(event, date="2099-08-17", end_minute=600)
        self.assertEqual(self.local(event), (datetime(2099, 8, 17), datetime(2099, 8, 20)))

    def test_stretching_sets_the_end_but_not_before_the_start(self):
        event = self.create_event("Workshop", datetime(2099, 8, 12, 14, 0), datetime(2099, 8, 12, 15, 0))

        self.move(event, date="2099-08-12", end_minute=17 * 60 + 30)
        self.assertEqual(self.local(event), (datetime(2099, 8, 12, 14, 0), datetime(2099, 8, 12, 17, 30)))

        self.move(event, date="2099-08-12", end_minute=24 * 60)
        self.assertEqual(self.local(event)[1], datetime(2099, 8, 13, 0, 0))

        response = self.client.post(
            self.url,
            {
                "form_name": "calendar_event_move",
                "event_id": event.pk,
                "date": "2099-08-12",
                "end_minute": 14 * 60,
            },
            follow=True,
        )
        self.assertContains(response, "Termin konnte nicht verschoben werden.")
        self.assertEqual(self.local(event)[1], datetime(2099, 8, 13, 0, 0))

    def test_only_own_events_move_and_a_series_occurrence_moves_alone(self):
        other = User.objects.create_user(username="jonas@example.com", email="jonas@example.com")
        foreign = CalendarEvent.objects.create(
            user=other,
            title="Fremd",
            start_at=datetime(2099, 8, 12, 9, 0, tzinfo=self.berlin),
            end_at=datetime(2099, 8, 12, 10, 0, tzinfo=self.berlin),
        )
        CalendarEventAttendee.objects.create(event=foreign, user=self.user)

        self.move(foreign, date="2099-08-14")
        self.assertEqual(self.local(foreign)[0], datetime(2099, 8, 12, 9, 0))

        series_id = uuid.uuid4()
        first = self.create_event(
            "Jour fixe", datetime(2099, 8, 10, 9, 0), datetime(2099, 8, 10, 10, 0), recurrence_id=series_id
        )
        second = self.create_event(
            "Jour fixe", datetime(2099, 8, 17, 9, 0), datetime(2099, 8, 17, 10, 0), recurrence_id=series_id
        )

        self.move(first, date="2099-08-11", start_minute=13 * 60)

        self.assertEqual(self.local(first)[0], datetime(2099, 8, 11, 13, 0))
        self.assertEqual(self.local(second)[0], datetime(2099, 8, 17, 9, 0))
        first.refresh_from_db()
        self.assertEqual(first.recurrence_id, series_id)

    def test_move_is_gated_by_the_event_creation_flag(self):
        event = self.create_event("Review", datetime(2099, 8, 12, 9, 30), datetime(2099, 8, 12, 11, 0))
        SystemSettings.objects.update_or_create(pk=1, defaults={"calendar_event_creation_enabled": False})

        response = self.move(event, date="2099-08-14")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.local(event)[0], datetime(2099, 8, 12, 9, 30))

    def test_own_events_carry_move_data_and_resize_handles(self):
        self.create_event("Review", datetime(2099, 8, 12, 9, 30), datetime(2099, 8, 12, 11, 0))
        self.create_event("Nacht", datetime(2099, 8, 13, 22, 0), datetime(2099, 8, 14, 2, 0))

        response = self.client.get(self.url)

        self.assertContains(response, 'data-event-move="', count=3)
        self.assertContains(response, 'data-move-start="570"')
        # The night event can only be stretched where it ends, on the next day.
        self.assertContains(response, "data-event-resize", count=2)
