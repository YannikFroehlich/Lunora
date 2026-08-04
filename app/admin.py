from django.contrib import admin

from app.models import (
    CalendarEvent,
    CalendarReminder,
    CalendarSource,
    ChatMessage,
    ChatMessageReaction,
    Conversation,
    ConversationMember,
    Profile,
)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("display_name", "user", "theme", "timezone_name", "updated_at")
    search_fields = ("display_name", "user__username", "user__email")


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
    list_display = ("title", "user", "is_done", "created_at")
    list_filter = ("is_done", "created_at")
    search_fields = ("title", "user__username")
