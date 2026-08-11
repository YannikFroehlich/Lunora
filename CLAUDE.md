# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Lunora is a Django 5.2 workspace dashboard (dashboard, weather, calendar, reminders, direct messages, rich-text notes) with a single Django app (`app/`) and a small Vite-built notes editor bundle. Only three third-party Python packages are used (Django, Pillow, reportlab) — HTTP calls, iCal parsing, and env loading are all hand-rolled on the stdlib. Keep it that way unless asked otherwise.

## Commands

All commands run from the repository root with `.venv` activated (`.venv\Scripts\Activate.ps1`).

```bash
python manage.py migrate
python manage.py runserver
python manage.py check
python manage.py test
```

Single test class / method:

```bash
python manage.py test app.tests.NotesTests.test_stale_revision_returns_conflict_without_overwriting
```

Notes editor frontend (Vite lib build, IIFE, output committed to the repo):

```bash
npm ci
npm run build
npm test
```

Background automations — calendar sync, due reminder e-mails, weekly summaries. Exactly **one** loop process per deployment, otherwise external calendars and mail providers get hit twice:

```bash
python manage.py run_automations --loop
```

```bash
python manage.py purge_expired_notes
```

JS syntax check for the hand-written page scripts (they are not bundled or linted): `node --check app/static/js/weather.js`.

## Architecture

### Request layering

`app/views/*.py` handle request flow only. Template data preparation belongs in `app/view_models.py`, validation in `app/forms.py`, and anything external or reusable in `app/services/`.

`app/urls.py` does `import app.views as view`, so **every new view must be re-exported from `app/views/__init__.py`** (both the import and `__all__`) or the URLconf will fail.

Rendered UI strings are German; code identifiers, comments, and commit messages are English.

### Feature flags (`app/services/system_settings.py`)

`SystemSettings` is a forced-singleton row (`save()` pins `pk = 1`) toggled from the superuser-only `/administration/` page. Every gated view calls `feature_enabled("<key>")` first and returns `disabled_feature_response(request, key, json_response=...)` — a 503 HTML page or a 503 JSON body depending on the endpoint kind. `app.context_processors.system_settings` also injects `feature_flags` into every template so navigation can hide disabled areas, and `run_scheduled_tasks` re-checks the same flags. A new gated feature needs all three touch points plus a key in `FEATURE_FIELDS`/`FEATURE_LABELS`.

`normal_login_enabled` is separate: when off, only staff/superusers may authenticate (`EmailLoginForm.confirm_login_allowed`) and registration returns 503. `EmailLoginForm` resolves the identifier as either e-mail or username.

### Notes (the most involved subsystem)

- Documents are Tiptap ProseMirror JSON in `Note.document`. **Never trust client JSON**: `app/services/note_content.py` re-validates every node/mark against explicit allowlists (node types, mark types, fonts, sizes, line heights, hex colors, size/depth/node-count limits) and derives `plain_text` server-side. Adding an editor feature means extending both `frontend/notes.js` and that allowlist.
- Concurrency is optimistic on `Note.revision`. A stale `base_revision` raises `NoteConflictError` → HTTP 409 with `{"error": "revision_conflict", "note": ...}`; the client resubmits with `conflict_resolution: true`. `NoteVersion` snapshots are written on interval/restore/conflict and pruned.
- Pin/archive live in `NoteUserState`, not on `Note`, because notes are shared. `accessible_notes()` annotates `state_is_pinned`, `state_is_archived`, `is_shared_with_user`, `has_unseen_share` — query through it (or `get_accessible_note`) rather than `Note.objects` so permission scoping and those annotations stay consistent.
- Attachments are stored under `PRIVATE_MEDIA_ROOT` via `private_note_storage`, addressed by UUID `file_id`, and only served through `note_attachment_download`, which re-checks note access. They are never under `MEDIA_URL`.
- PDF export renders server-side with reportlab (`app/services/note_pdf.py`) so permissions apply to the export too.
- `frontend/notes.js` builds to `app/static/js/bundles/notes.js`, which **is committed**. After editing anything in `frontend/`, run `npm run build` and commit the regenerated bundle. `npm test` (vitest) only covers `frontend/**/*.test.js` — the small pure helpers.

### Messages live updates

The messages page is server-rendered; `messages_live_updates` re-renders the same partials in `app/templates/app/partials/` via `render_to_string` and returns them as HTML strings inside a JSON payload that `app/static/js/messages.js` swaps in. The full view and the live view must build compatible template contexts — changing a partial's context requirements means updating both `messages()` and `messages_live_updates()`.

### Calendar

`app/services/calendar_service.py` contains a hand-written iCal unfolder/parser (including weekly recurrence expansion) — no `icalendar` dependency. Fetching is SSRF-hardened: `app/services/url_safety.py` rejects non-public hosts/ports and re-validates resolved addresses, and `NoRedirectHandler` blocks redirects so a public URL cannot bounce to an internal one. iCal URLs are private user data — do not log full URLs or surface them outside the owning user's settings page.

Synced events dedupe on `(source, external_id)`; manually created events have `source = None` and are excluded from source-based clearing.

### Weather

`app/services/weather_service.py` keeps the OpenWeather key strictly server-side, caches JSON responses for `WEATHER_CACHE_SECONDS`, and proxies map tiles through `weather_map_tile` so the key never reaches the browser. Without a key the whole page falls back to demo data (`_fallback_*` helpers) — new weather features need both the API path and a fallback path, since tests cover the keyless behavior.

### Per-user time and formatting

`app.middleware.UserTimezoneMiddleware` activates the signed-in profile's timezone for each request and deactivates it afterwards. Date/time strings for users must go through `app/services/user_preferences.py` (`format_user_date`, `format_user_time`, `format_user_datetime`, `localtime_for_user`) because format and timezone are per-profile settings, not global ones.

`app.context_processors.appearance_settings` derives the glass-UI CSS variables (accent mix, overlay alphas, blur, density) from `Profile` for every template, including anonymous requests.

### Notification idempotency

Delivery is "at most once" via columns rather than a queue: `CalendarReminder.email_notified_at` / `desktop_notified_at` and the `WeeklySummaryDelivery` unique `(user, week_start)` constraint. Desktop notifications are *claimed* by the browser (`claim_due_desktop_reminders` uses `select_for_update` + a batch update) so multiple open tabs cannot double-notify. Preserve this pattern for any new notification type.

## Configuration

`lunora/settings.py` reads a plain `.env` at the repo root with its own tiny parser (`load_env_file`, `os.environ.setdefault` — real environment variables win) plus `env_bool`/`env_int`/`env_list` helpers. `.env.example` is the documented reference. With `DJANGO_DEBUG=false`, missing `DJANGO_SECRET_KEY` or `DJANGO_ALLOWED_HOSTS` raises `ImproperlyConfigured`, and the HTTPS/HSTS/secure-cookie toggles default to on.

Do not commit `.env`, `db.sqlite3`, `media/`, or `private_media/`.

## Testing

All Python tests live in `app/tests.py` (Django `TestCase` + test client), grouped into per-area classes and asserting observable behavior: status codes, redirects, rendered templates, context contents, JSON payloads, form validation, and service return values. External HTTP is stubbed with local fake response classes (`FakeIcalResponse`, `FakeWeatherResponse`, `FakeWeatherTileResponse`) patched over the service's fetch helper — no network in tests. Model changes need a migration plus test coverage.

## Related files

`AGENTS.md` and `README.md` (German) cover overlapping ground; `README.md` is the authoritative list of env vars and setup steps.
