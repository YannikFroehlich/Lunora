import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm, UsernameField
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.db.models import Q

from app.models import (
    CalendarEvent,
    CalendarEventAttendee,
    CalendarReminder,
    CalendarSource,
    ChatMessage,
    CustomHoliday,
    HolidayOverride,
    Profile,
    SystemSettings,
    Task,
    VacationPeriod,
    VacationYear,
)
from app.services.calendar_service import expand_manual_recurrence
from app.services.chat_files import infer_attachment_kind, validate_note_upload
from app.services.image_uploads import PROFILE_IMAGE_ACCEPT, validate_profile_image_file
from app.services.user_preferences import get_user_zoneinfo, localtime_for_user
from app.services.url_safety import validate_calendar_url


LOGIN_ATTEMPT_LIMIT = 5
LOGIN_ATTEMPT_LOCKOUT_SECONDS = 15 * 60


def _login_attempt_cache_key(identifier):
    return f"login-attempts:{identifier.strip().casefold()}"


class EmailLoginForm(AuthenticationForm):
    username = UsernameField(
        label="E-Mail oder Benutzername",
        widget=forms.TextInput(
            attrs={
                "autofocus": True,
                "autocomplete": "username",
                "placeholder": "you@example.com oder Benutzername",
            }
        ),
    )

    def clean(self):
        """Throttle repeated failed logins per resolved identifier.

        Keyed on the resolved username (not the raw input) so alternating between
        an account's e-mail and username doesn't reset the attempt count. This uses
        the default cache, so the counter is per-process — sufficient for a single
        local `runserver` process, but it would need a shared cache backend (e.g.
        Redis) to hold up across multiple WSGI workers.
        """
        raw_identifier = self.cleaned_data.get("username")
        resolved_identifier = self._resolve_username(raw_identifier) if raw_identifier else None
        if resolved_identifier:
            self.cleaned_data["username"] = resolved_identifier
            cache_key = _login_attempt_cache_key(resolved_identifier)
            if cache.get(cache_key, 0) >= LOGIN_ATTEMPT_LIMIT:
                raise forms.ValidationError(
                    "Zu viele fehlgeschlagene Anmeldeversuche. Bitte warte einige Minuten und versuche es erneut.",
                    code="login_locked",
                )

        try:
            cleaned_data = super().clean()
        except forms.ValidationError:
            if resolved_identifier:
                cache_key = _login_attempt_cache_key(resolved_identifier)
                cache.set(cache_key, cache.get(cache_key, 0) + 1, LOGIN_ATTEMPT_LOCKOUT_SECONDS)
            raise

        if resolved_identifier:
            cache.delete(_login_attempt_cache_key(resolved_identifier))
        return cleaned_data

    def _resolve_username(self, identifier):
        identifier = identifier.strip()
        usernames = list(
            User.objects.filter(Q(username__iexact=identifier) | Q(email__iexact=identifier))
            .values_list("username", flat=True)
            .distinct()[:2]
        )
        if len(usernames) == 1:
            return usernames[0]
        return identifier

    def confirm_login_allowed(self, user):
        from app.services.system_settings import user_can_login

        super().confirm_login_allowed(user)
        if not user_can_login(user):
            raise forms.ValidationError(
                "Der Login ist vorübergehend deaktiviert.",
                code="login_disabled",
            )


class SystemSettingsForm(forms.ModelForm):
    class Meta:
        model = SystemSettings
        fields = [
            "normal_login_enabled",
            "calendar_event_creation_enabled",
            "calendar_reminders_enabled",
            "calendar_sync_enabled",
            "messages_enabled",
            "notes_enabled",
            "vacation_planner_enabled",
            "weather_enabled",
            "dashboard_customization_enabled",
            "tasks_enabled",
        ]
        labels = {
            "normal_login_enabled": "Login und Registrierung für Nutzer",
            "calendar_event_creation_enabled": "Kalender: eigene Termine erstellen",
            "calendar_reminders_enabled": "Kalender: Erinnerungen",
            "calendar_sync_enabled": "Kalender: Synchronisierung und Quellen",
            "messages_enabled": "Nachrichten",
            "notes_enabled": "Notizen",
            "vacation_planner_enabled": "Urlaubsplaner",
            "weather_enabled": "Wetter",
            "dashboard_customization_enabled": "Dashboard anpassen",
            "tasks_enabled": "Aufgaben",
        }
        widgets = {
            "normal_login_enabled": forms.CheckboxInput(),
            "calendar_event_creation_enabled": forms.CheckboxInput(),
            "calendar_reminders_enabled": forms.CheckboxInput(),
            "calendar_sync_enabled": forms.CheckboxInput(),
            "messages_enabled": forms.CheckboxInput(),
            "notes_enabled": forms.CheckboxInput(),
            "vacation_planner_enabled": forms.CheckboxInput(),
            "weather_enabled": forms.CheckboxInput(),
            "dashboard_customization_enabled": forms.CheckboxInput(),
            "tasks_enabled": forms.CheckboxInput(),
        }


def _clean_half_step_days(value, *, field_label):
    if value is None:
        return value
    decimal_value = Decimal(value)
    if decimal_value < 0:
        raise forms.ValidationError(f"{field_label} darf nicht negativ sein.")
    if decimal_value * 2 != int(decimal_value * 2):
        raise forms.ValidationError(f"{field_label} darf nur ganze oder halbe Tage enthalten.")
    return decimal_value


class VacationYearForm(forms.ModelForm):
    class Meta:
        model = VacationYear
        fields = ["allowance_days", "subdivision"]
        labels = {
            "allowance_days": "Urlaubstage pro Jahr",
            "subdivision": "Bundesland",
        }
        widgets = {
            "allowance_days": forms.NumberInput(attrs={"min": "0", "step": "0.5", "inputmode": "decimal"}),
            "subdivision": forms.Select(),
        }

    def clean_allowance_days(self):
        return _clean_half_step_days(self.cleaned_data.get("allowance_days"), field_label="Urlaubstage")


class VacationPeriodForm(forms.ModelForm):
    class Meta:
        model = VacationPeriod
        fields = ["name", "start_date", "end_date", "notes"]
        labels = {
            "name": "Urlaubsart",
            "start_date": "Startdatum",
            "end_date": "Enddatum",
            "notes": "Hinweise",
        }
        widgets = {
            "name": forms.Select(),
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"placeholder": "Optional", "rows": 3}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_notes(self):
        return self.cleaned_data.get("notes", "").strip()

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        if start_date and end_date and end_date < start_date:
            self.add_error("end_date", "Das Enddatum muss am oder nach dem Startdatum liegen.")
        if start_date and end_date and (end_date - start_date).days > 730:
            self.add_error("end_date", "Ein Urlaub darf höchstens zwei Jahre umfassen.")
        return cleaned_data


class CustomHolidayForm(forms.ModelForm):
    is_half_day = forms.BooleanField(label="Halber Feiertag", required=False)

    class Meta:
        model = CustomHoliday
        fields = ["date", "name", "is_half_day"]
        labels = {
            "date": "Datum",
            "name": "Name",
        }
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "name": forms.TextInput(attrs={"placeholder": "z. B. Betriebsruhe", "autocomplete": "off"}),
        }

    def clean_name(self):
        return self.cleaned_data["name"].strip()

    def save(self, commit=True):
        holiday = super().save(commit=False)
        holiday.day_value = Decimal("0.5") if self.cleaned_data.get("is_half_day") else Decimal("1.0")
        if commit:
            holiday.save()
        return holiday


class HolidayOverrideForm(forms.ModelForm):
    day_value = forms.ChoiceField(
        label="Wertung",
        choices=[
            ("1.0", "Ganzer Feiertag"),
            ("0.5", "Halber Feiertag"),
            ("0.0", "Deaktiviert"),
        ],
    )

    class Meta:
        model = HolidayOverride
        fields = ["name", "day_value"]
        labels = {
            "name": "Name",
        }
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Optionaler eigener Name", "autocomplete": "off"}),
        }

    def clean_name(self):
        return self.cleaned_data.get("name", "").strip()

    def clean_day_value(self):
        return Decimal(self.cleaned_data["day_value"])


class RegistrationForm(UserCreationForm):
    name = forms.CharField(label="Name", max_length=120, widget=forms.TextInput(attrs={"placeholder": "Dein Name"}))
    email = forms.EmailField(label="E-Mail", widget=forms.EmailInput(attrs={"placeholder": "you@example.com"}))

    class Meta:
        model = User
        fields = ["name", "email", "password1", "password2"]

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(username__iexact=email).exists() or User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Diese E-Mail-Adresse ist bereits registriert.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        name = self.cleaned_data["name"].strip()
        email = self.cleaned_data["email"].strip().lower()
        user.username = email
        user.email = email
        user.first_name = name
        if commit:
            user.save()
            Profile.objects.create(user=user, display_name=name)
        return user


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["display_name", "profile_image"]
        labels = {
            "display_name": "Name",
            "profile_image": "Profilbild",
        }
        widgets = {
            "display_name": forms.TextInput(attrs={"placeholder": "Dein Name"}),
            "profile_image": forms.ClearableFileInput(attrs={"accept": PROFILE_IMAGE_ACCEPT}),
        }

    def clean_profile_image(self):
        image = self.cleaned_data.get("profile_image")
        if image and hasattr(image, "content_type"):
            try:
                validate_profile_image_file(image)
            except forms.ValidationError:
                raise
            except Exception as error:
                raise forms.ValidationError("Das Profilbild konnte nicht geprüft werden.") from error
        return image

    def save(self, commit=True):
        old_image_name = ""
        if self.instance.pk:
            old_image_name = (
                Profile.objects.filter(pk=self.instance.pk)
                .values_list("profile_image", flat=True)
                .first()
                or ""
            )

        profile = super().save(commit=commit)
        new_image_name = getattr(profile.profile_image, "name", "") or ""

        if commit and old_image_name and old_image_name != new_image_name:
            default_storage.delete(old_image_name)

        return profile


class AppearanceForm(forms.ModelForm):
    REGION_FIELD_FALLBACKS = {
        "date_format": "de_numeric",
        "time_format": "24h",
        "timezone_name": "Europe/Berlin",
    }

    class Meta:
        model = Profile
        fields = [
            "theme",
            "accent_color",
            "background_softness",
            "density",
            "date_format",
            "time_format",
            "timezone_name",
        ]
        labels = {
            "date_format": "Datumsformat",
            "time_format": "Zeitformat",
            "timezone_name": "Zeitzone",
        }
        widgets = {
            "theme": forms.RadioSelect(),
            "accent_color": forms.RadioSelect(),
            "background_softness": forms.NumberInput(
                attrs={"class": "softness-slider", "type": "range", "min": "0", "max": "100"}
            ),
            "density": forms.RadioSelect(),
            "date_format": forms.Select(attrs={"class": "region-select"}),
            "time_format": forms.Select(attrs={"class": "region-select"}),
            "timezone_name": forms.Select(attrs={"class": "region-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in self.REGION_FIELD_FALLBACKS:
            self.fields[field_name].required = False

    def clean_background_softness(self):
        value = self.cleaned_data["background_softness"]
        if value < 0 or value > 100:
            raise forms.ValidationError("Bitte wähle einen Wert zwischen 0 und 100.")
        return value

    def _clean_optional_region_field(self, field_name):
        value = self.cleaned_data.get(field_name)
        if value:
            return value
        return getattr(self.instance, field_name, "") or self.REGION_FIELD_FALLBACKS[field_name]

    def clean_date_format(self):
        return self._clean_optional_region_field("date_format")

    def clean_time_format(self):
        return self._clean_optional_region_field("time_format")

    def clean_timezone_name(self):
        return self._clean_optional_region_field("timezone_name")


class ProfilePreferencesForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = [
            "notify_email",
            "notify_reminders",
            "notify_desktop",
            "weekly_summary",
            "weather_default_city",
        ]
        labels = {
            "notify_email": "E-Mail Benachrichtigungen",
            "notify_reminders": "Erinnerungen",
            "notify_desktop": "Web-Push-Zustellung",
            "weekly_summary": "Wöchentliche Zusammenfassung",
            "weather_default_city": "Standard-Wetterort",
        }
        widgets = {
            "notify_email": forms.CheckboxInput(),
            "notify_reminders": forms.CheckboxInput(),
            "notify_desktop": forms.CheckboxInput(),
            "weekly_summary": forms.CheckboxInput(),
            "weather_default_city": forms.TextInput(
                attrs={
                    "placeholder": "z. B. Bünde,de",
                    "autocomplete": "address-level2",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in [
            "notify_email",
            "notify_reminders",
            "notify_desktop",
            "weekly_summary",
        ]:
            self.fields[field_name].required = False

    def clean_weather_default_city(self):
        return self.cleaned_data.get("weather_default_city", "").strip()


class MessageForm(forms.ModelForm):
    attachment = forms.FileField(label="Anhang", required=False)

    class Meta:
        model = ChatMessage
        fields = ["body"]
        labels = {"body": "Nachricht"}
        widgets = {
            "body": forms.Textarea(
                attrs={
                    "placeholder": "Nachricht schreiben ...",
                    "autocomplete": "off",
                    "rows": 1,
                }
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["body"].required = False

    def clean_body(self):
        return self.cleaned_data["body"].strip()

    def clean_attachment(self):
        upload = self.cleaned_data.get("attachment")
        if upload:
            validate_note_upload(upload, kind=infer_attachment_kind(upload.name))
        return upload

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("body") and not cleaned_data.get("attachment"):
            raise forms.ValidationError("Bitte gib eine Nachricht ein oder füge einen Anhang hinzu.")
        return cleaned_data


class ConversationStartForm(forms.Form):
    recipient = forms.ModelMultipleChoiceField(
        label="Kontakt(e)",
        queryset=User.objects.none(),
        widget=forms.CheckboxSelectMultiple,
    )
    title = forms.CharField(
        label="Gruppenname (optional)",
        max_length=140,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "z. B. Familie oder Projektteam", "autocomplete": "off"}),
    )
    body = forms.CharField(
        label="Erste Nachricht",
        max_length=4000,
        required=False,
        widget=forms.Textarea(
            attrs={
                "placeholder": "Optionale erste Nachricht ...",
                "rows": 3,
            }
        ),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        users = (
            User.objects.filter(is_active=True)
            .exclude(pk=getattr(user, "pk", None))
            .order_by("first_name", "email", "username")
        )
        self.fields["recipient"].queryset = users

    def clean_body(self):
        return self.cleaned_data.get("body", "").strip()


class CalendarSourceForm(forms.ModelForm):
    name = forms.CharField(
        label="Kalendername",
        max_length=120,
        widget=forms.TextInput(attrs={"placeholder": "z. B. Arbeit, Familie oder Geburtstage", "autocomplete": "off"}),
    )

    ical_url = forms.CharField(
        label="Google Kalender-Link",
        widget=forms.URLInput(
            attrs={
                "placeholder": "https://calendar.google.com/calendar/ical/...",
                "autocomplete": "off",
            }
        ),
        help_text="Nutze den privaten iCal-Link aus den Google-Kalendereinstellungen.",
    )

    class Meta:
        model = CalendarSource
        fields = ["name", "ical_url", "color", "enabled"]
        labels = {
            "color": "Farbe",
            "enabled": "Automatisch synchronisieren",
        }
        widgets = {
            "color": forms.RadioSelect(),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["enabled"].required = False
        if not self.instance.pk:
            self.fields["enabled"].initial = True

    def clean_name(self):
        return self.cleaned_data["name"].strip()

    def clean_ical_url(self):
        url = self.cleaned_data["ical_url"].strip()
        try:
            normalized_url = validate_calendar_url(url)
        except ValueError as error:
            raise forms.ValidationError(str(error)) from error

        user = self.user or getattr(self.instance, "user", None)
        if user:
            duplicate_sources = CalendarSource.objects.filter(user=user, ical_url=normalized_url)
            if self.instance.pk:
                duplicate_sources = duplicate_sources.exclude(pk=self.instance.pk)
            if duplicate_sources.exists():
                raise forms.ValidationError("Dieser Kalender-Link ist bereits gespeichert.")

        return normalized_url


class CalendarReminderForm(forms.ModelForm):
    due_at = forms.DateTimeField(
        label="Fällig am",
        required=False,
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            attrs={
                "type": "datetime-local",
                "autocomplete": "off",
            },
            format="%Y-%m-%dT%H:%M",
        ),
    )

    class Meta:
        model = CalendarReminder
        fields = ["title", "due_at"]
        labels = {"title": "Neue Erinnerung"}
        widgets = {
            "title": forms.TextInput(
                attrs={
                    "placeholder": "Neue Erinnerung",
                    "autocomplete": "off",
                }
            )
        }

    def clean_title(self):
        return self.cleaned_data["title"].strip()


class TaskForm(forms.ModelForm):
    due_at = forms.DateTimeField(
        label="Fällig am",
        required=False,
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            attrs={
                "type": "datetime-local",
                "autocomplete": "off",
            },
            format="%Y-%m-%dT%H:%M",
        ),
    )

    class Meta:
        model = Task
        fields = ["title", "due_at"]
        labels = {"title": "Neue Aufgabe"}
        widgets = {
            "title": forms.TextInput(
                attrs={
                    "placeholder": "Neue Aufgabe hinzufügen …",
                    "autocomplete": "off",
                }
            )
        }

    def clean_title(self):
        return self.cleaned_data["title"].strip()


class _CalendarEventDateTimeForm(forms.Form):
    title = forms.CharField(
        label="Titel",
        max_length=255,
        widget=forms.TextInput(
            attrs={
                "placeholder": "z. B. Zahnarzttermin",
                "autocomplete": "off",
                "autofocus": True,
            }
        ),
    )
    event_date = forms.DateField(
        label="Datum",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d"],
    )
    start_time = forms.TimeField(
        label="Beginn",
        required=False,
        widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        input_formats=["%H:%M"],
    )
    end_time = forms.TimeField(
        label="Ende",
        required=False,
        widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        input_formats=["%H:%M"],
    )
    is_all_day = forms.BooleanField(label="Ganztägig", required=False)
    location = forms.CharField(
        label="Ort",
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Optional", "autocomplete": "off"}),
    )
    attendees = forms.ModelMultipleChoiceField(
        label="Teilnehmer einladen",
        queryset=User.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["attendees"].queryset = (
            User.objects.filter(is_active=True)
            .exclude(pk=getattr(user, "pk", None))
            .order_by("first_name", "email", "username")
        )

    def clean_title(self):
        return self.cleaned_data["title"].strip()

    def clean_location(self):
        return self.cleaned_data.get("location", "").strip()

    def clean(self):
        cleaned_data = super().clean()
        event_date = cleaned_data.get("event_date")
        start_time = cleaned_data.get("start_time")
        end_time = cleaned_data.get("end_time")
        is_all_day = cleaned_data.get("is_all_day", False)

        if not event_date:
            return cleaned_data

        user_timezone = get_user_zoneinfo(self.user)
        if is_all_day:
            start_at = datetime.combine(event_date, time.min, tzinfo=user_timezone)
            end_at = start_at + timedelta(days=1)
        else:
            if not start_time:
                self.add_error("start_time", "Bitte gib eine Startzeit an.")
                return cleaned_data

            start_at = datetime.combine(event_date, start_time, tzinfo=user_timezone)
            if end_time:
                end_at = datetime.combine(event_date, end_time, tzinfo=user_timezone)
                if end_at <= start_at:
                    self.add_error("end_time", "Die Endzeit muss nach der Startzeit liegen.")
                    return cleaned_data
            else:
                end_at = start_at + timedelta(hours=1)

        cleaned_data["start_at"] = start_at
        cleaned_data["end_at"] = end_at
        return cleaned_data


class CalendarEventForm(_CalendarEventDateTimeForm):
    repeat = forms.ChoiceField(
        label="Wiederholung",
        choices=[
            ("none", "Keine"),
            ("DAILY", "Täglich"),
            ("WEEKLY", "Wöchentlich"),
            ("MONTHLY", "Monatlich"),
            ("YEARLY", "Jährlich"),
        ],
        required=False,
        initial="none",
    )
    repeat_until = forms.DateField(
        label="Wiederholen bis",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d"],
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        if not self.is_bound:
            now = localtime_for_user(profile_or_user=user)
            start_at = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            self.initial.update(
                {
                    "event_date": start_at.date(),
                    "start_time": start_at.time(),
                    "end_time": (start_at + timedelta(hours=1)).time(),
                }
            )

    def clean(self):
        cleaned_data = super().clean()
        event_date = cleaned_data.get("event_date")
        if not event_date or "start_at" not in cleaned_data:
            return cleaned_data

        user_timezone = get_user_zoneinfo(self.user)
        repeat = cleaned_data.get("repeat") or "none"
        if repeat != "none":
            repeat_until = cleaned_data.get("repeat_until")
            if not repeat_until:
                self.add_error("repeat_until", "Bitte gib ein Enddatum für die Wiederholung an.")
                return cleaned_data
            if repeat_until < event_date:
                self.add_error("repeat_until", "Das Enddatum muss nach dem Startdatum liegen.")
                return cleaned_data
            if (repeat_until - event_date).days > 1825:
                self.add_error("repeat_until", "Die Wiederholung darf höchstens 5 Jahre umfassen.")
                return cleaned_data
            cleaned_data["repeat_until_at"] = datetime.combine(repeat_until, time.max, tzinfo=user_timezone)

        return cleaned_data

    def save(self, *, user):
        if not self.is_valid():
            raise ValueError("Ein ungültiges Terminformular kann nicht gespeichert werden.")

        start_at = self.cleaned_data["start_at"]
        end_at = self.cleaned_data["end_at"]
        duration = end_at - start_at
        repeat = self.cleaned_data.get("repeat") or "none"

        if repeat == "none":
            occurrence_starts = [start_at]
            recurrence_id = None
        else:
            occurrence_starts = expand_manual_recurrence(start_at, repeat, self.cleaned_data["repeat_until_at"])
            recurrence_id = uuid.uuid4()

        events = CalendarEvent.objects.bulk_create(
            [
                CalendarEvent(
                    user=user,
                    source=None,
                    title=self.cleaned_data["title"],
                    location=self.cleaned_data["location"],
                    start_at=occurrence_start,
                    end_at=occurrence_start + duration,
                    is_all_day=self.cleaned_data["is_all_day"],
                    recurrence_id=recurrence_id,
                    recurrence_rule="" if repeat == "none" else repeat,
                )
                for occurrence_start in occurrence_starts
            ]
        )

        attendees = self.cleaned_data.get("attendees")
        if attendees:
            invitation_rows = CalendarEventAttendee.objects.bulk_create(
                [
                    CalendarEventAttendee(event=event, user=attendee, invited_by=user)
                    for event in events
                    for attendee in attendees
                ]
            )
            from app.services.notifications import materialize_event_invitation_notifications

            materialize_event_invitation_notifications(invitation_rows)

        return events[0] if events else None


class CalendarEventEditForm(_CalendarEventDateTimeForm):
    def __init__(self, *args, user=None, instance=None, **kwargs):
        self.instance = instance
        super().__init__(*args, user=user, **kwargs)
        if instance is not None and not self.is_bound:
            instance_start = localtime_for_user(instance.start_at, user)
            instance_end = localtime_for_user(instance.end_at, user)
            self.initial.update(
                {
                    "title": instance.title,
                    "event_date": instance_start.date(),
                    "start_time": instance_start.time(),
                    "end_time": instance_end.time(),
                    "is_all_day": instance.is_all_day,
                    "location": instance.location,
                    "attendees": list(instance.attendees.values_list("user_id", flat=True)),
                }
            )

    def save(self):
        event = self.instance
        event.title = self.cleaned_data["title"]
        event.location = self.cleaned_data["location"]
        event.start_at = self.cleaned_data["start_at"]
        event.end_at = self.cleaned_data["end_at"]
        event.is_all_day = self.cleaned_data["is_all_day"]
        event.save(update_fields=["title", "location", "start_at", "end_at", "is_all_day", "updated_at"])

        new_attendee_ids = {attendee.id for attendee in self.cleaned_data.get("attendees") or []}
        existing_attendee_ids = set(event.attendees.values_list("user_id", flat=True))

        to_remove = existing_attendee_ids - new_attendee_ids
        if to_remove:
            event.attendees.filter(user_id__in=to_remove).delete()

        to_add = new_attendee_ids - existing_attendee_ids
        if to_add:
            invitation_rows = CalendarEventAttendee.objects.bulk_create(
                [
                    CalendarEventAttendee(event=event, user_id=user_id, invited_by=self.user)
                    for user_id in to_add
                ]
            )
            from app.services.notifications import materialize_event_invitation_notifications

            materialize_event_invitation_notifications(invitation_rows)

        return event
