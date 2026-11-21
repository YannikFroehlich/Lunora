"""Daily notes: one note per user and day, opened from /notes/today/.

A new daily note starts with a link back to the previous one, today's appointments and the tasks
due today (both as plain lists, so ticking something in the note never pretends to finish a task),
and an empty "Notizen" section. Afterwards it is an ordinary note; only `Note.daily_date` marks it.
"""

from datetime import datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q

from app.models import CalendarEvent, CalendarEventAttendee, Note, NoteLink, NoteUserState
from app.services.note_content import document_plain_text, validate_note_document
from app.services.system_settings import feature_enabled
from app.services.tasks import visible_tasks
from app.services.user_preferences import format_user_date, format_user_time, localtime_for_user

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MAX_ITEMS = 30


def _text(value):
    return {"type": "text", "text": value}


def _heading(value):
    return {"type": "heading", "attrs": {"level": 2}, "content": [_text(value)]}


def _bullets(lines):
    return {
        "type": "bulletList",
        "content": [
            {"type": "listItem", "content": [{"type": "paragraph", "content": [_text(line)]}]}
            for line in lines
        ],
    }


def _paragraph(value=""):
    return {"type": "paragraph", "content": [_text(value)]} if value else {"type": "paragraph"}


def _day_bounds(user, day):
    zone = localtime_for_user(profile_or_user=user).tzinfo
    start = datetime.combine(day, time.min, tzinfo=zone)
    return start, start + timedelta(days=1)


def _event_lines(user, day):
    start, end = _day_bounds(user, day)
    events = CalendarEvent.objects.filter(
        Q(user=user) | Q(attendees__user=user, attendees__status=CalendarEventAttendee.STATUS_ACCEPTED)
    ).filter(start_at__lt=end, end_at__gt=start)
    if not feature_enabled("calendar_subscriptions"):
        events = events.filter(subscription=None)
    events = events.distinct().order_by("start_at", "id")[:MAX_ITEMS]
    return [
        f"{'Ganztägig' if event.is_all_day else format_user_time(event.start_at, user)} – {event.title}"
        for event in events
    ]


def _task_lines(user, day):
    _, end = _day_bounds(user, day)
    tasks = visible_tasks(user).filter(is_done=False, due_at__lt=end).order_by("due_at", "id")[:MAX_ITEMS]
    start, _ = _day_bounds(user, day)
    return [f"{task.title}{' (überfällig)' if task.due_at < start else ''}" for task in tasks]


def daily_note_title(user, day):
    return f"Tagesnotiz · {WEEKDAYS[day.weekday()]}, {format_user_date(day, user)}"


def build_daily_document(user, day, previous=None):
    content = []
    if previous is not None:
        content.append(
            {
                "type": "paragraph",
                "content": [
                    _text("Vorherige Tagesnotiz: "),
                    {"type": "noteLink", "attrs": {"noteId": previous.pk, "label": previous.title[:200]}},
                ],
            }
        )
    content.append(_heading("Termine"))
    events = _event_lines(user, day)
    content.append(_bullets(events) if events else _paragraph("Keine Termine."))
    if feature_enabled("tasks"):
        content.append(_heading("Aufgaben"))
        tasks = _task_lines(user, day)
        content.append(_bullets(tasks) if tasks else _paragraph("Nichts fällig."))
    content.append(_heading("Notizen"))
    content.append(_paragraph())
    document = {"type": "doc", "content": content}
    validate_note_document(document)
    return document


def open_daily_note(user, day):
    """The user's daily note for `day` (in the trash too, so a deleted one isn't recreated twice),
    created when there is none yet."""
    existing = Note.objects.filter(owner=user, daily_date=day).first()
    if existing is not None:
        return existing
    previous = (
        Note.objects.filter(owner=user, daily_date__lt=day, deleted_at__isnull=True)
        .order_by("-daily_date")
        .first()
    )
    document = build_daily_document(user, day, previous)
    try:
        with transaction.atomic():
            note = Note.objects.create(
                owner=user,
                title=daily_note_title(user, day),
                document=document,
                plain_text=document_plain_text(document),
                last_edited_by=user,
                daily_date=day,
            )
            NoteUserState.objects.create(note=note, user=user, position=0)
            if previous is not None:
                NoteLink.objects.create(source_note=note, target_note=previous)
    except IntegrityError:
        # Opened twice at the same moment: the other request created it.
        return Note.objects.get(owner=user, daily_date=day)
    return note
