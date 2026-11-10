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
    CalendarReminder,
    Profile,
    SystemSettings,
    Task,
    UserNotification,
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
            days["2026-12-22"]["markers"][0]["url"],
            "/vacation-planner/?year=2026&month=12&day=2026-12-22#vacation-calendar",
        )
        self.assertEqual(days["2026-12-22"]["events"], [])
        self.assertEqual(
            days["2026-12-25"]["markers"],
            [
                {
                    "label": "Erster Weihnachtstag",
                    "tone": "red",
                    "url": "/vacation-planner/?year=2026&month=12&day=2026-12-25#vacation-calendar",
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

    def test_tasks_assigned_to_the_user_show_up_but_cannot_be_dragged(self):
        owner = User.objects.create_user(username="jonas", email="jonas@example.com", password="secret-12345")
        Profile.objects.create(user=owner, display_name="Jonas")
        due_at = datetime(2099, 8, 12, 10, tzinfo=self.berlin)
        Task.objects.create(user=owner, assignee=self.user, title="Folien", due_at=due_at)
        Task.objects.create(user=owner, title="Nicht meine", due_at=due_at)
        Task.objects.create(user=self.user, title="Eigene", due_at=due_at)

        response, days = self.week("2099-08-12")

        tasks = {task["label"]: task for task in days["2099-08-12"]["tasks"]}
        self.assertEqual(set(tasks), {"Folien", "Eigene"})
        self.assertEqual((tasks["Folien"]["can_move"], tasks["Folien"]["owner_name"]), (False, "Jonas"))
        self.assertTrue(tasks["Eigene"]["can_move"])
        self.assertContains(response, "zugewiesen von Jonas")
        self.assertEqual(response.content.decode().count("data-task-drag="), 1)

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


class CalendarLocationAvailabilityYearTests(TestCase):
    def setUp(self):
        self.berlin = ZoneInfo("Europe/Berlin")
        self.mira = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        self.lukas = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        self.anna = User.objects.create_user(
            username="anna@example.com", email="anna@example.com", password="secret-12345"
        )
        for user, name in ((self.mira, "Mira"), (self.lukas, "Lukas"), (self.anna, "Anna")):
            Profile.objects.create(user=user, display_name=name, timezone_name="Europe/Berlin")
        self.client.login(username="mira@example.com", password="secret-12345")

    def event(self, user, title, start, hours=1, **extra):
        return CalendarEvent.objects.create(
            user=user, title=title, start_at=start, end_at=start + timedelta(hours=hours), **extra
        )

    def availability(self, **params):
        query = {"event_date": "2099-03-10", "start_time": "10:00", "end_time": "11:00", **params}
        return self.client.get("/calendar/availability/", query)

    def test_location_link_points_to_map_or_meeting(self):
        from app.services.calendar_service import location_link

        self.assertIsNone(location_link("  "))
        address = location_link("Hauptstraße 5, Berlin")
        self.assertEqual(
            address["url"], "https://www.openstreetmap.org/search?query=Hauptstra%C3%9Fe%205%2C%20Berlin"
        )
        self.assertFalse(address["is_online"])
        meeting = location_link("https://meet.example.com/abc")
        self.assertEqual(meeting["url"], "https://meet.example.com/abc")
        self.assertTrue(meeting["is_online"])
        # Anything that is not an http(s) address is searched on the map, never linked directly.
        self.assertFalse(location_link("javascript:alert(1)")["is_online"])

    def test_upcoming_events_show_the_location_link(self):
        start = timezone.now() + timedelta(days=2)
        self.event(self.mira, "Zahnarzt", start, location="Hauptstraße 5, Berlin")

        response = self.client.get("/calendar/?view=month")

        self.assertContains(
            response, "https://www.openstreetmap.org/search?query=Hauptstra%C3%9Fe%205%2C%20Berlin"
        )
        self.assertContains(response, "Hauptstraße 5, Berlin")

    def test_edit_dialog_fields_have_their_own_ids(self):
        response = self.client.get("/calendar/?view=month")
        self.assertContains(response, 'id="id_title"', count=1)
        self.assertContains(response, 'id="id_edit_title"', count=1)

    def test_availability_reports_free_busy_and_away_without_details(self):
        SystemSettings.objects.update_or_create(pk=1, defaults={"vacation_planner_enabled": True})
        self.event(self.lukas, "Geheimes Treffen", datetime(2099, 3, 10, 10, 30, tzinfo=self.berlin))
        VacationPeriod.objects.create(
            user=self.anna, name="tarifurlaub", start_date=date(2099, 3, 9), end_date=date(2099, 3, 12)
        )

        response = self.availability(attendees=[self.lukas.pk, self.anna.pk])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        statuses = {row["id"]: row["status"] for row in response.json()["users"]}
        self.assertEqual(statuses, {self.lukas.pk: "busy", self.anna.pk: "away"})
        self.assertNotContains(response, "Geheimes Treffen")

        free = self.availability(attendees=[self.lukas.pk], start_time="11:30", end_time="12:00")
        self.assertEqual(free.json()["users"][0]["status"], "free")

    def test_availability_counts_accepted_invitations_only(self):
        start = datetime(2099, 3, 10, 9, 0, tzinfo=self.berlin)
        meeting = self.event(self.anna, "Planung", start, hours=3)
        attendee = CalendarEventAttendee.objects.create(event=meeting, user=self.lukas, invited_by=self.anna)

        self.assertEqual(self.availability(attendees=[self.lukas.pk]).json()["users"][0]["status"], "free")
        attendee.status = CalendarEventAttendee.STATUS_ACCEPTED
        attendee.save()
        self.assertEqual(self.availability(attendees=[self.lukas.pk]).json()["users"][0]["status"], "busy")

    def test_availability_counts_subscribed_calendars_while_the_feature_is_on(self):
        from app.models import CalendarSubscription

        subscription = CalendarSubscription.objects.create(
            user=self.lukas, name="Privat", url="https://calendar.example.com/x.ics", color="green"
        )
        self.event(
            self.lukas,
            "Geheimer Abo-Termin",
            datetime(2099, 3, 10, 10, 0, tzinfo=self.berlin),
            subscription=subscription,
        )

        response = self.availability(attendees=[self.lukas.pk])
        self.assertEqual(response.json()["users"][0]["status"], "busy")
        self.assertNotContains(response, "Geheimer Abo-Termin")

        SystemSettings.objects.update_or_create(pk=1, defaults={"calendar_subscriptions_enabled": False})
        self.assertEqual(self.availability(attendees=[self.lukas.pk]).json()["users"][0]["status"], "free")

    def test_availability_ignores_the_event_being_edited(self):
        start = datetime(2099, 3, 10, 10, 0, tzinfo=self.berlin)
        own = self.event(self.mira, "Abstimmung", start)
        CalendarEventAttendee.objects.create(
            event=own, user=self.lukas, invited_by=self.mira, status=CalendarEventAttendee.STATUS_ACCEPTED
        )
        foreign = self.event(self.lukas, "Fremd", start + timedelta(days=1))

        response = self.availability(attendees=[self.lukas.pk], exclude_event=own.pk)
        self.assertEqual(response.json()["users"][0]["status"], "free")
        # Someone else's event id is not honoured as an exclusion.
        response = self.availability(
            attendees=[self.lukas.pk], event_date="2099-03-11", exclude_event=foreign.pk
        )
        self.assertEqual(response.json()["users"][0]["status"], "busy")

    def test_availability_rejects_invalid_requests(self):
        self.assertEqual(self.availability(attendees=[self.mira.pk]).status_code, 400)
        self.assertEqual(self.availability(attendees=[self.lukas.pk], start_time="").status_code, 400)
        long_span = self.availability(attendees=[self.lukas.pk], is_all_day="on", end_date="2099-06-01")
        self.assertEqual(long_span.status_code, 400)
        self.client.logout()
        self.assertEqual(self.availability(attendees=[self.lukas.pk]).status_code, 302)

    def test_availability_respects_the_feature_flag(self):
        SystemSettings.objects.update_or_create(pk=1, defaults={"calendar_event_creation_enabled": False})
        self.assertEqual(self.availability(attendees=[self.lukas.pk]).status_code, 503)

    def test_year_view_shows_twelve_months_with_busy_levels(self):
        for hour in (8, 10, 12):
            self.event(self.mira, "Termin", datetime(2099, 2, 3, hour, 0, tzinfo=self.berlin))
        self.event(self.mira, "Einzeln", datetime(2099, 2, 4, 9, 0, tzinfo=self.berlin))
        invite = self.event(self.lukas, "Einladung", datetime(2099, 7, 1, 9, 0, tzinfo=self.berlin))
        CalendarEventAttendee.objects.create(event=invite, user=self.mira, invited_by=self.lukas)

        response = self.client.get("/calendar/?view=year&year=2099")

        self.assertEqual(response.status_code, 200)
        grid = response.context["calendar_year"]
        self.assertEqual(len(grid["months"]), 12)
        days = {
            (index + 1, day["number"]): day
            for index, month in enumerate(grid["months"])
            for week in month["weeks"]
            for day in week
            if day
        }
        self.assertEqual(len(days), 365)
        self.assertEqual(days[(2, 3)]["level"], 3)
        self.assertIn("3 Termine", days[(2, 3)]["label"])
        self.assertEqual(days[(2, 4)]["level"], 1)
        self.assertEqual(days[(7, 1)]["level"], 1)
        self.assertEqual(days[(2, 5)]["level"], 0)
        self.assertEqual(days[(2, 3)]["url"], "/calendar/?view=day&date=2099-02-03")
        self.assertEqual(response.context["period_label"], "2099")
        self.assertEqual(response.context["prev_url"], "/calendar/?view=year&year=2098")
        self.assertEqual(response.context["next_url"], "/calendar/?view=year&year=2100")
        self.assertContains(response, 'href="/calendar/?view=month&amp;year=2099&amp;month=2"')

        self.mira.profile.refresh_from_db()
        self.assertEqual(self.mira.profile.calendar_view, "year")
        # /calendar/ without a period now reopens the year view.
        self.assertIsNotNone(self.client.get("/calendar/").context["calendar_year"])

    def test_year_view_falls_back_to_the_current_year_for_invalid_input(self):
        response = self.client.get("/calendar/?view=year&year=abc")
        self.assertEqual(response.context["calendar_year"]["year"], timezone.localdate().year)
        response = self.client.get("/calendar/?view=year&year=99999")
        self.assertEqual(response.context["calendar_year"]["year"], timezone.localdate().year)


class CalendarEventReminderTests(TestCase):
    def setUp(self):
        self.berlin = ZoneInfo("Europe/Berlin")
        self.mira = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        self.lukas = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        Profile.objects.create(user=self.mira, display_name="Mira", timezone_name="Europe/Berlin")
        Profile.objects.create(user=self.lukas, display_name="Lukas", timezone_name="Europe/Berlin")
        self.client.login(username="mira@example.com", password="secret-12345")

    def create_event(self, **extra):
        data = {
            "form_name": "calendar_event_add",
            "title": "Zahnarzt",
            "event_date": "2099-03-10",
            "start_time": "10:00",
            "end_time": "11:00",
            "category": "appointment",
            "reminder_minutes": "15",
            **extra,
        }
        response = self.client.post("/calendar/?view=month&year=2099&month=3", data)
        self.assertEqual(response.status_code, 302)
        return CalendarEvent.objects.filter(title=data["title"]).order_by("start_at")

    def test_creating_an_event_creates_its_reminder(self):
        event = self.create_event().get()

        self.assertEqual(event.reminder_minutes, 15)
        reminder = CalendarReminder.objects.get(event=event)
        self.assertEqual(reminder.user, self.mira)
        self.assertEqual(reminder.due_at, datetime(2099, 3, 10, 9, 45, tzinfo=self.berlin))
        self.assertEqual(reminder.title, "Zahnarzt")

        # Event reminders stay out of the hand-made reminder list.
        response = self.client.get("/calendar/?view=month&year=2099&month=3")
        self.assertEqual(response.context["reminders"], [])
        self.assertContains(response, 'data-edit-reminder="15"')

    def test_no_reminder_by_default_and_for_a_series_one_per_occurrence(self):
        self.assertFalse(
            CalendarReminder.objects.filter(event__in=self.create_event(reminder_minutes="")).exists()
        )

        events = list(
            self.create_event(
                title="Training", repeat="WEEKLY", repeat_until="2099-03-24", reminder_minutes="60"
            )
        )
        self.assertEqual(len(events), 3)
        self.assertEqual(CalendarReminder.objects.filter(event__in=events).count(), 3)

    def test_invalid_reminder_value_is_rejected(self):
        response = self.client.post(
            "/calendar/",
            {
                "form_name": "calendar_event_add",
                "title": "Komisch",
                "event_date": "2099-03-10",
                "start_time": "10:00",
                "reminder_minutes": "7",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CalendarEvent.objects.filter(title="Komisch").exists())

    def test_moving_rearms_and_removing_deletes_the_reminder(self):
        from app.services.calendar_service import move_calendar_event

        event = self.create_event().get()
        reminder = CalendarReminder.objects.get(event=event)
        reminder.email_notified_at = timezone.now()
        reminder.save(update_fields=["email_notified_at"])
        UserNotification.objects.create(
            recipient=self.mira,
            kind="calendar_reminder",
            title="x",
            source_key=f"calendar-reminder:{reminder.pk}",
        )

        move_calendar_event(self.mira, event.pk, target_date=date(2099, 3, 12))
        reminder.refresh_from_db()
        self.assertEqual(reminder.due_at, datetime(2099, 3, 12, 9, 45, tzinfo=self.berlin))
        self.assertIsNone(reminder.email_notified_at)
        self.assertFalse(
            UserNotification.objects.filter(source_key=f"calendar-reminder:{reminder.pk}").exists()
        )

        response = self.client.post(
            "/calendar/",
            {
                "form_name": "calendar_event_edit",
                "event_id": event.pk,
                "title": "Zahnarzt",
                "event_date": "2099-03-12",
                "start_time": "10:00",
                "end_time": "11:00",
                "category": "appointment",
                "reminder_minutes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(CalendarReminder.objects.filter(event=event).exists())

    def test_accepting_an_invitation_adds_a_reminder_and_declining_removes_it(self):
        event = self.create_event(attendees=[self.lukas.pk]).get()
        self.assertFalse(CalendarReminder.objects.filter(event=event, user=self.lukas).exists())
        attendee = CalendarEventAttendee.objects.get(event=event, user=self.lukas)

        self.client.logout()
        self.client.login(username="lukas@example.com", password="secret-12345")
        self.client.post(
            "/calendar/", {"form_name": "event_rsvp", "attendee_id": attendee.pk, "status": "accepted"}
        )
        self.assertTrue(CalendarReminder.objects.filter(event=event, user=self.lukas).exists())

        self.client.post(
            "/calendar/", {"form_name": "event_rsvp", "attendee_id": attendee.pk, "status": "declined"}
        )
        self.assertFalse(CalendarReminder.objects.filter(event=event, user=self.lukas).exists())
        self.assertTrue(CalendarReminder.objects.filter(event=event, user=self.mira).exists())

    def test_due_event_reminder_is_delivered_with_event_texts(self):
        from django.core import mail

        from app.services.notifications import (
            claim_due_desktop_reminders,
            materialize_due_user_notifications,
            send_due_reminder_emails,
        )

        event = self.create_event(location="Hauptstraße 5").get()
        due = datetime(2099, 3, 10, 9, 50, tzinfo=self.berlin)
        self.mira.profile.notify_email = True
        self.mira.profile.notify_desktop = True
        self.mira.profile.save()

        materialize_due_user_notifications(now=due)
        notification = UserNotification.objects.get(recipient=self.mira, kind="calendar_reminder")
        self.assertEqual(notification.title, "Termin: Zahnarzt")
        self.assertIn("Beginnt 10.03.2099", notification.body)
        self.assertIn("Hauptstraße 5", notification.body)
        self.assertEqual(notification.url, "/calendar/?view=day&date=2099-03-10")

        self.assertEqual(send_due_reminder_emails(now=due)["sent"], 1)
        self.assertEqual(mail.outbox[-1].subject, "Lunora: Zahnarzt")
        self.assertEqual(send_due_reminder_emails(now=due)["sent"], 0)

        claimed = claim_due_desktop_reminders(self.mira, now=due)
        self.assertEqual(claimed[0]["title"], "Termin: Zahnarzt")
        self.assertEqual(claimed[0]["url"], "/calendar/?view=day&date=2099-03-10")
        self.assertEqual(claim_due_desktop_reminders(self.mira, now=due), [])
        self.assertEqual(CalendarReminder.objects.filter(event=event).count(), 1)

    def test_past_events_keep_their_delivered_reminder(self):
        from app.services.calendar_service import sync_event_reminders

        start = timezone.now() - timedelta(hours=1)
        event = CalendarEvent.objects.create(
            user=self.mira,
            title="Vorbei",
            start_at=start,
            end_at=start + timedelta(hours=1),
            reminder_minutes=15,
        )
        reminder = CalendarReminder.objects.create(user=self.mira, event=event, title="Vorbei", due_at=start)
        event.reminder_minutes = None
        sync_event_reminders([event])
        self.assertTrue(CalendarReminder.objects.filter(pk=reminder.pk).exists())


class CalendarAgendaViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        self.lukas = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        self.profile = Profile.objects.create(
            user=self.user, display_name="Mira", timezone_name="Europe/Berlin"
        )
        Profile.objects.create(user=self.lukas, display_name="Lukas", timezone_name="Europe/Berlin")
        self.client.login(username="mira@example.com", password="secret-12345")
        self.berlin = ZoneInfo("Europe/Berlin")

    def event(self, title, start, hours=1, user=None, **extra):
        return CalendarEvent.objects.create(
            user=user or self.user,
            title=title,
            start_at=start,
            end_at=start + timedelta(hours=hours),
            **extra,
        )

    def test_agenda_lists_only_days_with_entries_from_the_anchor(self):
        self.event("Zahnarzt", datetime(2099, 3, 10, 9, 0, tzinfo=self.berlin), location="Hauptstraße 1")
        self.event("Workshop", datetime(2099, 3, 20, 14, 0, tzinfo=self.berlin), hours=2)
        self.event("Davor", datetime(2099, 3, 9, 9, 0, tzinfo=self.berlin))
        self.event("Zu spät", datetime(2099, 4, 7, 9, 0, tzinfo=self.berlin))
        Task.objects.create(
            user=self.user, title="Bericht", due_at=datetime(2099, 3, 10, 17, 0, tzinfo=self.berlin)
        )

        response = self.client.get("/calendar/?view=agenda&date=2099-03-10")

        agenda = response.context["calendar_agenda"]
        self.assertEqual([day["date_input"] for day in agenda["days"]], ["2099-03-10", "2099-03-20"])
        first = agenda["days"][0]
        self.assertEqual(
            [(item["label"], item["time"]) for item in first["events"]], [("Zahnarzt", "09:00–10:00")]
        )
        self.assertEqual([task["label"] for task in first["tasks"]], ["Bericht"])
        self.assertTrue(first["events"][0]["can_delete"])
        self.assertEqual(agenda["label"], "10.03.2099 – 06.04.2099")
        self.assertEqual(response.context["prev_url"], "/calendar/?view=agenda&date=2099-02-10")
        self.assertEqual(response.context["next_url"], "/calendar/?view=agenda&date=2099-04-07")
        self.assertContains(response, 'class="calendar-agenda"')
        self.assertContains(response, "Hauptstraße 1")
        self.assertContains(response, "/calendar/?view=day&amp;date=2099-03-10")
        self.assertContains(response, "Vorherige vier Wochen")

    def test_multi_day_events_show_start_and_end_per_day(self):
        self.event("Messe", datetime(2099, 3, 10, 18, 0, tzinfo=self.berlin), hours=48)

        agenda = self.client.get("/calendar/?view=agenda&date=2099-03-10").context["calendar_agenda"]

        self.assertEqual(
            [(day["date_input"], day["events"][0]["time"]) for day in agenda["days"]],
            [("2099-03-10", "ab 18:00"), ("2099-03-11", "Ganztägig"), ("2099-03-12", "bis 18:00")],
        )

    def test_event_ending_at_midnight_stays_on_its_day(self):
        self.event("Abend", datetime(2099, 3, 10, 22, 0, tzinfo=self.berlin), hours=2)

        agenda = self.client.get("/calendar/?view=agenda&date=2099-03-10").context["calendar_agenda"]

        self.assertEqual([day["date_input"] for day in agenda["days"]], ["2099-03-10"])
        self.assertEqual(agenda["days"][0]["events"][0]["time"], "22:00–00:00")

    def test_invitations_are_marked_and_not_editable(self):
        event = self.event("Teamessen", datetime(2099, 3, 12, 19, 0, tzinfo=self.berlin), user=self.lukas)
        CalendarEventAttendee.objects.create(event=event, user=self.user, invited_by=self.lukas)

        agenda = self.client.get("/calendar/?view=agenda&date=2099-03-10").context["calendar_agenda"]

        item = agenda["days"][0]["events"][0]
        self.assertTrue(item["is_invited"])
        self.assertFalse(item["can_delete"])

    def test_agenda_is_remembered_and_empty_range_says_so(self):
        response = self.client.get("/calendar/?view=agenda&date=2099-03-10")

        self.assertContains(response, "Nichts geplant")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.calendar_view, "agenda")
        self.assertEqual(self.client.get("/calendar/").context["calendar_view"], "agenda")

    def test_view_switch_offers_the_agenda_on_the_current_anchor(self):
        response = self.client.get("/calendar/?view=week&date=2099-03-11")

        option = next(item for item in response.context["calendar_view_options"] if item["key"] == "agenda")
        self.assertEqual(option["url"], "/calendar/?view=agenda&date=2099-03-11")
        self.assertEqual(option["label"], "Agenda")


class CalendarMidnightEventTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=self.user, display_name="Mira", timezone_name="Europe/Berlin")
        self.client.login(username="mira@example.com", password="secret-12345")
        start = datetime(2099, 3, 10, 22, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        CalendarEvent.objects.create(
            user=self.user, title="Abend", start_at=start, end_at=start + timedelta(hours=2)
        )

    def test_month_grid_shows_an_event_ending_at_midnight_on_its_day_only(self):
        rows = self.client.get("/calendar/?view=month&year=2099&month=3").context["calendar_rows"]

        days = {
            day["date_input"]: [event["label"] for event in day["events"]] for week in rows for day in week
        }
        self.assertEqual(days["2099-03-10"], ["Abend"])
        self.assertEqual(days["2099-03-11"], [])

    def test_year_view_counts_it_once(self):
        months = self.client.get("/calendar/?view=year&year=2099").context["calendar_year"]["months"]
        march = next(month for month in months if month["label"].startswith("März"))
        levels = {day["number"]: day["level"] for week in march["weeks"] for day in week if day}

        self.assertGreater(levels[10], 0)
        self.assertEqual(levels[11], 0)


class CalendarEventCommentTests(TestCase):
    def setUp(self):
        self.mira, self.lukas, self.anna, self.ben = (
            User.objects.create_user(
                username=f"{name}@example.com", email=f"{name}@example.com", password="pw-12345"
            )
            for name in ("mira", "lukas", "anna", "ben")
        )
        for user, name in (
            (self.mira, "Mira"),
            (self.lukas, "Lukas"),
            (self.anna, "Anna"),
            (self.ben, "Ben"),
        ):
            Profile.objects.create(user=user, display_name=name, timezone_name="Europe/Berlin")
        start = datetime(2099, 3, 10, 19, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        self.event = CalendarEvent.objects.create(
            user=self.mira,
            title="Teamessen",
            start_at=start,
            end_at=start + timedelta(hours=2),
            location="Pizzeria",
        )
        CalendarEventAttendee.objects.create(event=self.event, user=self.lukas, invited_by=self.mira)
        CalendarEventAttendee.objects.create(
            event=self.event,
            user=self.anna,
            invited_by=self.mira,
            status=CalendarEventAttendee.STATUS_DECLINED,
        )
        self.url = f"/calendar/events/{self.event.id}/"

    def comment(self, user, body):
        self.client.force_login(user)
        return self.client.post(self.url, {"form_name": "event_comment", "body": body})

    def test_detail_page_shows_event_and_invitees_to_participants_only(self):
        self.client.force_login(self.lukas)
        response = self.client.get(self.url)
        self.assertContains(response, "Teamessen")
        self.assertContains(response, "Pizzeria")
        self.assertContains(response, "Organisiert von")
        self.assertContains(response, "Abgesagt")
        self.assertEqual(response.context["own_attendee"]["status"], CalendarEventAttendee.STATUS_INVITED)

        self.client.force_login(self.ben)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_comment_notifies_organizer_and_open_invitees_but_not_decliners(self):
        response = self.comment(self.lukas, "Ich bringe Nachtisch mit.")

        comment = self.event.comments.get()
        self.assertRedirects(response, f"{self.url}#comment-{comment.id}", fetch_redirect_response=False)
        notifications = UserNotification.objects.filter(kind=UserNotification.KIND_EVENT_COMMENT)
        self.assertEqual(list(notifications.values_list("recipient_id", flat=True)), [self.mira.id])
        self.assertEqual(notifications.get().title, "Lukas hat „Teamessen“ kommentiert")
        self.assertEqual(notifications.get().url, f"{self.url}#comment-{comment.id}")

        self.comment(self.mira, "Super!")
        self.assertEqual(
            set(notifications.filter(actor=self.mira).values_list("recipient_id", flat=True)), {self.lukas.id}
        )

    def test_declined_invitees_may_still_comment(self):
        self.comment(self.anna, "Leider nicht, viel Spaß!")

        self.assertEqual(self.event.comments.get().author, self.anna)

    def test_outsiders_and_empty_comments_are_rejected(self):
        self.client.force_login(self.ben)
        self.assertEqual(
            self.client.post(self.url, {"form_name": "event_comment", "body": "Hallo"}).status_code, 404
        )

        response = self.comment(self.lukas, "   ")
        self.assertContains(response, "Bitte schreibe einen Kommentar.")
        self.assertFalse(self.event.comments.exists())

    def test_events_without_invitees_have_no_comments(self):
        start = datetime(2099, 3, 11, 9, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        solo = CalendarEvent.objects.create(user=self.mira, title="Allein", start_at=start, end_at=start)
        self.client.force_login(self.mira)

        response = self.client.post(
            f"/calendar/events/{solo.id}/", {"form_name": "event_comment", "body": "Hallo"}
        )

        self.assertContains(response, "Kommentare gibt es nur bei Terminen mit Eingeladenen.")

    def test_authors_delete_their_own_comments_and_notifications(self):
        self.comment(self.lukas, "Doch später")
        comment = self.event.comments.get()

        self.client.force_login(self.mira)
        self.client.post(self.url, {"form_name": "event_comment_delete", "comment_id": comment.id})
        self.assertTrue(self.event.comments.exists())

        self.client.force_login(self.lukas)
        self.client.post(self.url, {"form_name": "event_comment_delete", "comment_id": comment.id})
        self.assertFalse(self.event.comments.exists())
        self.assertFalse(UserNotification.objects.filter(kind=UserNotification.KIND_EVENT_COMMENT).exists())

    def test_rsvp_works_on_the_detail_page(self):
        self.client.force_login(self.lukas)
        attendee = self.event.attendees.get(user=self.lukas)

        self.client.post(
            self.url, {"form_name": "event_rsvp", "attendee_id": attendee.id, "status": "accepted"}
        )

        attendee.refresh_from_db()
        self.assertEqual(attendee.status, CalendarEventAttendee.STATUS_ACCEPTED)

    def test_calendar_links_to_the_detail_page(self):
        self.client.force_login(self.lukas)

        response = self.client.get("/calendar/?view=month&year=2099&month=3")

        self.assertContains(response, f'href="{self.url}"')

    def test_comment_mail_goes_out_through_inbox_delivery(self):
        from django.core import mail

        from app.services.notifications import send_pending_user_notification_emails

        Profile.objects.filter(user=self.mira).update(notify_email=True)
        self.comment(self.lukas, "Tisch ist reserviert")

        self.assertEqual(send_pending_user_notification_emails()["sent"], 1)
        self.assertEqual(mail.outbox[0].to, ["mira@example.com"])


class CalendarFreeSlotTests(TestCase):
    def setUp(self):
        self.berlin = ZoneInfo("Europe/Berlin")
        self.mira = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        self.lukas = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        for user, name in ((self.mira, "Mira"), (self.lukas, "Lukas")):
            Profile.objects.create(user=user, display_name=name, timezone_name="Europe/Berlin")
        self.client.login(username="mira@example.com", password="secret-12345")
        # Monday, 9 March 2099, 09:10 Berlin time.
        self.monday = datetime(2099, 3, 9, 9, 10, tzinfo=self.berlin)

    def at(self, day_offset, hour, minute=0):
        return (self.monday + timedelta(days=day_offset)).replace(hour=hour, minute=minute)

    def event(self, user, start, hours=1):
        return CalendarEvent.objects.create(
            user=user, title="Belegt", start_at=start, end_at=start + timedelta(hours=hours)
        )

    def slots(self, users=(), duration=timedelta(hours=1)):
        from app.services.calendar_service import free_slots

        return free_slots(self.mira, users, duration, self.monday)

    def test_first_free_half_hour_after_both_calendars_then_one_per_day(self):
        self.event(self.mira, self.at(0, 9, 30), hours=2)  # 09:30–11:30
        self.event(self.lukas, self.at(0, 11, 30))  # 11:30–12:30
        self.event(self.mira, self.at(1, 8), hours=10)  # all of Tuesday
        CalendarEventAttendee.objects.create(
            event=self.event(self.mira, self.at(2, 8), hours=0.5),
            user=self.lukas,
            status=CalendarEventAttendee.STATUS_ACCEPTED,
        )

        slots = self.slots([self.lukas])

        self.assertEqual(
            slots,
            [
                (self.at(0, 12, 30), self.at(0, 13, 30)),
                (self.at(2, 8, 30), self.at(2, 9, 30)),
                (self.at(3, 8), self.at(3, 9)),
            ],
        )

    def test_weekends_evenings_and_vacations_are_skipped(self):
        VacationPeriod.objects.create(
            user=self.lukas, name="tarifurlaub", start_date=date(2099, 3, 10), end_date=date(2099, 3, 13)
        )

        slots = self.slots([self.lukas], duration=timedelta(hours=9))

        # Monday from 09:30 leaves 8.5 hours; Tuesday to Friday Lukas is away; the weekend is off.
        self.assertEqual([begin for begin, _end in slots], [self.at(7, 8), self.at(8, 8), self.at(9, 8)])

    def test_events_longer_than_a_working_day_get_no_suggestions(self):
        self.assertEqual(self.slots(duration=timedelta(hours=11)), [])

    def test_endpoint_returns_only_times_for_the_entered_length(self):
        self.event(self.lukas, self.at(0, 10), hours=3)  # 10:00–13:00, a private appointment

        response = self.client.get(
            "/calendar/availability/slots/",
            {
                "event_date": "2099-03-09",
                "start_time": "10:00",
                "end_time": "10:30",
                "attendees": [self.lukas.pk],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        first = response.json()["slots"][0]
        self.assertEqual(
            first,
            {
                "date": "2099-03-09",
                "start_time": "13:00",
                "end_time": "13:30",
                "label": "Mo 09.03.2099, 13:00–13:30",
            },
        )
        self.assertNotIn("Belegt", response.content.decode())

    def test_endpoint_needs_a_complete_time(self):
        response = self.client.get("/calendar/availability/slots/", {"event_date": "2099-03-09"})

        self.assertEqual(response.status_code, 400)
