import uuid

from django.conf import settings
from django.db import models
from django.db.models import Count, Exists, OuterRef, Prefetch
from django.utils import timezone

from app.services.chat_files import chat_upload_to
from app.services.image_uploads import validate_profile_image_file
from app.services.note_content import empty_note_document
from app.services.note_files import note_upload_to, private_note_storage


class SystemSettings(models.Model):
    normal_login_enabled = models.BooleanField(default=True)
    calendar_event_creation_enabled = models.BooleanField(default=True)
    calendar_reminders_enabled = models.BooleanField(default=True)
    calendar_sync_enabled = models.BooleanField(default=True)
    messages_enabled = models.BooleanField(default=True)
    notes_enabled = models.BooleanField(default=True)
    vacation_planner_enabled = models.BooleanField(default=True)
    weather_enabled = models.BooleanField(default=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Systemeinstellung"
        verbose_name_plural = "Systemeinstellungen"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return "Lunora Systemeinstellungen"


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
    notify_email = models.BooleanField(default=False)
    notify_reminders = models.BooleanField(default=True)
    notify_desktop = models.BooleanField(default=False)
    weekly_summary = models.BooleanField(default=False)
    analytics_enabled = models.BooleanField(default=True)
    usage_data_enabled = models.BooleanField(default=False)
    note_shortcuts = models.JSONField(default=dict, blank=True)
    weather_default_city = models.CharField(max_length=120, blank=True, default="Bünde,de")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def name(self):
        return self.display_name or self.user.get_username()

    def __str__(self):
        return self.name


class WeatherLocation(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="weather_locations")
    query = models.CharField(max_length=120, blank=True)
    lat = models.FloatField(blank=True, null=True)
    lon = models.FloatField(blank=True, null=True)
    name = models.CharField(max_length=120, blank=True)
    details = models.CharField(max_length=120, blank=True)
    label = models.CharField(max_length=200, blank=True)
    is_default = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    last_alert_kind = models.CharField(max_length=40, blank=True)
    last_alert_notified_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.label or self.name or self.query


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
        if self.is_group:
            return "GR"
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
        """Find the existing 1:1 conversation between two users, if any.

        Membership is checked via `Exists` subqueries rather than chained `.filter()`
        calls on `member_rows`, because each chained filter on the same reverse relation
        adds its own join, and `Count("member_rows", distinct=True)` would then only
        count rows from the last-added join instead of all of the conversation's members.
        """
        has_first_member = ConversationMember.objects.filter(conversation=OuterRef("pk"), user=first_user)
        has_second_member = ConversationMember.objects.filter(conversation=OuterRef("pk"), user=second_user)
        return (
            cls.objects.filter(is_group=False)
            .annotate(has_first_member=Exists(has_first_member), has_second_member=Exists(has_second_member))
            .filter(has_first_member=True, has_second_member=True)
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
    typing_until = models.DateTimeField(blank=True, null=True)
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
    reply_to = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="replies",
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


class ChatMessageAttachment(models.Model):
    KIND_IMAGE = "image"
    KIND_FILE = "file"
    KIND_CHOICES = [(KIND_IMAGE, "Bild"), (KIND_FILE, "Datei")]

    message = models.OneToOneField(ChatMessage, on_delete=models.CASCADE, related_name="attachment")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="chat_attachments",
    )
    file_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    file = models.FileField(storage=private_note_storage, upload_to=chat_upload_to, max_length=500)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=160)
    size = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return self.original_name


class CalendarSource(models.Model):
    COLOR_CHOICES = [
        ("blue", "Blau"),
        ("green", "Gruen"),
        ("red", "Rot"),
        ("sand", "Sand"),
        ("violet", "Violett"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calendar_sources")
    name = models.CharField(max_length=120, default="Google Kalender")
    ical_url = models.URLField(max_length=1000)
    color = models.CharField(max_length=12, choices=COLOR_CHOICES, default="blue")
    is_visible = models.BooleanField(default=True)
    enabled = models.BooleanField(default=True)
    sync_interval_minutes = models.PositiveSmallIntegerField(default=15)
    sync_requested_at = models.DateTimeField(blank=True, null=True)
    last_sync_attempt_at = models.DateTimeField(blank=True, null=True)
    last_synced_at = models.DateTimeField(blank=True, null=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "ical_url"], name="unique_calendar_source_url_per_user"),
        ]

    def __str__(self):
        return self.name


class CalendarEvent(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calendar_events")
    source = models.ForeignKey(
        CalendarSource,
        on_delete=models.CASCADE,
        related_name="events",
        blank=True,
        null=True,
    )
    external_id = models.CharField(max_length=500, blank=True, default="")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=255, blank=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    is_all_day = models.BooleanField(default=False)
    recurrence_id = models.UUIDField(null=True, blank=True, db_index=True)
    recurrence_rule = models.CharField(
        max_length=10,
        blank=True,
        default="",
        choices=[
            ("DAILY", "Täglich"),
            ("WEEKLY", "Wöchentlich"),
            ("MONTHLY", "Monatlich"),
            ("YEARLY", "Jährlich"),
        ],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_at", "title"]
        constraints = [
            models.UniqueConstraint(fields=["source", "external_id"], name="unique_calendar_event_per_source"),
        ]

    def __str__(self):
        return self.title


class CalendarEventAttendee(models.Model):
    STATUS_INVITED = "invited"
    STATUS_ACCEPTED = "accepted"
    STATUS_DECLINED = "declined"
    STATUS_CHOICES = [
        (STATUS_INVITED, "Eingeladen"),
        (STATUS_ACCEPTED, "Zugesagt"),
        (STATUS_DECLINED, "Abgesagt"),
    ]

    event = models.ForeignKey(CalendarEvent, on_delete=models.CASCADE, related_name="attendees")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calendar_invitations")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_INVITED)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="+",
    )
    responded_at = models.DateTimeField(blank=True, null=True)
    email_notified_at = models.DateTimeField(blank=True, null=True)
    desktop_notified_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["event", "user"], name="unique_attendee_per_event"),
        ]

    def __str__(self):
        return f"{self.user} @ {self.event}"


class CalendarReminder(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calendar_reminders")
    title = models.CharField(max_length=180)
    due_at = models.DateTimeField(blank=True, null=True)
    is_done = models.BooleanField(default=False)
    email_notified_at = models.DateTimeField(blank=True, null=True)
    desktop_notified_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["is_done", "-created_at"]

    def __str__(self):
        return self.title


class WeeklySummaryDelivery(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="weekly_summaries")
    week_start = models.DateField()
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-week_start", "-sent_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "week_start"], name="unique_weekly_summary_per_user"),
        ]

    def __str__(self):
        return f"{self.user} – Woche ab {self.week_start:%d.%m.%Y}"


class VacationYear(models.Model):
    SUBDIVISION_CHOICES = [
        ("BB", "Brandenburg"),
        ("BE", "Berlin"),
        ("BW", "Baden-Württemberg"),
        ("BY", "Bayern"),
        ("HB", "Bremen"),
        ("HE", "Hessen"),
        ("HH", "Hamburg"),
        ("MV", "Mecklenburg-Vorpommern"),
        ("NI", "Niedersachsen"),
        ("NW", "Nordrhein-Westfalen"),
        ("RP", "Rheinland-Pfalz"),
        ("SH", "Schleswig-Holstein"),
        ("SL", "Saarland"),
        ("SN", "Sachsen"),
        ("ST", "Sachsen-Anhalt"),
        ("TH", "Thüringen"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vacation_years")
    year = models.PositiveSmallIntegerField()
    allowance_days = models.DecimalField(max_digits=5, decimal_places=1)
    subdivision = models.CharField(max_length=2, choices=SUBDIVISION_CHOICES, default="NW")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-year"]
        constraints = [
            models.UniqueConstraint(fields=["user", "year"], name="unique_vacation_year_per_user"),
        ]

    def __str__(self):
        return f"{self.user} – Urlaub {self.year}"


class VacationPeriod(models.Model):
    TARIFURLAUB = "tarifurlaub"
    SONDERURLAUB = "sonderurlaub"
    UEBERSTUNDENABBAU = "ueberstundenabbau"
    TYPE_CHOICES = [
        (TARIFURLAUB, "Tarifurlaub"),
        (SONDERURLAUB, "Sonderurlaub"),
        (UEBERSTUNDENABBAU, "Überstundenabbau"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vacation_periods")
    name = models.CharField(max_length=160, choices=TYPE_CHOICES, default=TARIFURLAUB)
    start_date = models.DateField()
    end_date = models.DateField()
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_date", "name", "id"]
        indexes = [
            models.Index(fields=["user", "start_date", "end_date"], name="app_vacperiod_user_range_idx"),
        ]

    def __str__(self):
        return self.get_name_display()


class OfficialHoliday(models.Model):
    subdivision = models.CharField(max_length=2, choices=VacationYear.SUBDIVISION_CHOICES)
    date = models.DateField()
    name = models.CharField(max_length=160)
    day_value = models.DecimalField(max_digits=2, decimal_places=1, default=1)
    active = models.BooleanField(default=True)
    source = models.CharField(max_length=80, default="holidays")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date", "name"]
        constraints = [
            models.UniqueConstraint(fields=["subdivision", "date", "name"], name="unique_official_holiday"),
        ]

    def __str__(self):
        return f"{self.name} ({self.subdivision}, {self.date:%d.%m.%Y})"


class CustomHoliday(models.Model):
    vacation_year = models.ForeignKey(VacationYear, on_delete=models.CASCADE, related_name="custom_holidays")
    date = models.DateField()
    name = models.CharField(max_length=160)
    day_value = models.DecimalField(max_digits=2, decimal_places=1, default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date", "name"]
        constraints = [
            models.UniqueConstraint(fields=["vacation_year", "date", "name"], name="unique_custom_holiday"),
        ]

    def __str__(self):
        return self.name


class HolidayOverride(models.Model):
    vacation_year = models.ForeignKey(VacationYear, on_delete=models.CASCADE, related_name="holiday_overrides")
    official_holiday = models.ForeignKey(OfficialHoliday, on_delete=models.CASCADE, related_name="user_overrides")
    name = models.CharField(max_length=160, blank=True)
    day_value = models.DecimalField(max_digits=2, decimal_places=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["official_holiday__date", "official_holiday__name"]
        constraints = [
            models.UniqueConstraint(fields=["vacation_year", "official_holiday"], name="unique_holiday_override"),
        ]

    def __str__(self):
        return self.name or self.official_holiday.name


class Note(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="owned_notes")
    title = models.CharField(max_length=200, default="Unbenannte Notiz")
    document = models.JSONField(default=empty_note_document)
    plain_text = models.TextField(blank=True)
    revision = models.PositiveIntegerField(default=1)
    last_edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="last_edited_notes",
    )
    deleted_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [
            models.Index(fields=["owner", "deleted_at", "updated_at"], name="app_note_owner_state_idx"),
        ]

    def __str__(self):
        return self.title


class NoteTemplate(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="note_templates")
    name = models.CharField(max_length=100)
    document = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "name"], name="unique_note_template_name_per_owner"),
        ]

    def __str__(self):
        return self.name


class NoteFolder(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="note_folders")
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="children",
    )
    name = models.CharField(max_length=100)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        indexes = [
            models.Index(fields=["owner", "parent", "name"], name="app_notefolder_tree_idx"),
        ]

    def __str__(self):
        return self.name


class NoteLink(models.Model):
    source_note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name="outgoing_links")
    target_note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name="incoming_links")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source_note", "target_note"], name="unique_note_link"),
        ]

    def __str__(self):
        return f"{self.source_note_id} -> {self.target_note_id}"


class NoteShare(models.Model):
    ROLE_READER = "reader"
    ROLE_EDITOR = "editor"
    ROLE_CHOICES = [(ROLE_READER, "Leser"), (ROLE_EDITOR, "Bearbeiter")]

    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name="shares")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shared_notes")
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_READER)
    first_opened_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["note", "user"], name="unique_note_share_per_user"),
        ]

    def __str__(self):
        return f"{self.user} – {self.note} ({self.role})"


class NoteUserState(models.Model):
    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name="user_states")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="note_states")
    folder = models.ForeignKey(
        NoteFolder,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="note_states",
    )
    position = models.PositiveIntegerField(default=0)
    is_pinned = models.BooleanField(default=False)
    pinned_at = models.DateTimeField(blank=True, null=True)
    is_archived = models.BooleanField(default=False)
    archived_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["note", "user"], name="unique_note_state_per_user"),
        ]


class NoteAttachment(models.Model):
    KIND_IMAGE = "image"
    KIND_FILE = "file"
    KIND_CHOICES = [(KIND_IMAGE, "Bild"), (KIND_FILE, "Datei")]

    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name="attachments")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="note_attachments",
    )
    file_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    file = models.FileField(storage=private_note_storage, upload_to=note_upload_to, max_length=500)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=160)
    size = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return self.original_name


class NoteVersion(models.Model):
    REASON_AUTOSAVE = "autosave"
    REASON_RESTORE = "restore"
    REASON_CONFLICT = "conflict"
    REASON_CHOICES = [
        (REASON_AUTOSAVE, "Automatisch"),
        (REASON_RESTORE, "Vor Wiederherstellung"),
        (REASON_CONFLICT, "Vor Überschreiben"),
    ]

    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name="versions")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="note_versions",
    )
    source_revision = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    document = models.JSONField()
    reason = models.CharField(max_length=12, choices=REASON_CHOICES, default=REASON_AUTOSAVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["note", "created_at"], name="app_notever_note_time_idx")]

    def __str__(self):
        return f"{self.note} – Revision {self.source_revision}"


class NoteCommentThread(models.Model):
    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name="comment_threads")
    thread_id = models.UUIDField(unique=True)
    anchor_text = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="+",
    )
    is_resolved = models.BooleanField(default=False)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="+",
    )
    resolved_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return self.anchor_text[:60] or str(self.thread_id)


class NoteComment(models.Model):
    thread = models.ForeignKey(NoteCommentThread, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="+",
    )
    body = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return self.body[:60]


class NoteActivityNotification(models.Model):
    KIND_MENTION = "mention"
    KIND_COMMENT = "comment"
    KIND_CHOICES = [
        (KIND_MENTION, "Erwähnung"),
        (KIND_COMMENT, "Kommentar"),
    ]

    note = models.ForeignKey(Note, on_delete=models.CASCADE, related_name="activity_notifications")
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="note_activity_notifications",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="+",
    )
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    excerpt = models.CharField(max_length=200, blank=True)
    email_notified_at = models.DateTimeField(blank=True, null=True)
    desktop_notified_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.get_kind_display()} für {self.recipient}"
