# Repository Guidelines

## Project Structure & Module Organization

Lunora is a Django project with a single main app. The project package lives in `lunora/` and contains global settings, URL routing, ASGI, and WSGI configuration. The `app/` package contains the product code: models in `app/models.py`, forms in `app/forms.py`, request handlers in `app/views.py`, URL mappings in `app/urls.py`, context processors in `app/context_processors.py`, presentation data helpers in `app/view_models.py`, and integration or domain helpers in `app/services/`.

Templates are under `app/templates/app/`; reusable page fragments, especially for live message updates, belong in `app/templates/app/partials/`. Static CSS and JavaScript live in `app/static/css/` and `app/static/js/`, usually with page-matched names such as `calendar.css`, `messages.css`, and `messages.js`. Image assets are under `app/static/img/`. User-uploaded media is served from `media/` during development and should not be treated as committed source. Database migrations belong in `app/migrations/`.

Current feature areas include authentication and profile settings, appearance and regional preferences, weather lookup, calendar sync from iCal/Google Calendar links, reminders, and direct messaging with unread counts, pinning, reactions, muting, blocking, and live-update partials.

## Build, Test, and Development Commands

- `python -m venv .venv`: create a local virtual environment if one is not present.
- `.venv\Scripts\Activate.ps1`: activate the virtual environment on PowerShell.
- `python manage.py migrate`: apply database migrations to `db.sqlite3`.
- `python manage.py runserver`: run the local Django development server.
- `python manage.py test`: run Django's test suite.

Run commands from the repository root. No dependency lockfile is currently committed; if adding dependencies, document installation steps and consider adding a `requirements.txt`.

## Coding Style & Naming Conventions

Use standard Python style with 4-space indentation and clear snake_case names for modules, functions, variables, and Django views. Keep views focused on request flow and delegate data preparation to `view_models.py`, form validation to `forms.py`, and external or reusable domain behavior to `app/services/`. Keep Django templates named after the page they render, with matching static files when page-specific behavior exists.

For models, keep user-facing account data in `Profile`, calendar data in the calendar models, and messaging data in the conversation/message models. When changing a model, add a migration and keep data migrations small and explicit. For JavaScript, keep page-specific behavior in the matching file and prefer progressive enhancement over duplicating server-rendered state.

## Testing Guidelines

Tests currently live in `app/tests.py` and use Django's `TestCase` and test client. Name test methods with `test_` and focus on observable behavior: redirects, rendered templates, response status codes, context-visible content, JSON payloads, form behavior, and service output. Existing coverage includes settings/profile updates, calendar sync and iCal parsing, reminders, messaging permissions, unread counts, message actions, read receipts, and live update responses. Run `python manage.py test` before opening a pull request.

## Commit & Pull Request Guidelines

Recent commits use short, imperative summaries, for example `URL Pattern and Settings View Added` or `Build Lunora mockup-inspired workspace UI`. Keep commits focused and describe the user-visible change. Pull requests should include a concise summary, test results, linked issues when applicable, and screenshots for template or static asset changes.

## Security & Configuration Tips

Local configuration is loaded from `.env` in `lunora/settings.py`. Do not commit real secrets, API keys, uploaded media, or production database files. Weather configuration is read from `OPENWEATHER_API_KEY`, `WEATHER_API_KEY`, and related base URL variables; keep those values server-side. Calendar sources store private iCal URLs, so avoid logging full URLs or exposing them in templates beyond the owning user's settings.
