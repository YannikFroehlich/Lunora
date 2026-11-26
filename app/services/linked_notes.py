"""Links from tasks and calendar events to a note.

A task or event carries an optional `note`; its owner sets it (task edit dialog, event detail page,
or the note's "Checkliste als Aufgaben" action). Every side only shows a link the viewer may open:
the note's footer lists linked tasks and events the viewer sees, and a task or event names its note
only for viewers with access to that note."""

from django.db.models import Q
from django.db.models.functions import Lower
from django.urls import reverse

from app.models import CalendarEvent
from app.services.notes import accessible_notes
from app.services.system_settings import feature_enabled
from app.services.tasks import visible_tasks
from app.services.user_preferences import format_user_date

MAX_LINKED_ITEMS = 20


def linkable_notes(user):
    """The notes `user` may link to, for a choice field."""
    return accessible_notes(user).order_by(Lower("title"), "id")


def note_linked_items(note, user):
    """[{title, url, icon, meta}] of the tasks and events linked to `note` that `user` sees."""
    items = []
    if feature_enabled("tasks"):
        tasks = visible_tasks(user).filter(note=note).order_by("is_done", "due_at", "-id")[:MAX_LINKED_ITEMS]
        items += [
            {
                "title": task.title,
                "url": reverse("tasks"),
                "icon": "fa-regular fa-square-check" if task.is_done else "fa-regular fa-square",
                "meta": "erledigt"
                if task.is_done
                else format_user_date(task.due_at, user)
                if task.due_at
                else "",
            }
            for task in tasks
        ]
    # Same visibility as the calendar: own events plus invitations.
    events = (
        CalendarEvent.objects.filter(Q(user=user) | Q(attendees__user=user), note=note)
        .distinct()
        .order_by("start_at")[:MAX_LINKED_ITEMS]
    )
    items += [
        {
            "title": event.title,
            "url": reverse("calendar_event_detail", args=[event.pk]),
            "icon": "fa-regular fa-calendar",
            "meta": format_user_date(event.start_at, user),
        }
        for event in events
    ]
    return items


def link_event_note(user, event, note_id):
    """Link (or with an empty `note_id`, unlink) the owner's `event` to a note they may open.
    Returns False for a note they can't."""
    if not note_id:
        event.note = None
    else:
        note = linkable_notes(user).filter(pk=note_id).first()
        if note is None:
            return False
        event.note = note
    event.save(update_fields=["note"])
    return True
