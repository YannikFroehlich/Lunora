from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0002_refactor_profile_for_auth"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="theme",
            field=models.CharField(
                choices=[("light", "Heller Modus"), ("dark", "Dunkler Modus")],
                default="light",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="profile",
            name="accent_color",
            field=models.CharField(
                choices=[
                    ("#c2a276", "Sand"),
                    ("#7f916b", "Salbei"),
                    ("#a5aa74", "Olive"),
                    ("#9eb1b6", "Nebelblau"),
                    ("#aaa2be", "Lavendel"),
                    ("#c1a09a", "Rose"),
                ],
                default="#c2a276",
                max_length=7,
            ),
        ),
        migrations.AddField(
            model_name="profile",
            name="background_softness",
            field=models.PositiveSmallIntegerField(default=55),
        ),
        migrations.AddField(
            model_name="profile",
            name="density",
            field=models.CharField(
                choices=[
                    ("comfortable", "Komfortabel"),
                    ("balanced", "Ausgeglichen"),
                    ("compact", "Kompakt"),
                ],
                default="comfortable",
                max_length=12,
            ),
        ),
    ]
