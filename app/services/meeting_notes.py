"""A meeting note for a calendar event: "Notiz anlegen" on the event's detail page.

One note per event (`CalendarEvent.note`), owned by whoever made it and shared as editors with the
organizer and every invitee who hasn't declined, so everybody taking part writes in the same note.
"""

from django.core.exceptions import ValidationError
from django.db import transaction

from app.models import CalendarEvent, CalendarEventAttendee, Note, NoteShare
from app.services.note_content import _bullet_item, _heading, _task_item, document_plain_text
from app.services.notes import create_note, display_name, get_accessible_note, share_note
from app.services.user_preferences import (
    format_user_date,
    format_user_time,
    get_user_weekday_name,
    localtime_for_user,
)


def meeting_note_for(event, user):
    """The event's note when `user` may open it, else None."""
    if not event.note_id:
        return None
    try:
        return get_accessible_note(user, event.note_id)
    except Note.DoesNotExist:
        return None


def _participants(event):
    """Organizer first, then the invitees who haven't declined, all still active."""
    invitees = [
        row.user
        for row in event.attendees.select_related("user__profile").order_by("created_at", "id")
        if row.status != CalendarEventAttendee.STATUS_DECLINED
    ]
    return [person for person in [event.user, *invitees] if person.is_active]


def _when(event, user):
    start = localtime_for_user(event.start_at, user)
    label = f"{get_user_weekday_name(start, user)}, {format_user_date(start, user)}"
    if event.is_all_day:
        return f"{label}, ganztägig"
    end = localtime_for_user(event.end_at, user)
    return f"{label}, {format_user_time(start, user)}–{format_user_time(end, user)}"


def meeting_document(event, user, people):
    facts = _when(event, user) + (f" · {event.location}" if event.location else "")
    agenda = [line.strip(" -•*\t") for line in event.description.splitlines()]
    agenda = [line for line in agenda if line][:30]
    return {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": facts}]},
            _heading("Teilnehmende"),
            {"type": "bulletList", "content": [_bullet_item(display_name(person)) for person in people]},
            _heading("Agenda"),
            {"type": "bulletList", "content": [_bullet_item(line) for line in agenda] or [_bullet_item()]},
            _heading("Notizen"),
            {"type": "paragraph"},
            _heading("To-dos"),
            {"type": "taskList", "content": [_task_item()]},
        ],
    }


@transaction.atomic
def create_meeting_note(event, user):
    """The event's note, made now unless one exists that `user` can open (then that one)."""
    event = CalendarEvent.objects.select_for_update().select_related("user__profile").get(pk=event.pk)
    existing = meeting_note_for(event, user)
    if existing:
        return existing
    if event.note_id and Note.objects.filter(pk=event.note_id, deleted_at__isnull=True).exists():
        owner = Note.objects.select_related("owner__profile").get(pk=event.note_id).owner
        raise ValidationError(
            f"Zu diesem Termin gibt es schon eine Notiz von {display_name(owner)}. "
            "Bitte darum, dass sie mit dir geteilt wird."
        )
    people = _participants(event)
    start = localtime_for_user(event.start_at, user)
    note = create_note(user, title=f"{event.title} – {format_user_date(start, user)}"[:200])
    note.document = meeting_document(event, user, people)
    note.plain_text = document_plain_text(note.document)
    note.save(update_fields=["document", "plain_text"])
    for person in people:
        if person.pk != user.pk:
            share_note(user, note.pk, person.pk, NoteShare.ROLE_EDITOR)
    # .update() so linking the note doesn't count as an edit of the event.
    CalendarEvent.objects.filter(pk=event.pk).update(note=note)
    return note
