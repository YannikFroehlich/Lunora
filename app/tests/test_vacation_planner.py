import json
import sys
from datetime import date
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from app.models import (
    CustomHoliday,
    HolidayOverride,
    OfficialHoliday,
    Profile,
    SystemSettings,
    VacationPeriod,
    VacationYear,
)
from app.services.vacation_planner import (
    annual_summary,
    calculate_period,
    decimal_label,
    effective_holidays_for_year,
    generated_public_holidays,
    import_public_holidays,
    month_calendar,
    month_summary,
)


class VacationPlannerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="planner@example.com", email="planner@example.com", password="secret-12345"
        )
        Profile.objects.create(user=self.user, display_name="Planner")
        self.other = User.objects.create_user(
            username="other-planner@example.com", email="other-planner@example.com", password="secret-12345"
        )
        Profile.objects.create(user=self.other, display_name="Other")
        self.client.login(username="planner@example.com", password="secret-12345")
        self.vacation_year = VacationYear.objects.create(
            user=self.user,
            year=2026,
            allowance_days=Decimal("3.0"),
            subdivision="NW",
        )

    def test_half_holiday_reduces_required_vacation_days(self):
        CustomHoliday.objects.create(
            vacation_year=self.vacation_year,
            date=date(2026, 1, 6),
            name="Halber Testfeiertag",
            day_value=Decimal("0.5"),
        )

        calculation = calculate_period(self.user, date(2026, 1, 5), date(2026, 1, 7))

        self.assertEqual(calculation["calendar_days"], 3)
        self.assertEqual(calculation["weekend_days"], 0)
        self.assertEqual(calculation["holiday_count"], 1)
        self.assertEqual(calculation["required_days"], Decimal("2.5"))

    def test_annual_summary_counts_overlapping_vacation_dates_once(self):
        CustomHoliday.objects.create(
            vacation_year=self.vacation_year,
            date=date(2026, 1, 6),
            name="Halber Testfeiertag",
            day_value=Decimal("0.5"),
        )
        VacationPeriod.objects.create(
            user=self.user,
            name=VacationPeriod.TARIFURLAUB,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 7),
        )
        VacationPeriod.objects.create(
            user=self.user,
            name=VacationPeriod.SONDERURLAUB,
            start_date=date(2026, 1, 6),
            end_date=date(2026, 1, 8),
        )

        summary = annual_summary(self.user, 2026)

        self.assertEqual(summary["planned_days"], Decimal("3.5"))
        self.assertEqual(summary["remaining_days"], Decimal("-0.5"))
        self.assertTrue(summary["is_overbooked"])

    def test_preview_reports_overlaps_and_missing_years(self):
        VacationPeriod.objects.create(
            user=self.user,
            name=VacationPeriod.TARIFURLAUB,
            start_date=date(2026, 1, 6),
            end_date=date(2026, 1, 8),
        )

        response = self.client.post(
            "/vacation-planner/preview/",
            data=json.dumps({"start_date": "2026-01-05", "end_date": "2027-01-03"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["missing_years"], [2027])
        self.assertEqual(data["overlaps"][0]["name"], "Tarifurlaub")

    def test_page_requires_login_and_feature_flag(self):
        self.client.logout()
        response = self.client.get("/vacation-planner/")
        self.assertEqual(response.status_code, 302)

        self.client.login(username="planner@example.com", password="secret-12345")
        SystemSettings.objects.create(vacation_planner_enabled=False)
        response = self.client.get("/vacation-planner/?year=2026")
        self.assertEqual(response.status_code, 503)

    def test_periods_are_filtered_per_user(self):
        VacationPeriod.objects.create(
            user=self.user,
            name=VacationPeriod.TARIFURLAUB,
            start_date=date(2026, 2, 2),
            end_date=date(2026, 2, 3),
            notes="Eigener Hinweis",
        )
        VacationPeriod.objects.create(
            user=self.other,
            name=VacationPeriod.TARIFURLAUB,
            start_date=date(2026, 2, 2),
            end_date=date(2026, 2, 3),
            notes="Fremder Hinweis",
        )

        response = self.client.get("/vacation-planner/?year=2026&month=2")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertContains(response, "Eigener Hinweis")
        self.assertNotContains(response, "Fremder Hinweis")

    def test_empty_planner_with_saved_year_offers_vacation_action(self):
        response = self.client.get("/vacation-planner/?year=2026")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertContains(response, 'id="vacation-period-editor"')
        self.assertContains(response, 'data-vacation-focus="period"')
        self.assertContains(response, "Urlaub planen")
        self.assertNotContains(response, "Jahr zuerst einrichten")

    def test_empty_planner_without_year_points_to_year_setup(self):
        self.vacation_year.delete()

        response = self.client.get("/vacation-planner/?year=2027")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertContains(response, 'id="vacation-year-settings"')
        self.assertContains(response, 'data-vacation-focus="year"', count=3)
        self.assertContains(response, "Jahr zuerst einrichten")
        self.assertContains(response, "Jahr einrichten")
        self.assertNotContains(response, 'data-vacation-focus="period"')

    def test_public_holiday_import_command_is_idempotent(self):
        stale = OfficialHoliday.objects.create(
            subdivision="NW",
            date=date(2026, 6, 1),
            name="Alter Feiertag",
            day_value=Decimal("1.0"),
            active=True,
        )
        output = StringIO()
        call_command("import_public_holidays", from_year=2026, to_year=2026, subdivision="NW", stdout=output)
        first_count = OfficialHoliday.objects.filter(subdivision="NW", date__year=2026).count()
        call_command(
            "import_public_holidays", from_year=2026, to_year=2026, subdivision="NW", stdout=StringIO()
        )

        stale.refresh_from_db()
        self.assertGreater(first_count, 0)
        self.assertFalse(stale.active)
        self.assertEqual(
            OfficialHoliday.objects.filter(subdivision="NW", date__year=2026).count(), first_count
        )

    def test_decimal_label_formats_whole_and_half_values(self):
        self.assertEqual(decimal_label(Decimal("1.0")), "1")
        self.assertEqual(decimal_label(Decimal("0.5")), "0,5")
        self.assertEqual(decimal_label(Decimal("2.5")), "2,5")
        self.assertEqual(decimal_label(Decimal("0.0")), "0")

    def test_fallback_holidays_used_when_holidays_package_unavailable(self):
        with patch.dict(sys.modules, {"holidays": None}):
            holidays_list = list(generated_public_holidays(2026, "NW"))

        self.assertTrue(all(holiday["source"] == "fallback" for holiday in holidays_list))
        names = {holiday["name"] for holiday in holidays_list}
        self.assertIn("Neujahr", names)
        self.assertIn("Fronleichnam", names)
        self.assertIn("Allerheiligen", names)
        self.assertNotIn("Mariä Himmelfahrt", names)

    def test_fallback_holidays_are_subdivision_specific(self):
        with patch.dict(sys.modules, {"holidays": None}):
            bavaria_names = {holiday["name"] for holiday in generated_public_holidays(2026, "BY")}
            saxony_names = {holiday["name"] for holiday in generated_public_holidays(2026, "SN")}

        self.assertIn("Mariä Himmelfahrt", bavaria_names)
        self.assertIn("Buß- und Bettag", saxony_names)
        self.assertNotIn("Buß- und Bettag", bavaria_names)

    def test_fallback_easter_based_holidays_match_holidays_package(self):
        easter_based_names = {
            "Karfreitag",
            "Ostermontag",
            "Christi Himmelfahrt",
            "Pfingstmontag",
            "Fronleichnam",
        }
        for year in (2026, 2027, 2028):
            real_dates = {
                holiday["name"]: holiday["date"]
                for holiday in generated_public_holidays(year, "NW")
                if holiday["name"] in easter_based_names
            }
            with patch.dict(sys.modules, {"holidays": None}):
                fallback_dates = {
                    holiday["name"]: holiday["date"]
                    for holiday in generated_public_holidays(year, "NW")
                    if holiday["name"] in easter_based_names
                }
            self.assertEqual(set(real_dates), easter_based_names, year)
            self.assertEqual(fallback_dates, real_dates, year)

    def test_import_public_holidays_covers_multiple_years_and_subdivisions(self):
        imported = import_public_holidays(2026, 2027, subdivisions=["NW", "BY"])

        self.assertGreater(imported, 0)
        for subdivision in ("NW", "BY"):
            for year in (2026, 2027):
                self.assertTrue(
                    OfficialHoliday.objects.filter(
                        subdivision=subdivision, date__year=year, active=True
                    ).exists(),
                    f"{subdivision} {year}",
                )
        self.assertTrue(
            OfficialHoliday.objects.filter(
                subdivision="BY", name="Heilige Drei Könige", date__year=2026
            ).exists()
        )
        self.assertFalse(
            OfficialHoliday.objects.filter(
                subdivision="NW", name="Heilige Drei Könige", date__year=2026
            ).exists()
        )

    def test_calculate_period_splits_required_days_across_year_boundary(self):
        VacationYear.objects.create(
            user=self.user, year=2027, allowance_days=Decimal("25.0"), subdivision="NW"
        )

        calculation = calculate_period(self.user, date(2026, 12, 30), date(2027, 1, 2))

        self.assertEqual(calculation["calendar_days"], 4)
        self.assertEqual(calculation["missing_years"], [])
        per_year = {row["year"]: row for row in calculation["per_year"]}
        self.assertEqual(set(per_year), {2026, 2027})
        self.assertEqual(per_year[2026]["calendar_days"], 2)
        self.assertEqual(per_year[2026]["weekend_days"], 0)
        self.assertEqual(per_year[2026]["required_days"], Decimal("2.0"))
        self.assertEqual(per_year[2027]["calendar_days"], 2)
        self.assertEqual(per_year[2027]["weekend_days"], 1)
        self.assertEqual(per_year[2027]["holiday_count"], 1)
        self.assertEqual(per_year[2027]["required_days"], Decimal("0.0"))
        self.assertEqual(calculation["required_days"], Decimal("2.0"))

    def test_holiday_override_reduces_required_days_and_renames_holiday(self):
        official_holiday = OfficialHoliday.objects.create(
            subdivision="NW",
            date=date(2026, 6, 4),
            name="Fronleichnam",
            day_value=Decimal("1.0"),
            active=True,
        )
        HolidayOverride.objects.create(
            vacation_year=self.vacation_year,
            official_holiday=official_holiday,
            name="Firmenfeiertag",
            day_value=Decimal("0.5"),
        )

        holidays = effective_holidays_for_year(self.vacation_year)
        self.assertEqual(holidays[date(2026, 6, 4)].day_value, Decimal("0.5"))
        self.assertEqual(holidays[date(2026, 6, 4)].names, ("Firmenfeiertag",))

        calculation = calculate_period(self.user, date(2026, 6, 4), date(2026, 6, 4))
        self.assertEqual(calculation["required_days"], Decimal("0.5"))

    def test_holiday_override_can_fully_disable_a_holiday(self):
        official_holiday = OfficialHoliday.objects.create(
            subdivision="NW",
            date=date(2026, 6, 4),
            name="Fronleichnam",
            day_value=Decimal("1.0"),
            active=True,
        )
        HolidayOverride.objects.create(
            vacation_year=self.vacation_year, official_holiday=official_holiday, day_value=Decimal("0.0")
        )

        holidays = effective_holidays_for_year(self.vacation_year)
        self.assertNotIn(date(2026, 6, 4), holidays)

        calculation = calculate_period(self.user, date(2026, 6, 4), date(2026, 6, 4))
        self.assertEqual(calculation["required_days"], Decimal("1.0"))
        self.assertEqual(calculation["holiday_count"], 0)

    def test_month_summary_and_month_calendar_reflect_period_and_holiday(self):
        OfficialHoliday.objects.create(
            subdivision="NW",
            date=date(2026, 6, 4),
            name="Fronleichnam",
            day_value=Decimal("1.0"),
            active=True,
        )
        VacationPeriod.objects.create(
            user=self.user,
            name=VacationPeriod.TARIFURLAUB,
            start_date=date(2026, 6, 8),
            end_date=date(2026, 6, 9),
        )

        summary = month_summary(self.user, 2026, 6)
        self.assertEqual(summary["period_count"], 1)
        self.assertEqual(summary["planned_days"], Decimal("2.0"))
        self.assertEqual(summary["holiday_count"], 1)

        calendar_model = month_calendar(self.user, 2026, 6)
        day_cell = next(
            cell for week in calendar_model["rows"] for cell in week if cell["date"] == date(2026, 6, 4)
        )
        self.assertIsNotNone(day_cell["holiday"])
        self.assertEqual(day_cell["holiday"].names, ("Fronleichnam",))

    def test_vacation_year_save_view_creates_and_updates_allowance(self):
        self.vacation_year.delete()

        response = self.client.post(
            "/vacation-planner/year/",
            {"year": "2027", "allowance_days": "25.5", "subdivision": "BY"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        vacation_year = VacationYear.objects.get(user=self.user, year=2027)
        self.assertEqual(vacation_year.allowance_days, Decimal("25.5"))
        self.assertEqual(vacation_year.subdivision, "BY")
        self.assertContains(response, "Urlaubsjahr gespeichert.")
        # The saved values, not the suggestion (default NW), are shown again after the redirect.
        self.assertEqual(response.context["year_form"]["subdivision"].value(), "BY")
        self.assertEqual(response.context["year_form"]["allowance_days"].value(), Decimal("25.5"))

    def test_vacation_year_save_view_rejects_non_half_step_allowance(self):
        response = self.client.post(
            "/vacation-planner/year/",
            {"year": "2026", "allowance_days": "25.3", "subdivision": "NW"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.vacation_year.refresh_from_db()
        self.assertEqual(self.vacation_year.allowance_days, Decimal("3.0"))
        self.assertContains(response, "Das Urlaubsjahr konnte nicht gespeichert werden.")

    def test_vacation_year_save_view_rejects_negative_allowance(self):
        response = self.client.post(
            "/vacation-planner/year/",
            {"year": "2026", "allowance_days": "-5", "subdivision": "NW"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.vacation_year.refresh_from_db()
        self.assertEqual(self.vacation_year.allowance_days, Decimal("3.0"))
        self.assertContains(response, "Das Urlaubsjahr konnte nicht gespeichert werden.")

    def test_custom_holiday_save_view_creates_half_day_holiday(self):
        response = self.client.post(
            "/vacation-planner/holiday/save/",
            {"year": "2026", "date": "2026-08-14", "name": "Betriebsruhe", "is_half_day": "on"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        holiday = CustomHoliday.objects.get(vacation_year=self.vacation_year, name="Betriebsruhe")
        self.assertEqual(holiday.date, date(2026, 8, 14))
        self.assertEqual(holiday.day_value, Decimal("0.5"))
        self.assertContains(response, "Feiertag gespeichert.")

    def test_custom_holiday_save_view_rejects_date_outside_selected_year(self):
        response = self.client.post(
            "/vacation-planner/holiday/save/",
            {"year": "2026", "date": "2027-01-02", "name": "Falsches Jahr"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(CustomHoliday.objects.filter(name="Falsches Jahr").exists())
        self.assertContains(response, "Eigene Feiertage müssen im gewählten Jahr liegen.")

    def test_custom_holiday_delete_view_only_removes_own_vacation_year_holiday(self):
        other_year = VacationYear.objects.create(
            user=self.other, year=2026, allowance_days=Decimal("20.0"), subdivision="NW"
        )
        own_holiday = CustomHoliday.objects.create(
            vacation_year=self.vacation_year, date=date(2026, 8, 14), name="Eigen", day_value=Decimal("1.0")
        )
        other_holiday = CustomHoliday.objects.create(
            vacation_year=other_year, date=date(2026, 8, 14), name="Fremd", day_value=Decimal("1.0")
        )

        response = self.client.post(
            "/vacation-planner/holiday/delete/",
            {"year": "2026", "holiday_id": other_holiday.id},
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(CustomHoliday.objects.filter(pk=own_holiday.id).exists())
        self.assertTrue(CustomHoliday.objects.filter(pk=other_holiday.id).exists())

    def test_official_holiday_override_save_and_reset(self):
        official_holiday = OfficialHoliday.objects.create(
            subdivision="NW",
            date=date(2026, 6, 4),
            name="Fronleichnam",
            day_value=Decimal("1.0"),
            active=True,
        )

        response = self.client.post(
            "/vacation-planner/official-holiday/save/",
            {
                "year": "2026",
                "official_holiday_id": official_holiday.id,
                "name": "Firmenfeiertag",
                "day_value": "0.5",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        override = HolidayOverride.objects.get(
            vacation_year=self.vacation_year, official_holiday=official_holiday
        )
        self.assertEqual(override.day_value, Decimal("0.5"))
        self.assertEqual(override.name, "Firmenfeiertag")
        self.assertContains(response, "Feiertag angepasst.")

        response = self.client.post(
            "/vacation-planner/official-holiday/reset/",
            {"year": "2026", "official_holiday_id": official_holiday.id},
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(
            HolidayOverride.objects.filter(
                vacation_year=self.vacation_year, official_holiday=official_holiday
            ).exists()
        )
        self.assertContains(response, "Feiertag zurückgesetzt.")

    def test_official_holiday_override_save_rejects_mismatched_subdivision(self):
        official_holiday = OfficialHoliday.objects.create(
            subdivision="BY",
            date=date(2026, 6, 4),
            name="Fronleichnam (Bayern)",
            day_value=Decimal("1.0"),
            active=True,
        )

        response = self.client.post(
            "/vacation-planner/official-holiday/save/",
            {"year": "2026", "official_holiday_id": official_holiday.id, "name": "", "day_value": "0.0"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(HolidayOverride.objects.filter(official_holiday=official_holiday).exists())
        self.assertContains(response, "Der Feiertag konnte nicht gefunden werden.")

    def test_vacation_period_delete_view_ignores_other_users_period(self):
        own_period = VacationPeriod.objects.create(
            user=self.user,
            name=VacationPeriod.TARIFURLAUB,
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 3),
        )
        other_period = VacationPeriod.objects.create(
            user=self.other,
            name=VacationPeriod.TARIFURLAUB,
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 3),
        )

        response = self.client.post(
            "/vacation-planner/period/delete/", {"year": "2026", "period_id": other_period.id}, follow=True
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(VacationPeriod.objects.filter(pk=own_period.id).exists())
        self.assertTrue(VacationPeriod.objects.filter(pk=other_period.id).exists())

    def test_vacation_period_save_view_blocks_when_target_year_not_configured(self):
        response = self.client.post(
            "/vacation-planner/period/save/",
            {
                "year": "2026",
                "name": VacationPeriod.TARIFURLAUB,
                "start_date": "2027-01-04",
                "end_date": "2027-01-05",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(VacationPeriod.objects.filter(user=self.user, start_date=date(2027, 1, 4)).exists())
        self.assertContains(response, "Bitte bestätige zuerst die Urlaubsjahre: 2027.")

    def test_vacation_period_save_view_blocks_overlapping_period(self):
        VacationPeriod.objects.create(
            user=self.user,
            name=VacationPeriod.TARIFURLAUB,
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 4),
        )

        response = self.client.post(
            "/vacation-planner/period/save/",
            {
                "year": "2026",
                "name": VacationPeriod.SONDERURLAUB,
                "start_date": "2026-03-03",
                "end_date": "2026-03-05",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(VacationPeriod.objects.filter(user=self.user, start_date=date(2026, 3, 3)).exists())
        self.assertContains(response, "Der Zeitraum überschneidet sich mit: Tarifurlaub.")

    def test_vacation_period_save_view_updates_existing_period(self):
        period = VacationPeriod.objects.create(
            user=self.user,
            name=VacationPeriod.TARIFURLAUB,
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 3),
        )

        response = self.client.post(
            "/vacation-planner/period/save/",
            {
                "year": "2026",
                "period_id": period.id,
                "name": VacationPeriod.SONDERURLAUB,
                "start_date": "2026-03-02",
                "end_date": "2026-03-04",
                "notes": "Verlängert",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200, response.content)
        period.refresh_from_db()
        self.assertEqual(period.name, VacationPeriod.SONDERURLAUB)
        self.assertEqual(period.end_date, date(2026, 3, 4))
        self.assertEqual(period.notes, "Verlängert")
