from django.utils import timezone


def get_dashboard_context():
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
    return {
        "active_page": "home",
        "today_label": now.strftime("%d.%m.%Y"),
        "time_label": now.strftime("%H:%M"),
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
        "notes": [
            {"text": "Projektbrief prüfen", "done": True},
            {"text": "Landingpage designen", "done": False},
            {"text": "Präsentation vorbereiten", "done": False},
        ],
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
