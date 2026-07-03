from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import migrations, models
import django.db.models.deletion


def link_existing_profiles(apps, schema_editor):
    profile_model = apps.get_model("app", "Profile")
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    user_model = apps.get_model(app_label, model_name)

    for profile in profile_model.objects.filter(user__isnull=True):
        email = (profile.email or f"profile-{profile.pk}@lunora.local").strip().lower()
        username = email
        suffix = 2
        while user_model.objects.filter(username__iexact=username).exists():
            username = f"{email}-{suffix}"
            suffix += 1

        user = user_model(
            username=username,
            email=email,
            first_name=profile.display_name,
            password=profile.password_hash or make_password(None),
        )
        user.save()
        profile.user = user
        profile.save(update_fields=["user"])


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="user",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="profile",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RenameField(
            model_name="profile",
            old_name="name",
            new_name="display_name",
        ),
        migrations.RunPython(link_existing_profiles, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="profile",
            name="user",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="profile",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RemoveField(
            model_name="profile",
            name="email",
        ),
        migrations.RemoveField(
            model_name="profile",
            name="password_hash",
        ),
    ]
