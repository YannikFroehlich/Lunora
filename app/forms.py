from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm, UsernameField
from django.contrib.auth.models import User

from app.models import CalendarReminder, CalendarSource, Profile


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
            "profile_image": forms.ClearableFileInput(attrs={"accept": "image/*"}),
        }

    def clean_profile_image(self):
        image = self.cleaned_data.get("profile_image")
        if image and hasattr(image, "content_type") and not image.content_type.startswith("image/"):
            raise forms.ValidationError("Bitte lade eine Bilddatei hoch.")
        return image


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


class CalendarSourceForm(forms.ModelForm):
    class Meta:
        model = CalendarSource
        fields = ["ical_url", "enabled"]
        labels = {
            "ical_url": "Google Kalender-Link",
            "enabled": "Automatisch synchronisieren",
        }
        widgets = {
            "ical_url": forms.URLInput(
                attrs={
                    "placeholder": "https://calendar.google.com/calendar/ical/...",
                    "autocomplete": "off",
                }
            ),
        }
        help_texts = {
            "ical_url": "Nutze den privaten iCal-Link aus den Google-Kalendereinstellungen.",
        }

    def clean_ical_url(self):
        url = self.cleaned_data["ical_url"].strip()
        if url.startswith("webcal://"):
            url = "https://" + url.removeprefix("webcal://")
        if not url.startswith(("https://", "http://")):
            raise forms.ValidationError("Bitte fuege einen gueltigen Kalenderlink ein.")
        if "calendar.google.com" not in url and not url.lower().endswith(".ics"):
            raise forms.ValidationError("Bitte nutze einen Google-iCal-Link oder eine direkte .ics-URL.")
        return url

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["enabled"].initial = True


class CalendarReminderForm(forms.ModelForm):
    class Meta:
        model = CalendarReminder
        fields = ["title"]
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
