"""Personal data export: everything a user owns in Lunora as one ZIP archive (JSON, Markdown, files)."""

import json
import os
import posixpath
import shutil
import tempfile
import zipfile

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from django.utils.text import slugify

from app.models import (
    Bookmark,
    CalendarEvent,
    CalendarEventComment,
    CalendarReminder,
    CalendarSubscription,
    ChatMessage,
    ChatMessageAttachment,
    ExpenseGroup,
    Habit,
    MealPlanEntry,
    Note,
    NoteFolder,
    NoteTemplate,
    Recipe,
    ScheduledChatMessage,
    ShoppingItem,
    ShoppingListShare,
    ShoppingProduct,
    Task,
    TaskLabel,
    TaskList,
    TaskTemplate,
    TaskTimeEntry,
    TrashedItem,
    UmlDiagram,
    VacationPeriod,
    VacationYear,
    VocabularyList,
    Whiteboard,
)
from app.services.note_markdown import note_markdown_filename, render_note_markdown
from app.services.notes import display_name

README = """Lunora – Datenexport

Dieses Archiv enthält alle Daten, die du in Lunora angelegt hast:

- konto.json: Konto- und Profileinstellungen
- notizen.json, notizen/*.md: Notizen (Original-Dokument und Markdown), Ordner, Vorlagen
- notizen/anhaenge/: Dateien, die an deine Notizen angehängt sind
- aufgaben.json: Aufgaben, Listen, Labels und deine erfasste Zeit
- kalender.json: deine Termine, Erinnerungen und Kommentare an Terminen
- papierkorb.json: Gelöschtes (Aufgaben, Termine, Gewohnheiten, Vokabellisten, Rezepte), das noch wiederhergestellt werden kann
- nachrichten.json, nachrichten/anhaenge/: Nachrichten (auch geplante und Umfragen) und Dateien, die du gesendet hast
- vokabeln.json: Vokabellisten mit Lernfortschritt
- uml/*.json: UML-Diagramme (lassen sich im Editor wieder importieren)
- whiteboards/*.json: Whiteboards (lassen sich im Editor wieder importieren)
- urlaub.json: Urlaubsjahre, Urlaube und eigene Feiertage
- gewohnheiten.json: Gewohnheiten mit allen abgehakten Tagen
- lesezeichen.json: Lesezeichen vom Dashboard
- einkaufszettel.json: Artikel auf deinem Einkaufszettel
- rezepte.json, wochenplan.json: Rezepte und geplante Gerichte
- ausgaben.json: Gruppen mit geteilten Ausgaben, Personen mit Anzeigenamen

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
    "login_alerts",
    "weather_default_city",
    "dashboard_layout",
    "note_shortcuts",
]


def export_temp_file():
    """An anonymous file on the same disk as the uploads: /tmp is a RAM disk on the server."""
    os.makedirs(settings.PRIVATE_MEDIA_ROOT, exist_ok=True)
    return tempfile.TemporaryFile(dir=settings.PRIVATE_MEDIA_ROOT)


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
        _add_json(archive, "papierkorb.json", _trash(user))
        _add_json(archive, "nachrichten.json", _messages(user, archive))
        _add_json(archive, "vokabeln.json", _vocabulary(user))
        _add_json(archive, "urlaub.json", _vacation(user))
        _add_json(archive, "gewohnheiten.json", _habits(user))
        _add_json(
            archive, "lesezeichen.json", list(Bookmark.objects.filter(owner=user).values("title", "url"))
        )
        _add_json(
            archive,
            "einkaufszettel.json",
            list(
                ShoppingItem.objects.filter(owner=user).values(
                    "name", "quantity", "is_done", "barcode", "created_at"
                )
            ),
        )
        _add_json(
            archive,
            "einkaufszettel-freigaben.json",
            [
                share.user.get_username()
                for share in ShoppingListShare.objects.filter(owner=user).select_related("user")
            ],
        )
        _add_json(
            archive,
            "einkaufsprodukte.json",
            list(
                ShoppingProduct.objects.filter(owner=user).values(
                    "barcode", "name", "brand", "package", "nutri_score", "nutrition", "created_at"
                )
            ),
        )
        _add_json(
            archive,
            "rezepte.json",
            list(
                Recipe.objects.filter(owner=user).values(
                    "title",
                    "servings",
                    "ingredients",
                    "instructions",
                    "source_url",
                    "created_at",
                    "updated_at",
                )
            ),
        )
        _add_json(
            archive,
            "wochenplan.json",
            [
                {"date": entry.date, "recipe": entry.recipe.title, "servings": entry.servings}
                for entry in MealPlanEntry.objects.filter(owner=user).select_related("recipe")
            ],
        )
        _add_json(archive, "ausgaben.json", _expense_groups(user))
        for diagram in UmlDiagram.objects.filter(owner=user):
            name = f"uml/{diagram.pk}-{slugify(diagram.title) or 'diagramm'}.json"
            _add_json(archive, name, {"title": diagram.title, "document": diagram.document})
        for board in Whiteboard.objects.filter(owner=user):
            name = f"whiteboards/{board.pk}-{slugify(board.title) or 'whiteboard'}.json"
            _add_json(
                archive,
                name,
                {"format": "lunora-whiteboard", "title": board.title, "document": board.document},
            )


def _expense_groups(user):
    """The user's expense groups with every expense, people by display name (others' e-mails stay out)."""
    groups = []
    for group in ExpenseGroup.objects.filter(members__user=user).prefetch_related(
        "members__user__profile", "expenses__shares__user__profile", "expenses__paid_by__profile"
    ):
        groups.append(
            {
                "name": group.name,
                "members": [display_name(member.user) for member in group.members.all()],
                "expenses": [
                    {
                        "title": expense.title,
                        "category": expense.category,
                        "amount": expense.amount,
                        "spent_on": expense.spent_on,
                        "is_settlement": expense.is_settlement,
                        "paid_by": display_name(expense.paid_by) if expense.paid_by else None,
                        "shares": [
                            {
                                "person": display_name(share.user) if share.user else None,
                                "amount": share.amount,
                            }
                            for share in expense.shares.all()
                        ],
                    }
                    for expense in group.expenses.all()
                ],
                "monthly": [
                    {
                        "title": rule.title,
                        "amount": rule.amount,
                        "day_of_month": rule.day_of_month,
                        "next_on": rule.next_on,
                        "paid_by": display_name(rule.paid_by) if rule.paid_by else None,
                    }
                    for rule in group.recurring_expenses.select_related("paid_by__profile")
                ],
            }
        )
    return groups


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
        data["profile"]["passkeys"] = list(user.passkeys.values("name", "created_at", "last_used_at"))
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
    tasks = (
        Task.objects.filter(user=user)
        .prefetch_related("labels", "comments__author", "shares__user")
        .order_by("id")
    )
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
                "recurrence_interval": task.recurrence_interval,
                "recurrence_weekdays": task.recurrence_weekdays,
                "is_done": task.is_done,
                "is_in_progress": task.is_in_progress,
                "shared_with": [share.user.get_username() for share in task.shares.all()],
                "list_id": task.task_list_id,
                "parent_id": task.parent_id,
                "label_ids": [label.pk for label in task.labels.all()],
                "created_at": task.created_at,
                "comments": [
                    {
                        "author": comment.author.get_username(),
                        "body": comment.body,
                        "created_at": comment.created_at,
                        "edited_at": comment.edited_at,
                    }
                    for comment in task.comments.all()
                ],
            }
            for task in tasks
        ],
        # Own tracked time, also on tasks of others (shared or assigned).
        "time_entries": [
            {
                "task_id": entry.task_id,
                "task": entry.task.title,
                "started_at": entry.started_at,
                "ended_at": entry.ended_at,
                "seconds": entry.duration_seconds(),
            }
            for entry in TaskTimeEntry.objects.filter(user=user).select_related("task").order_by("started_at")
        ],
        "templates": [
            {
                "title": template.title,
                "priority": template.priority,
                "list_id": template.task_list_id,
                "label_ids": [label.pk for label in template.labels.all()],
                "subtasks": template.subtask_titles,
            }
            for template in TaskTemplate.objects.filter(owner=user).prefetch_related("labels")
        ],
    }


def _calendar(user):
    return {
        "events": list(
            # Events of subscribed calendars are copies of someone else's data, re-fetched on every sync.
            CalendarEvent.objects.filter(user=user, subscription=None).values(
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
        # The address stays out: it is a credential that reads the external calendar.
        "subscriptions": list(
            CalendarSubscription.objects.filter(user=user).values("name", "color", "icon", "created_at")
        ),
        "comments": [
            {"event": comment.event.title, "body": comment.body, "created_at": comment.created_at}
            for comment in CalendarEventComment.objects.filter(author=user).select_related("event")
        ],
    }


TRASH_EXPORT_FIELDS = (
    "title",
    "description",
    "location",
    "start_at",
    "end_at",
    "is_all_day",
    "due_at",
    "is_done",
    "cadence",
    "source_language",
    "target_language",
    "servings",
    "ingredients",
    "instructions",
)
TRASH_EXPORT_MODELS = {"app.task", "app.calendarevent", "app.habit", "app.vocabularylist", "app.recipe"}


def _trash(user):
    # Only the deleted things themselves: the snapshot also holds other people's ids.
    return [
        {
            "kind": item.kind,
            "deleted_at": item.deleted_at,
            "items": [
                {name: row["fields"][name] for name in TRASH_EXPORT_FIELDS if name in row["fields"]}
                for row in item.payload
                if row.get("model") in TRASH_EXPORT_MODELS
            ],
        }
        for item in TrashedItem.objects.filter(user=user)
    ]


def _messages(user, archive):
    messages = (
        ChatMessage.objects.filter(sender=user, is_deleted=False)
        .select_related("conversation", "poll")
        .prefetch_related("conversation__member_rows__user__profile", "poll__options__votes")
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
            "forwarded": message.is_forwarded,
            "created_at": message.created_at,
            "edited_at": message.edited_at,
            "attachment": attachment_paths.get(message.pk),
            "poll": _poll_export(message),
        }
        for message in messages
    ] + [
        {
            "conversation_id": scheduled.conversation_id,
            "conversation": scheduled.conversation.display_title_for(user),
            "body": scheduled.body,
            "scheduled_for": scheduled.send_at,
            "created_at": scheduled.created_at,
        }
        for scheduled in ScheduledChatMessage.objects.filter(sender=user)
        .select_related("conversation")
        .prefetch_related("conversation__member_rows__user__profile")
    ]


def _poll_export(message):
    poll = getattr(message, "poll", None)
    if poll is None:
        return None
    return {
        "question": poll.question,
        "allows_multiple": poll.allows_multiple,
        "closed_at": poll.closed_at,
        "options": [{"text": option.text, "votes": len(option.votes.all())} for option in poll.options.all()],
    }


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
            "reminder_time": habit.reminder_time,
            "created_at": habit.created_at,
            "checked_days": [check.date for check in habit.checks.all()],
        }
        for habit in Habit.objects.filter(owner=user).prefetch_related("checks")
    ]
