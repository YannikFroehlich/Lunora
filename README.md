# Lunora

Lunora ist ein kleines Django-basiertes Workspace-Dashboard mit ruhiger Glas-UI. Die App bündelt Dashboard, Wetter, Kalender, Erinnerungen, Profil-/Design-Einstellungen und direkte Nachrichten zwischen Accounts.

## Funktionen

- Authentifizierung mit Registrierung, Login und Logout
- Profil- und Erscheinungsbild-Einstellungen mit Theme, Akzentfarbe, Dichte, Datumsformat, Zeitformat und Zeitzone
- Dashboard mit Uhr, Wetterausblick, Schnellzugriffen, kommenden Terminen und Nachrichten-Badge
- Wetterseite mit Ortssuche, Demo-Daten ohne API-Key, OpenWeather-Anbindung und Regenradar-Proxy
- Kalenderseite mit privatem iCal-/Google-Kalender-Link, Synchronisierung, Monatsübersicht, Tagesliste, kommenden Terminen und Erinnerungen
- Nachrichtenseite mit Direktunterhaltungen, ungelesenen Nachrichten, Live-Updates, Reaktionen, angepinnten Nachrichten, Lesestatus, Stummschalten und Blockieren
- Responsive UI mit gemeinsamen Design-Tokens, Darkmode-Kontrast, Fokuszuständen und gestylten Scrollbars

## Tech Stack

- Python
- Django 5.2.9
- SQLite für lokale Entwicklung
- Django Templates
- Vanilla CSS und JavaScript

## Projektstruktur

```text
Lunora/
|-- app/
|   |-- migrations/              # Datenbankmigrationen
|   |-- services/                # Wetter-, Kalender- und Preference-Logik
|   |-- static/
|   |   |-- css/                 # Globale und seitenspezifische Styles
|   |   |-- js/                  # Seitenspezifische Interaktionen
|   |   `-- img/                 # Bildassets
|   |-- templates/app/           # Seiten-Templates
|   |   `-- partials/            # Wiederverwendbare Teil-Templates
|   |-- views/                   # Modulare View-Dateien
|   |-- forms.py
|   |-- models.py
|   |-- urls.py
|   `-- view_models.py
|-- lunora/                      # Django-Projektkonfiguration
|-- manage.py
|-- requirements.txt
`-- README.md
```

## Lokales Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Der Entwicklungsserver läuft anschließend standardmäßig unter:

```text
http://127.0.0.1:8000/
```

`runserver` läuft dauerhaft, bis er mit `Ctrl+C` beendet wird.

## Konfiguration

Lokale Konfiguration wird aus einer `.env` im Projektroot geladen.

Beispiel:

```env
DJANGO_SECRET_KEY=dev-secret-key
DJANGO_DEBUG=true
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost
DJANGO_LANGUAGE_CODE=de-de
DJANGO_TIME_ZONE=Europe/Berlin

OPENWEATHER_API_KEY=
WEATHER_DEFAULT_CITY=Buende,de
```

Wichtige Variablen:

- `DJANGO_SECRET_KEY`: Django Secret Key
- `DJANGO_DEBUG`: `true` oder `false`
- `DJANGO_ALLOWED_HOSTS`: kommagetrennte Hostliste
- `OPENWEATHER_API_KEY` oder `WEATHER_API_KEY`: aktiviert echte Wetterdaten und Radar-Layer
- `WEATHER_DEFAULT_CITY`: Standardort für Wetterdaten
- `DJANGO_LANGUAGE_CODE`: Sprache, standardmäßig `de-de`
- `DJANGO_TIME_ZONE`: Standardzeitzone, standardmäßig `Europe/Berlin`

Ohne Wetter-API-Key zeigt Lunora Demo-Wetterdaten und eine Radar-Vorschau.

## Tests und Checks

```powershell
python manage.py check
python manage.py test
```

Für JavaScript-Syntaxchecks:

```powershell
node --check app\static\js\weather.js
node --check app\static\js\messages.js
```

## Entwicklungshinweise

- Views sind nach Bereichen in `app/views/` aufgeteilt.
- Daten für Templates sollten bevorzugt in `app/view_models.py` vorbereitet werden.
- Externe oder wiederverwendbare Logik gehört nach `app/services/`.
- Seitenspezifische Styles und Skripte liegen in `app/static/css/` und `app/static/js/`.
- Bei Änderungen an Models immer Migrationen erstellen und mit Tests absichern.

## Sicherheit

- Keine echten Secrets, API-Keys, privaten Kalenderlinks, lokalen Datenbanken oder Uploads committen.
- `.env`, `db.sqlite3` und `media/` sind lokale Entwicklungsdaten.
- Kalenderquellen können private iCal-URLs enthalten und sollten nicht geloggt oder öffentlich angezeigt werden.
- Wetter-API-Keys bleiben serverseitig und werden nicht an Browser-JavaScript weitergegeben.
