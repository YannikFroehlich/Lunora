from django.db import migrations, models


def delete_imported_events(apps, schema_editor):
    # Events mirrored from external iCal sources go away together with the import feature.
    apps.get_model("app", "CalendarEvent").objects.filter(source__isnull=False).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0047_alter_profile_timezone_name"),
    ]

    operations = [
        migrations.RunPython(delete_imported_events, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="calendarevent",
            name="unique_calendar_event_per_source",
        ),
        migrations.RemoveField(
            model_name="calendarevent",
            name="external_id",
        ),
        migrations.RemoveField(
            model_name="calendarevent",
            name="source",
        ),
        migrations.DeleteModel(
            name="CalendarSource",
        ),
        migrations.AddField(
            model_name="calendarevent",
            name="category",
            field=models.CharField(
                choices=[
                    ("exam", "Klassenarbeit"),
                    ("test", "Test"),
                    ("presentation", "Referat"),
                    ("deadline", "Abgabe"),
                    ("other", "Sonstiges"),
                ],
                default="other",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="profile",
            name="calendar_feed_token",
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
        migrations.RenameField(
            model_name="systemsettings",
            old_name="calendar_sync_enabled",
            new_name="calendar_feed_enabled",
        ),
    ]
