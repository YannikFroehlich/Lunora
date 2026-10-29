# Changelog

All notable changes to Lunora are documented here, newest first. This project doesn't cut
versioned releases, so entries are grouped by date instead of a version number. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

- New "Farbthema" setting under Darstellung (Sand, Terrakotta, Rosé, Lavendel, Ozean, Lagune, Wald,
  Schiefer) recolors the whole page (background, panels and text) in light and dark mode. The
  background waves are now drawn by the page itself and pick up the accent color too.
- The focus timer no longer sits directly against the page header; it gets the same spacing as the
  other tool pages.
- The bookmarks widget adds new links through a plus in its header that opens a small dialog, so
  the widget no longer carries the name and address fields itself.
- Vacations and public holidays from the vacation planner now appear in the calendar, in "Nächste
  Termine" on the calendar page and the dashboard, and in the calendar subscription (ICS feed).
- Create calendar events directly in the month grid: right-click a day (tap it on a phone), or drag
  across several days for an all-day event spanning them. Events can now have an end date ("Bis").
- The vacation calendar is larger and works directly: right-click a day (tap it on a phone) or drag
  across several days to add a vacation or a custom holiday, or to edit the vacations and holidays
  on those days, in a dialog without leaving the calendar.
- New "Gewohnheiten" tool: track daily or weekly habits, tick off today or the past week, and see
  your streak. A new dashboard widget ticks today's habits off without leaving the dashboard.
- New "Fokus-Timer" tool: focus intervals with short and long breaks, a sound and an optional
  browser notification at the end of each phase, and adjustable lengths that this browser
  remembers.
- New "Lesezeichen" dashboard widget for your own links to frequently used websites.
- Import data from other apps in the "Sicherheit" settings tab: Markdown files become notes (a
  Lunora export imports back as the same notes) and .ics calendar files become events; events that
  were already imported are skipped.
- The tools area is no longer described as school-only.
- Fix the vacation planner showing the suggested federal state (e.g. Nordrhein-Westfalen) instead
  of the saved one after saving a year; the saved state was stored but not displayed.
- The vacation calendar switches months without reloading the page or jumping to the top, and long
  holiday names no longer spill out of their labels.
- Round header and icon buttons keep a readable icon on hover in dark mode with every accent color.
- The "Kompaktheit" appearance setting is removed; all pages use the comfortable spacing.
- Dashboard widgets sit on a phone-style grid: "Klein" is a 2×2 tile, "Mittel" a wide 4×2 tile,
  "Groß" a 4×4 tile and "Volle Breite" a strip across the whole width. Widgets line up without
  holes, small ones pair up side by side on tablets, and phones keep one column. Widgets you had
  set to "Groß" become "Volle Breite" so they stay as wide as before.
- Fix deleting, restoring, moving and saving notes failing with "Der Server hat keine gültige
  Antwort gesendet" on the PostgreSQL production database. Browser notifications for note
  mentions/comments and calendar invitations could fail the same way and are fixed too.
- The Lunora logo on the dashboard and the sign-in pages follows your accent color (and a
  dashboard widget's own color) instead of always staying sand-colored.
- The calendar is no longer school-specific: event categories are now Termin, Meeting, Deadline,
  Privat and Sonstiges, and new events start as "Termin". Existing Klassenarbeiten, Tests and
  Referate become "Termin", Abgaben become "Deadline". Open tasks with a due date now also appear
  in the month view on their due day and can be ticked off there directly.
- Settings page polish: the section tabs size to their labels ("Benachrichtigungen" no longer
  overflows) and scroll sideways below 920 px, the selected theme and density options are clearly
  filled instead of only outlined, hovering an option no longer looks like selecting it, hover
  effects no longer stick after tapping on phones, the density options stack on narrow phones
  instead of breaking words, and the notification matrix fits a 375 px screen.
- Redesigned, more customizable dashboard: every widget can be small, medium, large or full width
  and get its own color, list widgets show 3, 5 or 8 entries, the weather widget can hide
  tomorrow, the statistics widget opens on week or month, and the quick-access widget shows only
  the tiles you pick. Three one-click presets ("Übersicht", "Fokus", "Kompakt") are a starting
  point. Widgets now share one header style and only take the height their content needs instead
  of stretching into empty space; tasks with their "erledigt" button moved out of the
  notifications widget into the tasks widget, and "Letzte Tools" became "Zuletzt genutzt", ordered
  by the sections you opened last in this browser. Also fixes a screen-reader status text that was
  visible in the weather widget.
- New logo: a simple gold "orbit" mark replaces the detailed moon illustration on the dashboard,
  login and password pages, offline page, favicon and app icons, and stays legible as a 16 px tab
  icon. Installed apps pick up the new icon after their next update.
- Command palette: press Ctrl+K (Cmd+K on Mac) on any page to jump to a page or settings section by
  typing, or to search everywhere for the typed text.
- See where you are signed in: the "Sicherheit" settings tab lists your devices with browser, system
  and last activity, and signs out single devices or all others at once.
- Delete your account yourself in the "Sicherheit" settings tab after confirming your password. All
  your data is removed, including attachments and your profile picture.
- Get notified when someone shares a vocabulary list with you: the share shows up in the
  notification inbox and, if enabled, by e-mail or Web Push. A new "Werkzeuge" row in the
  notification settings controls these channels separately from notes.
- Optional two-factor login: in the new "Sicherheit" settings tab, users can connect an authenticator
  app via QR code and get ten one-time recovery codes. After the password, the login then asks for a
  6-digit code; wrong codes are throttled per account, and the admin sign-in goes through the same
  check.
- Personal data export: the "Sicherheit" settings tab downloads everything you created in Lunora as a
  ZIP archive (notes as Markdown with attachments, tasks, events, sent messages, vocabulary lists, UML
  diagrams, vacation data and settings).
- Share vocabulary lists with other users by e-mail or username. Recipients find them under „Mit dir
  geteilt" and take over a copy that they practice with their own learning progress.
- The global search also finds vocabulary lists (by title or by the words they contain) and UML
  diagrams.
- Turn the calendar into a standalone school calendar: importing external iCal/Google calendars
  is removed (previously imported events are deleted), and events now carry a category
  (Klassenarbeit, Test, Referat, Abgabe, Sonstiges) that sets their color. Instead, each user can
  create a private subscription link in the settings and subscribe to their Lunora calendar from
  Google Calendar, Apple Calendar or Outlook; the link can be regenerated or disabled at any time.
  The admin feature flag "Kalendersynchronisierung" becomes "Kalender-Abo".
- Font sizes follow one shared type scale instead of about 40 slightly different values, so labels,
  captions and headings look the same size on every page.
- Unify the look across all pages: Calendar, Messages, Weather and Tasks no longer override the
  accent color with their own green/rose/blue/violet palette (Tasks also dropped its forced dark
  theme), so every page follows the accent chosen in the settings. Status colors, radii, focus
  rings, switches, checkboxes and dialog backdrops now come from shared tokens in `base.css`; dark
  mode derives a lighter accent so accent text stays readable. Also fixes the message search button
  being cut off, missing padding on search result panels, transparent note dialogs and unmuted
  text in the vacation planner.
- Improve legibility in light and dark mode: notices and tab bars that sat directly on the background
  image get a glass surface, buttons and badges on the accent color use dark text, and muted text,
  accent text and status colors were darkened (light) or lightened (dark) to reach readable contrast.
- Update the notes editor build to Vite 8 (Rolldown) and vitest 5, KaTeX to 0.19.1, reportlab to
  5.1 and ruff to 0.17.2, plus the latest compatible transitive Python dependencies (cryptography,
  aiohttp, certifi, tzdata, …) in `constraints.txt`.
- Deployments validate the new commit in a temporary directory (own venv, `pip install` with
  constraints, `check --deploy`) before the production checkout is touched, so a broken dependency
  or configuration no longer leaves half-deployed code on disk.
- Self-host KaTeX's stylesheet and fonts instead of loading them from jsDelivr: the notes page no
  longer contacts a third-party CDN, and formulas keep their styling offline.
- Update KaTeX (formula rendering in notes) to 0.19; patch updates for highlight.js, ESLint and
  Prettier. `npm audit` is clean again.
- Pin every transitive Python dependency at its tested version in `constraints.txt`. CI and
  `scripts/deploy.sh` install with `-c constraints.txt`, so production gets exactly the tested set;
  regenerate it with `pip freeze` after changing a `requirements*.txt`.
- Update the notes editor to Tiptap 3.32 (upstream fixes for list-type switching, pasting and
  tables) and stop registering the underline extension twice, which Tiptap warned about.
- Upgrade Django to 6.1 and Pillow to 13, plus patch updates for gunicorn, psycopg, holidays,
  pywebpush, reportlab and ruff — deployments need `pip install -r requirements.txt`.
- Add a "Werkzeuge" (tools) page for school helpers, gated by a new `tools` feature flag, starting
  with a full UML class diagram editor: classes, abstract classes, interfaces, enums and notes;
  association, directed association, aggregation, composition, inheritance, realization,
  dependency and note links with labels and multiplicities; drag-and-drop, pan/zoom, multi-select,
  undo/redo, autosave, and export as PNG, SVG, PlantUML, Java skeleton code, or JSON (plus JSON
  import).
- Add a vocabulary trainer to the tools page: vocabulary lists with per-list languages, single and
  bulk entry (paste lines separated by tab, `;` or `=`), CSV export, and practice as flashcards,
  typing (accepts alternative meanings, optional bracketed parts and small typos) or multiple
  choice in either direction. Progress follows a five-box Leitner system, wrong answers come back
  within the same round, and terms can be read aloud via the browser's speech synthesis.
- Reduce idle CPU/GPU load: the messages page and dashboard pause live polling while the tab is
  hidden (so background tabs no longer mark chat messages as read), unchanged chat regions are no
  longer re-rendered on every poll, and the dashboard logo glow animates via opacity instead of
  repainting drop-shadow filters every frame.
- Cut server load of chat live updates: idle polls from the messages page and the dashboard's
  unread badge now get a tiny "unchanged" reply instead of re-rendering every chat partial, and
  opening a chat only writes the read marker when a newer message actually exists.
- Show a small avatar stack in the note editor header for other users who currently have the same
  shared note open, updated via a lightweight polling heartbeat.
- Automatically pick up a shared note's latest content when another user saves changes, without
  requiring a manual reload; falls back to the existing conflict dialog instead of silently
  discarding an unsaved local draft.
- Automatically redirect a shared note's other viewers back to the notes overview if the owner
  moves it to the trash, deletes it permanently, or revokes their share, instead of leaving them
  stuck on a note they can no longer access.
- Extend the global search to also cover tasks and vacation periods, alongside notes, messages,
  and calendar events.
- Add a `Ctrl+K`/`Cmd+K` shortcut to jump straight to search from anywhere.
- Add a skip-to-content link and honor the operating system's reduced-motion preference
  site-wide.
- Offer every IANA timezone (not just a fixed shortlist of ten) in the region settings.

## 2026-09-03

- Add a "Statistik" dashboard widget summarizing weekly task/note activity, upcoming events, and
  remaining vacation days.
- Add a Woche/Monat period toggle to the dashboard "Statistik" widget.
- Allow toggling a table's header row/column from the note editor's table context menu.
- Reject saving an overlapping vacation period server-side, closing a gap where only the
  client-side preview warned about overlaps.
- Add `ruff` (Python) and ESLint/Prettier (JavaScript) linting and formatting, enforced in CI.
- Extend the service worker to cache visited pages in a separate `PAGES_CACHE`, so previously
  viewed pages (not just the offline shell) still open without a network connection; the cache
  is cleared on logout so it can't resurface for the next person on a shared device.
- Expand vacation planner test coverage: holiday overrides, year-boundary calculations, the
  stdlib fallback holiday generator (validated against the `holidays` package's own output),
  and multi-year/multi-subdivision imports.

## 2026-09-01

- Add drag-and-drop reordering for tasks and task lists.
- Allow editing existing tasks and surface due tasks on the calendar.
- Add reading time, version comparison, and a left-nav layout to the notes workspace.
- Add drag-to-resize and free positioning for note images.
- Add a slash menu, drag/paste uploads, and table tooling to the note editor.
- Keep the dashboard clock's date and moment label live-updating.

## 2026-08-28

- Add a persistent notification center and Web Push notifications.
- Add extended task management (projects/lists, priorities, labels, subtasks) and a dashboard
  notification widget.
- Sync the note editor toolbar to the cursor's actual formatting, including a heading's
  effective bold/size, not just its marks.
- Split the note editor toolbar into Text/Insert tabs and add a table-of-contents panel.
- Fix a square-cornered focus ring on the note editor page.

## 2026-08-27

- Add dashboard customization with a widget catalog and quick actions.
- Add task management and content-hash static asset versioning.
- Add per-user note colors and icons.

## 2026-08-26

- Add an installable PWA with an offline fallback page.
- Improve product area navigation and visual hierarchy; polish German copy and the contextual
  dashboard greeting.

## 2026-08-25

- Overhaul the notes editor: folders, templates, note-to-note links, math (KaTeX), print
  support, and layout fixes.
- Add ranked full-text note search (PostgreSQL `tsvector` / SQLite substring fallback); fix two
  notes-page filter bugs.
- Harden the production deployment: idempotent setup, Gunicorn socket hardening, and a
  production health check.

## 2026-08-24

- Add calendar event editing.
- Prepare secure Ubuntu deployment (Gunicorn + Nginx).

## 2026-08-21

- Add the vacation planner with German public-holiday support and refreshed branding.

## 2026-08-18

- Add PWA icons and manifest, recurring calendar events, and app-wide search.

## 2026-08-11

- Add group chats, message attachments/replies, note mentions/comments, and calendar invites.
- Fix messaging/notes bugs; add password reset and login throttling.

## 2026-08-10

- Add notification automation (calendar reminders, weekly summaries) and improve mobile
  settings.

## 2026-08-05

- Add a rich-text notes workspace with sharing and PDF export.
- Add admin feature controls (system-wide feature flags, administration page), manual calendar
  events, and email-or-username login.

## 2026-08-04

- Add multi-calendar support and update the weather UI.

## 2026-06-25 – 2026-07-06

- Initial prototype: base Django project, workspace UI shell, dark mode, login and settings
  views, first calendar/iCal integration, and a responsive layout pass.
