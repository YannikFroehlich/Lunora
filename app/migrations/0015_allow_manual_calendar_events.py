import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0014_remove_calendarevent_tone_calendarsource_color_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="calendarevent",
            name="external_id",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AlterField(
            model_name="calendarevent",
            name="source",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="events",
                to="app.calendarsource",
            ),
        ),
    ]
