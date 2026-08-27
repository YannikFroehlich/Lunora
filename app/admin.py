from django.contrib import admin

from app.models import (
    CalendarEvent,
    CalendarReminder,
    CalendarSource,
    ChatMessage,
    ChatMessageReaction,
    Conversation,
    ConversationMember,
    Note,
    NoteAttachment,
    NoteShare,
    NoteUserState,
    NoteVersion,
    CustomHoliday,
    HolidayOverride,
    OfficialHoliday,
    Profile,
    SystemSettings,
    VacationPeriod,
    VacationYear,
    WeeklySummaryDelivery,
)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("display_name", "user", "theme", "timezone_name", "updated_at")
    search_fields = ("display_name", "user__username", "user__email")


@admin.register(SystemSettings)
class SystemSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "normal_login_enabled",
        "calendar_event_creation_enabled",
        "calendar_reminders_enabled",
        "calendar_sync_enabled",
        "messages_enabled",
        "notes_enabled",
        "vacation_planner_enabled",
        "weather_enabled",
        "dashboard_customization_enabled",
        "updated_at",
    )
    readonly_fields = ("created_at", "updated_at")


class ConversationMemberInline(admin.TabularInline):
    model = ConversationMember
    extra = 0
    autocomplete_fields = ("user",)


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    readonly_fields = ("created_at", "edited_at", "deleted_at", "pinned_at")
    autocomplete_fields = ("sender",)


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "is_group", "created_by", "updated_at")
    list_filter = ("is_group", "created_at", "updated_at")
    search_fields = ("title", "member_rows__user__username", "member_rows__user__email")
    autocomplete_fields = ("created_by",)
    inlines = (ConversationMemberInline, ChatMessageInline)


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "sender", "is_deleted", "is_pinned", "created_at")
    search_fields = ("body", "sender__username", "sender__email")
    list_filter = ("is_deleted", "is_pinned", "created_at")
    autocomplete_fields = ("conversation", "sender", "pinned_by")


@admin.register(ChatMessageReaction)
class ChatMessageReactionAdmin(admin.ModelAdmin):
    list_display = ("id", "message", "user", "emoji", "created_at")
    list_filter = ("emoji", "created_at")
    search_fields = ("message__body", "user__username", "user__email")
    autocomplete_fields = ("message", "user")


@admin.register(CalendarSource)
class CalendarSourceAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "color", "is_visible", "enabled", "last_synced_at", "updated_at")
    list_filter = ("color", "is_visible", "enabled")
    search_fields = ("name", "user__username", "ical_url")


@admin.register(CalendarEvent)
class CalendarEventAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "source", "start_at", "end_at")
    list_filter = ("is_all_day", "start_at")
    search_fields = ("title", "description", "location", "user__username")


@admin.register(CalendarReminder)
class CalendarReminderAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "is_done", "due_at", "email_notified_at", "desktop_notified_at")
    list_filter = ("is_done", "created_at")
    search_fields = ("title", "user__username")


@admin.register(WeeklySummaryDelivery)
class WeeklySummaryDeliveryAdmin(admin.ModelAdmin):
    list_display = ("user", "week_start", "sent_at")
    list_filter = ("week_start", "sent_at")
    search_fields = ("user__username", "user__email")
    autocomplete_fields = ("user",)


class NoteShareInline(admin.TabularInline):
    model = NoteShare
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "revision", "deleted_at", "updated_at")
    list_filter = ("deleted_at", "created_at", "updated_at")
    search_fields = ("title", "plain_text", "owner__username", "owner__email")
    autocomplete_fields = ("owner", "last_edited_by")
    inlines = (NoteShareInline,)


admin.site.register(NoteUserState)
admin.site.register(NoteAttachment)
admin.site.register(NoteVersion)


@admin.register(VacationYear)
class VacationYearAdmin(admin.ModelAdmin):
    list_display = ("user", "year", "allowance_days", "subdivision", "updated_at")
    list_filter = ("year", "subdivision")
    search_fields = ("user__username", "user__email")
    autocomplete_fields = ("user",)


@admin.register(VacationPeriod)
class VacationPeriodAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "start_date", "end_date", "updated_at")
    list_filter = ("start_date", "end_date")
    search_fields = ("name", "notes", "user__username", "user__email")
    autocomplete_fields = ("user",)


@admin.register(OfficialHoliday)
class OfficialHolidayAdmin(admin.ModelAdmin):
    list_display = ("name", "subdivision", "date", "day_value", "active", "source")
    list_filter = ("subdivision", "date", "active", "source")
    search_fields = ("name",)


@admin.register(CustomHoliday)
class CustomHolidayAdmin(admin.ModelAdmin):
    list_display = ("name", "vacation_year", "date", "day_value")
    list_filter = ("date", "day_value")
    search_fields = ("name", "vacation_year__user__username", "vacation_year__user__email")
    autocomplete_fields = ("vacation_year",)


@admin.register(HolidayOverride)
class HolidayOverrideAdmin(admin.ModelAdmin):
    list_display = ("vacation_year", "official_holiday", "name", "day_value")
    list_filter = ("day_value",)
    search_fields = ("name", "official_holiday__name", "vacation_year__user__username")
    autocomplete_fields = ("vacation_year", "official_holiday")
