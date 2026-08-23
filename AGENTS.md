# Repository Guidelines

## Project Structure & Module Organization

Lunora is a Django project with a single main app. The project package lives in `lunora/` and contains global settings, URL routing, ASGI, and WSGI configuration. The `app/` package contains the product code: models in `app/models.py`, forms in `app/forms.py`, URL mappings in `app/urls.py`, context processors in `app/context_processors.py`, request-scoped middleware in `app/middleware.py`, presentation data helpers in `app/view_models.py`, integration or domain helpers in `app/services/`, and management commands in `app/management/commands/`.

Request handlers live in the `app/views/` package, split by area (`auth_views.py`, `core_views.py`, `calendar_views.py`, `message_views.py`, `note_views.py`, `notification_views.py`, `weather_views.py`, `administration_views.py`). `app/urls.py` imports the package as a namespace, so every new view must also be re-exported from `app/views/__init__.py` — both the import and `__all__` — or the URLconf will fail to load.

Templates are under `app/templates/app/`; reusable page fragments, especially for live message updates, belong in `app/templates/app/partials/`. Static CSS and JavaScript live in `app/static/css/` and `app/static/js/`, usually with page-matched names such as `calendar.css`, `messages.css`, and `messages.js`. Image assets are under `app/static/img/`. The notes editor is the one bundled frontend: sources in `frontend/`, built by Vite into the committed bundle `app/static/js/bundles/notes.js`.

User-uploaded profile media is served from `media/` during development and should not be treated as committed source. Private note attachments live under `private_media/` (`DJANGO_PRIVATE_MEDIA_ROOT`) and are never served directly. Database migrations belong in `app/migrations/`.

Current feature areas include authentication with e-mail-or-username login, profile settings, appearance and regional preferences, weather lookup with a keyless demo fallback, calendar sync from iCal/Google Calendar links plus manually created events, reminders with e-mail and desktop notifications, weekly summary e-mails, direct messaging with unread counts, pinning, reactions, muting, blocking, and live-update partials, a rich-text notes workspace with sharing, versions, private attachments and PDF export, and a superuser-only administration page with system-wide feature flags.

## Build, Test, and Development Commands

- `python -m venv .venv`: create a local virtual environment if one is not present.
- `.venv\Scripts\Activate.ps1`: activate the virtual environment on PowerShell.
- `python -m pip install -r requirements.txt`: install Python dependencies.
- `npm ci`: install the pinned frontend toolchain for the notes editor.
- `npm run build`: rebuild `app/static/js/bundles/notes.js` from `frontend/`.
- `python manage.py migrate`: apply database migrations to `db.sqlite3`.
- `python manage.py runserver`: run the local Django development server.
- `python manage.py check`: run Django's system checks.
- `python manage.py test`: run Django's test suite.
- `npm test`: run the vitest suite over `frontend/**/*.test.js`.
- `python manage.py run_automations [--loop]`: run calendar sync, due reminder e-mails, and weekly summaries once, or continuously.
- `python manage.py purge_expired_notes`: permanently delete notes that have been in the trash for 30 days.

Run commands from the repository root. Python dependencies are pinned in `requirements.txt` and frontend versions in `package-lock.json`; update the appropriate lockfile in the same commit as the code that needs the dependency. Only one `run_automations --loop` process may run per deployment, so external calendars and mail providers are not contacted twice.

## Coding Style & Naming Conventions

Use standard Python style with 4-space indentation and clear snake_case names for modules, functions, variables, and Django views. Keep views focused on request flow and delegate data preparation to `view_models.py`, form validation to `forms.py`, and external or reusable domain behavior to `app/services/`. Keep Django templates named after the page they render, with matching static files when page-specific behavior exists.

User-facing strings are German; identifiers, comments, and commit messages are English. Format dates and times for users through `app/services/user_preferences.py` rather than Django's date filters, because format and timezone are per-profile settings applied by `UserTimezoneMiddleware`.

For models, keep user-facing account data in `Profile`, calendar data in the calendar models, messaging data in the conversation/message models, note data in the `Note*` models, and global toggles in the singleton `SystemSettings` row. When changing a model, add a migration and keep data migrations small and explicit.

For JavaScript, keep page-specific behavior in the matching file in `app/static/js/` and prefer progressive enhancement over duplicating server-rendered state. The notes editor is the exception: edit `frontend/`, then run `npm run build` and commit the regenerated bundle in the same change. Editor capabilities must also be allowed server-side in `app/services/note_content.py`, which re-validates every node, mark, and attribute of the submitted document.

New user-gated features should respect the flag system: guard the view with `feature_enabled(...)` and `disabled_feature_response(...)`, expose the flag through `app/context_processors.py` so navigation can hide it, and re-check it in `app/services/scheduled_tasks.py` if background work is involved.

## Testing Guidelines

Python tests live in `app/tests.py` and use Django's `TestCase` and test client, grouped into per-area classes. Name test methods with `test_` and focus on observable behavior: redirects, rendered templates, response status codes, context-visible content, JSON payloads, form behavior, and service output. External HTTP is stubbed with local fake response objects patched over the relevant service helper — tests must not reach the network.

Existing coverage includes settings/profile updates, profile image validation, calendar sync and iCal parsing, iCal fetch safety, manual events, reminders, scheduled automations and notification idempotency, weather map and point endpoints, messaging permissions, unread counts, message actions, read receipts, live update responses, administration feature flags, and the notes workspace including revision conflicts and share roles.

Run a single case with `python manage.py test app.tests.NotesTests.test_stale_revision_returns_conflict_without_overwriting`. Run `python manage.py test` and, when `frontend/` changed, `npm test` and `npm run build` before opening a pull request.

## Commit & Pull Request Guidelines

Recent commits use short, imperative summaries, for example `Add notification automation and improve mobile settings` or `Add multi-calendar support and update weather UI`. Keep commits focused and describe the user-visible change. Pull requests should include a concise summary, test results, linked issues when applicable, and screenshots for template or static asset changes.

## Security & Configuration Tips

Local configuration is loaded from `.env` in `lunora/settings.py`; `.env.example` documents every supported variable. Real environment variables take precedence over `.env`. With `DJANGO_DEBUG=false`, a missing `DJANGO_SECRET_KEY` or `DJANGO_ALLOWED_HOSTS` raises `ImproperlyConfigured` and the HTTPS, HSTS, and secure-cookie settings default to enabled.

Do not commit real secrets, API keys, uploaded media, private note attachments, or `db.sqlite3`. Weather configuration is read from `OPENWEATHER_API_KEY`, `WEATHER_API_KEY`, and related base URL variables; keys stay server-side and map tiles are proxied so browser code never sees them. Calendar sources store private iCal URLs, so avoid logging full URLs or exposing them in templates beyond the owning user's settings; fetches are restricted to public hosts by `app/services/url_safety.py` and must not follow redirects. Note attachments are addressed by UUID and may only be delivered through the permission-checked download view.
