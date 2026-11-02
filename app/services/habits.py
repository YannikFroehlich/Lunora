"""Habit tracker: daily or weekly habits, checked off per day, with streaks."""

from datetime import timedelta

from app.models import Habit, HabitCheck, UserNotification

# How far back the page lets you tick off a day you forgot.
EDITABLE_DAYS = 7
# Checks loaded for streaks; a habit whose streak reaches this far back reloads its whole history.
STREAK_WINDOW_DAYS = 400
WEEKDAY_SHORT = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
# The statistics heatmap: one column per week, ending with the current one (about a year).
HEATMAP_WEEKS = 53


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


def longest_streak(habit, checked_dates):
    """The longest run of consecutive days (daily) or weeks (weekly) among the given checks."""
    if habit.cadence == Habit.CADENCE_WEEKLY:
        periods, step = sorted({_week_start(day) for day in checked_dates}), timedelta(weeks=1)
    else:
        periods, step = sorted(set(checked_dates)), timedelta(days=1)
    best = run = 0
    previous = None
    for period in periods:
        run = run + 1 if previous is not None and period - previous == step else 1
        best = max(best, run)
        previous = period
    return best


def habit_stats(habit, checked_dates, today, streak):
    """Heatmap of the last HEATMAP_WEEKS weeks plus totals, best streak and recent completion rate."""
    first_day = _week_start(today) - timedelta(weeks=HEATMAP_WEEKS - 1)
    in_range = {day for day in checked_dates if first_day <= day <= today}
    if habit.cadence == Habit.CADENCE_WEEKLY:
        recent = {_week_start(today) - timedelta(weeks=offset) for offset in range(12)}
        rate = round(len(recent & {_week_start(day) for day in in_range}) / 12 * 100)
        rate_label = "der letzten 12 Wochen"
    else:
        recent_start = today - timedelta(days=29)
        rate = round(sum(1 for day in in_range if day >= recent_start) / 30 * 100)
        rate_label = "der letzten 30 Tage"
    best = max(longest_streak(habit, in_range), streak)
    return {
        "total": len(in_range),
        "best_streak_label": _streak_label(habit, best),
        "rate": rate,
        "rate_label": rate_label,
        "heatmap": [
            {"date": day, "checked": day in in_range, "is_future": day > today}
            for day in (first_day + timedelta(days=offset) for offset in range(HEATMAP_WEEKS * 7))
        ],
    }


def _streak_reaches(habit, streak, today, window_start):
    """Whether a streak counted from checks since window_start may continue before it (one period
    of slack, since an open today shifts the count back by one)."""
    window_days = (today - window_start).days
    if habit.cadence == Habit.CADENCE_WEEKLY:
        return streak >= window_days // 7 - 1
    return streak >= window_days - 1


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
        # Done now, so an unread "still open" reminder is no longer true.
        UserNotification.objects.filter(
            recipient=user, source_key__startswith=f"habit:{habit.pk}:", read_at__isnull=True
        ).delete()
    return habit


def habit_items(user, today):
    """Template rows for every habit: last week of days, streak and whether it's done for now."""
    habits = list(Habit.objects.filter(owner=user))
    window_start = _week_start(today - timedelta(days=STREAK_WINDOW_DAYS))
    checks = {}
    for habit_id, day in HabitCheck.objects.filter(habit__owner=user, date__gte=window_start).values_list(
        "habit_id", "date"
    ):
        checks.setdefault(habit_id, set()).add(day)
    days = [today - timedelta(days=offset) for offset in range(EDITABLE_DAYS - 1, -1, -1)]
    items = []
    for habit in habits:
        checked = checks.get(habit.pk, set())
        streak = current_streak(habit, checked, today)
        if _streak_reaches(habit, streak, today, window_start):
            full_history = set(HabitCheck.objects.filter(habit=habit).values_list("date", flat=True))
            streak = current_streak(habit, full_history, today)
        items.append(
            {
                "id": habit.pk,
                "title": habit.title,
                "color": habit.color,
                "cadence": habit.cadence,
                "cadence_label": habit.get_cadence_display(),
                "reminder_time": habit.reminder_time,
                "streak": streak,
                "streak_label": _streak_label(habit, streak),
                "done_now": is_done_now(habit, checked, today),
                "stats": habit_stats(habit, checked, today, streak),
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
