from django.conf import settings
from django.db import models


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
    profile_image = models.FileField(upload_to="profiles/", blank=True)
    theme = models.CharField(max_length=12, choices=THEME_CHOICES, default="light")
    accent_color = models.CharField(max_length=7, choices=ACCENT_COLOR_CHOICES, default="#c2a276")
    background_softness = models.PositiveSmallIntegerField(default=55)
    density = models.CharField(max_length=12, choices=DENSITY_CHOICES, default="comfortable")
    date_format = models.CharField(max_length=24, choices=DATE_FORMAT_CHOICES, default="de_numeric")
    time_format = models.CharField(max_length=12, choices=TIME_FORMAT_CHOICES, default="24h")
    timezone_name = models.CharField(max_length=64, choices=TIMEZONE_CHOICES, default="Europe/Berlin")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def name(self):
        return self.display_name or self.user.get_username()

    def __str__(self):
        return self.name


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
    is_done = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["is_done", "-created_at"]

    def __str__(self):
        return self.title
