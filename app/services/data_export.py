"""Personal data export: everything a user owns in Lunora as one ZIP archive (JSON, Markdown, files)."""

import json
import posixpath
import shutil
import zipfile

from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from django.utils.text import slugify

from app.models import (
    Bookmark,
    CalendarEvent,
    CalendarReminder,
    ChatMessage,
    ChatMessageAttachment,
    Habit,
    Note,
    NoteFolder,
    NoteTemplate,
    Task,
    TaskLabel,
    TaskList,
    UmlDiagram,
    VacationPeriod,
    VacationYear,
    VocabularyList,
)
from app.services.note_markdown import note_markdown_filename, render_note_markdown

README = """Lunora – Datenexport

Dieses Archiv enthält alle Daten, die du in Lunora angelegt hast:

- konto.json: Konto- und Profileinstellungen
- notizen.json, notizen/*.md: Notizen (Original-Dokument und Markdown), Ordner, Vorlagen
- notizen/anhaenge/: Dateien, die an deine Notizen angehängt sind
- aufgaben.json: Aufgaben, Listen und Labels
- kalender.json: deine Termine und Erinnerungen
- nachrichten.json, nachrichten/anhaenge/: Nachrichten und Dateien, die du gesendet hast
- vokabeln.json: Vokabellisten mit Lernfortschritt
- uml/*.json: UML-Diagramme (lassen sich im Editor wieder importieren)
- urlaub.json: Urlaubsjahre, Urlaube und eigene Feiertage
- gewohnheiten.json: Gewohnheiten mit allen abgehakten Tagen
- lesezeichen.json: Lesezeichen vom Dashboard

Erstellt am {created}.
"""

PROFILE_FIELDS = [
    "display_name",
    "theme",
    "accent_color",
    "color_theme",
    "theme_strength",
    "background_softness",
    "date_format",
    "time_format",
    "timezone_name",
    "calendar_view",
    "notify_email",
    "notify_reminders",
    "notify_desktop",
    "notification_quiet_hours_enabled",
    "notification_quiet_start",
    "notification_quiet_end",
    "weekly_summary",
    "weather_default_city",
    "dashboard_layout",
    "note_shortcuts",
]


def export_filename(user, now=None):
    stamp = timezone.localdate(now or timezone.now()).isoformat()
    return f"lunora-export-{slugify(user.get_username()) or 'konto'}-{stamp}.zip"


def write_user_export(user, fileobj):
    """Write the user's export archive into the binary file object."""
    with zipfile.ZipFile(fileobj, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("LIESMICH.txt", README.format(created=timezone.now().isoformat(timespec="seconds")))
        _add_json(archive, "konto.json", _account(user, archive))
        _add_json(archive, "notizen.json", _notes(user, archive))
        _add_json(archive, "aufgaben.json", _tasks(user))
        _add_json(archive, "kalender.json", _calendar(user))
        _add_json(archive, "nachrichten.json", _messages(user, archive))
        _add_json(archive, "vokabeln.json", _vocabulary(user))
        _add_json(archive, "urlaub.json", _vacation(user))
        _add_json(archive, "gewohnheiten.json", _habits(user))
        _add_json(
            archive, "lesezeichen.json", list(Bookmark.objects.filter(owner=user).values("title", "url"))
        )
        for diagram in UmlDiagram.objects.filter(owner=user):
            name = f"uml/{diagram.pk}-{slugify(diagram.title) or 'diagramm'}.json"
            _add_json(archive, name, {"title": diagram.title, "document": diagram.document})


def _add_json(archive, name, data):
    archive.writestr(name, json.dumps(data, cls=DjangoJSONEncoder, ensure_ascii=False, indent=2))


def _add_file(archive, name, field_file):
    """Copy a stored file into the archive; returns the archive path or None when it is missing."""
    try:
        with field_file.open("rb") as source, archive.open(name, "w") as target:
            shutil.copyfileobj(source, target)
    except (FileNotFoundError, OSError, ValueError):
        return None
    return name


def _safe_name(file_id, original_name):
    # basename() drops any directory part a crafted upload name might carry.
    return f"{file_id}-{posixpath.basename(original_name.replace(chr(92), '/')) or 'datei'}"


def _account(user, archive):
    profile = getattr(user, "profile", None)
    data = {
        "username": user.get_username(),
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "date_joined": user.date_joined,
        "last_login": user.last_login,
    }
    if profile is not None:
        data["profile"] = {field: getattr(profile, field) for field in PROFILE_FIELDS}
        data["profile"]["two_factor_enabled"] = profile.totp_enabled_at is not None
        if profile.profile_image:
            extension = posixpath.splitext(profile.profile_image.name)[1]
            data["profile"]["profile_image"] = _add_file(
                archive, f"konto/profilbild{extension}", profile.profile_image
            )
    return data


def _notes(user, archive):
    notes = []
    for note in Note.objects.filter(owner=user).prefetch_related("attachments").order_by("id"):
        markdown_name = f"notizen/{note.pk}-{note_markdown_filename(note)}"
        archive.writestr(markdown_name, render_note_markdown(note))
        attachments = [
            {
                "name": item.original_name,
                "content_type": item.content_type,
                "size": item.size,
                "file": _add_file(
                    archive,
                    f"notizen/anhaenge/{note.pk}/{_safe_name(item.file_id, item.original_name)}",
                    item.file,
                ),
            }
            for item in note.attachments.all()
        ]
        notes.append(
            {
                "id": note.pk,
                "title": note.title,
                "document": note.document,
                "plain_text": note.plain_text,
                "markdown": markdown_name,
                "in_trash": note.deleted_at is not None,
                "created_at": note.created_at,
                "updated_at": note.updated_at,
                "attachments": attachments,
            }
        )
    return {
        "notes": notes,
        "folders": list(NoteFolder.objects.filter(owner=user).values("id", "name", "parent_id")),
        "templates": list(NoteTemplate.objects.filter(owner=user).values("name", "document", "created_at")),
    }


def _tasks(user):
    tasks = Task.objects.filter(user=user).prefetch_related("labels").order_by("id")
    return {
        "lists": list(TaskList.objects.filter(owner=user).values("id", "name", "color")),
        "labels": list(TaskLabel.objects.filter(owner=user).values("id", "name", "color")),
        "tasks": [
            {
                "id": task.pk,
                "title": task.title,
                "due_at": task.due_at,
                "priority": task.priority,
                "recurrence_rule": task.recurrence_rule,
                "is_done": task.is_done,
                "list_id": task.task_list_id,
                "parent_id": task.parent_id,
                "label_ids": [label.pk for label in task.labels.all()],
                "created_at": task.created_at,
            }
            for task in tasks
        ],
    }


def _calendar(user):
    return {
        "events": list(
            CalendarEvent.objects.filter(user=user).values(
                "title",
                "category",
                "description",
                "location",
                "start_at",
                "end_at",
                "is_all_day",
                "recurrence_rule",
            )
        ),
        "reminders": list(
            CalendarReminder.objects.filter(user=user).values("title", "due_at", "is_done", "created_at")
        ),
    }


def _messages(user, archive):
    messages = (
        ChatMessage.objects.filter(sender=user, is_deleted=False)
        .select_related("conversation")
        .prefetch_related("conversation__member_rows__user__profile")
        .order_by("created_at", "id")
    )
    attachment_paths = {
        item.message_id: _add_file(
            archive, f"nachrichten/anhaenge/{_safe_name(item.file_id, item.original_name)}", item.file
        )
        for item in ChatMessageAttachment.objects.filter(message__sender=user, message__is_deleted=False)
    }
    return [
        {
            "conversation_id": message.conversation_id,
            "conversation": message.conversation.display_title_for(user),
            "body": message.body,
            "created_at": message.created_at,
            "attachment": attachment_paths.get(message.pk),
        }
        for message in messages
    ]


def _vocabulary(user):
    return [
        {
            "title": item.title,
            "source_language": item.source_language,
            "target_language": item.target_language,
            "cards": list(
                item.cards.values(
                    "term", "translation", "note", "box", "due_at", "correct_count", "wrong_count"
                )
            ),
        }
        for item in VocabularyList.objects.filter(owner=user)
    ]


def _vacation(user):
    return {
        "years": [
            {
                "year": year.year,
                "allowance_days": year.allowance_days,
                "subdivision": year.subdivision,
                "custom_holidays": list(year.custom_holidays.values("date", "name", "day_value")),
            }
            for year in VacationYear.objects.filter(user=user)
        ],
        "periods": list(
            VacationPeriod.objects.filter(user=user).values("name", "start_date", "end_date", "notes")
        ),
    }


def _habits(user):
    return [
        {
            "title": habit.title,
            "cadence": habit.cadence,
            "color": habit.color,
            "created_at": habit.created_at,
            "checked_days": [check.date for check in habit.checks.all()],
        }
        for habit in Habit.objects.filter(owner=user).prefetch_related("checks")
    ]
