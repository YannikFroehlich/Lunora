import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from app.models import CustomHoliday, HolidayOverride, OfficialHoliday, VacationPeriod, VacationYear
from app.services.user_preferences import format_user_date, get_user_month_name, get_user_weekday_name, localtime_for_user


DEFAULT_SUBDIVISION = "NW"
HALF_DAY = Decimal("0.5")
FULL_DAY = Decimal("1.0")
ZERO_DAY = Decimal("0.0")


@dataclass(frozen=True)
class HolidayInfo:
    date: date
    names: tuple[str, ...]
    day_value: Decimal
    is_custom: bool = False


def decimal_label(value):
    decimal_value = Decimal(value)
    if decimal_value == decimal_value.to_integral():
        return str(int(decimal_value))
    return str(decimal_value).replace(".", ",")


def date_range(start_date, end_date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def years_for_range(start_date, end_date):
    return range(start_date.year, end_date.year + 1)


def shifted_month(year, month, direction):
    month += direction
    if month < 1:
        return year - 1, 12
    if month > 12:
        return year + 1, 1
    return year, month


def year_context_for_user(user, year):
    vacation_year = VacationYear.objects.filter(user=user, year=year).first()
    previous_year = VacationYear.objects.filter(user=user, year__lt=year).order_by("-year").first()
    suggested_allowance = previous_year.allowance_days if previous_year else Decimal("30.0")
    suggested_subdivision = previous_year.subdivision if previous_year else DEFAULT_SUBDIVISION
    return {
        "vacation_year": vacation_year,
        "suggested_allowance": suggested_allowance,
        "suggested_subdivision": suggested_subdivision,
        "subdivision_choices": VacationYear.SUBDIVISION_CHOICES,
    }


def ensure_official_holidays(year, subdivision):
    if OfficialHoliday.objects.filter(subdivision=subdivision, date__year=year).exists():
        return

    for holiday in generated_public_holidays(year, subdivision):
        OfficialHoliday.objects.update_or_create(
            subdivision=subdivision,
            date=holiday["date"],
            name=holiday["name"],
            defaults={
                "day_value": holiday.get("day_value", FULL_DAY),
                "active": True,
                "source": holiday.get("source", "fallback"),
            },
        )


def generated_public_holidays(year, subdivision):
    try:
        import holidays
    except ImportError:
        yield from _fallback_public_holidays(year, subdivision)
        return

    country = holidays.country_holidays("DE", subdiv=subdivision, years=[year], language="de", observed=False)
    for holiday_date, name in sorted(country.items()):
        yield {"date": holiday_date, "name": str(name), "day_value": FULL_DAY, "source": "holidays"}


def import_public_holidays(from_year, to_year, subdivisions=None):
    subdivisions = subdivisions or [choice[0] for choice in VacationYear.SUBDIVISION_CHOICES]
    imported = 0
    for year in range(from_year, to_year + 1):
        for subdivision in subdivisions:
            seen_ids = set()
            for holiday in generated_public_holidays(year, subdivision):
                obj, _created = OfficialHoliday.objects.update_or_create(
                    subdivision=subdivision,
                    date=holiday["date"],
                    name=holiday["name"],
                    defaults={
                        "day_value": holiday.get("day_value", FULL_DAY),
                        "active": True,
                        "source": holiday.get("source", "fallback"),
                    },
                )
                imported += 1
                seen_ids.add(obj.pk)
            OfficialHoliday.objects.filter(subdivision=subdivision, date__year=year).exclude(pk__in=seen_ids).update(active=False)

    return imported


def _fallback_public_holidays(year, subdivision):
    easter = _easter_sunday(year)
    holidays = {
        date(year, 1, 1): "Neujahr",
        easter - timedelta(days=2): "Karfreitag",
        easter + timedelta(days=1): "Ostermontag",
        date(year, 5, 1): "Tag der Arbeit",
        easter + timedelta(days=39): "Christi Himmelfahrt",
        easter + timedelta(days=50): "Pfingstmontag",
        date(year, 10, 3): "Tag der Deutschen Einheit",
        date(year, 12, 25): "1. Weihnachtstag",
        date(year, 12, 26): "2. Weihnachtstag",
    }
    state_specific = {
        "BW": [(date(year, 1, 6), "Heilige Drei Könige"), (easter + timedelta(days=60), "Fronleichnam"), (date(year, 11, 1), "Allerheiligen")],
        "BY": [(date(year, 1, 6), "Heilige Drei Könige"), (easter + timedelta(days=60), "Fronleichnam"), (date(year, 8, 15), "Mariä Himmelfahrt"), (date(year, 11, 1), "Allerheiligen")],
        "BE": [(date(year, 3, 8), "Internationaler Frauentag")],
        "BB": [(easter, "Ostersonntag"), (easter + timedelta(days=49), "Pfingstsonntag"), (date(year, 10, 31), "Reformationstag")],
        "HB": [(date(year, 10, 31), "Reformationstag")],
        "HE": [(easter + timedelta(days=60), "Fronleichnam")],
        "HH": [(date(year, 10, 31), "Reformationstag")],
        "MV": [(date(year, 3, 8), "Internationaler Frauentag"), (date(year, 10, 31), "Reformationstag")],
        "NI": [(date(year, 10, 31), "Reformationstag")],
        "NW": [(easter + timedelta(days=60), "Fronleichnam"), (date(year, 11, 1), "Allerheiligen")],
        "RP": [(easter + timedelta(days=60), "Fronleichnam"), (date(year, 11, 1), "Allerheiligen")],
        "SL": [(easter + timedelta(days=60), "Fronleichnam"), (date(year, 8, 15), "Mariä Himmelfahrt"), (date(year, 11, 1), "Allerheiligen")],
        "SN": [(date(year, 10, 31), "Reformationstag"), (_saxony_day_of_repentance(year), "Buß- und Bettag")],
        "ST": [(date(year, 1, 6), "Heilige Drei Könige"), (date(year, 10, 31), "Reformationstag")],
        "SH": [(date(year, 10, 31), "Reformationstag")],
        "TH": [(date(year, 9, 20), "Weltkindertag"), (date(year, 10, 31), "Reformationstag")],
    }
    for holiday_date, name in state_specific.get(subdivision, []):
        holidays[holiday_date] = name
    for holiday_date, name in sorted(holidays.items()):
        yield {"date": holiday_date, "name": name, "day_value": FULL_DAY, "source": "fallback"}


def _easter_sunday(year):
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _saxony_day_of_repentance(year):
    christmas = date(year, 12, 25)
    fourth_advent = christmas - timedelta(days=(christmas.weekday() + 1) % 7)
    first_advent = fourth_advent - timedelta(weeks=3)
    return first_advent - timedelta(days=11)


def effective_holidays_for_year(vacation_year):
    ensure_official_holidays(vacation_year.year, vacation_year.subdivision)
    official_rows = OfficialHoliday.objects.filter(
        subdivision=vacation_year.subdivision,
        date__year=vacation_year.year,
        active=True,
    )
    overrides = {
        override.official_holiday_id: override
        for override in HolidayOverride.objects.filter(vacation_year=vacation_year).select_related("official_holiday")
    }
    grouped = {}
    for row in official_rows:
        override = overrides.get(row.id)
        day_value = override.day_value if override else row.day_value
        if day_value <= ZERO_DAY:
            continue
        name = override.name if override and override.name else row.name
        _merge_holiday(grouped, row.date, name, day_value, is_custom=False)

    for custom_holiday in CustomHoliday.objects.filter(vacation_year=vacation_year):
        _merge_holiday(grouped, custom_holiday.date, custom_holiday.name, custom_holiday.day_value, is_custom=True)

    return grouped


def _merge_holiday(grouped, holiday_date, name, day_value, *, is_custom):
    current = grouped.get(holiday_date)
    if not current:
        grouped[holiday_date] = HolidayInfo(holiday_date, (name,), Decimal(day_value), is_custom=is_custom)
        return
    grouped[holiday_date] = HolidayInfo(
        holiday_date,
        tuple(dict.fromkeys((*current.names, name))),
        max(current.day_value, Decimal(day_value)),
        current.is_custom or is_custom,
    )


def calculate_period(user, start_date, end_date, *, exclude_period_id=None):
    calendar_days = (end_date - start_date).days + 1
    weekend_days = 0
    holiday_dates = set()
    holiday_credit = ZERO_DAY
    required_days = ZERO_DAY
    per_year = []
    missing_years = []

    vacation_years = {
        row.year: row
        for row in VacationYear.objects.filter(user=user, year__in=list(years_for_range(start_date, end_date)))
    }

    holiday_maps = {
        year: effective_holidays_for_year(vacation_year)
        for year, vacation_year in vacation_years.items()
    }

    for year in years_for_range(start_date, end_date):
        year_start = max(start_date, date(year, 1, 1))
        year_end = min(end_date, date(year, 12, 31))
        year_weekend_days = 0
        year_holiday_dates = set()
        year_holiday_credit = ZERO_DAY
        year_required_days = ZERO_DAY
        if year not in vacation_years:
            missing_years.append(year)
        holidays = holiday_maps.get(year, {})
        for current in date_range(year_start, year_end):
            is_weekend = current.weekday() >= 5
            holiday = holidays.get(current)
            if is_weekend:
                weekend_days += 1
                year_weekend_days += 1
                if holiday:
                    holiday_dates.add(current)
                    year_holiday_dates.add(current)
                continue
            if holiday:
                credit = min(FULL_DAY, holiday.day_value)
                holiday_credit += credit
                year_holiday_credit += credit
                holiday_dates.add(current)
                year_holiday_dates.add(current)
                required = max(ZERO_DAY, FULL_DAY - credit)
            else:
                required = FULL_DAY
            required_days += required
            year_required_days += required
        per_year.append(
            {
                "year": year,
                "calendar_days": (year_end - year_start).days + 1,
                "weekend_days": year_weekend_days,
                "holiday_count": len(year_holiday_dates),
                "holiday_credit": year_holiday_credit,
                "required_days": year_required_days,
                "configured": year in vacation_years,
            }
        )

    overlaps = overlapping_periods(user, start_date, end_date, exclude_period_id=exclude_period_id)
    return {
        "calendar_days": calendar_days,
        "weekend_days": weekend_days,
        "holiday_count": len(holiday_dates),
        "holiday_credit": holiday_credit,
        "required_days": required_days,
        "per_year": per_year,
        "missing_years": missing_years,
        "overlaps": overlaps,
    }


def overlapping_periods(user, start_date, end_date, *, exclude_period_id=None):
    queryset = VacationPeriod.objects.filter(user=user, start_date__lte=end_date, end_date__gte=start_date)
    if exclude_period_id:
        queryset = queryset.exclude(pk=exclude_period_id)
    return [
        {"id": period.id, "name": period.get_name_display(), "start_date": period.start_date, "end_date": period.end_date}
        for period in queryset.order_by("start_date", "name")
    ]


def annual_summary(user, year):
    vacation_year = VacationYear.objects.filter(user=user, year=year).first()
    if not vacation_year:
        summary = {
            "allowance_days": ZERO_DAY,
            "planned_days": ZERO_DAY,
            "remaining_days": ZERO_DAY,
            "usage_percent": 0,
            "usage_capped": 0,
            "is_overbooked": False,
        }
        return _with_summary_labels(summary)
    holidays = effective_holidays_for_year(vacation_year)
    used_dates = set()
    for period in VacationPeriod.objects.filter(user=user, start_date__lte=date(year, 12, 31), end_date__gte=date(year, 1, 1)):
        for current in date_range(max(period.start_date, date(year, 1, 1)), min(period.end_date, date(year, 12, 31))):
            used_dates.add(current)

    planned_days = ZERO_DAY
    for current in used_dates:
        if current.weekday() >= 5:
            continue
        holiday = holidays.get(current)
        planned_days += max(ZERO_DAY, FULL_DAY - min(FULL_DAY, holiday.day_value)) if holiday else FULL_DAY

    remaining_days = vacation_year.allowance_days - planned_days
    usage_percent = int((planned_days / vacation_year.allowance_days) * 100) if vacation_year.allowance_days else 0
    summary = {
        "allowance_days": vacation_year.allowance_days,
        "planned_days": planned_days,
        "remaining_days": remaining_days,
        "usage_percent": usage_percent,
        "usage_capped": min(100, usage_percent),
        "is_overbooked": planned_days > vacation_year.allowance_days,
    }
    return _with_summary_labels(summary)


def _with_summary_labels(summary):
    summary["allowance_label"] = decimal_label(summary["allowance_days"])
    summary["planned_label"] = decimal_label(summary["planned_days"])
    summary["remaining_label"] = decimal_label(summary["remaining_days"])
    return summary


def month_summary(user, year, month):
    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])
    vacation_year = VacationYear.objects.filter(user=user, year=year).first()
    holidays = effective_holidays_for_year(vacation_year) if vacation_year else {}

    used_dates = set()
    period_count = 0
    for period in VacationPeriod.objects.filter(user=user, start_date__lte=month_end, end_date__gte=month_start):
        period_count += 1
        for current in date_range(max(period.start_date, month_start), min(period.end_date, month_end)):
            used_dates.add(current)

    weekend_days = sum(1 for current in date_range(month_start, month_end) if current.weekday() >= 5)
    holiday_count = sum(1 for current in date_range(month_start, month_end) if current in holidays)

    planned_days = ZERO_DAY
    for current in used_dates:
        if current.weekday() >= 5:
            continue
        holiday = holidays.get(current)
        planned_days += max(ZERO_DAY, FULL_DAY - min(FULL_DAY, holiday.day_value)) if holiday else FULL_DAY

    return {
        "period_count": period_count,
        "planned_days": planned_days,
        "planned_label": decimal_label(planned_days),
        "weekend_days": weekend_days,
        "holiday_count": holiday_count,
    }


def period_items(user, year):
    now_date = localtime_for_user(profile_or_user=user).date()
    periods = VacationPeriod.objects.filter(user=user, start_date__lte=date(year, 12, 31), end_date__gte=date(year, 1, 1))
    return [
        {
            "period": period,
            "calculation": _with_calculation_labels(
                calculate_period(user, period.start_date, period.end_date, exclude_period_id=period.id)
            ),
            "status": status_for_period(period, now_date),
        }
        for period in periods.order_by("start_date", "name")
    ]


def status_for_period(period, today):
    if period.start_date <= today <= period.end_date:
        return {"key": "current", "label": "Aktuell"}
    if period.end_date < today:
        return {"key": "taken", "label": "Genommen"}
    return {"key": "planned", "label": "Geplant"}


def _with_calculation_labels(calculation):
    calculation["required_days_label"] = decimal_label(calculation["required_days"])
    calculation["holiday_credit_label"] = decimal_label(calculation["holiday_credit"])
    for row in calculation["per_year"]:
        row["required_days_label"] = decimal_label(row["required_days"])
        row["holiday_credit_label"] = decimal_label(row["holiday_credit"])
    return calculation


def holiday_items(vacation_year):
    if not vacation_year:
        return {"official": [], "custom": []}
    ensure_official_holidays(vacation_year.year, vacation_year.subdivision)
    overrides = {
        override.official_holiday_id: override
        for override in HolidayOverride.objects.filter(vacation_year=vacation_year).select_related("official_holiday")
    }
    official = []
    for holiday in OfficialHoliday.objects.filter(subdivision=vacation_year.subdivision, date__year=vacation_year.year, active=True):
        override = overrides.get(holiday.id)
        value = override.day_value if override else holiday.day_value
        official.append(
            {
                "holiday": holiday,
                "override": override,
                "name": override.name if override and override.name else holiday.name,
                "day_value": value,
                "is_disabled": value <= ZERO_DAY,
            }
        )
    return {
        "official": official,
        "custom": list(CustomHoliday.objects.filter(vacation_year=vacation_year).order_by("date", "name")),
    }


def month_calendar(user, year, month):
    now = localtime_for_user(profile_or_user=user).date()
    vacation_year = VacationYear.objects.filter(user=user, year=year).first()
    holidays = effective_holidays_for_year(vacation_year) if vacation_year else {}
    periods = list(VacationPeriod.objects.filter(user=user, start_date__lte=date(year, month, calendar.monthrange(year, month)[1]), end_date__gte=date(year, month, 1)))
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    rows = []
    for week in weeks:
        row = []
        for current in week:
            day_periods = [period for period in periods if period.start_date <= current <= period.end_date]
            holiday = holidays.get(current)
            row.append(
                {
                    "date": current,
                    "number": current.day,
                    "muted": current.month != month,
                    "weekend": current.weekday() >= 5,
                    "today": current == now,
                    "holiday": holiday,
                    "vacations": day_periods[:3],
                    "overflow": max(0, len(day_periods) - 3),
                }
            )
        rows.append(row)
    prev_year, prev_month = shifted_month(year, month, -1)
    next_year, next_month = shifted_month(year, month, 1)
    return {
        "rows": rows,
        "month_label": f"{get_user_month_name(date(year, month, 1), user)} {year}",
        "weekday_labels": [get_user_weekday_name(date(2026, 8, day), user)[:2] for day in range(17, 24)],
        "prev_month": {"year": prev_year, "month": prev_month},
        "next_month": {"year": next_year, "month": next_month},
    }


def vacation_planner_context(user, *, year=None, month=None):
    now = localtime_for_user(profile_or_user=user)
    try:
        selected_year = int(year or now.year)
    except (TypeError, ValueError):
        selected_year = now.year
    try:
        selected_month = int(month or (now.month if selected_year == now.year else 1))
        if selected_month < 1 or selected_month > 12:
            raise ValueError
    except (TypeError, ValueError):
        selected_month = now.month

    year_context = year_context_for_user(user, selected_year)
    vacation_year = year_context["vacation_year"]
    summary = annual_summary(user, selected_year)
    holidays = holiday_items(vacation_year)
    year_options = sorted({now.year, selected_year, selected_year - 1, selected_year + 1, *(VacationYear.objects.filter(user=user).values_list("year", flat=True))})
    return {
        "active_page": "vacation_planner",
        "selected_year": selected_year,
        "selected_month": selected_month,
        "prev_year": selected_year - 1,
        "next_year": selected_year + 1,
        "year_options": year_options,
        "year_context": year_context,
        "summary": summary,
        "period_items": period_items(user, selected_year),
        "holiday_items": holidays,
        "calendar_model": month_calendar(user, selected_year, selected_month),
        "month_summary": month_summary(user, selected_year, selected_month),
        "today_year": now.year,
        "today_month": now.month,
        "today_label": format_user_date(now, user),
    }
