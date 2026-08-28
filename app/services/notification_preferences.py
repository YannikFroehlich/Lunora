from django.core.exceptions import ObjectDoesNotExist

from app.models import NotificationPreference, UserNotification
from app.services.user_preferences import localtime_for_user


CHANNEL_INBOX = "inbox"
CHANNEL_EMAIL = "email"
CHANNEL_WEB_PUSH = "web_push"
CHANNEL_FIELDS = {
    CHANNEL_INBOX: "inbox_enabled",
    CHANNEL_EMAIL: "email_enabled",
    CHANNEL_WEB_PUSH: "web_push_enabled",
}

CATEGORY_DEFINITIONS = (
    {
        "key": NotificationPreference.CATEGORY_CALENDAR,
        "label": "Kalender",
        "hint": "Termine, Erinnerungen und Einladungen",
        "icon": "fa-calendar-days",
    },
    {
        "key": NotificationPreference.CATEGORY_TASKS,
        "label": "Aufgaben",
        "hint": "Fälligkeiten und Aufgabenhinweise",
        "icon": "fa-list-check",
    },
    {
        "key": NotificationPreference.CATEGORY_NOTES,
        "label": "Notizen",
        "hint": "Erwähnungen, Kommentare und Freigaben",
        "icon": "fa-note-sticky",
    },
    {
        "key": NotificationPreference.CATEGORY_WEATHER,
        "label": "Wetter",
        "hint": "Warnungen für gespeicherte Orte",
        "icon": "fa-cloud-bolt",
    },
)

KIND_TO_CATEGORY = {
    UserNotification.KIND_CALENDAR_REMINDER: NotificationPreference.CATEGORY_CALENDAR,
    UserNotification.KIND_EVENT_INVITATION: NotificationPreference.CATEGORY_CALENDAR,
    UserNotification.KIND_TASK_DUE: NotificationPreference.CATEGORY_TASKS,
    UserNotification.KIND_NOTE_MENTION: NotificationPreference.CATEGORY_NOTES,
    UserNotification.KIND_NOTE_COMMENT: NotificationPreference.CATEGORY_NOTES,
    UserNotification.KIND_NOTE_SHARE: NotificationPreference.CATEGORY_NOTES,
    UserNotification.KIND_WEATHER_ALERT: NotificationPreference.CATEGORY_WEATHER,
}


def notification_preference_map(user_ids):
    clean_user_ids = {user_id for user_id in user_ids if user_id is not None}
    if not clean_user_ids:
        return {}
    return {
        (user_id, category): {
            CHANNEL_INBOX: inbox_enabled,
            CHANNEL_EMAIL: email_enabled,
            CHANNEL_WEB_PUSH: web_push_enabled,
        }
        for user_id, category, inbox_enabled, email_enabled, web_push_enabled in (
            NotificationPreference.objects.filter(user_id__in=clean_user_ids).values_list(
                "user_id",
                "category",
                "inbox_enabled",
                "email_enabled",
                "web_push_enabled",
            )
        )
    }


def category_for_notification_kind(kind):
    return KIND_TO_CATEGORY.get(kind)


def preference_channel_enabled(preference_map, user_id, category, channel):
    if category is None:
        return True
    return preference_map.get((user_id, category), {}).get(channel, True)


def notification_channel_enabled(user, kind, channel, *, preference_map=None):
    category = category_for_notification_kind(kind)
    preferences = preference_map
    if preferences is None:
        preferences = notification_preference_map([user.pk])
    if not preference_channel_enabled(preferences, user.pk, category, channel):
        return False

    try:
        profile = user.profile
    except ObjectDoesNotExist:
        return channel == CHANNEL_INBOX

    if channel == CHANNEL_EMAIL and not profile.notify_email:
        return False
    if channel == CHANNEL_WEB_PUSH and not profile.notify_desktop:
        return False
    if kind in {UserNotification.KIND_CALENDAR_REMINDER, UserNotification.KIND_TASK_DUE}:
        if channel in {CHANNEL_EMAIL, CHANNEL_WEB_PUSH} and not profile.notify_reminders:
            return False
    return True


def enabled_notification_kinds(user, channel):
    preferences = notification_preference_map([user.pk])
    return [
        kind
        for kind in KIND_TO_CATEGORY
        if notification_channel_enabled(user, kind, channel, preference_map=preferences)
    ]


def filter_channel_items(items, *, user_id_getter, category, channel):
    items = list(items)
    preferences = notification_preference_map(user_id_getter(item) for item in items)
    return [
        item
        for item in items
        if preference_channel_enabled(
            preferences,
            user_id_getter(item),
            category,
            channel,
        )
    ]


def web_push_is_quiet_for_user(user, *, now=None):
    try:
        profile = user.profile
    except ObjectDoesNotExist:
        return False
    if not profile.notification_quiet_hours_enabled:
        return False

    start = profile.notification_quiet_start
    end = profile.notification_quiet_end
    if start == end:
        return False

    local_time = localtime_for_user(now, user).time().replace(tzinfo=None)
    if start < end:
        return start <= local_time < end
    return local_time >= start or local_time < end
