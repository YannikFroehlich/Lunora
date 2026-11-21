"""Lunora Wrapped: a user's year in numbers, shown as slides at /wrapped/.

Everything is counted from data that already exists, within the calendar year in the user's own
time zone, and only for areas whose feature flag is on; an area with nothing to show gets no slide.
Only the user's own activity counts (their tasks, notes, messages …), never other people's.
"""

from collections import Counter
from datetime import datetime

from django.db.models import Q, Sum

from app.models import (
    CalendarEvent,
    ChatMessage,
    Expense,
    ExpenseShare,
    Habit,
    HabitCheck,
    KanbanBoard,
    MealPlanEntry,
    Note,
    Task,
    TaskTimeEntry,
    UmlDiagram,
    VocabularyCard,
    Whiteboard,
    WishListReservation,
)
from app.services.expenses import euro
from app.services.habits import longest_streak
from app.services.system_settings import feature_enabled
from app.services.user_preferences import localtime_for_user
from app.services.vocabulary import MAX_BOX

MONTHS = [
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
]


def number(value):
    return f"{value:,}".replace(",", ".")


def year_bounds(user, year):
    zone = localtime_for_user(profile_or_user=user).tzinfo
    return datetime(year, 1, 1, tzinfo=zone), datetime(year + 1, 1, 1, tzinfo=zone)


def available_years(user, current_year):
    """The years since the account exists, newest first."""
    first = min(localtime_for_user(user.date_joined, user).year, current_year)
    return list(range(current_year, first - 1, -1))


def _slide(key, icon, kicker, value, label, lines=()):
    return {"key": key, "icon": icon, "kicker": kicker, "value": value, "label": label, "lines": list(lines)}


def _tasks(user, start, end):
    in_year = {"updated_at__gte": start, "updated_at__lt": end}
    # ponytail: tasks keep no completion time, so "done this year" means done and last changed this
    # year; add a completed_at column if this has to be exact.
    done = Task.objects.filter(
        Q(user=user, assignee__isnull=True) | Q(assignee=user), is_done=True, **in_year
    )
    count = done.count()
    if not count:
        return []
    created = Task.objects.filter(user=user, created_at__gte=start, created_at__lt=end).count()
    return [
        _slide(
            "tasks",
            "fa-list-check",
            "Aufgaben",
            number(count),
            "Aufgaben erledigt",
            [f"{number(created)} neu angelegt"],
        )
    ]


def _time(user, start, end):
    totals = Counter()
    entries = TaskTimeEntry.objects.filter(
        user=user, started_at__gte=start, started_at__lt=end, ended_at__isnull=False
    )
    for title, started, ended in entries.values_list("task__title", "started_at", "ended_at"):
        totals[title] += (ended - started).total_seconds()
    seconds = sum(totals.values())
    if seconds < 60:
        return []
    hours = seconds / 3600
    top, top_seconds = totals.most_common(1)[0]
    value = f"{number(round(hours))} h" if hours >= 10 else f"{hours:.1f} h".replace(".", ",")
    lines = [f"Am längsten: {top} ({top_seconds / 3600:.1f} h)".replace(".", ",")]
    return [_slide("time", "fa-stopwatch", "Zeit", value, "konzentriert an Aufgaben gearbeitet", lines)]


def _habits(user, year):
    checks = {}
    for habit_id, day in HabitCheck.objects.filter(habit__owner=user, date__year=year).values_list(
        "habit_id", "date"
    ):
        checks.setdefault(habit_id, []).append(day)
    total = sum(len(days) for days in checks.values())
    if not total:
        return []
    habits = Habit.objects.in_bulk(list(checks))
    streaks = {habit_id: longest_streak(habits[habit_id], days) for habit_id, days in checks.items()}
    best_id = max(streaks, key=streaks.get)
    favorite_id = max(checks, key=lambda habit_id: len(checks[habit_id]))
    unit = "Wochen" if habits[best_id].cadence == Habit.CADENCE_WEEKLY else "Tage"
    lines = [
        f"Längste Serie: {streaks[best_id]} {unit} – {habits[best_id].title}",
        f"Am treuesten: {habits[favorite_id].title} ({number(len(checks[favorite_id]))}×)",
    ]
    return [_slide("habits", "fa-seedling", "Gewohnheiten", number(total), "Mal abgehakt", lines)]


def _notes(user, start, end, year):
    notes = Note.objects.filter(
        owner=user, deleted_at__isnull=True, created_at__gte=start, created_at__lt=end
    )
    texts = list(notes.values_list("plain_text", flat=True))
    if not texts:
        return []
    words = sum(len(text.split()) for text in texts)
    lines = [f"{number(words)} Wörter"]
    daily = Note.objects.filter(owner=user, daily_date__year=year).count()
    if daily:
        lines.append(f"{number(daily)} Tagesnotizen")
    return [_slide("notes", "fa-note-sticky", "Notizen", number(len(texts)), "Notizen geschrieben", lines)]


def _calendar(user, start, end):
    starts = list(
        CalendarEvent.objects.filter(
            user=user, subscription=None, start_at__gte=start, start_at__lt=end
        ).values_list("start_at", flat=True)
    )
    if not starts:
        return []
    months = Counter(localtime_for_user(value, user).month for value in starts)
    month, count = months.most_common(1)[0]
    lines = [f"Vollster Monat: {MONTHS[month - 1]} ({number(count)})"]
    return [
        _slide("calendar", "fa-calendar-days", "Kalender", number(len(starts)), "Termine eingetragen", lines)
    ]


def _messages(user, start, end):
    sent = ChatMessage.objects.filter(
        sender=user, is_deleted=False, created_at__gte=start, created_at__lt=end
    )
    count = sent.count()
    if not count:
        return []
    chats = sent.values("conversation_id").distinct().count()
    return [
        _slide(
            "messages",
            "fa-comments",
            "Nachrichten",
            number(count),
            "Nachrichten geschrieben",
            [f"in {number(chats)} Chats"],
        )
    ]


def _meals(user, year):
    titles = list(
        MealPlanEntry.objects.filter(owner=user, date__year=year).values_list("recipe__title", flat=True)
    )
    if not titles:
        return []
    favorite, times = Counter(titles).most_common(1)[0]
    lines = [f"Am liebsten: {favorite} ({number(times)}×)"]
    return [_slide("meals", "fa-utensils", "Essen", number(len(titles)), "Mahlzeiten geplant", lines)]


def _expenses(user, year):
    shares = ExpenseShare.objects.filter(
        user=user, expense__is_settlement=False, expense__spent_on__year=year
    )
    total = shares.aggregate(total=Sum("amount"))["total"]
    if not total:
        return []
    paid = Expense.objects.filter(paid_by=user, is_settlement=False, spent_on__year=year).count()
    return [
        _slide(
            "expenses",
            "fa-scale-balanced",
            "Geteilte Ausgaben",
            euro(total),
            "dein Anteil an gemeinsamen Ausgaben",
            [f"{number(paid)} Ausgaben selbst bezahlt"],
        )
    ]


def _vocabulary(user, start, end):
    cards = VocabularyCard.objects.filter(
        vocabulary_list__owner=user, last_reviewed_at__gte=start, last_reviewed_at__lt=end
    )
    count = cards.count()
    if not count:
        return []
    mastered = cards.filter(box=MAX_BOX).count()
    return [
        _slide(
            "vocabulary",
            "fa-language",
            "Vokabeln",
            number(count),
            "Karten geübt",
            [f"{number(mastered)} im obersten Fach"],
        )
    ]


def _boards(user, start, end):
    in_year = {"owner": user, "created_at__gte": start, "created_at__lt": end}
    counts = [
        (Whiteboard.objects.filter(**in_year).count(), "Whiteboards"),
        (KanbanBoard.objects.filter(**in_year).count(), "Kanban-Boards"),
        (UmlDiagram.objects.filter(**in_year).count(), "UML-Diagramme"),
    ]
    total = sum(count for count, _label in counts)
    if not total:
        return []
    lines = [f"{number(count)} {label}" for count, label in counts if count]
    return [
        _slide("boards", "fa-pen-ruler", "Kreativ", number(total), "Boards und Diagramme angelegt", lines)
    ]


def _gifts(user, start, end):
    count = WishListReservation.objects.filter(user=user, created_at__gte=start, created_at__lt=end).count()
    if not count:
        return []
    return [_slide("gifts", "fa-gift", "Geschenke", number(count), "Geschenke von Wunschlisten reserviert")]


def year_in_review(user, year):
    """The slides for `year`, in a fixed order; empty when there is nothing to show."""
    start, end = year_bounds(user, year)
    slides = []
    if feature_enabled("tasks"):
        slides += _tasks(user, start, end) + _time(user, start, end)
    if feature_enabled("notes"):
        slides += _notes(user, start, end, year)
    slides += _calendar(user, start, end)
    if feature_enabled("messages"):
        slides += _messages(user, start, end)
    if feature_enabled("tools"):
        slides += (
            _habits(user, year)
            + _meals(user, year)
            + _expenses(user, year)
            + _vocabulary(user, start, end)
            + _boards(user, start, end)
            + _gifts(user, start, end)
        )
    return slides
