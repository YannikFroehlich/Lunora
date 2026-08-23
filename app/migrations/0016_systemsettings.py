from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0015_allow_manual_calendar_events"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SystemSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("normal_login_enabled", models.BooleanField(default=True)),
                ("calendar_event_creation_enabled", models.BooleanField(default=True)),
                ("calendar_reminders_enabled", models.BooleanField(default=True)),
                ("calendar_sync_enabled", models.BooleanField(default=True)),
                ("messages_enabled", models.BooleanField(default=True)),
                ("weather_enabled", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Systemeinstellung",
                "verbose_name_plural": "Systemeinstellungen",
            },
        ),
    ]
