from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm, UsernameField
from django.contrib.auth.models import User
from django.core.files.storage import default_storage

from app.models import CalendarReminder, CalendarSource, ChatMessage, Profile
from app.services.image_uploads import PROFILE_IMAGE_ACCEPT, validate_profile_image_file
from app.services.url_safety import validate_calendar_url


class EmailLoginForm(AuthenticationForm):
    username = UsernameField(
        label="E-Mail",
        widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email", "placeholder": "you@example.com"}),
    )


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
                raise forms.ValidationError("Das Profilbild konnte nicht geprueft werden.") from error
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
            raise forms.ValidationError("Bitte waehle einen Wert zwischen 0 und 100.")
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
            "analytics_enabled",
            "usage_data_enabled",
            "weather_default_city",
        ]
        labels = {
            "notify_email": "E-Mail Benachrichtigungen",
            "notify_reminders": "Erinnerungen",
            "notify_desktop": "Desktop Hinweise",
            "weekly_summary": "Woechentliche Zusammenfassung",
            "analytics_enabled": "Analysen",
            "usage_data_enabled": "Nutzungsdaten",
            "weather_default_city": "Standard-Wetterort",
        }
        widgets = {
            "notify_email": forms.CheckboxInput(),
            "notify_reminders": forms.CheckboxInput(),
            "notify_desktop": forms.CheckboxInput(),
            "weekly_summary": forms.CheckboxInput(),
            "analytics_enabled": forms.CheckboxInput(),
            "usage_data_enabled": forms.CheckboxInput(),
            "weather_default_city": forms.TextInput(
                attrs={
                    "placeholder": "z. B. Buende,de",
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
            "analytics_enabled",
            "usage_data_enabled",
        ]:
            self.fields[field_name].required = False

    def clean_weather_default_city(self):
        return self.cleaned_data.get("weather_default_city", "").strip()


class MessageForm(forms.ModelForm):
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

    def clean_body(self):
        body = self.cleaned_data["body"].strip()
        if not body:
            raise forms.ValidationError("Bitte gib eine Nachricht ein.")
        return body


class ConversationStartForm(forms.Form):
    recipient = forms.ModelChoiceField(
        label="Kontakt",
        queryset=User.objects.none(),
        empty_label="Kontakt auswählen",
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
        users = User.objects.exclude(pk=getattr(user, "pk", None)).order_by("first_name", "email", "username")
        self.fields["recipient"].queryset = users

    def clean_body(self):
        return self.cleaned_data.get("body", "").strip()


class CalendarSourceForm(forms.ModelForm):
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
        fields = ["ical_url", "enabled"]
        labels = {
            "enabled": "Automatisch synchronisieren",
        }

    def clean_ical_url(self):
        url = self.cleaned_data["ical_url"].strip()
        try:
            return validate_calendar_url(url)
        except ValueError as error:
            raise forms.ValidationError(str(error)) from error

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["enabled"].initial = True


class CalendarReminderForm(forms.ModelForm):
    due_at = forms.DateTimeField(
        label="Faellig am",
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
