import json
from decimal import Decimal

from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date

from app.forms import CustomHolidayForm, HolidayOverrideForm, VacationPeriodForm, VacationYearForm
from app.models import CustomHoliday, HolidayOverride, OfficialHoliday, VacationPeriod, VacationYear
from app.services.system_settings import disabled_feature_response, feature_enabled
from app.services.vacation_planner import calculate_period, decimal_label, vacation_planner_context


def _planner_redirect(year, month=None):
    url = f"{reverse('vacation_planner')}?year={year}"
    if month:
        url += f"&month={month}"
    return redirect(url)


def _selected_year(request):
    try:
        return int(request.POST.get("year") or request.GET.get("year"))
    except (TypeError, ValueError):
        return None


def _selected_month(request):
    try:
        month = int(request.POST.get("month") or request.GET.get("month"))
        if 1 <= month <= 12:
            return month
    except (TypeError, ValueError):
        return None
    return None


def _serialize_calculation(calculation):
    def convert_value(value):
        if isinstance(value, Decimal):
            return float(value)
        return value

    return {
        "calendar_days": calculation["calendar_days"],
        "weekend_days": calculation["weekend_days"],
        "holiday_count": calculation["holiday_count"],
        "holiday_credit": float(calculation["holiday_credit"]),
        "holiday_credit_label": decimal_label(calculation["holiday_credit"]),
        "required_days": float(calculation["required_days"]),
        "required_days_label": decimal_label(calculation["required_days"]),
        "per_year": [
            {key: convert_value(value) for key, value in row.items()} for row in calculation["per_year"]
        ],
        "missing_years": calculation["missing_years"],
        "overlaps": [
            {
                "id": overlap["id"],
                "name": overlap["name"],
                "start_date": overlap["start_date"].isoformat(),
                "end_date": overlap["end_date"].isoformat(),
            }
            for overlap in calculation["overlaps"]
        ],
    }


@login_required
def vacation_planner(request):
    if not feature_enabled("vacation_planner"):
        return disabled_feature_response(request, "vacation_planner")

    selected_year = _selected_year(request)
    selected_month = _selected_month(request)

    context = vacation_planner_context(request.user, year=selected_year, month=selected_month)
    vacation_year = context["year_context"]["vacation_year"]
    context.update(
        {
            "year_form": VacationYearForm(
                instance=vacation_year,
                initial={
                    "allowance_days": context["year_context"]["suggested_allowance"],
                    "subdivision": context["year_context"]["suggested_subdivision"],
                },
            ),
            "period_form": VacationPeriodForm(user=request.user),
            "custom_holiday_form": CustomHolidayForm(),
            "override_form": HolidayOverrideForm(),
        }
    )
    return render(request, "app/vacation_planner.html", context)


@login_required
def vacation_year_save(request):
    if not feature_enabled("vacation_planner"):
        return disabled_feature_response(request, "vacation_planner")
    if request.method != "POST":
        return _planner_redirect(_selected_year(request) or 2026)

    year = _selected_year(request)
    if not year:
        django_messages.error(request, "Bitte wähle ein gültiges Jahr.")
        return redirect("vacation_planner")

    vacation_year = VacationYear.objects.filter(user=request.user, year=year).first()
    form = VacationYearForm(request.POST, instance=vacation_year)
    if form.is_valid():
        vacation_year = form.save(commit=False)
        vacation_year.user = request.user
        vacation_year.year = year
        vacation_year.save()
        django_messages.success(request, "Urlaubsjahr gespeichert.")
    else:
        django_messages.error(request, "Das Urlaubsjahr konnte nicht gespeichert werden.")
    return _planner_redirect(year, _selected_month(request))


@login_required
def vacation_period_save(request):
    if not feature_enabled("vacation_planner"):
        return disabled_feature_response(request, "vacation_planner")
    if request.method != "POST":
        return redirect("vacation_planner")

    period_id = request.POST.get("period_id")
    period = None
    if period_id:
        period = VacationPeriod.objects.filter(user=request.user, pk=period_id).first()
        if not period:
            django_messages.error(request, "Dieser Urlaub wurde nicht gefunden.")
            return _planner_redirect(_selected_year(request) or 2026, _selected_month(request))

    form = VacationPeriodForm(request.POST, user=request.user, instance=period)
    if form.is_valid():
        calculation = calculate_period(
            request.user,
            form.cleaned_data["start_date"],
            form.cleaned_data["end_date"],
            exclude_period_id=period.id if period else None,
        )
        if calculation["missing_years"]:
            years = ", ".join(str(year) for year in calculation["missing_years"])
            django_messages.error(request, f"Bitte bestätige zuerst die Urlaubsjahre: {years}.")
            return _planner_redirect(
                _selected_year(request) or form.cleaned_data["start_date"].year, _selected_month(request)
            )
        if calculation["overlaps"]:
            names = ", ".join(overlap["name"] for overlap in calculation["overlaps"])
            django_messages.error(request, f"Der Zeitraum überschneidet sich mit: {names}.")
            return _planner_redirect(
                _selected_year(request) or form.cleaned_data["start_date"].year, _selected_month(request)
            )
        period = form.save(commit=False)
        period.user = request.user
        period.save()
        django_messages.success(request, "Urlaub gespeichert.")
    else:
        django_messages.error(request, "Der Urlaub konnte nicht gespeichert werden.")
    return _planner_redirect(
        _selected_year(request) or (period.start_date.year if period else 2026), _selected_month(request)
    )


@login_required
def vacation_period_delete(request):
    if not feature_enabled("vacation_planner"):
        return disabled_feature_response(request, "vacation_planner")
    if request.method == "POST":
        deleted_count, _details = VacationPeriod.objects.filter(
            user=request.user, pk=request.POST.get("period_id")
        ).delete()
        if deleted_count:
            django_messages.success(request, "Urlaub gelöscht.")
    return _planner_redirect(_selected_year(request) or 2026, _selected_month(request))


@login_required
def vacation_preview(request):
    if not feature_enabled("vacation_planner"):
        return disabled_feature_response(request, "vacation_planner", json_response=True)
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Nur POST ist erlaubt."}, status=405)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        payload = request.POST

    start_date = parse_date(payload.get("start_date") or "")
    end_date = parse_date(payload.get("end_date") or "")
    if not start_date or not end_date:
        return JsonResponse({"ok": False, "error": "Bitte Start- und Enddatum angeben."}, status=400)
    if end_date < start_date:
        return JsonResponse(
            {"ok": False, "error": "Das Enddatum muss am oder nach dem Startdatum liegen."}, status=400
        )

    calculation = calculate_period(
        request.user,
        start_date,
        end_date,
        exclude_period_id=payload.get("vacation_id") or None,
    )
    return JsonResponse({"ok": True, **_serialize_calculation(calculation)})


@login_required
def custom_holiday_save(request):
    if not feature_enabled("vacation_planner"):
        return disabled_feature_response(request, "vacation_planner")
    if request.method != "POST":
        return redirect("vacation_planner")

    year = _selected_year(request)
    vacation_year = VacationYear.objects.filter(user=request.user, year=year).first()
    if not vacation_year:
        django_messages.error(request, "Bitte speichere zuerst das Urlaubsjahr.")
        return _planner_redirect(year or 2026, _selected_month(request))

    holiday_id = request.POST.get("holiday_id")
    holiday = None
    if holiday_id:
        holiday = CustomHoliday.objects.filter(vacation_year=vacation_year, pk=holiday_id).first()
        if not holiday:
            django_messages.error(request, "Dieser Feiertag wurde nicht gefunden.")
            return _planner_redirect(year, _selected_month(request))

    form = CustomHolidayForm(request.POST, instance=holiday)
    if form.is_valid():
        custom_holiday = form.save(commit=False)
        if custom_holiday.date.year != year:
            django_messages.error(request, "Eigene Feiertage müssen im gewählten Jahr liegen.")
        else:
            custom_holiday.vacation_year = vacation_year
            custom_holiday.save()
            django_messages.success(request, "Feiertag gespeichert.")
    else:
        django_messages.error(request, "Der Feiertag konnte nicht gespeichert werden.")
    return _planner_redirect(year, _selected_month(request))


@login_required
def custom_holiday_delete(request):
    if not feature_enabled("vacation_planner"):
        return disabled_feature_response(request, "vacation_planner")
    year = _selected_year(request)
    vacation_year = VacationYear.objects.filter(user=request.user, year=year).first()
    if request.method == "POST" and vacation_year:
        deleted_count, _details = CustomHoliday.objects.filter(
            vacation_year=vacation_year, pk=request.POST.get("holiday_id")
        ).delete()
        if deleted_count:
            django_messages.success(request, "Feiertag gelöscht.")
    return _planner_redirect(year or 2026, _selected_month(request))


@login_required
def official_holiday_override_save(request):
    if not feature_enabled("vacation_planner"):
        return disabled_feature_response(request, "vacation_planner")
    year = _selected_year(request)
    vacation_year = VacationYear.objects.filter(user=request.user, year=year).first()
    official_holiday = OfficialHoliday.objects.filter(
        pk=request.POST.get("official_holiday_id"),
        date__year=year,
        subdivision=getattr(vacation_year, "subdivision", None),
    ).first()
    if request.method != "POST" or not vacation_year or not official_holiday:
        django_messages.error(request, "Der Feiertag konnte nicht gefunden werden.")
        return _planner_redirect(year or 2026, _selected_month(request))

    override = HolidayOverride.objects.filter(
        vacation_year=vacation_year, official_holiday=official_holiday
    ).first()
    form = HolidayOverrideForm(request.POST, instance=override)
    if form.is_valid():
        override = form.save(commit=False)
        override.vacation_year = vacation_year
        override.official_holiday = official_holiday
        override.save()
        django_messages.success(request, "Feiertag angepasst.")
    else:
        django_messages.error(request, "Die Feiertagsanpassung konnte nicht gespeichert werden.")
    return _planner_redirect(year, _selected_month(request))


@login_required
def official_holiday_override_reset(request):
    if not feature_enabled("vacation_planner"):
        return disabled_feature_response(request, "vacation_planner")
    year = _selected_year(request)
    vacation_year = VacationYear.objects.filter(user=request.user, year=year).first()
    if request.method == "POST" and vacation_year:
        HolidayOverride.objects.filter(
            vacation_year=vacation_year,
            official_holiday_id=request.POST.get("official_holiday_id"),
        ).delete()
        django_messages.success(request, "Feiertag zurückgesetzt.")
    return _planner_redirect(year or 2026, _selected_month(request))
