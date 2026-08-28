# Repository Guidelines

## Project Structure & Module Organization

Lunora is a Django project with a single main app. The project package lives in `lunora/` and contains global settings, URL routing, ASGI, and WSGI configuration. The `app/` package contains the product code: models in `app/models.py`, forms in `app/forms.py`, URL mappings in `app/urls.py`, context processors in `app/context_processors.py`, request-scoped middleware in `app/middleware.py`, presentation data helpers in `app/view_models.py`, integration or domain helpers in `app/services/`, and management commands in `app/management/commands/`.

Request handlers live in the `app/views/` package, split by area (`auth_views.py`, `core_views.py`, `calendar_views.py`, `message_views.py`, `note_views.py`, `notification_views.py`, `weather_views.py`, `administration_views.py`, `tasks_views.py`, `vacation_planner_views.py`, `search_views.py`, `pwa_views.py`). `app/urls.py` imports the package as a namespace, so every new view must also be re-exported from `app/views/__init__.py` — both the import and `__all__` — or the URLconf will fail to load. Custom template tags live in `app/templatetags/`.

Templates are under `app/templates/app/`; reusable page fragments, especially for live message updates, belong in `app/templates/app/partials/`. Static CSS and JavaScript live in `app/static/css/` and `app/static/js/`, usually with page-matched names such as `calendar.css`, `messages.css`, and `messages.js`. Image assets are under `app/static/img/`. The notes editor is the one bundled frontend: sources in `frontend/`, built by Vite into the committed bundle `app/static/js/bundles/notes.js`.

User-uploaded profile media is served from `media/` during development and should not be treated as committed source. Private note attachments live under `private_media/` (`DJANGO_PRIVATE_MEDIA_ROOT`) and are never served directly. Database migrations belong in `app/migrations/`.

Current feature areas include authentication with e-mail-or-username login, profile settings, appearance and regional preferences, weather lookup with a keyless demo fallback, calendar sync from iCal/Google Calendar links plus manually created and invitable events, reminders with e-mail and Web Push notifications, a persistent notification center with unread state, per-category Inbox/e-mail/Web-Push preferences, Web-Push quiet hours, per-device push subscriptions, test delivery, and retryable delivery rows, a per-user task list with the same notification pattern, weekly summary e-mails, direct messaging (including group chats, file/image attachments, and replies) with unread counts, pinning, reactions, muting, blocking, and live-update partials, a rich-text notes workspace with sharing, versions, per-user color/icon/folder tagging, templates, note-to-note links, math, private attachments, and PDF/Markdown export, a German vacation planner with public-holiday accounting, a customizable dashboard with a fixed widget catalog and per-user layout, and a superuser-only administration page with system-wide feature flags.

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

Always run `python manage.py migrate` after pulling changes and before handing off, committing, or pushing completed repository work, even when no new migration is expected. This is a separate required step from `python manage.py makemigrations --check --dry-run` and from the test suite; report whether migrations were applied or whether none were pending.

## Coding Style & Naming Conventions

Use standard Python style with 4-space indentation and clear snake_case names for modules, functions, variables, and Django views. Keep views focused on request flow and delegate data preparation to `view_models.py`, form validation to `forms.py`, and external or reusable domain behavior to `app/services/`. Keep Django templates named after the page they render, with matching static files when page-specific behavior exists.

User-facing strings are German; identifiers, comments, and commit messages are English. Format dates and times for users through `app/services/user_preferences.py` rather than Django's date filters, because format and timezone are per-profile settings applied by `UserTimezoneMiddleware`.

For models, keep user-facing account data in `Profile`, calendar data in the calendar models, messaging data in the conversation/message models, note data in the `Note*` models, and global toggles in the singleton `SystemSettings` row. When changing a model, add a migration and keep data migrations small and explicit. After generating a migration, always run `python manage.py migrate` against the local development database and verify its applied status before handing off the change.

For JavaScript, keep page-specific behavior in the matching file in `app/static/js/` and prefer progressive enhancement over duplicating server-rendered state. The notes editor is the exception: edit `frontend/`, then run `npm run build` and commit the regenerated bundle in the same change. Editor capabilities must also be allowed server-side in `app/services/note_content.py`, which re-validates every node, mark, and attribute of the submitted document.

Every page template loads `static_versioning` (`{% load static static_versioning %}`) and links its own CSS/JS with `{% versioned_static %}` instead of `{% static %}`, so a content hash busts client and service-worker caches automatically — carry this over to any new page template.

New user-gated features should respect the flag system: guard the view with `feature_enabled(...)` and `disabled_feature_response(...)`, expose the flag through `app/context_processors.py` so navigation can hide it, and re-check it in `app/services/scheduled_tasks.py` if background work is involved.

## Testing Guidelines

Python tests live in `app/tests.py` and use Django's `TestCase` and test client, grouped into per-area classes. Name test methods with `test_` and focus on observable behavior: redirects, rendered templates, response status codes, context-visible content, JSON payloads, form behavior, and service output. External HTTP is stubbed with local fake response objects patched over the relevant service helper — tests must not reach the network.

Existing coverage includes settings/profile updates, profile image validation, calendar sync and iCal parsing, iCal fetch safety, manual events and attendee invitations, reminders, tasks, scheduled automations, notification idempotency, detailed channel preferences, Web-Push quiet hours and test delivery, weather map and point endpoints, messaging permissions, unread counts, message actions, read receipts, live update responses, global search, static asset versioning, administration feature flags, dashboard customization, the vacation planner, and the notes workspace including revision conflicts and share roles.

Run a single case with `python manage.py test app.tests.NotesTests.test_stale_revision_returns_conflict_without_overwriting`. Run `python manage.py test` and, when `frontend/` changed, `npm test` and `npm run build` before opening a pull request.

## Commit & Pull Request Guidelines

Recent commits use short, imperative summaries, for example `Add notification automation and improve mobile settings` or `Add multi-calendar support and update weather UI`. Keep commits focused and describe the user-visible change. Pull requests should include a concise summary, test results, linked issues when applicable, and screenshots for template or static asset changes.

## Security & Configuration Tips

Local configuration is loaded from `.env` in `lunora/settings.py`; `.env.example` documents every supported variable. Real environment variables take precedence over `.env`. With `DJANGO_DEBUG=false`, a missing `DJANGO_SECRET_KEY` or `DJANGO_ALLOWED_HOSTS` raises `ImproperlyConfigured` and the HTTPS, HSTS, and secure-cookie settings default to enabled.

Do not commit real secrets, API keys, VAPID private keys, uploaded media, private note attachments, or `db.sqlite3`. Weather configuration is read from `OPENWEATHER_API_KEY`, `WEATHER_API_KEY`, and related base URL variables; keys stay server-side and map tiles are proxied so browser code never sees them. Calendar sources store private iCal URLs, so avoid logging full URLs or exposing them in templates beyond the owning user's settings; fetches are restricted to public hosts by `app/services/url_safety.py` and must not follow redirects. Web Push endpoints are capability URLs: never log or expose them, keep endpoint-host validation in place, and expose only the public VAPID key to templates. Note attachments are addressed by UUID and may only be delivered through the permission-checked download view.

## Production Operations Runbook

The production baseline below was verified on 24 August 2026. Treat it as operational
context, not as a substitute for checking current state before a deployment. When
guiding the operator interactively, label every instruction with its execution
location: `Windows PC (PowerShell)`, `Ubuntu server`, or `Cloudflare dashboard`.

### Production Topology and Access

- Public application: `https://lunora.yfserver.de`; `/login/`, `/register/`, and
  `/admin/` are the main entry points.
- Production runs on Ubuntu host `yfserv1`. SSH uses user `yunnik`, port `22`, key-only
  authentication, and is allowed by UFW only from `192.168.178.0/24`. The server's
  current LAN address is `192.168.178.175`. Keep the private SSH key outside this
  repository and use the local SSH agent; never record the key, passphrase, or tunnel
  token in project files.
- The GitHub repository is public. `develop` is the integration branch and `main` is
  the production branch. Deploy only committed, pushed, reviewed changes from `main`;
  leave the local development checkout on `develop` after a release.
- The production checkout is `/srv/lunora/app`, the virtual environment is
  `/srv/lunora/venv`, and the service account is `lunora`.
- Requests follow Cloudflare Tunnel -> Nginx on `127.0.0.1:8080` -> Gunicorn on
  `/run/lunora/gunicorn.sock` -> Django. Do not expose Nginx, Gunicorn, PostgreSQL, or
  Redis directly to the LAN or Internet and do not open ports `80`, `443`, `5432`, or
  `6379` in UFW for this deployment.
- Production uses PostgreSQL database and role `lunora` plus Redis database `1` at
  `127.0.0.1:6379`. SQLite remains the local-development database only.

### Secrets and External Services

- Production environment variables live only in `/etc/lunora/lunora.env`, owned by
  `root:lunora` with mode `0640`. Secret values are stored in the operator's password
  manager. Never print, copy into chat, log, commit, or include them in diagnostics.
- Required secrets include `DJANGO_SECRET_KEY`, `DJANGO_DB_PASSWORD`,
  `DJANGO_EMAIL_HOST_PASSWORD`, `CLOUDFLARE_TURNSTILE_SITE_KEY`, and
  `CLOUDFLARE_TURNSTILE_SECRET_KEY`, plus the VAPID private key referenced by
  `WEB_PUSH_VAPID_PRIVATE_KEY`. `OPENWEATHER_API_KEY` is expected to be present;
  verify only whether it is non-empty and never display its value. The application can
  fall back to keyless demo weather if it is absent.
- SMTP uses `smtp.strato.de:465` with SSL, user and sender
  `webmaster@yfserver.de`, default/server sender `Lunora <webmaster@yfserver.de>`, and
  Django admin address `webmaster@yfserver.de`. The SMTP password is environment-only.
- The initial Django superuser is username `yannik`, e-mail
  `webmaster@yfserver.de`; its password remains only in the password manager.
- Cloudflare Tunnel `lunora-yfserv1` publishes `lunora.yfserver.de` to
  `http://localhost:8080`. Public registration is intentional, so do not place
  Cloudflare Access in front of the whole site. Turnstile must remain required for the
  registration hostname. Scoped WAF or rate-limit rules for login, registration, and
  password reset are optional hardening and must not affect other `yfserver.de` apps.
- The remotely managed Cloudflare tunnel token is stored in
  `/etc/cloudflared/token`, owned by root with mode `0600`. The systemd drop-in
  `/etc/systemd/system/cloudflared.service.d/override.conf` starts cloudflared with
  `--token-file`; never output the token or replace this with an inline `--token`.

### Services, Storage, and Network Baseline

- Required enabled services are `lunora-web`, `lunora-automations`, `nginx`,
  `postgresql`, `redis-server`, and `cloudflared`. Only one
  `lunora-automations` process may run.
- Required enabled timers are `lunora-backup.timer`, `lunora-purge.timer`, and
  `lunora-auto-deploy.timer`.
  Backups run nightly around 02:30 Europe/Berlin with a randomized delay; note-trash
  purging runs around 03:20 with a randomized delay.
- Local backups are stored below `/srv/lunora/backups` with 14-day retention and
  contain a PostgreSQL custom-format dump, uploads archive, and SHA-256 manifest.
  Local backups do not protect against server or disk loss; an encrypted off-site copy
  remains recommended.
- Public uploads live in `/srv/lunora/app/media`; private note attachments live in
  `/srv/lunora/app/private_media`; collected static files live in
  `/srv/lunora/app/staticfiles`. Preserve ownership, setgid directory modes, and the
  distinction between public and private media.
- The server currently reaches the network through Wi-Fi interface `wlp19s0`. Netplan
  overlay `/etc/netplan/99-lunora-network-online.yaml` marks unused Ethernet interface
  `enp7s0` optional and Wi-Fi required, avoiding a two-minute boot timeout when no LAN
  cable is connected. Connecting Ethernet later is still allowed.
- HSTS is enabled for one year. `includeSubDomains` and preload intentionally remain
  disabled, so those two `manage.py check --deploy` warnings are expected unless the
  entire parent domain has first been audited for HSTS compatibility.

### Normal Release Procedure

Before production deployment, run the full Django test suite locally and run frontend
tests plus a bundle build whenever `frontend/` changed. Merge or fast-forward the tested
commit to `main` and push it. GitHub branch protection should reject direct pushes and
require the `Tests` check from `.github/workflows/ci.yml` before merging because every
new `main` commit is production eligible. CI uses GitHub-hosted isolated runners; never
change the production workflow to a persistent self-hosted runner while the repository
is public.

`lunora-auto-deploy.timer` checks `origin/main` from the server roughly every minute;
it does not require inbound SSH or a GitHub token. For a new Fast-Forward commit it
creates one pre-deployment backup, runs the application deployment as `yunnik`, restarts
services as root, performs an internal health check, and only then records the successful
commit in `/var/lib/lunora-auto-deploy`. The unit must keep `UMask=0027`, and the driver
must verify every tracked file is readable as `lunora` before restarting services; a
stricter umask can make Git-written files unreadable to Gunicorn. A failure leaves the
previous success marker in place and is retried. The root-owned installed driver
`/usr/local/sbin/lunora-auto-deploy` must only be updated manually from
`scripts/auto-deploy.sh`; never execute repository code directly as root.

The manual fallback remains available on the Ubuntu server and must run as `yunnik`,
never as root:

```bash
cd /srv/lunora/app
./scripts/deploy.sh
```

The deployment script refuses a dirty production checkout, fast-forwards from `origin/main`,
installs pinned Python requirements, runs Django deployment checks, migrations and
`collectstatic`, then restarts the web and single automation services. If environment
variables or systemd unit files changed, install the updated files as described in
`DEPLOYMENT.md`, reload systemd, and restart only the affected services. Make a fresh
backup before risky schema or storage changes.

### Safe Post-Deployment Checks

Never include environment contents, database URLs, SMTP credentials, Turnstile secrets,
Cloudflare tokens, private calendar URLs, or sensitive logs in diagnostic output. Use
status-only checks such as:

```bash
systemctl is-active lunora-web lunora-automations nginx postgresql redis-server cloudflared lunora-backup.timer lunora-purge.timer
systemctl is-enabled lunora-web lunora-automations nginx postgresql redis-server cloudflared lunora-backup.timer lunora-purge.timer lunora-auto-deploy.timer
systemctl list-timers lunora-auto-deploy.timer --no-pager
systemctl --failed --no-legend
curl --fail --silent --show-error --output /dev/null --write-out '%{http_code}\n' \
  --header 'Host: lunora.yfserver.de' --header 'X-Forwarded-Proto: https' \
  http://127.0.0.1:8080/login/
curl --fail --silent --show-error --output /dev/null --write-out '%{http_code}\n' \
  https://lunora.yfserver.de/login/
curl --fail --silent --show-error --output /dev/null --write-out '%{http_code}\n' \
  https://lunora.yfserver.de/register/
```

After infrastructure changes, also test a real login, public registration with
Turnstile, password reset/e-mail delivery, authorized private-attachment access, one
automation cycle, a new backup, and a full server reboot. Validate a backup without
restoring over production by checking `SHA256SUMS`, `pg_restore --list database.dump`,
and `tar -tzf uploads.tar.gz`. The baseline deployment passed these checks, including
external HTTPS and reboot recovery, but they must be repeated after relevant changes.
