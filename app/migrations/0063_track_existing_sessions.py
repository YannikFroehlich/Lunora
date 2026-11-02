from django.db import migrations
from django.utils import timezone


def track_existing_sessions(apps, schema_editor):
    """Give every live signed-in session a UserSession row, so revoking sessions can rely on them."""
    from django.contrib.sessions.backends.db import SessionStore

    Session = apps.get_model("sessions", "Session")
    UserSession = apps.get_model("app", "UserSession")
    User = apps.get_model("auth", "User")
    tracked = set(UserSession.objects.values_list("session_key", flat=True))
    user_ids = set(User.objects.values_list("pk", flat=True))
    decoder = SessionStore()
    now = timezone.now()
    rows = []
    for session in Session.objects.filter(expire_date__gt=now).exclude(session_key__in=tracked).iterator():
        user_id = decoder.decode(session.session_data).get("_auth_user_id")
        if user_id and str(user_id).isdigit() and int(user_id) in user_ids:
            rows.append(UserSession(user_id=int(user_id), session_key=session.session_key, last_seen_at=now))
    UserSession.objects.bulk_create(rows, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0062_task_assignee"),
        ("sessions", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(track_existing_sessions, migrations.RunPython.noop),
    ]
