from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0024_alter_officialholiday_subdivision_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="calendarsource",
            name="last_sync_attempt_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="calendarsource",
            name="sync_requested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
