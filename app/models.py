from django.conf import settings
from django.db import models
from django.db.models import Count, Prefetch
from django.utils import timezone

from app.services.image_uploads import validate_profile_image_file


class Profile(models.Model):
    THEME_CHOICES = [
        ("light", "Heller Modus"),
        ("dark", "Dunkler Modus"),
    ]
    DENSITY_CHOICES = [
        ("comfortable", "Komfortabel"),
        ("balanced", "Ausgeglichen"),
        ("compact", "Kompakt"),
    ]
    ACCENT_COLOR_CHOICES = [
        ("#c2a276", "Sand"),
        ("#7f916b", "Salbei"),
        ("#a5aa74", "Olive"),
        ("#9eb1b6", "Nebelblau"),
        ("#aaa2be", "Lavendel"),
        ("#c1a09a", "Rose"),
    ]
    DATE_FORMAT_CHOICES = [
        ("de_numeric", "31.12.2026"),
        ("de_long", "31. Dezember 2026"),
        ("iso", "2026-12-31"),
        ("us_numeric", "12/31/2026"),
    ]
    TIME_FORMAT_CHOICES = [
        ("24h", "24-Stunden"),
        ("12h", "12-Stunden"),
    ]
    TIMEZONE_CHOICES = [
        ("Europe/Berlin", "Europe/Berlin"),
        ("Europe/Amsterdam", "Europe/Amsterdam"),
        ("Europe/London", "Europe/London"),
        ("Europe/Paris", "Europe/Paris"),
        ("Europe/Rome", "Europe/Rome"),
        ("Europe/Madrid", "Europe/Madrid"),
        ("UTC", "UTC"),
        ("America/New_York", "America/New_York"),
        ("America/Los_Angeles", "America/Los_Angeles"),
        ("Asia/Tokyo", "Asia/Tokyo"),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    display_name = models.CharField(max_length=120)
    profile_image = models.FileField(upload_to="profiles/", blank=True, validators=[validate_profile_image_file])
    theme = models.CharField(max_length=12, choices=THEME_CHOICES, default="light")
    accent_color = models.CharField(max_length=7, choices=ACCENT_COLOR_CHOICES, default="#c2a276")
    background_softness = models.PositiveSmallIntegerField(default=55)
    density = models.CharField(max_length=12, choices=DENSITY_CHOICES, default="comfortable")
    date_format = models.CharField(max_length=24, choices=DATE_FORMAT_CHOICES, default="de_numeric")
    time_format = models.CharField(max_length=12, choices=TIME_FORMAT_CHOICES, default="24h")
    timezone_name = models.CharField(max_length=64, choices=TIMEZONE_CHOICES, default="Europe/Berlin")
    notify_email = models.BooleanField(default=True)
    notify_reminders = models.BooleanField(default=True)
    notify_desktop = models.BooleanField(default=True)
    weekly_summary = models.BooleanField(default=False)
    analytics_enabled = models.BooleanField(default=True)
    usage_data_enabled = models.BooleanField(default=False)
    weather_default_city = models.CharField(max_length=120, blank=True, default="Buende,de")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def name(self):
        return self.display_name or self.user.get_username()

    def __str__(self):
        return self.name


class Conversation(models.Model):
    title = models.CharField(max_length=140, blank=True)
    is_group = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="created_conversations",
    )
    participants = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="ConversationMember",
        related_name="conversations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]

    def __str__(self):
        return self.title or f"Unterhaltung #{self.pk}"

    @staticmethod
    def initials_for_user(user):
        full_name = (user.get_full_name() or user.get_username() or "?").strip()
        parts = [part for part in full_name.replace("@", " ").replace(".", " ").split() if part]
        if len(parts) >= 2:
            return f"{parts[0][0]}{parts[1][0]}".upper()
        if parts:
            return parts[0][:2].upper()
        return "?"

    @staticmethod
    def display_name_for_user(user):
        profile_name = getattr(getattr(user, "profile", None), "display_name", "")
        return profile_name or user.get_full_name() or user.email or user.get_username()

    def display_title_for(self, user):
        if self.title:
            return self.title
        other_participants = [member.user for member in self.member_rows.all() if member.user_id != user.id]
        if other_participants:
            return ", ".join(self.display_name_for_user(member_user) for member_user in other_participants[:3])
        return "Nur du"

    def avatar_for(self, user):
        if self.title:
            return "".join(word[0] for word in self.title.split()[:2]).upper() or "GR"
        other = next((member.user for member in self.member_rows.all() if member.user_id != user.id), None)
        return self.initials_for_user(other or user)

    @classmethod
    def visible_for(cls, user):
        return (
            cls.objects.filter(member_rows__user=user, member_rows__is_archived=False)
            .prefetch_related(
                Prefetch(
                    "member_rows",
                    queryset=ConversationMember.objects.select_related("user", "user__profile"),
                )
            )
            .distinct()
        )

    @classmethod
    def find_direct_between(cls, first_user, second_user):
        return (
            cls.objects.filter(is_group=False, member_rows__user=first_user)
            .filter(member_rows__user=second_user)
            .annotate(member_count=Count("member_rows", distinct=True))
            .filter(member_count=2)
            .first()
        )

    def mark_read_for(self, user):
        ConversationMember.objects.filter(conversation=self, user=user).update(last_read_at=timezone.now())


class ConversationMember(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="member_rows")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversation_memberships")
    is_archived = models.BooleanField(default=False)
    is_blocked = models.BooleanField(default=False)
    muted_until = models.DateTimeField(blank=True, null=True)
    last_read_at = models.DateTimeField(blank=True, null=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["joined_at"]
        constraints = [
            models.UniqueConstraint(fields=["conversation", "user"], name="unique_conversation_member"),
        ]

    def __str__(self):
        return f"{self.user} in {self.conversation}"

    @property
    def is_muted(self):
        return bool(self.muted_until and self.muted_until > timezone.now())

    def unread_count(self):
        messages = self.conversation.messages.exclude(sender=self.user)
        if self.last_read_at:
            messages = messages.filter(created_at__gt=self.last_read_at)
        return messages.count()


class ChatMessage(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_chat_messages")
    body = models.TextField(max_length=4000)
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(blank=True, null=True)
    is_pinned = models.BooleanField(default=False)
    pinned_at = models.DateTimeField(blank=True, null=True)
    pinned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="pinned_chat_messages",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["conversation", "created_at"]),
            models.Index(fields=["sender", "created_at"]),
            models.Index(fields=["conversation", "is_pinned", "pinned_at"], name="app_chatmes_pin_idx"),
        ]

    @property
    def display_body(self):
        if self.is_deleted:
            return "Diese Nachricht wurde gelöscht."
        return self.body

    def __str__(self):
        return self.display_body[:80]


class ChatMessageReaction(models.Model):
    EMOJI_CHOICES = [
        ("👍", "👍"),
        ("❤️", "❤️"),
        ("😂", "😂"),
        ("😮", "😮"),
        ("😢", "😢"),
        ("🙏", "🙏"),
    ]

    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_message_reactions")
    emoji = models.CharField(max_length=8, choices=EMOJI_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["message", "user"], name="unique_chat_reaction_per_user"),
        ]

    def __str__(self):
        return f"{self.emoji} von {self.user}"


class CalendarSource(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calendar_source")
    name = models.CharField(max_length=120, default="Google Kalender")
    ical_url = models.URLField(max_length=1000)
    enabled = models.BooleanField(default=True)
    sync_interval_minutes = models.PositiveSmallIntegerField(default=15)
    last_synced_at = models.DateTimeField(blank=True, null=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class CalendarEvent(models.Model):
    TONE_CHOICES = [
        ("blue", "Blau"),
        ("green", "Gruen"),
        ("red", "Rot"),
        ("sand", "Sand"),
        ("violet", "Violett"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calendar_events")
    source = models.ForeignKey(CalendarSource, on_delete=models.CASCADE, related_name="events")
    external_id = models.CharField(max_length=500)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=255, blank=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    is_all_day = models.BooleanField(default=False)
    tone = models.CharField(max_length=12, choices=TONE_CHOICES, default="blue")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_at", "title"]
        constraints = [
            models.UniqueConstraint(fields=["source", "external_id"], name="unique_calendar_event_per_source"),
        ]

    def __str__(self):
        return self.title


class CalendarReminder(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calendar_reminders")
    title = models.CharField(max_length=180)
    due_at = models.DateTimeField(blank=True, null=True)
    is_done = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["is_done", "-created_at"]

    def __str__(self):
        return self.title
