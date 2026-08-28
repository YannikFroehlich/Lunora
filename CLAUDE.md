# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Lunora is a Django 6.0 workspace dashboard (dashboard, weather, calendar, reminders, direct messages, rich-text notes) with a single Django app (`app/`) and a small Vite-built notes editor bundle. Third-party Python packages are kept deliberately minimal: `Django`, `Pillow`, `reportlab`, `gunicorn` (production WSGI server), `redis` (production cache backend), `psycopg` (PostgreSQL driver, needed for production and for the ranked full-text note search SQL path), `pywebpush` (Web Push delivery), and `holidays` (German public-holiday generation for the vacation planner, with a stdlib fallback). HTTP calls, iCal parsing, and env loading are still hand-rolled on the stdlib. Don't add further dependencies without asking first.

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

Bulk-import official public holidays for the vacation planner over a year range (optionally scoped to one federal `--subdivision`):

```bash
python manage.py import_public_holidays --from-year 2026 --to-year 2027
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

- Documents are Tiptap ProseMirror JSON in `Note.document`. **Never trust client JSON**: `app/services/note_content.py` re-validates every node/mark against explicit allowlists (node types, mark types, fonts, sizes, line heights, hex colors, size/depth/node-count limits) and derives `plain_text` server-side. Adding an editor feature means extending both `frontend/notes.js` and that allowlist. Math is a `mathInline`/`mathBlock` node holding a raw `latex` string (length-capped, rendered client-side with KaTeX) validated the same way.
- Concurrency is optimistic on `Note.revision`. A stale `base_revision` raises `NoteConflictError` → HTTP 409 with `{"error": "revision_conflict", "note": ...}`; the client resubmits with `conflict_resolution: true`. `NoteVersion` snapshots are written on interval/restore/conflict and pruned.
- Pin/archive/color/icon/folder live in `NoteUserState`, not on `Note`, because notes are shared and these are per-viewer — the same shared note can sit in a different folder, or be pinned, for its owner and each person it's shared with. `accessible_notes()` annotates `state_is_pinned`, `state_is_archived`, `is_shared_with_user`, `has_unseen_share`, `state_color`, `state_icon` — query through it (or `get_accessible_note`) rather than `Note.objects` so permission scoping and those annotations stay consistent. `set_note_style` validates `color`/`icon` against `NoteUserState.COLOR_CHOICES`/`ICON_CHOICES` before saving.
- Folders (`NoteFolder`, self-referential `parent`, depth capped by `FOLDER_MAX_DEPTH`) and notes share one ordered tree per user. `move_note_tree_item` handles drag-and-drop moves (`before`/`after`/`inside`/`root`) and re-indexes the affected container via `_reindex_tree_container`; `_validate_folder_move_destination` blocks moving a folder into its own descendant. A note can be created from a built-in template (`NOTE_TEMPLATES`) or a per-owner custom one (`NoteTemplate`, saved from an existing note via `create_note_template`).
- Attachments are stored under `PRIVATE_MEDIA_ROOT` via `private_note_storage`, addressed by UUID `file_id`, and only served through `note_attachment_download`, which re-checks note access. They are never under `MEDIA_URL`.
- PDF export renders server-side with reportlab (`app/services/note_pdf.py`); Markdown export (`app/services/note_markdown.py`) is a plain server-side renderer of the same document. Both go through the normal access check so permissions apply to exports too.
- Search goes through `app/services/note_search.py` (`search_notes`), used by both the notes list and `global_search` — never re-filter with `icontains` at a call site. It dispatches on `connection.vendor`: PostgreSQL matches a weighted `tsvector` (`title` A, `plain_text` B, `german` config) ranked with `SearchRank`; SQLite falls back to substring matching with an equivalent SQL-side rank. Both share `parse_search_query` (bare words AND, `"phrases"`, `-exclusions`) so behaviour matches. Two things are deliberate: terms are matched as `:*` prefixes **and** kept as substrings, because German compounds mean "Rakete" must find "Raketenstart" and "start" must too; and the tsvector is built per query rather than stored, so `Note` needs no denormalised column — the upgrade path if it gets slow is a `SearchVectorField` + GIN index populated in `save_note`. Terms fed to `search_type="raw"` must stay bare words (`^\w+$`) or PostgreSQL raises a query-time error. Because the test suite runs on SQLite, `NotePostgresSearchSqlTests` compiles the production SQL against a PostgreSQL backend to cover that branch.
- `frontend/notes.js` builds to `app/static/js/bundles/notes.js`, which **is committed**. After editing anything in `frontend/`, run `npm run build` and commit the regenerated bundle. `npm test` (vitest) only covers `frontend/**/*.test.js` — the small pure helpers.
- The editor toolbar (`notes.html`) keeps only the most-used controls (undo/redo/save, bold/italic/underline/strike, lists) always visible; everything else sits behind two `data-toolbar-tab="text"|"insert"` tabs (`activateToolbarTab` in `notes.js`, modeled directly on `settings.js`'s `activateSettingsSection`) toggling sibling `data-toolbar-panel` groups. This works with zero changes to command dispatch because `runCommand`/`applyFormatControl`/`updateToolbarState` already select every control by its `data-command`/`data-format` attribute rather than DOM position — moving a button between panels never breaks it. The code-language `<select>` (`data-code-language-group`) deliberately stays a *sibling* of both panels, not nested inside one, because its visibility is driven by cursor position (inside/outside a code block), independent of which tab is active. `openFormatControl` (used when a keyboard shortcut like Alt+Shift+F targets a `[data-format]` control) activates that control's owning tab first if it's currently hidden, so shortcuts never silently focus a `display:none` element.
- Code blocks get syntax highlighting via `@tiptap/extension-code-block-lowlight` + `lowlight`'s `common` language bundle (StarterKit's own `codeBlock` is disabled via `codeBlock: false`). `note_content.py`'s `ALLOWED_CODE_LANGUAGES` must stay a subset of that bundle's registered language keys, or a saved language would highlight in the editor but fail server-side validation.
- `@`-mentions are a `mention` atom node (`{userId, label}`) built on `@tiptap/suggestion` (no `tippy.js` — the popup is a manually positioned `<div>`, matching `messages.js`'s context-menu pattern). The suggestion list (`note_mention_candidates_api`) and the server-side save-time check (`_validate_mention_references` in `app/services/notes.py`) both restrict candidates to users who already have access to the note (owner + `NoteShare` rows) — mentioning someone without access is rejected, not auto-shared.
- Note-to-note links are a `noteLink` atom node (`{noteId, label}`); `note_link_candidates_api` suggests targets from `accessible_notes()` and `_validate_note_link_references` re-checks the same access at save time, the same pattern as mentions. `save_note` diffs the document's `noteLink` references against `NoteLink` rows (`_set_note_links`) to keep backlinks in sync without a stored reverse index.
- Inline comments are a `commentThread` mark (`{threadId}`, a client-generated UUID) plus `NoteCommentThread`/`NoteComment` rows. `save_note` cross-checks every `commentThread.threadId` found in the document against real `NoteCommentThread` rows for that note, the same way it already does for attachment/mention references — a document can't reference a thread it never created via `note_comments_api`. `duplicate_note` strips `commentThread` marks from the copy (`_strip_comment_marks`) because the new note has no threads of its own yet; forgetting that step makes the duplicate un-savable.
- Both mentions and comments notify through the shared `NoteActivityNotification` model (`kind` = `mention`/`comment`) — see Notification idempotency below.

### Messages live updates

The messages page is server-rendered; `messages_live_updates` re-renders the same partials in `app/templates/app/partials/` via `render_to_string` and returns them as HTML strings inside a JSON payload that `app/static/js/messages.js` swaps in. The full view and the live view must build compatible template contexts — changing a partial's context requirements means updating both `messages()` and `messages_live_updates()`. The payload also carries a plain-string `typing_label` (built by `_typing_label()` from each `ConversationMember.typing_until`, pinged by the client via `chat_typing_ping`) that isn't tied to any partial — `messages.js` writes it directly into `#typing-indicator`.

Conversations can be group chats (`Conversation.is_group`, created by `start_conversation` when 2+ recipients are selected); group membership changes via the `member_action` form's `add_member`/`leave_group` actions. Chat messages may carry a single optional file/image attachment (`ChatMessageAttachment`, stored the same way as `NoteAttachment` — see `app/services/chat_files.py`) and an optional `reply_to` (a lightweight quote of another message in the same conversation, not a nested thread) — both ride through the same `messages()`/`messages_live_updates()` context-building helpers (`_build_attachment_item`, `_build_reply_preview`, `_message_preview_text`), so extending one still means keeping both views' contexts in sync.

### Calendar

`app/services/calendar_service.py` contains a hand-written iCal unfolder/parser (including weekly recurrence expansion) — no `icalendar` dependency. Fetching is SSRF-hardened: `app/services/url_safety.py` rejects non-public hosts/ports and re-validates resolved addresses, and `NoRedirectHandler` blocks redirects so a public URL cannot bounce to an internal one. iCal URLs are private user data — do not log full URLs or surface them outside the owning user's settings page.

Synced events dedupe on `(source, external_id)`; manually created events have `source = None` and are excluded from source-based clearing.

Manual events can invite other Lunora users via `CalendarEventAttendee` (`CalendarEventForm.attendees`, checkbox-multi-select same as `ConversationStartForm.recipient` in Messages) — synced/iCal events cannot, since they're read-only mirrors of an external feed, not something a Lunora user "owns" in a shareable sense. `view_models.get_calendar_context` unions the requesting user's own events with `Q(attendees__user=user)` so invitees see the event on their own calendar (`.distinct()` is required — the join can otherwise multiply an event with several attendees). Accept/decline goes through the `calendar()` view's `event_rsvp` form_name branch, scoped to the requesting user's own `CalendarEventAttendee` row only.

### Weather

`app/services/weather_service.py` keeps the OpenWeather key strictly server-side, caches JSON responses for `WEATHER_CACHE_SECONDS`, and proxies map tiles through `weather_map_tile` so the key never reaches the browser. Without a key the whole page falls back to demo data (`_fallback_*` helpers) — new weather features need both the API path and a fallback path, since tests cover the keyless behavior.

### Per-user time and formatting

`app.middleware.UserTimezoneMiddleware` activates the signed-in profile's timezone for each request and deactivates it afterwards. Date/time strings for users must go through `app/services/user_preferences.py` (`format_user_date`, `format_user_time`, `format_user_datetime`, `localtime_for_user`) because format and timezone are per-profile settings, not global ones.

`app.context_processors.appearance_settings` derives the glass-UI CSS variables (accent mix, overlay alphas, blur, density) from `Profile` for every template, including anonymous requests.

### Focus rings on wrapped inputs

`base.css`'s global `input:focus-visible` rule (actually `a`/`button`/`summary`/`input`/`textarea`/`select`/`[tabindex]`) puts a `box-shadow` on the focused element itself, following *its own* border-radius. Several search/text fields render as a borderless `<input>` (`border: 0`, no radius) inside a decorative rounded wrapper div (background + border + border-radius); the same happens to non-input focusable elements carrying `tabindex` (e.g. a `contenteditable` region) inside a rounded container. Focusing the input then draws a square-cornered glow that visibly breaks out of the rounded wrapper. Fix on the wrapper, not the inner element: add `<wrapper>:focus-within { border-color: rgba(181, 150, 104, 0.62); box-shadow: 0 0 0 3px rgba(181, 150, 104, 0.14); }` and `<wrapper> input:focus-visible { box-shadow: none; }` (swap `input` for whatever the focusable descendant actually is). See `.search-field` (weather.css), `.notes-search` (notes.css), `.reminder-date-field` (calendar.css), or `.note-editor-page` (notes.css — wrapping the `tabindex`-bearing `.tiptap` contenteditable) for reference.

### Notification idempotency

Delivery is "at most once" via columns rather than a queue: `CalendarReminder.email_notified_at` / `desktop_notified_at`, `CalendarEventAttendee.email_notified_at` / `desktop_notified_at`, `NoteActivityNotification.email_notified_at` / `desktop_notified_at`, `Task.email_notified_at` / `desktop_notified_at`, and the `WeeklySummaryDelivery` unique `(user, week_start)` constraint. Desktop notifications are *claimed* by the browser (`claim_due_desktop_reminders`, `claim_due_event_invitations`, `claim_due_note_activity`, `claim_due_desktop_tasks` each use `select_for_update` + a batch update) so multiple open tabs cannot double-notify; `claim_desktop_notifications` (the single `/notifications/claim/` endpoint `base.js` polls) merges all four lists into one JSON response, each gated by its own feature flag. Preserve this pattern for any new notification type.

The persistent inbox is backed by `UserNotification`, which stores one durable row per recipient/source using the unique `(recipient, source_key)` constraint, with `read_at` controlling its unread badge. `NotificationPreference` stores per-user channel choices for calendar, tasks, notes, and weather; missing rows deliberately mean all category channels enabled for backwards compatibility, while the global `Profile.notify_email` and `Profile.notify_desktop` fields remain master switches. `app/services/notification_preferences.py` is the shared policy layer and must be used by Inbox, e-mail, Web Push, and browser-fallback paths. `app/services/notifications.py` materializes due reminders/tasks and mirrors calendar invitations, note mentions/comments/shares, and detected weather alerts without duplicates. `/notifications/` backfills existing supported source rows for the current user; opening an item or the explicit state action changes only that user's inbox row. New notification-capable source flows must feed both their delivery channel, when applicable, and `UserNotification`. Web Push uses `WebPushSubscription` per browser/device and a unique `WebPushDelivery` per subscription/inbox row; `app/services/web_push.py` validates provider hosts, honors profile-local quiet hours, retries transient failures, removes HTTP 404/410 subscriptions, and is run by `run_automations`. The settings test action targets only the signed-in user's exact device subscription. Never log endpoints or expose the VAPID private key.

### Tasks

`Task` (`app/models.py`) is a per-user to-do gated by the `tasks` feature flag, with an optional `due_at`, `priority` (none/low/medium/high), a `task_list` FK (null = "Inbox"), a `labels` M2M, and a one-level-only `parent` self-FK for subtasks. `app/services/tasks.py` holds the domain logic — `app/views/tasks_views.py` stays thin, dispatching `form_name` POST branches (`task_add`/`task_toggle`/`task_delete`, plus list/label CRUD) into it, the same single-view-many-branches shape the view already had before lists/labels existed. `get_tasks_context` groups subtasks under their parent (`item.subtasks`) rather than returning a flat list, and derives `due_state`/`status_label`/`status_tone` plus counts (`all`/`open`/`done`/`overdue`/`today`/`upcoming`) per item; `today` includes overdue-and-open, `upcoming` is the next 7 days.
- `TaskForm` declares `task_list`/`parent`/`labels` as querysets scoped to the request user in `__init__` (the same pattern as `CalendarEventForm.attendees`) — this is also what caps subtask nesting at one level: the `parent` field's queryset only offers top-level tasks, so a subtask can never itself be chosen as a parent. There is deliberately no separate service-side re-validation of that constraint; the form's scoped queryset already enforces it.
- Recurrence (`recurrence_rule`: none/DAILY/WEEKLY/MONTHLY/YEARLY, reusing `CalendarEventForm.repeat`'s choices for UI consistency) advances **on completion**, not by bulk-creating a series up front like calendar's `expand_manual_recurrence` — `toggle_task` in `app/services/tasks.py` keeps the completed instance and creates exactly one new sibling with `due_at` stepped forward one cadence unit, `is_done=False`, and blank notified-at columns, so it flows through the existing idempotent notification claiming untouched.
- The Tasks page (`tasks.html`) has a sidebar with smart views (Alle/Heute/Demnächst) and the user's lists, layered as a second client-side filter dimension (`data-task-view`/`data-task-list`) in `tasks.js` alongside the pre-existing state filter (open/done/overdue) — both dimensions combine in `taskMatchesFilter`/`taskMatchesView`. A subtask row inherits its *filter* view/list from its parent (`filter_view`/`filter_task_list_id` in the context dict, distinct from the row's own `view`/`task_list_id` used for its own badge) so it's never hidden orphaned when its parent is filtered out.
- A quick-add control (e.g. "Neues Label") that visually lives inside another `<form>` cannot itself be a nested `<form>` — HTML5 silently drops a `<form>` start tag while one is already open. Such controls instead use the `form="<id>"` attribute to associate their inputs/button with a sibling `<form>` rendered outside the outer one (see the `task-label-quick-add` form in `tasks.html`). Relatedly, a Django `{# ... #}` comment cannot span multiple lines — it silently stops being treated as a comment at all and leaks its raw text (including any `<form>`-like substrings) into the page; use `{% comment %}...{% endcomment %}` for anything longer than one line.
- Due tasks follow the same idempotent email/desktop notification pattern as calendar reminders (`send_due_task_reminder_emails`, `claim_due_desktop_tasks` in `app/services/notifications.py`) — see Notification idempotency above. The dashboard's `tasks` widget (see Dashboard customization below) shows a summary and is gated by the same flag.

### Vacation planner

`app/services/vacation_planner.py` computes German vacation-day accounting per user per year: `VacationYear` (allowance + federal-state `subdivision`), `VacationPeriod` (booked ranges), `OfficialHoliday` (state public holidays, generated via `holidays.country_holidays("DE", ...)` with a hand-written `_fallback_public_holidays` stdlib fallback when the package is absent), `CustomHoliday`, and `HolidayOverride` (per-user day-value adjustments to an official holiday). `calculate_period` walks a date range day-by-day to net out weekends and holiday credit against the required vacation days, split per calendar year (a period can span a year boundary); `annual_summary`/`month_summary`/`month_calendar` build the read views. `app/views/vacation_planner_views.py` holds one view per form (year, period, custom holiday, override) plus a JSON `vacation_preview` endpoint the client calls before submitting a period. Gated by the `vacation_planner` feature flag.

### Dashboard customization

`app/services/dashboard.py` defines the fixed widget catalog (`DASHBOARD_WIDGETS`), each with a template, CSS class, and optional `feature_flag`. A user's saved layout (`Profile.dashboard_layout` JSON: `{version, order, hidden}`) is passed through `normalize_dashboard_layout` on read (drops unknown IDs, appends missing ones) and `validate_dashboard_layout` on write (rejects anything that doesn't exactly cover the current widget set) before `dashboard_widgets_for_layout` filters by `feature_flags()` and hidden state. Gated by the `dashboard_customization` flag; adding a widget means registering it in `DASHBOARD_WIDGETS` and creating its partial template.

The `notifications` widget (`dashboard_notifications.html`) surfaces the latest unread `UserNotification` rows (`app/services/notifications.py`'s `dashboard_latest_notifications`, materializing due sources first the same way `notification_center` does) alongside today's-and-overdue open tasks (`dashboard_today_tasks` in `app/services/tasks.py`; its section is hidden when the `tasks` flag is off), each row with an inline one-click action ("gelesen" / "erledigt"). These reuse the existing `notification_toggle_read` and `task_toggle` endpoints rather than a new AJAX layer — both now accept an optional POST `return_to` (validated with `url_has_allowed_host_and_scheme`, must be a local path) so the action redirects back to the dashboard instead of navigating to the notifications/tasks page; omit it and they fall back to their original page-local redirect. Any future "act without leaving the dashboard" affordance should follow this `return_to` convention rather than introducing fetch/AJAX.

### Static asset versioning

Static CSS/JS files are cache-busted with a content hash rather than Django's `ManifestStaticFilesStorage`: `app/templatetags/static_versioning.py`'s `{% versioned_static %}` appends `?v=<sha256-of-file-contents>` to a `{% static %}` URL, and `{% static_version %}` combines several files into one digest (used for the service worker's cache name in `service-worker.js`). Every page template must `{% load static static_versioning %}` and use `versioned_static` instead of `static` for its own CSS/JS, or edits won't bust client caches / the PWA service worker cache.

## Configuration

`lunora/settings.py` reads a plain `.env` at the repo root with its own tiny parser (`load_env_file`, `os.environ.setdefault` — real environment variables win) plus `env_bool`/`env_int`/`env_list` helpers. `.env.example` is the documented reference. With `DJANGO_DEBUG=false`, missing `DJANGO_SECRET_KEY` or `DJANGO_ALLOWED_HOSTS` raises `ImproperlyConfigured`, and the HTTPS/HSTS/secure-cookie toggles default to on.

Do not commit `.env`, `db.sqlite3`, `media/`, or `private_media/`.

## Testing

All Python tests live in `app/tests.py` (Django `TestCase` + test client), grouped into per-area classes and asserting observable behavior: status codes, redirects, rendered templates, context contents, JSON payloads, form validation, and service return values. External HTTP is stubbed with local fake response classes (`FakeIcalResponse`, `FakeWeatherResponse`, `FakeWeatherTileResponse`) patched over the service's fetch helper — no network in tests. Model changes need a migration plus test coverage.

## Related files

`AGENTS.md` and `README.md` (German) cover overlapping ground; `README.md` is the authoritative list of env vars and setup steps.
