import calendar
from datetime import datetime, time, timedelta

from django.utils import timezone

from app.models import CalendarEvent, CalendarReminder
from app.services.weather_service import get_weather_context


def get_dashboard_context(user=None):
    now = timezone.localtime()
    weekday_names = [
        "Montag",
        "Dienstag",
        "Mittwoch",
        "Donnerstag",
        "Freitag",
        "Samstag",
        "Sonntag",
    ]
    month_names = [
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
    dashboard_weather = _dashboard_weather_context()

    return {
        "active_page": "home",
        "today_label": now.strftime("%d.%m.%Y"),
        "time_label": now.strftime("%H:%M"),
        "dashboard_weather": dashboard_weather,
        "clock": {
            "time": now.strftime("%H:%M"),
            "weekday": weekday_names[now.weekday()],
            "day": now.strftime("%d"),
            "month": month_names[now.month - 1],
            "year": now.strftime("%Y"),
            "timezone": timezone.get_current_timezone_name(),
        },
        "nav_tiles": [
            {"label": "Dashboard", "icon": "fa-table-cells-large", "url_name": "home"},
            {"label": "Wetter", "icon": "fa-cloud-sun", "url_name": "weather"},
            {"label": "Kalender", "icon": "fa-calendar-days", "url_name": "calendar"},
            {"label": "Projekte", "icon": "fa-folder", "url_name": "home"},
            {"label": "Nachrichten", "icon": "fa-message", "url_name": "messages"},
        ],
        "recent_tools": [
            {"title": "Notizen", "subtitle": "Weiter schreiben", "icon": "fa-note-sticky"},
            {"title": "Planer", "subtitle": "Termine ansehen", "icon": "fa-calendar-check"},
            {"title": "Dateien", "subtitle": "Zuletzt geöffnet", "icon": "fa-folder-open"},
            {"title": "Analysen", "subtitle": "Einblicke ansehen", "icon": "fa-chart-simple"},
        ],
        "upcoming_dashboard_events": _dashboard_upcoming_events(user, now) if user else [],
        "legacy_notes": [
            {"text": "Projektbrief prüfen", "done": True},
            {"text": "Landingpage designen", "done": False},
            {"text": "Präsentation vorbereiten", "done": False},
        ],
    }


def _dashboard_weather_context():
    weather_context = get_weather_context({})
    current = weather_context.get("current", {})
    daily_forecast = weather_context.get("daily_forecast") or []
    tomorrow = daily_forecast[0] if daily_forecast else {}

    return {
        "today": {
            "temperature": current.get("temperature", 24),
            "feels_like": current.get("feels_like", current.get("temperature", 24)),
            "description": current.get("description", "Teilweise bewölkt"),
            "icon": current.get("icon", "fa-cloud-sun"),
        },
        "tomorrow": {
            "day": tomorrow.get("day", "Morgen"),
            "high": tomorrow.get("high", current.get("high", current.get("temperature", 24))),
            "low": tomorrow.get("low", current.get("low", current.get("temperature", 18))),
            "rain": tomorrow.get("rain", 10),
            "description": tomorrow.get("description", "Teilweise bewölkt"),
            "icon": tomorrow.get("icon", "fa-cloud-sun"),
        },
    }


def get_settings_context():
    return {
        "active_page": "settings",
        "accent_colors": ["#c2a276", "#7f916b", "#a5aa74", "#9eb1b6", "#aaa2be", "#c1a09a"],
        "region_rows": [
            {"label": "Sprache", "value": "Deutsch"},
            {"label": "Datumsformat", "value": "25. Juni 2026"},
            {"label": "Zeitformat", "value": "24-Stunden"},
            {"label": "Zeitzone", "value": "Europe/Berlin"},
        ],
        "notification_rows": [
            {"label": "E-Mail Benachrichtigungen", "hint": "Wichtige Updates erhalten"},
            {"label": "Erinnerungen", "hint": "Aufgaben und Termine im Blick behalten"},
            {"label": "Desktop Hinweise", "hint": "Benachrichtigungen auf diesem Gerät"},
            {"label": "Wöchentliche Zusammenfassung", "hint": "Kurzer Rückblick per E-Mail"},
        ],
    }


def get_calendar_context():
    return {
        "active_page": "calendar",
        "calendar_rows": [
            [
                {"number": "29", "muted": True, "events": []},
                {"number": "30", "muted": True, "events": []},
                {"number": "1", "events": []},
                {"number": "2", "events": []},
                {"number": "3", "events": []},
                {"number": "4", "events": []},
                {"number": "5", "events": []},
            ],
            [
                {"number": "6", "events": []},
                {"number": "7", "events": [{"label": "Team-Meeting", "tone": "blue"}]},
                {"number": "8", "events": []},
                {"number": "9", "events": [{"label": "Workout", "tone": "green"}]},
                {"number": "10", "events": []},
                {"number": "11", "events": [{"label": "Abendessen", "tone": "red"}]},
                {"number": "12", "events": []},
            ],
            [
                {"number": "13", "events": [{"label": "Arzttermin", "tone": "sand"}]},
                {"number": "14", "events": []},
                {"number": "15", "events": [{"label": "Projektabgabe", "tone": "violet"}]},
                {"number": "16", "events": []},
                {"number": "17", "events": []},
                {"number": "18", "events": [{"label": "Geburtstag", "tone": "red"}]},
                {"number": "19", "events": []},
            ],
            [
                {"number": "20", "events": []},
                {"number": "21", "today": True, "events": [{"label": "Call mit Kunde", "tone": "blue"}, {"label": "Fokuszeit", "tone": "sand"}]},
                {"number": "22", "events": []},
                {"number": "23", "events": [{"label": "Workout", "tone": "green"}]},
                {"number": "24", "events": []},
                {"number": "25", "events": [{"label": "Abendessen", "tone": "red"}]},
                {"number": "26", "events": []},
            ],
            [
                {"number": "27", "events": []},
                {"number": "28", "events": [{"label": "Team-Meeting", "tone": "blue"}]},
                {"number": "29", "events": []},
                {"number": "30", "events": []},
                {"number": "31", "events": [{"label": "Monatsabschluss", "tone": "sand"}]},
                {"number": "1", "muted": True, "events": []},
                {"number": "2", "muted": True, "events": []},
            ],
        ],
        "today_events": [
            {"time": "09:00", "title": "Team-Meeting", "icon": "fa-users", "tone": "blue"},
            {"time": "11:30", "title": "Call mit Kunde", "icon": "fa-phone", "tone": "blue"},
            {"time": "14:00", "title": "Fokuszeit", "icon": "fa-clock", "tone": "sand"},
            {"time": "18:30", "title": "Abendessen", "icon": "fa-utensils", "tone": "red"},
        ],
        "upcoming_events": [
            {"date": "Mi, 22. Mai", "title": "Workout", "category": "Gesundheit", "icon": "fa-dumbbell"},
            {"date": "Do, 23. Mai", "title": "Projektbesprechung", "category": "Arbeit", "icon": "fa-briefcase"},
            {"date": "Sa, 25. Mai", "title": "Abendessen mit Lisa", "category": "Privat", "icon": "fa-user-group"},
            {"date": "Mo, 27. Mai", "title": "Arzttermin Kontrolluntersuchung", "category": "Gesundheit", "icon": "fa-stethoscope"},
            {"date": "Di, 28. Mai", "title": "Team-Meeting", "category": "Arbeit", "icon": "fa-users"},
        ],
        "reminders": [
            "Rechnung bezahlen",
            "Geschenk für Geburtstagsfeier kaufen",
            "Unterlagen für Steuererklärung hochladen",
        ],
    }


def get_messages_context():
    return {
        "active_page": "messages",
        "contacts": [
            {"name": "Mia Berger", "preview": "Alles klar, danke dir! Ich schaue es mir an.", "time": "10:32", "unread": 2, "avatar": "MB", "active": True},
            {"name": "Team Design", "preview": "Lukas: Neue Mockups sind online.", "time": "09:48", "unread": 1, "avatar": "TD"},
            {"name": "Projekt Gruppe", "preview": "Anna: Können wir das morgen besprechen?", "time": "Gestern", "unread": 0, "avatar": "PG"},
            {"name": "Anna Schulz", "preview": "Danke für die schnelle Rückmeldung!", "time": "Gestern", "unread": 0, "avatar": "AS"},
            {"name": "Marketing Team", "preview": "Julian: Kampagnenplan ist aktualisiert.", "time": "Mo", "unread": 0, "avatar": "MT"},
            {"name": "Paul Weber", "preview": "Klingt gut, ich melde mich später.", "time": "Mo", "unread": 0, "avatar": "PW"},
        ],
        "messages": [
            {"text": "Hey, hast du dir die Präsentation schon angesehen?", "time": "10:21", "side": "in"},
            {"text": "Ja, sieht super aus! Besonders die neuen Grafiken gefallen mir.", "time": "10:22", "side": "out"},
            {"text": "Freut mich!", "time": "10:22", "side": "in"},
            {"text": "Könntest du noch einen Blick auf Folie 12 werfen? Da bin ich mir bei der Formulierung unsicher.", "time": "10:23", "side": "in"},
            {"text": "Klar, ich schaue es mir direkt an.", "time": "10:24", "side": "out"},
            {"text": "Präsentation_v2.pptx", "time": "10:25", "side": "out", "attachment": "2.4 MB - PPTX"},
            {"text": "Danke dir!", "time": "10:26", "side": "in"},
            {"text": "Ich habe ein paar Anmerkungen hinzugefügt. Schau mal, ob das für dich passt.", "time": "10:29", "side": "out"},
            {"text": "Präsentation_v2_Review.pptx", "time": "10:29", "side": "out", "attachment": "2.7 MB - PPTX"},
            {"text": "Alles klar, danke dir! Ich schaue es mir an.", "time": "10:32", "side": "in"},
        ],
        "shared_files": [
            {"name": "Präsentation_v2.pptx", "meta": "2.4 MB - heute, 10:25", "icon": "fa-file-powerpoint"},
            {"name": "Moodboard_2024.pdf", "meta": "5.1 MB - Gestern, 16:42", "icon": "fa-file-pdf"},
            {"name": "Design_System.sketch", "meta": "12.3 MB - 12. Mai 2024", "icon": "fa-file-lines"},
        ],
        "members": [
            {"name": "Mia Berger (Du)", "status": "Online", "avatar": "MB"},
            {"name": "Lukas Meier", "status": "Online", "avatar": "LM"},
            {"name": "Anna Schulz", "status": "Zuletzt aktiv: Gestern", "avatar": "AS"},
        ],
    }


def _dashboard_upcoming_events(user, now):
    events = CalendarEvent.objects.filter(user=user, end_at__gte=now).select_related("source").order_by("start_at")[:5]
    return [
        {
            "title": event.title,
            "date": timezone.localtime(event.start_at).strftime("%d.%m.%Y"),
            "time": "Ganztagig" if event.is_all_day else timezone.localtime(event.start_at).strftime("%H:%M"),
            "tone": event.tone,
        }
        for event in events
    ]


def get_calendar_context(user, *, year=None, month=None):
    now = timezone.localtime()
    try:
        year = int(year or now.year)
        month = int(month or now.month)
        if month < 1 or month > 12:
            raise ValueError
    except (TypeError, ValueError):
        year = now.year
        month = now.month
    month_date = now.date().replace(year=year, month=month, day=1)
    month_names = [
        "Januar",
        "Februar",
        "Maerz",
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
    weekday_names = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    visible_start = weeks[0][0]
    visible_end = weeks[-1][-1] + timedelta(days=1)
    range_start = timezone.make_aware(datetime.combine(visible_start, time.min))
    range_end = timezone.make_aware(datetime.combine(visible_end, time.min))
    events = list(
        CalendarEvent.objects.filter(user=user, start_at__lt=range_end, end_at__gt=range_start)
        .select_related("source")
        .order_by("start_at", "title")
    )
    events_by_date = _group_calendar_events_by_date(events, visible_start, visible_end)

    rows = []
    for week in weeks:
        row = []
        for day in week:
            day_events = events_by_date.get(day, [])
            row.append(
                {
                    "number": str(day.day),
                    "muted": day.month != month,
                    "today": day == now.date(),
                    "events": [
                        {
                            "label": event.title,
                            "tone": event.tone,
                            "time": _calendar_event_time_label(event),
                        }
                        for event in day_events[:3]
                    ],
                    "overflow": max(0, len(day_events) - 3),
                }
            )
        rows.append(row)

    today_events = [
        {
            "time": _calendar_event_time_label(event),
            "title": event.title,
            "icon": "fa-calendar-day",
            "tone": event.tone,
        }
        for event in events_by_date.get(now.date(), [])
    ]
    upcoming_events = _upcoming_calendar_events(user, now)
    month_events = [
        event
        for event in events
        if timezone.localtime(event.start_at).date().year == year
        and timezone.localtime(event.start_at).date().month == month
    ]
    days_in_month = [month_date.replace(day=day) for day in range(1, calendar.monthrange(year, month)[1] + 1)]
    busy_days = {timezone.localtime(event.start_at).date() for event in month_events}
    reminder_items = CalendarReminder.objects.filter(user=user)[:8]
    chart_bars = _calendar_chart_bars(month_events, year, month)
    prev_month = _shift_month(year, month, -1)
    next_month = _shift_month(year, month, 1)

    return {
        "active_page": "calendar",
        "calendar_rows": rows,
        "month_label": f"{month_names[month - 1]} {year}",
        "today_label": f"{weekday_names[now.weekday()]}, {now.strftime('%d.%m.%Y')}",
        "today_events": today_events,
        "upcoming_events": upcoming_events,
        "prev_month": {"year": prev_month[0], "month": prev_month[1]},
        "next_month": {"year": next_month[0], "month": next_month[1]},
        "month_stats": {
            "events": len(month_events),
            "free_days": len(days_in_month) - len(busy_days),
            "synced_days": len(busy_days),
            "chart_bars": chart_bars,
        },
        "reminders": reminder_items,
    }


def _group_calendar_events_by_date(events, visible_start, visible_end):
    grouped = {}
    for event in events:
        start_date = max(timezone.localtime(event.start_at).date(), visible_start)
        event_end = timezone.localtime(event.end_at)
        end_date = event_end.date()
        if event.is_all_day:
            end_date = (event_end - timedelta(seconds=1)).date()
        end_date = min(end_date, visible_end - timedelta(days=1))

        current = start_date
        while current <= end_date:
            grouped.setdefault(current, []).append(event)
            current += timedelta(days=1)
    return grouped


def _calendar_event_time_label(event):
    if event.is_all_day:
        return "Ganztagig"
    return timezone.localtime(event.start_at).strftime("%H:%M")


def _upcoming_calendar_events(user, now):
    events = CalendarEvent.objects.filter(user=user, end_at__gte=now).select_related("source").order_by("start_at")[:6]
    return [
        {
            "date": timezone.localtime(event.start_at).strftime("%d.%m."),
            "title": event.title,
            "category": event.source.name,
            "icon": "fa-calendar-day",
        }
        for event in events
    ]


def _shift_month(year, month, direction):
    shifted_month = month + direction
    if shifted_month < 1:
        return year - 1, 12
    if shifted_month > 12:
        return year + 1, 1
    return year, shifted_month


def _calendar_chart_bars(events, year, month):
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    counts = []
    for week in weeks:
        week_dates = {day for day in week if day.month == month}
        count = sum(1 for event in events if timezone.localtime(event.start_at).date() in week_dates)
        counts.append(count)

    max_count = max(counts, default=0)
    if max_count == 0:
        return [{"height": 18, "count": 0} for _count in counts]

    return [
        {
            "height": 18 + round((count / max_count) * 48),
            "count": count,
        }
        for count in counts
    ]
