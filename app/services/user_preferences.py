from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.utils import timezone

from app.models import Profile

GERMAN_MONTH_NAMES = [
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
]

GERMAN_WEEKDAY_NAMES = [
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
    "Sonntag",
]

DEFAULT_DATE_FORMAT = "de_numeric"
DEFAULT_TIME_FORMAT = "24h"
DEFAULT_TIMEZONE_NAME = "Europe/Berlin"


def get_profile_from_user(user):
    if not user or not getattr(user, "is_authenticated", False):
        return None

    try:
        return user.profile
    except Profile.DoesNotExist:
        return None


def _resolve_profile(profile_or_user=None):
    if isinstance(profile_or_user, Profile):
        return profile_or_user
    return get_profile_from_user(profile_or_user)


def get_user_timezone_name(profile_or_user=None):
    profile = _resolve_profile(profile_or_user)
    value = getattr(profile, "timezone_name", "") or getattr(settings, "TIME_ZONE", DEFAULT_TIMEZONE_NAME)
    return value or DEFAULT_TIMEZONE_NAME


def get_user_zoneinfo(profile_or_user=None):
    timezone_name = get_user_timezone_name(profile_or_user)
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE_NAME)


def activate_user_timezone(user):
    timezone.activate(get_user_zoneinfo(user))


def localtime_for_user(value=None, profile_or_user=None):
    user_timezone = get_user_zoneinfo(profile_or_user)

    if value is None:
        value = timezone.now()

    if isinstance(value, datetime):
        if timezone.is_naive(value):
            value = timezone.make_aware(value, user_timezone)
        return value.astimezone(user_timezone)

    return value


def get_user_date_format(profile_or_user=None):
    profile = _resolve_profile(profile_or_user)
    return getattr(profile, "date_format", "") or DEFAULT_DATE_FORMAT


def get_user_time_format(profile_or_user=None):
    profile = _resolve_profile(profile_or_user)
    return getattr(profile, "time_format", "") or DEFAULT_TIME_FORMAT


def format_user_date(value, profile_or_user=None, *, include_year=True):
    if isinstance(value, datetime):
        value = localtime_for_user(value, profile_or_user).date()
    elif value is None:
        value = localtime_for_user(profile_or_user=profile_or_user).date()

    if not isinstance(value, date):
        return ""

    date_format = get_user_date_format(profile_or_user)

    if date_format == "de_long":
        if include_year:
            return f"{value.day}. {GERMAN_MONTH_NAMES[value.month - 1]} {value.year}"
        return f"{value.day}. {GERMAN_MONTH_NAMES[value.month - 1]}"

    if date_format == "iso":
        if include_year:
            return value.strftime("%Y-%m-%d")
        return value.strftime("%m-%d")

    if date_format == "us_numeric":
        if include_year:
            return value.strftime("%m/%d/%Y")
        return value.strftime("%m/%d")

    if include_year:
        return value.strftime("%d.%m.%Y")
    return value.strftime("%d.%m.")


def format_user_time(value, profile_or_user=None):
    if isinstance(value, datetime):
        value = localtime_for_user(value, profile_or_user)

    if value is None:
        value = localtime_for_user(profile_or_user=profile_or_user)

    time_format = get_user_time_format(profile_or_user)
    if time_format == "12h":
        return value.strftime("%I:%M %p").lstrip("0")

    return value.strftime("%H:%M")


def format_user_datetime(value, profile_or_user=None):
    if not value:
        return ""
    localized_value = localtime_for_user(value, profile_or_user)
    return f"{format_user_date(localized_value, profile_or_user)} {format_user_time(localized_value, profile_or_user)}"


def get_user_weekday_name(value, profile_or_user=None):
    if isinstance(value, datetime):
        value = localtime_for_user(value, profile_or_user)
    return GERMAN_WEEKDAY_NAMES[value.weekday()]


def get_user_month_name(value, profile_or_user=None):
    if isinstance(value, datetime):
        value = localtime_for_user(value, profile_or_user)
    return GERMAN_MONTH_NAMES[value.month - 1]
