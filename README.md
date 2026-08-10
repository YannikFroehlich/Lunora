# Lunora

Lunora ist ein kleines Django-basiertes Workspace-Dashboard mit ruhiger Glas-UI. Die App bündelt Dashboard, Wetter, Kalender, Erinnerungen, Profil-/Design-Einstellungen und direkte Nachrichten zwischen Accounts.

## Funktionen

- Authentifizierung mit Registrierung, Login und Logout
- Profil- und Erscheinungsbild-Einstellungen mit Theme, Akzentfarbe, Dichte, Datumsformat, Zeitformat und Zeitzone
- Dashboard mit Uhr, Wetterausblick, Schnellzugriffen, kommenden Terminen und Nachrichten-Badge
- Wetterseite mit Ortssuche, Demo-Daten ohne API-Key, OpenWeather-Anbindung und Regenradar-Proxy
- Kalenderseite mit privatem iCal-/Google-Kalender-Link, Synchronisierung, Monatsübersicht, Tagesliste, kommenden Terminen und Erinnerungen
- Nachrichtenseite mit Direktunterhaltungen, ungelesenen Nachrichten, Live-Updates, Reaktionen, angepinnten Nachrichten, Lesestatus, Stummschalten und Blockieren
- Notizbereich mit Rich-Text-Editor, Autosave, frei belegbaren Hotkeys, Hashtags, Freigaben, Versionen, privaten Anhängen, layoutgetreuem PDF-Export, Archiv und Papierkorb
- Responsive UI mit gemeinsamen Design-Tokens, Darkmode-Kontrast, Fokuszuständen und gestylten Scrollbars

## Tech Stack

- Python
- Django 5.2.9
- SQLite für lokale Entwicklung
- Django Templates
- Vanilla CSS und JavaScript
- Tiptap 3 und Vite für das lokal gebündelte Notiz-Frontend
- ReportLab und Pillow für den serverseitigen, berechtigungsgeprüften Notiz-PDF-Export

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
npm ci
npm run build
python manage.py migrate
python manage.py runserver
```

Der Entwicklungsserver läuft anschließend standardmäßig unter:

```text
http://127.0.0.1:8000/
```

`runserver` läuft dauerhaft, bis er mit `Ctrl+C` beendet wird.

Automatische Kalender-Synchronisierung, fällige Erinnerungs-E-Mails und Wochenberichte laufen in einem zweiten Prozess:

```powershell
python manage.py run_automations --loop
```

Pro Deployment darf nur ein dauerhafter Automatikprozess laufen, damit externe Kalender und E-Mail-Anbieter nicht doppelt angesprochen werden. Desktop-Hinweise werden bei erteilter Browserfreigabe zugestellt, solange mindestens ein Lunora-Tab geöffnet ist.

Für einen einzelnen Durchlauf, beispielsweise über die Windows-Aufgabenplanung oder Cron, genügt:

```powershell
python manage.py run_automations
```

## Konfiguration

Lokale Konfiguration wird aus einer `.env` im Projektroot geladen.

Beispiel:

```env
DJANGO_SECRET_KEY=dev-secret-key
DJANGO_DEBUG=true
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost
DJANGO_CSRF_TRUSTED_ORIGINS=
DJANGO_LANGUAGE_CODE=de-de
DJANGO_TIME_ZONE=Europe/Berlin
DJANGO_STATIC_ROOT=staticfiles
PROFILE_IMAGE_MAX_BYTES=2097152
PROFILE_IMAGE_MAX_WIDTH=4096
PROFILE_IMAGE_MAX_HEIGHT=4096
DJANGO_PRIVATE_MEDIA_ROOT=private_media

DJANGO_SECURE_SSL_REDIRECT=false
DJANGO_SESSION_COOKIE_SECURE=false
DJANGO_CSRF_COOKIE_SECURE=false
DJANGO_SECURE_HSTS_SECONDS=0

OPENWEATHER_API_KEY=
WEATHER_DEFAULT_CITY=Buende,de
WEATHER_CACHE_SECONDS=600
```

Wichtige Variablen:

- `DJANGO_SECRET_KEY`: Django Secret Key
- `DJANGO_DEBUG`: `true` oder `false`
- `DJANGO_ALLOWED_HOSTS`: kommagetrennte Hostliste
- `DJANGO_CSRF_TRUSTED_ORIGINS`: kommagetrennte HTTPS-Urspruenge fuer CSRF, z. B. `https://lunora.example.com`
- `DJANGO_STATIC_ROOT`: Zielordner fuer `collectstatic`, standardmaessig `staticfiles`
- `PROFILE_IMAGE_MAX_BYTES`, `PROFILE_IMAGE_MAX_WIDTH`, `PROFILE_IMAGE_MAX_HEIGHT`: Upload-Limits fuer Profilbilder
- `DJANGO_PRIVATE_MEDIA_ROOT`: nicht öffentlich ausgelieferter Speicherort für private Notizanhänge
- `DJANGO_SECURE_SSL_REDIRECT`: HTTPS-Weiterleitung, standardmaessig aktiv wenn `DJANGO_DEBUG=false`
- `DJANGO_SESSION_COOKIE_SECURE` und `DJANGO_CSRF_COOKIE_SECURE`: Secure-Cookies, standardmaessig aktiv wenn `DJANGO_DEBUG=false`
- `DJANGO_SECURE_HSTS_SECONDS`: HSTS-Dauer in Sekunden, standardmaessig `31536000` wenn `DJANGO_DEBUG=false`
- `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` und `DJANGO_SECURE_HSTS_PRELOAD`: nur aktivieren, wenn alle Subdomains dauerhaft HTTPS-only sind
- `DJANGO_USE_X_FORWARDED_PROTO`: fuer Deployments hinter einem vertrauenswuerdigen HTTPS-Reverse-Proxy
- `OPENWEATHER_API_KEY` oder `WEATHER_API_KEY`: aktiviert echte Wetterdaten und Radar-Layer
- `WEATHER_CACHE_SECONDS`: Cache-Dauer fuer OpenWeather-JSON-Antworten
- `WEATHER_DEFAULT_CITY`: Standardort für Wetterdaten
- `DJANGO_LANGUAGE_CODE`: Sprache, standardmäßig `de-de`
- `DJANGO_TIME_ZONE`: Standardzeitzone, standardmäßig `Europe/Berlin`
- `DJANGO_EMAIL_BACKEND` und die `DJANGO_EMAIL_*`-Variablen: E-Mail-Zustellung für Erinnerungen und Wochenberichte
- `LUNORA_AUTOMATION_INTERVAL_SECONDS`: Intervall des dauerhaft laufenden Automatikprozesses
- `LUNORA_WEEKLY_SUMMARY_HOUR`: lokale Montagstunde, ab der ein Wochenbericht versendet wird

Ohne Wetter-API-Key zeigt Lunora Demo-Wetterdaten und eine Radar-Vorschau.

## Tests und Checks

```powershell
python manage.py check
python manage.py test
npm test
npm run build
```

Für JavaScript-Syntaxchecks:

```powershell
node --check app\static\js\weather.js
node --check app\static\js\messages.js
```

Der Notizeditor wird aus `frontend/` nach `app/static/js/bundles/notes.js` gebaut. Die npm-Versionen sind in `package-lock.json` festgeschrieben.

Notizen im Papierkorb werden nach 30 Tagen durch folgenden Command endgültig gelöscht. In einer Deployment-Umgebung sollte er täglich eingeplant werden:

```powershell
python manage.py purge_expired_notes
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
- Notizanhänge liegen unter `DJANGO_PRIVATE_MEDIA_ROOT` und dürfen nur über die berechtigungsgeprüften Download-Endpunkte ausgeliefert werden.
- Wetter-API-Keys bleiben serverseitig und werden nicht an Browser-JavaScript weitergegeben.
