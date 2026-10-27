"""Habit tracker: daily or weekly habits, checked off per day, with streaks."""

from datetime import timedelta

from app.models import Habit, HabitCheck

# How far back the page lets you tick off a day you forgot.
EDITABLE_DAYS = 7
WEEKDAY_SHORT = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def _week_start(day):
    return day - timedelta(days=day.weekday())


def is_done_now(habit, checked_dates, today):
    """Daily: checked today. Weekly: checked on any day of the current week."""
    if habit.cadence == Habit.CADENCE_WEEKLY:
        return any(_week_start(day) == _week_start(today) for day in checked_dates)
    return today in checked_dates


def current_streak(habit, checked_dates, today):
    """Consecutive days (daily) or weeks (weekly) ending today; an open today doesn't break it yet."""
    if habit.cadence == Habit.CADENCE_WEEKLY:
        periods = {_week_start(day) for day in checked_dates}
        current, step = _week_start(today), timedelta(weeks=1)
    else:
        periods = set(checked_dates)
        current, step = today, timedelta(days=1)
    if current not in periods:
        current -= step
    streak = 0
    while current in periods:
        streak += 1
        current -= step
    return streak


def _streak_label(habit, streak):
    if habit.cadence == Habit.CADENCE_WEEKLY:
        return f"{streak} {'Woche' if streak == 1 else 'Wochen'}"
    return f"{streak} {'Tag' if streak == 1 else 'Tage'}"


def toggle_habit_check(user, habit_id, day, today):
    """Flip the check for one day; only today and the EDITABLE_DAYS before it can be changed."""
    habit = Habit.objects.filter(owner=user, pk=habit_id).first()
    if habit is None or not today - timedelta(days=EDITABLE_DAYS - 1) <= day <= today:
        return None
    deleted, _details = HabitCheck.objects.filter(habit=habit, date=day).delete()
    if not deleted:
        HabitCheck.objects.create(habit=habit, date=day)
    return habit


def habit_items(user, today):
    """Template rows for every habit: last week of days, streak and whether it's done for now."""
    habits = list(Habit.objects.filter(owner=user))
    # ponytail: loads every check for the streak; cap by date if users keep habits for many years.
    checks = {}
    for habit_id, day in HabitCheck.objects.filter(habit__owner=user).values_list("habit_id", "date"):
        checks.setdefault(habit_id, set()).add(day)
    days = [today - timedelta(days=offset) for offset in range(EDITABLE_DAYS - 1, -1, -1)]
    items = []
    for habit in habits:
        checked = checks.get(habit.pk, set())
        streak = current_streak(habit, checked, today)
        items.append(
            {
                "id": habit.pk,
                "title": habit.title,
                "color": habit.color,
                "cadence": habit.cadence,
                "cadence_label": habit.get_cadence_display(),
                "streak": streak,
                "streak_label": _streak_label(habit, streak),
                "done_now": is_done_now(habit, checked, today),
                "days": [
                    {
                        "date": day.isoformat(),
                        "label": WEEKDAY_SHORT[day.weekday()],
                        "number": day.day,
                        "checked": day in checked,
                        "is_today": day == today,
                    }
                    for day in days
                ],
            }
        )
    return items
