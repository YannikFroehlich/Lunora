# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Lunora is a Django 6.1 workspace dashboard (dashboard, weather, calendar, reminders, direct messages, rich-text notes) with a single Django app (`app/`) and a Vite-built notes editor split into ES-module chunks. Third-party Python packages are kept deliberately minimal: `Django`, `Pillow`, `reportlab`, `gunicorn` (production WSGI server), `redis` (production cache backend), `psycopg` (PostgreSQL driver, needed for production and for the ranked full-text note search SQL path), `pywebpush` (Web Push delivery), `cryptography` (passkey signature checks; installed through `pywebpush` anyway), and `holidays` (German public-holiday generation for the vacation planner, with a stdlib fallback). HTTP calls, iCal parsing, and env loading are still hand-rolled on the stdlib. Don't add further dependencies without asking first.

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
python manage.py test app.tests.test_notes.NotesTests.test_stale_revision_returns_conflict_without_overwriting
```

Notes editor frontend (Vite ES-module build, entry and hashed chunks committed to the repo):

```bash
npm ci
npm run build
npm test
```

Background automations — due reminder e-mails, Web Push, weekly summaries, external calendar syncs. Exactly **one** loop process per deployment, otherwise mail and push providers and subscribed calendars get hit twice:

```bash
python manage.py run_automations --loop
```

```bash
python manage.py purge_expired_notes
```

Bulk-import official public holidays for the vacation planner over a year range (optionally scoped to one federal `--subdivision`):

```bash
python manage.py import_public_holidays --from-year 2026 --to-year 2027
```

Linting and formatting (config in `pyproject.toml`, `eslint.config.js`, `.prettierrc.json`; enforced in CI):

```bash
ruff check .
ruff format .
npm run lint
npm run format
```

CI (`.github/workflows/ci.yml`, Python 3.14, Node 24) runs, and a PR needs green: `ruff check .`, `ruff format --check .`, `npm run lint`, `npm run format:check`, `python manage.py check`, `python manage.py makemigrations --check --dry-run`, `python manage.py test --parallel auto`, `npm test`, and `npm run build` followed by `git diff --exit-code -- app/static/js/bundles/notes.js` (fails when the committed bundle is stale). Run `python manage.py migrate` after pulling and before handing off work, even when no new migration is expected.

`ruff` needs installing once via `pip install -r requirements-dev.txt -c constraints.txt` (adds `ruff` on top of the production `requirements.txt`). `requirements*.txt` pin only direct dependencies; `constraints.txt` pins every transitive one at the tested version and is passed with `-c` by CI and `scripts/deploy.sh`. After changing any `requirements*.txt`, reinstall without `-c` and regenerate it with `python -m pip freeze > constraints.txt` (Git Bash/Linux — PowerShell 5.1's `>` doesn't write plain UTF-8); a pin that disagrees with `constraints.txt` makes `pip install` fail with a conflict. The hand-written page scripts under `app/static/js/` are linted and formatted the same as `frontend/`, but still loaded as plain `<script>` tags rather than bundled — `node --check app/static/js/weather.js` remains a quick syntax-only fallback when you don't want to run the full toolchain.

## Architecture

### Request layering

`app/views/*.py` handle request flow only. Template data preparation belongs in `app/view_models.py`, validation in `app/forms.py`, and anything external or reusable in `app/services/`.

`app/urls.py` does `import app.views as view`, so **every new view must be re-exported from `app/views/__init__.py`** (both the import and `__all__`) or the URLconf will fail.

Rendered UI strings are German; code identifiers, comments, and commit messages are English.

### Feature flags (`app/services/system_settings.py`)

`SystemSettings` is a forced-singleton row (`save()` pins `pk = 1`) toggled from the superuser-only `/administration/` page. Every gated view calls `feature_enabled("<key>")` first and returns `disabled_feature_response(request, key, json_response=...)` — a 503 HTML page or a 503 JSON body depending on the endpoint kind. `app.context_processors.system_settings` also injects `feature_flags` into every template so navigation can hide disabled areas, and `run_scheduled_tasks` re-checks the same flags. A new gated feature needs all three touch points plus a key in `FEATURE_FIELDS`/`FEATURE_LABELS`. Inside a request the row is read once: `get_system_settings` memoizes it in a `ContextVar` scope opened by the `system_settings_scope` middleware (first in `MIDDLEWARE`) and cleared by `SystemSettings.save()`; outside a request (the automations loop, shell) every call reads the database, so flipped switches apply at once.

Accounts and security (login throttling, TOTP two-factor, passkeys, data export/import, storage quota, sessions, account deletion) are detailed in [docs/architecture/accounts-security.md](docs/architecture/accounts-security.md). Rules that apply everywhere:

- Throttle counters go through the atomic `count_hit` in `app/services/throttle.py`, never `cache.get` plus `cache.set`.
- A new user-owned model belongs in the data export (`app/services/data_export.py`), with secrets (feed tokens, TOTP fields, public-link tokens, subscription URLs) left out; a model holding user files also belongs in `delete_account`'s file cleanup.
- Every path that writes a new file for a user calls `ensure_storage_available` first, or it bypasses `SystemSettings.user_storage_limit_mb`.
- Keep every authenticated request flowing through `UserSessionMiddleware`; `UserSession` rows are the per-user session index.

### Global search

`/search/` (`app/views/search_views.py`, engine `GlobalSearch` in `app/services/global_search.py`) searches every enabled area with the note search's syntax (all words, `"phrases"`, `-exclusions`, `#tag` for notes and bookmarks, `nach:`/`vor:` date bounds via `split_date_filters`; an area missing from `DATE_FIELDS` shows no hits under a date filter). Notes use `search_notes`; every other area goes through `note_search.filter_by_query(queryset, query, fields)` on a queryset that is already permission-scoped (`accessible_*`, `visible_tasks`, `owner=`). Areas are listed in `SEARCH_AREAS` (`app/services/saved_searches.py`) with their feature flag; `?in=<area>` shows one area with more hits. `SavedSearch` rows (`/search/saved/`) are chips above the search; with `notify` on, `check_saved_searches` (run by `run_automations`, at most hourly per search) compares the hits against `known_hits` and puts new ones in the inbox (`KIND_SAVED_SEARCH`). `/search/quick/` returns the top hits per area as JSON for the command palette's live list, built from the same `GlobalSearch.results`, so a new area shows up in all three.

### Notes

The most involved subsystem; details in [docs/architecture/notes.md](docs/architecture/notes.md). Invariants:

- Documents are Tiptap ProseMirror JSON. **Never trust client JSON**: `app/services/note_content.py` re-validates every node/mark against allowlists and derives `plain_text` server-side. A new editor feature extends both `frontend/notes.js` and that allowlist (and `note_html.py`, the second trust boundary for public links).
- Query notes through `accessible_notes()`/`get_accessible_note` (permission scoping plus per-viewer `NoteUserState` annotations), never `Note.objects`. Search goes through `note_search.search_notes`, never `icontains` at a call site; a raw `queryset.update()` of title/plain_text must call `refresh_note_search_vectors`.
- Concurrency is optimistic on `Note.revision` (409 `revision_conflict`). Attachments live under `PRIVATE_MEDIA_ROOT`, served only through access-checked views.
- `frontend/` builds to `app/static/js/bundles/` (entry plus hashed chunks), **committed**: run `npm run build` after any `frontend/` change and commit the whole chunk directory. KaTeX stays a dynamic import.
- Mentions, note links and comment threads are re-checked against note access at save time; `duplicate_note` strips comment marks.

### Messages

Details in [docs/architecture/messages.md](docs/architecture/messages.md). Chat logic lives in `app/services/chat.py`. The page is server-rendered and `messages_live_updates` re-renders the same partials into JSON, so **`messages()` and `messages_live_updates()` must build compatible contexts**: changing a partial's context means updating both. Attachment content types come from the extension, never the client; message text never goes into URLs.

### Calendar

Details in [docs/architecture/calendar.md](docs/architecture/calendar.md). Invariants:

- The `.ics` feed (`/calendar/feed/<token>.ics`, no login) is hand-written RFC 5545; `calendar_feed_token` is a credential, never logged or rendered outside the owner's settings.
- Subscribed external calendars are read-only: every write path filters on `subscription=None`. Their URL is a credential, fetched only through `safe_fetch`.
- Invitee queries need `.distinct()`; availability endpoints return only free/busy/away or slot times, never other people's event details.
- Call `sync_event_reminders` after anything that changes an event's start, title, reminder or attendees' answers.

### Weather

`app/services/weather_service.py` keeps the OpenWeather key strictly server-side, caches JSON responses for `WEATHER_CACHE_SECONDS`, and proxies map tiles through `weather_map_tile` so the key never reaches the browser. Without a key the whole page falls back to demo data (`_fallback_*` helpers) — new weather features need both the API path and a fallback path, since tests cover the keyless behavior. The initial `/home/` render must remain provider-free: `get_home_context` emits a placeholder and `home.js` fetches the personalized summary from `/home/weather/` only for a visible weather widget, with a ten-minute client freshness guard. Keep that endpoint private/no-store and return JSON feature-flag errors.

### Per-user time and formatting

`app.middleware.UserTimezoneMiddleware` activates the signed-in profile's timezone for each request and deactivates it afterwards. Date/time strings for users must go through `app/services/user_preferences.py` (`format_user_date`, `format_user_time`, `format_user_datetime`, `localtime_for_user`) because format and timezone are per-profile settings, not global ones.

`app.context_processors.appearance_settings` derives the glass-UI CSS variables (accent, overlay alphas, blur) from `Profile` for every template, including anonymous requests. `base.html` sets only `--color-accent` inline; `base.css` derives `--color-accent-strong` from it per theme with `color-mix()` (darker in light mode, lighter in dark mode), so don't set the strong variant inline again — an inline value would override the dark-theme derivation.

`Profile.theme` is `light`/`dark`/`system`; `base.html` renders it as `data-theme-preference` and only a light/dark fallback as `data-theme`, which the inline head script (and `getThemePreference`/`getInitialTheme` in `base.js`, also on `prefers-color-scheme` changes) resolves — CSS only ever sees `data-theme="light|dark"`. `syncThemeColorMeta` keeps `<meta name="theme-color">` on the resolved `--bg-base` after every theme, color-theme, strength or accent change. `Profile.color_theme` (`COLOR_THEME_CHOICES`) is rendered as `data-color-theme` on `<html>` and selects a neutral-palette block in `base.css` (background, text colors, glass surfaces, shadows, solid panel); `sand` lives in `:root, [data-color-theme="sand"]`, every other theme has a light block plus a `[data-theme="dark"][data-color-theme="…"]` block that must set the same properties, or light values leak into dark mode. A new theme needs a `COLOR_THEME_CHOICES` entry (plus migration) and both blocks; its light block also sets `--theme-swatch`, the signature tone of its settings swatch. Theme blocks define the *inputs* `--theme-bg`/`--theme-glass-bg`/`--theme-glass-bg-soft`/`--theme-panel-solid`; `:root` derives `--color-bg`, `--glass-bg`, `--glass-bg-soft` and `--surface-panel-solid` from them by mixing in `--theme-swatch` scaled by `--theme-strength` (`Profile.theme_strength` / 100, inline on `<html>`) and `--theme-mix` (smaller in dark mode) — so never set those four derived tokens in a theme block. The page background is no image: `partials/page_background.html` is an inline SVG of waves (included in `base.html` and `offline.html`) whose fills come from `--bg-base`/`--bg-wave-soft`/`--bg-wave-deep`, derived from the theme's `--color-bg` and the accent. The settings swatches carry `data-color-theme` themselves to preview each palette, so don't scope those selectors to `:root`.

### Design tokens

`base.css` `:root` (with `[data-theme="dark"]` overrides) is the single source for colors, radii and focus states; page stylesheets must not redefine `--color-accent`/`--color-accent-strong` or introduce their own accent palette — every area follows the profile accent. Use `color-mix(in srgb, var(--color-accent) N%, transparent)` instead of hard-coded `rgba(181, 150, 104, …)` tints; `--color-danger`/`--color-success`/`--color-warning` (plus `--color-danger-solid` for filled badges with light text) for status colors; `--color-on-accent` (dark) for text on accent fills — the selectable accents are mid-light, so white text on them fails contrast — and `--color-on-solid` (light) for text on `--color-danger-solid`/warning or dark fills; elements that sit directly on the page background (not inside a `.glass-panel`) need their own `var(--surface-panel)` + `backdrop-filter` surface, or their text becomes unreadable over light parts of the background image in dark mode; `--surface-control`/`--surface-control-hover` for translucent control backgrounds; `--surface-panel-solid` for opaque dialogs/popovers; `--radius-xs` (8) / `-sm` (12) / `-md` (16) / `-lg` (22) / `-pill` instead of ad-hoc pixel radii; and the type scale `--text-2xs` (0.7rem) / `-xs` (0.76) / `-sm` (0.82) / `-md` (0.88) / `-base` (0.95) / `-lg` (1.05) / `-xl` (1.3) / `-2xl` (1.45) / `-3xl` (2) instead of ad-hoc `rem` font sizes — only display sizes (hero titles, clock, big numbers, `clamp()`) and the editor's relative `em` headings stay outside the scale. `DesignTokenTests` in `app/tests/test_core.py` enforces the accent, gold-tint and font-size rules by scanning every stylesheet, so a violation fails CI with its `file:line`. Shared components live in `base.css` too: the `.tone-*` palette for user-picked colors, `.switch`, the custom checkbox, and the `dialog::backdrop` (dialogs containing a `.glass-panel` automatically get the opaque surface). While a modal dialog is open, `html:has(dialog:modal)` locks page scrolling and `dialog *` uses `overscroll-behavior: contain`; `trackPageOverflow` in `base.js` sets `data-page-scrolls` so only pages that really scroll keep their scrollbar gutter (no sideways jump either way). Open dialogs with `showModal()` to get this — `show()` stays non-modal and doesn't lock.

### Accessibility

`app/tests/test_accessibility.py` renders every main page (signed in and public) and fails on a second `<main>`, a missing `<h1>`, duplicate ids, or a form field without an accessible name (`<label>`, `aria-label`/`aria-labelledby` or `title`). Two forms on one page with the same field names must not share ids: give the second one its own `auto_id` prefix in its `__init__` (as `CalendarEventEditForm`, `CalendarReminderForm`, `ConversationStartForm`, `CustomHolidayForm`, `TaskListForm`, `TaskLabelForm` do). Page sidebars inside `<main>` are `<section aria-label>`/`<div>`, not `<aside>`. A `<form>` may not carry `role="tabpanel"`, so settings panels with a form are `<section role="tabpanel">` around a `<form class="settings-panel-form">` (`display: contents`). Every drag interaction needs a keyboard route: dialogs for calendar events, the board's status buttons, Alt+↑/↓ in `tasks.js`, the notes "Verschieben" dialog, dashboard arrows.

### Focus rings on wrapped inputs

`base.css`'s global `input:focus-visible` rule (actually `a`/`button`/`summary`/`input`/`textarea`/`select`/`[tabindex]`) puts a `box-shadow` on the focused element itself, following *its own* border-radius. Several search/text fields render as a borderless `<input>` (`border: 0`, no radius) inside a decorative rounded wrapper div (background + border + border-radius); the same happens to non-input focusable elements carrying `tabindex` (e.g. a `contenteditable` region) inside a rounded container. Focusing the input then draws a square-cornered glow that visibly breaks out of the rounded wrapper. Fix on the wrapper, not the inner element: add `<wrapper>:focus-within { border-color: var(--focus-border); box-shadow: var(--focus-ring); }` and `<wrapper> input:focus-visible { box-shadow: none; }` (swap `input` for whatever the focusable descendant actually is). See `.search-field` (weather.css), `.notes-search` (notes.css), `.reminder-date-field` (calendar.css), or `.note-editor-page` (notes.css — wrapping the `tabindex`-bearing `.tiptap` contenteditable) for reference.

### Notification idempotency

Delivery is "at most once" via columns rather than a queue: `CalendarReminder.email_notified_at` / `desktop_notified_at`, `CalendarEventAttendee.email_notified_at` / `desktop_notified_at`, `NoteActivityNotification.email_notified_at` / `desktop_notified_at`, `Task.email_notified_at` / `desktop_notified_at`, and the `WeeklySummaryDelivery` unique `(user, week_start)` constraint. Desktop notifications are *claimed* by the browser (`claim_due_desktop_reminders`, `claim_due_event_invitations`, `claim_due_note_activity`, `claim_due_desktop_tasks` each use `select_for_update` + a batch update) so multiple open tabs cannot double-notify; `claim_desktop_notifications` (the single `/notifications/claim/` endpoint `base.js` polls) merges all four lists into one JSON response, each gated by its own feature flag. Preserve this pattern for any new notification type.

The persistent inbox is backed by `UserNotification`, which stores one durable row per recipient/source using the unique `(recipient, source_key)` constraint, with `read_at` controlling its unread badge. `NotificationPreference` stores per-user channel choices for calendar, tasks, notes, weather, and tools (shared vocabulary lists); missing rows deliberately mean all category channels enabled for backwards compatibility, while the global `Profile.notify_email` and `Profile.notify_desktop` fields remain master switches. `app/services/notification_preferences.py` is the shared policy layer and must be used by Inbox, e-mail, Web Push, and browser-fallback paths. `app/services/notifications.py` materializes due reminders/tasks and mirrors calendar invitations, note mentions/comments/shares, vocabulary list shares, and detected weather alerts without duplicates. Materialization belongs to the explicit notification-center and delivery/scheduled paths; the global context processor only counts existing unread rows and normal dashboard rendering must not create inbox rows. `/notifications/` backfills existing supported source rows for the current user; opening an item or the explicit state action changes only that user's inbox row. New notification-capable source flows must feed both their delivery channel, when applicable, and `UserNotification`. Web Push uses `WebPushSubscription` per browser/device and a unique `WebPushDelivery` per subscription/inbox row; `app/services/web_push.py` validates provider hosts, honors profile-local quiet hours, retries transient failures, removes HTTP 404/410 subscriptions, and is run by `run_automations`. The settings test action targets only the signed-in user's exact device subscription. Never log endpoints or expose the VAPID private key.

### Tasks

Details in [docs/architecture/tasks.md](docs/architecture/tasks.md). Domain logic is in `app/services/tasks.py`; the view only dispatches `form_name` branches. `visible_tasks(user)` (own + assigned + shared, via subquery so no duplicates) is the read/toggle scope; edit/delete/reorder stay filtered on `user=`. Every status change goes through `_apply_task_status`; any path that moves `due_at` later calls `rearm_task_reminders`. Recurrence advances on completion (one new sibling), not as a pre-built series.

### Trash

Details in [docs/architecture/trash.md](docs/architecture/trash.md). Deleting tasks, events, habits, vocabulary lists and recipes goes through `app/services/trash.py` (`trash_*` snapshot, then real delete) plus `trashed_message` for the undo flash. A new model hanging off a trashed one belongs in its snapshot and in `CHILDREN`.

### Vacation planner

Details in [docs/architecture/vacation-planner.md](docs/architecture/vacation-planner.md). `app/services/vacation_planner.py` does German vacation-day accounting per user and year (`holidays` package with a stdlib fallback), gated by `vacation_planner`.

### Tools

Details per tool in [docs/architecture/tools.md](docs/architecture/tools.md). `/tools/` is a hub gated by the `tools` flag; cards come from `TOOLS` in `app/view_models.py`. A new tool gets a view under `/tools/<name>/`, a re-export from `app/views/__init__.py`, a `TOOLS` entry, a command palette link and, if it holds searchable text, an area in `global_search`. Rules across tools:

- **Never trust client JSON** for UML diagrams and whiteboards: `uml_content.py`/`whiteboard_content.py` rebuild documents against exact allowlists, and the JS `LIMITS` mirror them (change both sides together).
- Fetching any user-supplied URL goes through `app/services/safe_fetch.py` (the SSRF boundary), throttled per user with `count_hit`: recipe import, wish lists, bookmark previews, calendar subscriptions.
- Sharing (whiteboards, kanban, wish lists, vocabulary, shopping, expenses) goes through `accessible_*`/`list_owner_for` helpers built on subqueries; owner-only actions keep filtering on `owner=request.user`. A share creates an inbox row that is deleted again when the share ends.

### Dashboard customization

Details in [docs/architecture/dashboard.md](docs/architecture/dashboard.md). Widgets are registered in `DASHBOARD_WIDGETS` (`app/services/dashboard.py`); layouts pass `normalize_dashboard_layout` on read and `validate_dashboard_layout` on write (`_setting_error` is the single allowlist). A new widget needs a registry entry, its partial starting with `partials/dashboard_widget_head.html`, and a useful empty state, since existing users get it appended. Dashboard actions use the `return_to` convention and soft regions, not a separate AJAX layer.

### Soft submit (in-place updates)

POST forms inside a `[data-soft-region]`, or carrying `data-soft` themselves, are sent by `base.js` with `fetch` instead of a page load; the views stay plain POST→redirect→GET. From the HTML of the answer, every region on the page is swapped by its `id` (region element kept, attributes and children replaced — `test_accessibility.py` fails on a region without id), open `<details>` and scroll positions of elements with an id survive, focus moves to the same control in the new markup, and the flash messages follow. A form outside every region (e.g. in a `<dialog>`) is reset and its dialog closed after a redirect; no redirect (form errors), a different target path, 4xx or a network error fall back to the normal submit, 5xx to a reload. Regions are mostly `.soft-contents` (`display: contents`) wrappers so the layout doesn't change. Page scripts must survive a swap: delegate events to `document`, re-query elements, and redo per-render setup on the `lunora:soft-update` event (`tasks.js`, `calendar.js`, `task_timer.js`). Programmatic submits use `requestSubmit()` — `submit()` fires no submit event and reloads the page (`data-auto-submit` already picks the right one). A form that always leads to another page (opening a notification, copying a list, deleting the shown recipe) carries `data-no-soft` so it isn't fetched once for nothing. Regions so far: the vacation planner, the recipes page's meal plan, expense group detail, the navigation's unread-notification count (`#nav-notification-count`, a span inside the link because a region drops its own focus ring; its `data-unread-count` is mirrored onto the installed app's icon by `syncAppBadge` in `base.js` after every swap, and the service worker sets a plain badge on push), shopping list, habits, tasks, the whole calendar layout, the notification center, vocabulary lists and list detail, recipe detail, and the dashboard's tasks/habits/notifications/shopping/bookmarks widgets. The vacation planner is one region (`#vacation-content`, carrying `data-vacation-year`) with its context menu and dialog outside it; forms moved into that dialog get `data-soft` while there, and a node whose placeholder was swapped away is dropped on close. Anything outside a region (other widgets) stays stale until the next page load. Full page loads cross-fade via `@view-transition` in `base.css`.

### Command palette

`app/templates/app/partials/command_palette.html` (included in `base.html` for signed-in users) is a `<dialog>` of plain links, gated by `feature_flags` like the navigation; `initCommandPalette` in `base.js` opens it on Ctrl/Cmd+K, filters entries by their text plus `data-keywords` (case- and accent-insensitive), and falls back to `/search/?q=` for the typed text. While typing it also lists live hits from `/search/quick/`; quick capture sends the line to the tasks/calendar quick-add or `share_note`, and a leading `!a`/`!t`/`!n` picks one of those directly. Pages opened from the palette move to the top next time (their element ids in `localStorage` under `lunora-recent-commands`; never typed text or search hits). A new page or settings section that users navigate to belongs in that partial.

### Static asset versioning

Static CSS/JS entry files are cache-busted with a content hash rather than Django's `ManifestStaticFilesStorage`: `app/templatetags/static_versioning.py`'s `{% versioned_static %}` appends `?v=<sha256-of-file-contents>` to a `{% static %}` URL, and `{% static_version %}` combines several files into one digest (used for the service worker's cache name in `service-worker.js`). Every page template must `{% load static static_versioning %}` and use `versioned_static` instead of `static` for its own CSS/JS entry assets. Vite chunks are imported by content-hashed filename instead. `deploy/nginx-lunora.conf` gives query-versioned assets and hashed files under `js/bundles/chunks/` a one-year immutable cache, while unversioned static assets and `/media/` remain at one hour. Static subresources referenced from CSS need an explicit version query because template tags cannot rewrite CSS `url(...)` values. Inter, Font Awesome and KaTeX's stylesheet and fonts are self-hosted under `app/static/vendor/` — no CDN, so formulas keep their styling offline. `npm run vendor:katex` (`scripts/vendor-katex.mjs`) re-copies KaTeX from `node_modules` with woff2-only `?v=katex-<version>` font URLs; rerun it after bumping `katex`, or `StaticVersioningTests.test_vendored_katex_stylesheet_matches_installed_katex` fails. Keep the Font Awesome stylesheet non-blocking via `media="print"` + `data-late-stylesheet` (the nonced head script in `base.html` swaps the media) and retain the `<noscript>` fallback. Don't switch it back to a `rel="preload"` swap: under the service worker Chrome can't reuse the preload ("cross-world service worker resource mismatch") and fetches the stylesheet twice on every page.

### Content-Security-Policy

`SECURE_CSP` in `lunora/settings.py` is enforced by Django's `ContentSecurityPolicyMiddleware`: scripts only from `'self'`, the page's nonce, Turnstile and the pinned Leaflet path on unpkg; inline styles stay allowed because appearance settings, the editor and Leaflet set style attributes. Inline event handlers can't work: use `data-auto-submit` (submit on change) and `data-confirm` (ask before submitting), both handled in `base.js`, or `addEventListener`. An inline `<script>` needs `nonce="{{ csp_nonce }}"`, which only exists when the template renders with the request; `offline.html` renders without one, so its script is the external `js/offline.js`. `ContentSecurityPolicyTests` scans all templates for scripts the policy would block. A new outside source (script, image, frame) must be added to the policy, or it fails silently in the browser. In production Cloudflare injects an inline challenge script into every page, which can't be tested locally, so check the browser console on the live site after changing the policy.

### Service worker offline caching

`app/templates/app/service-worker.js` precaches only the app shell (`APP_SHELL_URLS`: the offline page, its CSS, and brand icons) at install time — it must never gain a hardcoded personal-page path (`/notes/`, `/messages/`, `/calendar/`, `/home/`, media URLs); `PwaTests.test_service_worker_does_not_precache_personal_pages_or_media` enforces this. Beyond the shell, every same-origin full-page navigation (`request.mode === "navigate"`) goes through `servePage()`: network-first, and on a successful (`.ok`) response the page is also written into the separate `PAGES_CACHE` so the same URL can still open while offline, trimmed to `MAX_CACHED_PAGES` most-recent entries. `PAGES_CACHE` and `STATIC_CACHE` are both named with the same `{% static_version %}` hash, so a deploy that changes shell CSS/JS also drops old page snapshots (avoiding offline pages that reference static assets no longer being generated). Because the pages cache holds whatever authenticated content a user last viewed, the service worker intercepts the `POST /logout/` form submission and clears `PAGES_CACHE` once it completes, so a signed-out session's pages can't resurface for the next person on a shared device. The manifest's `share_target` POSTs to `/share/`; that POST has no CSRF token, so the service worker answers it with a 303 to `/share/#title=…&text=…&url=…` (a fragment never reaches the server or its logs), `app/static/js/share.js` fills the form from the fragment, and the form's buttons `formaction`-post to the existing task/bookmark/recipe/wish-list endpoints, notes to `share_note`. Runtime `/static/*` asset caching (`serveStaticAsset`) is unrelated and untouched by this — it's cache-first, keyed by either a query-versioned entry URL or a content-hashed Vite chunk URL.

### Audit log

`AuditLogEntry` rows (`app/services/audit_log.py`, `record(action, request, actor=..., summary=...)`) back the superuser-only "Audit-Log" on `/administration/` (`audit_log_page`, filters `all`/`security`/`logins`/`warnings`, 50 per page). Logins, logouts and failed logins come from Django's auth signals (`connect_signals` in `AppConfig.ready`); everything else is recorded at its call site (registration, password reset confirm, password change in settings (which also signs out the other sessions), 2FA enable/disable/recovery codes, session revokes, calendar feed reset/disable, data export, account deletion before `delete_account`, system settings changes with the changed switches, emergency logout). `record` writes in its own savepoint and never raises. Summaries are short German text and must never contain secrets (tokens, codes, passwords); a failed login only gets an actor when the typed identifier matches exactly one account, otherwise just "Unbekanntes Konto" — the typed text is never stored. `actor_label` keeps the e-mail after the account is deleted (FK `SET_NULL`). The IP comes from `LUNORA_CLIENT_IP_HEADER` (e.g. `CF-Connecting-IP`, only behind a proxy every request passes) or `REMOTE_ADDR`, validated with `ipaddress`. `run_scheduled_tasks` purges entries older than `AUDIT_LOG_RETENTION` (180 days). A new security-relevant action gets an `ACTION_*` constant (plus migration for the choices) and a `record` call.

### Health check

`/healthz/` (`healthz` in `core_views.py`, `app/services/health.py`) is public, GET-only and `never_cache`: it checks the database, the cache and the `AutomationHeartbeat` row that `run_automations` touches after every pass without a failed step (`_guarded` in `scheduled_tasks.py` keeps one failing step from stopping the later ones; overdue after three intervals, at least five minutes), answers 200/503 with only fixed status words, and logs causes server-side. The automations check only counts where `LUNORA_HEALTH_REQUIRE_AUTOMATIONS` is on (default: production). It is in `SECURE_REDIRECT_EXEMPT` so host-local monitors can use plain HTTP; never add versions, hostnames or exception text to its body.

## Configuration

`lunora/settings.py` reads a plain `.env` at the repo root with its own tiny parser (`load_env_file`, `os.environ.setdefault` — real environment variables win) plus `env_bool`/`env_int`/`env_list` helpers. `.env.example` is the documented reference. With `DJANGO_DEBUG=false`, missing `DJANGO_SECRET_KEY` or `DJANGO_ALLOWED_HOSTS` raises `ImproperlyConfigured`, and the HTTPS/HSTS/secure-cookie toggles default to on.

Do not commit `.env`, `db.sqlite3`, `media/`, or `private_media/`.

## Testing

Python tests live in the `app/tests/` package, one `test_<area>.py` module per area (Django `TestCase` + test client), grouped into per-area classes; shared fixtures and fake HTTP responses sit in `app/tests/helpers.py`. Tests assert observable behavior: status codes, redirects, rendered templates, context contents, JSON payloads, form validation, and service return values. External HTTP is stubbed with local fake response classes (`FakeJsonResponse`, `FakeWeatherResponse`, `FakeWeatherTileResponse`) patched over the service's fetch helper — no network in tests. `TEST_RUNNER` (`lunora/test_runner.py`) points `MEDIA_ROOT` and `PRIVATE_MEDIA_ROOT` at a temporary directory for the whole run, so uploads made by tests never land in the real media folders, and swaps in the MD5 password hasher — with the production PBKDF2 cost every user-creating test took about two seconds and the suite ran for hours, so don't add per-class `PASSWORD_HASHERS` overrides; both overrides reach `--parallel` workers too (forked ones inherit them, spawned ones on Windows get them through `IsolatedParallelTestSuite.process_setup`), but on Windows spawning makes parallel runs barely faster than serial ones; `PrivateNoteStorage` resolves its location lazily so such overrides reach it. Close streaming responses (`FileResponse` downloads) you don't consume, or the open file blocks temp-dir cleanup on Windows. Model changes need a migration plus test coverage.

## Related files

`docs/architecture/*.md` hold the per-area details moved out of this file (notes, messages, calendar, tasks, trash, vacation planner, tools, dashboard, accounts and security); read the one for the area you touch before changing it, and keep it current like this file. `AGENTS.md` and `README.md` (German) cover overlapping ground; `README.md` is the authoritative list of env vars and setup steps. `DEPLOYMENT.md` (German) covers the Ubuntu production host. Since 27 October 2026 Lunora is public at `https://lunora.yfserver.de`, hosted on the home server `yfsrv1` behind the Cloudflare Tunnel `lunora-yfsrv1` (`deploy/cloudflared.service`, token only in `/etc/cloudflared/token`; host specifics such as the STRATO SMTP setup are in `DEPLOYMENT.md` → "Besonderheiten auf yfsrv1"). **Every merge to `main` is production-effective**, and `main` is branch-protected (pull request from `develop` with a green `Tests` check, enforced for admins — never merge locally and push `main`): `lunora-auto-deploy.timer` polls `origin/main` about once a minute and runs `scripts/deploy.sh` (a preflight that exports the new commit into a temp dir via a throwaway index and `git checkout-index` — not `git archive | tar`, which fails under the unit's `RestrictSUIDSGID=true` on Ubuntu 26.04 — with its own venv, installs with `-c constraints.txt` and runs `check --deploy` plus `makemigrations --check --dry-run` before the live checkout is touched; then pip install, `check --deploy`, `migrate`, `collectstatic`, service restart) — treat a merge to `main` as a release and let CI on `develop` pass first. Every push to `develop` is deployed the same way to the dev instance `https://lunora-dev.yfserver.de` (separate checkout, database, service account and units; behind Cloudflare Access; `DEPLOYMENT.md` → "Dev-Instanz"). `scripts/deploy.sh` and `scripts/auto-deploy.sh` take `LUNORA_*` environment overrides for it, and their defaults must stay the production values. Every night at 03:00 `scripts/refresh-dev.sh` replaces the dev data with a copy of production (database, media, private_media; Web Push subscriptions dropped; it refuses to run unless the dev env uses the console mail backend), so treat the dev instance as holding real user data. Page titles use ` • ` as separator (`Notizen • Lunora`) and `base.html` appends `LUNORA_TITLE_SUFFIX` (`-dev` on the dev instance) after the title block: keep `{% block title %}` on one line, and JS that rewrites `document.title` (`uml.js`, `whiteboard.js`) must keep that suffix. CI runs the same missing-migrations check, which only works because migrations are platform-independent: choices derived from the runtime environment (like `Profile.timezone_name`'s, from `zoneinfo.available_timezones()`, which differs between Windows' `tzdata` package and Ubuntu's system database) must be passed as a module-level callable (`app.models.timezone_choices`), so migrations store a reference instead of the list. `CHANGELOG.md` tracks user-facing changes under an `## Unreleased` heading — add a bullet there for any new feature, behavior change, or notable fix (skip pure refactors, tests, or internal tooling).
