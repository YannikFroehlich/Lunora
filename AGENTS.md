# Repository Guidelines

## Project Structure & Module Organization

Lunora is a small Django project. The Django project package lives in `lunora/` and contains global settings, URL routing, ASGI, and WSGI configuration. The main application is `app/`; keep views in `app/views.py`, URL mappings in `app/urls.py`, presentation data helpers in `app/view_models.py`, and external integrations in `app/services/`. Templates are under `app/templates/app/`, static CSS and JavaScript are under `app/static/css/` and `app/static/js/`, and image assets are under `app/static/img/`. Database migrations belong in `app/migrations/`.

## Build, Test, and Development Commands

- `python -m venv .venv`: create a local virtual environment if one is not present.
- `.venv\Scripts\Activate.ps1`: activate the virtual environment on PowerShell.
- `python manage.py migrate`: apply database migrations to `db.sqlite3`.
- `python manage.py runserver`: run the local Django development server.
- `python manage.py test`: run Django's test suite.

No dependency lockfile is currently committed. If adding dependencies, document installation steps and consider adding a `requirements.txt`.

## Coding Style & Naming Conventions

Use standard Python style with 4-space indentation and clear snake_case names for modules, functions, variables, and Django views. Keep Django templates named after the page they render, such as `weather.html`, with matching static files like `weather.css` and `weather.js` when page-specific behavior exists. Prefer small view functions that delegate data preparation to `view_models.py` or service modules.

## Testing Guidelines

Tests currently start in `app/tests.py` and should use Django's `TestCase` or test client where appropriate. Name test methods with `test_` and focus on observable behavior: rendered templates, response status codes, context data, and service output. Run `python manage.py test` before opening a pull request.

## Commit & Pull Request Guidelines

Recent commits use short, imperative summaries, for example `URL Pattern and Settings View Added` or `Build Lunora mockup-inspired workspace UI`. Keep commits focused and describe the user-visible change. Pull requests should include a concise summary, test results, linked issues when applicable, and screenshots for template or static asset changes.

## Security & Configuration Tips

Local configuration is loaded from `.env`. Do not commit real secrets, API keys, or production database files. Weather configuration is read from `OPENWEATHER_API_KEY`, `WEATHER_API_KEY`, and related base URL variables in `lunora/settings.py`; keep those values server-side.
