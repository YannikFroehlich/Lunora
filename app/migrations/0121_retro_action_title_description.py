from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0120_task_description"),
    ]

    operations = [
        migrations.RenameField(model_name="retroaction", old_name="text", new_name="title"),
        migrations.AddField(
            model_name="retroaction",
            name="description",
            field=models.TextField(blank=True, default="", max_length=2000),
        ),
    ]
