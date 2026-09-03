# Changelog

All notable changes to Lunora are documented here, newest first. This project doesn't cut
versioned releases, so entries are grouped by date instead of a version number. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

- Add a "Statistik" dashboard widget summarizing weekly task/note activity, upcoming events, and
  remaining vacation days.
- Add a Woche/Monat period toggle to the dashboard "Statistik" widget.
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
