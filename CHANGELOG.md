# Changelog

All notable changes to Lunora are documented here, newest first. This project doesn't cut
versioned releases, so entries are grouped by date instead of a version number. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

- The sign-in e-mail now comes by default only for a new device or browser. Under Settings →
  Sicherheit → "E-Mail bei Anmeldung" it can be set to every sign-in or switched off.
- Fixed: shopping list items ticked off without a connection now stay ticked when the page is
  opened again while still offline, and are sent right away once it loads with a connection. The
  "im Wagen" count follows each tick at once, too.
- Fixed: on medium-wide windows the tasks toolbar ("Board", "Sortieren", the menu) was cut off and
  the task list could slide sideways after deleting a task. The toolbar now wraps.
- Fixed: the task counts next to "Eingang" and your lists update at once after adding, finishing
  or deleting a task.
- New accounts must confirm their e-mail address: registration sends a link, and signing in only
  works after it was opened (trying earlier sends the link again). Confirming sends a welcome mail;
  a completed password reset counts as confirmation too.
- Every successful sign-in sends an e-mail with time, device and IP address, so a login by
  someone else doesn't go unnoticed.
- The message after deleting something has a "Rückgängig" button that brings it straight back,
  without a detour through the trash.
- Habits (with their ticked days), vocabulary lists (with cards and learning progress) and recipes
  (with their places in the meal plan) now go to the trash too and can be restored for 30 days.
- The focus timer can book its focus blocks on a task: choose it under "Fokus-Zeit buchen auf",
  and the task's time tracking runs while you focus. Breaks don't count.
- The shopping list can be ticked off without a connection: the change shows at once and is sent
  as soon as the network is back.
- In a shared note, people who are typing right now are marked and named ("Mira bearbeitet
  gerade") next to the avatars of everyone who has the note open.
- Deleted tasks and events go to a trash first ("Papierkorb", linked from the tasks page and the
  calendar) and can be restored there for 30 days, with subtasks, comments, shares, tracked time,
  invitees and their answers. A restored task whose list or label was deleted meanwhile comes back
  without it.
- Recipes add their quantities up on the shopping list: 200 g Mehl from one recipe and 300 g from
  another become 500 g (g/kg and ml/l convert); quantities that don't fit together stay side by
  side ("1 Zehe + 2 Zehen"). A ticked-off item starts over with the new quantity.
- A recipe page converts to other servings ("Für 6 Portion(en)"), and the converted quantities go
  on the shopping list.
- Shared expenses have categories, and a group page shows each month's spending per category
  with your part of it. The CSV export has a category column.
- New dashboard widgets: "Heute gibt's" with today's (and tomorrow's) meals from the weekly plan,
  and "Geteilte Ausgaben" with your balance per group.
- The servings field in the weekly plan is labelled.
- Shared expenses can be split unevenly: by shares (e.g. 2 : 1) or by fixed amounts per person.
  Uneven splits show each person's amount in the list.
- Expenses can repeat every month (rent, subscriptions): Lunora adds them on their day, also
  for months it missed, until someone stops them under "Jeden Monat".
- A group's expenses download as a CSV file with one column per person.
- Each planned meal in the weekly plan has its number of servings; "Zutaten der Woche auf den
  Einkaufszettel" scales the quantities to the servings planned over the week.
- When someone enters your password correctly but then a wrong two-factor code, you get a warning
  in the inbox and by e-mail (at most one per 15 minutes), even with e-mail notifications switched
  off. It means someone knows your password.
- Turning two-factor login on signs out all your other devices, so sessions that only needed the
  password don't stay open.
- Fixed: the lock after five wrong two-factor codes could be outrun by sending many codes at
  once. Every attempt now counts before it is checked.
- New tool "Geteilte Ausgaben": a group for the flat, a holiday or a trip notes who paid what and
  splits it equally between the people chosen. Everyone sees their balance and the few payments
  that settle the group; "Bezahlt" records one once it has happened. Members add others by e-mail
  or username (they get an inbox notification and e-mail) and can leave once they are settled.
- Recipes have a weekly meal plan: put recipes on the days of the week and send all their
  ingredients to the shopping list in one click. An ingredient several recipes share lands on the
  list once.
- Recipes can be imported from a recipe website: paste the address and Lunora takes over title,
  portions, ingredients and steps, with a link back to the source. This works for the many sites
  that describe their recipes for search engines; the page is fetched by the Lunora server.
- "Freien Termin finden" in the event dialog suggests the next times on three different weekdays
  between 8 and 18 o'clock when you and everyone invited are free, for the length entered.
  Choosing one fills in date and time. Nobody sees anything about the others' appointments.
- The vacation planner no longer reloads the page when saving or deleting vacations and holidays,
  also from the calendar's right-click dialog.
- The number of unread notifications on the bell now updates right after an action, e.g. when an
  item is marked as read on the dashboard, instead of only on the next page load. Marking
  notifications as read, adding, editing and deleting vocabulary, removing vocabulary lists and
  editing recipes or putting them on the shopping list also no longer reload the page, and the
  vocabulary filter keeps its text while doing so.
- Ticking off, adding and deleting no longer reloads the whole page on the shopping list, tasks,
  calendar, habits and the dashboard's task, habit, notification, shopping and bookmark widgets:
  only the affected part updates, so scroll position, open details and the keyboard focus stay
  where they were. Moving events and tasks by drag and drop in the calendar works the same way.
  Other page changes fade over smoothly in browsers that support it.
- The dashboard has a "Einkaufszettel" widget: open items in store-aisle order with their emoji
  and quantity, ticked off right there, and a plus button to write down what's missing without
  leaving the dashboard. It sits next to the statistics in the default layout; dashboards set up
  before get it at the end and can move or hide it under "Dashboard anpassen".
- New tool "Rezepte": collect recipes with portions, ingredients (one per line, lines ending in ":"
  become sub-headings) and preparation. On a recipe, untick what's already at home and put the rest
  on your shopping list, or on a list shared with you, in one click. Quantities like "200 g" or
  "1 Pck." go into the item's quantity, and an ingredient already on the list comes back instead of
  being added twice.
- Scanning in the store now also ticks off items written under another name for the same product:
  "Möhren" for "Karotten", "Marmelade" for "Konfitüre", "Klopapier" for "Toilettenpapier".
- The notes sidebar and its search results now show every note. Accounts with more than 200 notes
  used to lose the rest from the list without any hint.
- Notes in folders no longer get cut off at the right edge of the sidebar when their title is long;
  the title ends with "…" instead.
- The shopping list can be shared: under "Gemeinsam nutzen" you name someone by e-mail or username,
  and from then on both of you add, tick off, scan and delete on the same list. A shared list
  refreshes itself every few seconds, so what one person ticks off in the store shows up for the
  other. Members switch between their own list and shared ones at the top of the page, can leave a
  list, and get an inbox notification (and e-mail) when a list is shared with them.
- The shopping list scans barcodes: "Scannen" opens the phone camera (browsers with barcode
  detection, e.g. Chrome on Android; elsewhere the number under the barcode is typed in). In the store
  a scan ticks off the matching item ("Milch" for "Frische Vollmilch"), at home it puts the product on
  the list. The first scan looks the product up on Open Food Facts and keeps it with brand, pack size,
  Nutri-Score and nutrition values, shown under the item; products Open Food Facts doesn't know are
  named once by hand. The lookup goes through the Lunora server, so Open Food Facts never sees who
  scanned what.
- "Neue Notiz" on a folder now also puts an imported Word document into that folder instead of the
  top level.
- Notes have fill-in fields: click into one and type, like in Word. Word documents bring theirs along
  (content controls and legacy form fields for text, dates and lists): Word's grey hint stays a grey
  hint until something is typed in, filled-in fields keep their value, and the Word export turns them
  back into Word input fields. New ones are inserted under "Einfügen" → "Eingabefeld" or with
  Ctrl+Alt+J. PDF, Markdown and the public link show an empty field as `__________`. Check boxes
  from Word become ☐/☒, and table rows inside repeating sections are no longer dropped.
- New tool "Einkaufszettel": a shopping list with optional quantities, tick items off in the store
  and clear the ticked ones in one go. Items get a matching emoji and are sorted by store aisle
  (fruit & veg, dairy, drinks, …), ticked ones move to "Im Wagen", the name field suggests over 1,500
  everyday products and brands with their emoji and aisle while typing, and adding something
  that's already on the list brings that row back instead of writing it twice.
- More protection against abuse: specially built Markdown files can no longer tie up the server,
  failed logins are also limited per client across all accounts, attempt counters can't be outrun
  with parallel requests, and a data export can be started once every two minutes. Markdown lines
  over 10,000 characters are imported without inline formatting, and files with very many long
  lines are refused. Malformed diagrams and two-factor codes now get a normal error message.
- Notes can be exported as Word documents (.docx), from the button next to PDF and Markdown or with
  Ctrl+Alt+W. Headings, text formatting, colors, lists, tables, links and pictures come along.
- While subscribed calendars are switched off for the site, their events no longer make someone
  show up as busy in the availability check of an invitation.
- Hardened the Word import and calendar subscriptions against crafted input: specially built .docx
  files can no longer tie up the server, a broken or malicious subscribed calendar no longer stops
  notifications for everyone, and calendar servers that answer extremely slowly are cut off after
  30 seconds. Such a calendar shows "Der Kalender konnte nicht gelesen werden." and keeps its
  previous events; an .ics file with impossible dates is reported instead of causing an error page.
  Descriptions of imported and subscribed calendar events are cut off after 10,000 characters.
- A failing background job no longer holds up the others: if, say, the reminder e-mails fail, Web
  Push and scheduled chat messages still go out. `/healthz/` keeps reporting the problem.
- The note page keeps its A4 width on smaller desktop windows too and scrolls sideways, like a sheet
  in Word, instead of getting narrower. Text wraps the same in every window, and imported tables no
  longer stick out of the page.
- Password reset mails are limited to three per address and ten per client within an hour, so
  nobody can flood someone's inbox with them. Further requests get the usual confirmation page, so
  the limit doesn't reveal whether an address has an account.
- Stricter protection against injected scripts: every page now sends a Content-Security-Policy, so
  the browser only runs Lunora's own scripts (plus Cloudflare Turnstile and the weather map's
  Leaflet) and refuses scripts smuggled in through content.
- The global search also finds tasks by their labels and comments.
- The administration page's "Speicher" panel lists the ten accounts using the most storage.
- Storage limit per user: files uploaded to notes and chats count towards a limit (1 GB by default)
  that administrators set under Administration → Speicher, where 0 means no limit. Uploads, Word
  imports with pictures, duplicated notes and forwarded attachments that would exceed it are
  rejected with a message; existing files stay. Einstellungen → Sicherheit shows how much you use.
- Word documents (.docx) can be imported as notes, from the "Neue Notiz" dialog on the notes page or
  the data import in settings. Headings, paragraphs and alignment, bold/italic/underline/strike,
  super-/subscript, text colors, highlights, font sizes and fonts (mapped to the editor's choices),
  nested bullet and numbered lists, tables (merged cells, cell colors, column widths) and links come
  along; embedded pictures become note images (GIF/BMP/TIFF are converted to PNG).
- The note editor's page is now plain white with black text instead of following the color theme,
  so text and highlight colors look as they do in Word. In dark mode it works like Word's: a dark
  grey page on which the document's colors flip their lightness (dark blue headings turn light
  blue, light cell shading dark), while pictures and code blocks stay as they are.
- Table cells in notes show their background color (e.g. from imported Word tables), and
  paragraphs, headings and cells can carry Word's shading, rules and colored accent bars.
- Word import is closer to the original: headings keep their style's color, size and shading, all
  text keeps Word's size at 100 % zoom (the note page is A4 like Word's, so tables keep their
  proportions), black and white text keep their color, colored callout boxes and formula boxes keep
  their background and left bar, table borders keep their color, and tiny empty spacing lines are
  dropped. Note tables use Word-like cell margins and left-aligned header cells.
- The note editor's header is more compact: on wide screens the note actions sit next to the title
  instead of on their own row, so more of the document is visible.
- Recurring tasks can repeat every n days, weeks, months or years ("alle 2 Wochen"), and weekly
  tasks on chosen weekdays ("Mo und Do"). The next due date now keeps its local time of day across
  daylight-saving changes.
- The calendar page's category legend is replaced by a "Kalender" panel listing your subscribed
  calendars; open one to change its name, color and icon (e.g. a cake for birthdays), refresh or
  remove it. Its events show that icon.
- The calendar page works better on phones: the toolbar is more compact ("Termin" and "Heute" side
  by side), the mouse hints are hidden, day dots show their event color, and the "Heute" list's edit
  and delete buttons no longer appear empty.
- Clicking a holiday or vacation on the calendar page opens the vacation planner with that day
  highlighted for three seconds.
- The calendar page no longer shows the month statistics panel (event count, free and active
  days, weekly bars) below the calendar, and the calendar and sidebar no longer stretch to each
  other's height, so there are no large empty areas anymore.
- "Nächste Termine" on the calendar page is easier to scan: each entry has a date tile (today's
  is highlighted), the title on one line and its time, category and location below, with the
  series, invitation and subscription icons kept on the right instead of wrapping.
- Subscribe to external calendars: under Einstellungen → Kalender, paste the secret iCal address
  of a private Google, Outlook or iCloud calendar (https or webcal), pick a name and color, and
  its events show up read-only in every calendar view and in "Nächste Termine" on the dashboard.
  Lunora refreshes them about every 30 minutes (or on "Jetzt aktualisieren"), with series and
  time zones, from half a year back to a bit over a year ahead. The address is never shown again,
  exported or logged, and subscribed events stay out of Lunora's own subscription link. The admin
  feature flag "Externe Kalender" switches it off.
- Time report by month: "Woche | Monat" on the Zeiterfassung page switches to a monthly view with
  one bar per calendar week and a CSV per month. Both views also sum the time per task list and per
  label (a task with several labels counts for each), and the Monday summary e-mail now lists last
  week's tracked time with the three biggest tasks.
- Time tracking for tasks: a play/stop button on every open task starts a timer (starting another
  one stops the first, finishing the task stops it too), a banner shows what's running, and each
  task shows the time you tracked on it. "Zeiterfassung" (`/tasks/time/`) shows the week per day
  and per task, lets you add forgotten time and delete entries, and downloads the week as CSV.
  Tracked time is private, also on shared or assigned tasks.
- Comments on events: every event with invitees has a detail page (linked from the agenda, the
  lists, invitations, invited events in the month view and the edit dialog) with when and where,
  everyone's answer, accepting/declining, and a comment thread. New comments notify the organizer
  and invitees who haven't declined, in the notification center, by e-mail and Web Push.
- Fixed: the message menu in a chat no longer closes by itself when a new message arrives while it
  is open, and keyboard focus stays on the focused message during live updates.
- Fixed: an event ending exactly at midnight no longer also shows on the next day in the month and
  year views.
- Edit chat messages: "Bearbeiten" in the menu of your own message (up to 24 hours after sending,
  not for forwarded messages or polls) puts the text back into the input field; Enter saves,
  Escape cancels. Edited messages say "bearbeitet", and changed @-mentions notify newly named
  people and withdraw the notification from those no longer named.
- Calendar agenda view: "Agenda" lists the next four weeks as a list of days with events, tasks,
  holidays and vacations, skipping empty days. Times show where multi-day events start and end,
  own events open the edit dialog, and tasks can be ticked off right there.
- Audit log on the administration page: logins and failed logins, logouts, registrations, password
  resets, 2FA changes, signed-out devices, calendar feed links, data exports, account deletions,
  changed switches and "Alle anderen ausloggen", with account, time and IP address, filterable and
  kept for 180 days. Failed logins for unknown accounts don't store what was typed.
- Polls in group chats: "+" → "Umfrage erstellen" asks a question with 2–10 answers (optionally
  several per person). Everyone taps an answer to vote or to take the vote back, sees the counts
  and who voted, and the creator can end the poll.
- Send chat messages later: "+" → "Später senden" picks a date and time (up to a year ahead, in your
  own timezone). Planned messages are listed above the input field only for you, where they can be
  sent right away or deleted; they go out on time through the background automations, or as soon
  as the chat is open.
- @-mentions in group chats: typing "@" suggests the members (and "@alle" for everyone). Mentioned
  people see their name highlighted, an "@" next to the chat's unread count, and get a notification
  in the notification center, by e-mail and Web Push even while they muted the chat. The new
  "Nachrichten" row in the notification settings turns these channels off.
- While a dialog is open (new event, task, forwarding, command palette, …) the page behind it no
  longer scrolls with the mouse wheel or touch; only the dialog itself scrolls.
- Forward chat messages: "Weiterleiten" in a message's menu sends it, including an image, file or
  voice message, to another of your conversations. It arrives as "Weitergeleitet" from you, without
  naming who wrote the original.
- Event reminders: an event can remind you "Zum Beginn", 5/15/30 minutes, 1–2 hours, 1–2 days or a
  week before it starts. The reminder reaches you and everyone who accepted the invitation, through
  the notification center, e-mail, Web Push and the browser, with the event's time, place and a link
  to its day. Moving or editing the event moves the reminder along; events with a reminder show a
  bell in the lists.
- Health check for monitoring: `/healthz/` answers 200 while the database, the cache and the
  background automations are working and 503 otherwise, with only a status word per check.
- Accessibility pass over all pages (checked with axe-core in light and dark mode, including open
  dialogs, and by keyboard): every form field now has a label for screen readers, labels no longer
  point at a field of another form on the same page, the notes editor, its checklist boxes and the
  dark-mode toggle have German labels, page regions are announced correctly, and the week view's
  event details have enough contrast. New keyboard alternatives: Alt+↑/↓ moves the focused task
  (manual sort) or list, Enter opens the actions of a focused chat message (Escape returns to it),
  and Enter/Space on a vacation-planner day opens its menu (Shift+Enter selects a range). Colour
  pickers and practice options show a visible focus ring.
- Faster note search on PostgreSQL: the searchable text of every note is now kept in an indexed
  column that is updated on save, instead of being re-analysed for every search. Results and their
  order stay the same.
- Tasks have a board view: "Board" next to "Liste" shows the tasks in the columns "Offen", "In Arbeit"
  and "Erledigt". Drag a card into another column, or use the three status buttons on each card. The
  new "In Arbeit" status also shows in the list and has its own filter.
- Share a task with several people ("Teilen mit" in a task's options): everyone it is shared with sees
  it with its subtasks under "Mit mir geteilt", can change its status and joins its comments, and is
  notified about the share and new comments. Only the owner edits or deletes it; anyone it was shared
  with can stop sharing it with themselves ("Teilen beenden").
- Fixed the task row buttons wrapping into a second line since "Als Vorlage speichern" was added.
- Calendar year view: "Jahr" in the view switch shows all twelve months at once, colours each day by
  how much is on it and marks holidays and vacations. A day opens its day view, a month name the
  month view.
- When inviting people to an event, the dialog shows next to each ticked person whether they are
  free, busy or on vacation at that time (only that status, no details of their events).
- An event's location links to a map search (OpenStreetMap) in the "Heute" and "Nächste Termine"
  lists, the invitations and the event dialogs; a web address as location opens the video call
  instead.
- Fixed labels in the calendar's dialogs pointing at the wrong field, because the create, edit and
  reminder forms used the same field ids.
- Notes with checklists show their progress (for example "2/5") in the note list; it turns green
  once every item is ticked and updates as soon as the note is saved.
- Voice messages in chats: the microphone button next to the text field records a message (up to
  five minutes, "Senden" or "Verwerfen"), which the chat plays back inline with its length. MP3,
  M4A, Ogg and WebM audio files can also be attached with the paperclip.
- Task comments can be edited (marked "bearbeitet") and deleted by their author.
- An assigned task can be declined or handed back ("Ablehnen oder zurückgeben", with an optional
  reason); the owner is notified and the task is theirs alone again.
- Task templates: save a task with its subtasks as a reusable checklist (copy icon in the row) and
  create new tasks from it in the "Vorlagen" sidebar section, optionally with a due date.
- Habit statistics: each habit shows a heatmap of the last year, the number of check-ins, its best
  streak and the completion rate of the last 30 days (12 weeks for weekly habits).
- Chat messages can be taken over as a task or an event from their context menu; the text lands in
  the quick-add field of the tasks page or the calendar, where a date can be added.
- Fixed the habit create form being squeezed into one row on phones.
- Tasks assigned to you now also appear in your calendar (with a small person icon). You can tick
  them off there; only the owner can drag them to another day.
- Habit reminders: give a habit a reminder time (when creating it, or with the bell on its card) and
  Lunora reminds you once in the notification center, by Web Push and by e-mail if it is still open
  then. Daily habits remind every day, weekly ones on Sunday if nothing was checked that week.
  Ticking the habit off removes the reminder again.
- Comments on assigned tasks: owner and assignee can write to each other below the task. The other
  side is notified in the notification center, by Web Push and by e-mail ("Aufgaben" category).
- Importing an .ics file keeps whole recurring series: "every second week", "Mondays and
  Wednesdays", "last Friday of the month" and similar rules now create all their dates instead of
  only the first one. Skipped dates (EXDATE) stay out, and single occurrences that were moved or
  cancelled in the source calendar are imported that way too.
- Assign tasks to other Lunora users: "Zuweisen an" in a task's options hands a top-level task
  to someone else. They get an inbox notice (plus e-mail and Web Push under the "Aufgaben"
  category), see the task and its subtasks under "Mir zugewiesen", can tick them off, and receive
  its due reminders instead of the owner. Editing and deleting stay with the owner.
- Search a chat's history: the search field in the chat header lists matching messages of that
  conversation, and a hit opens the history at that message, highlighted, with a link back to the
  newest messages.
- Drag your own calendar events with the mouse: onto another day in the month, week, three-day or day
  view (the time stays), or within the time grid to a new time. Drag the bottom edge of an event in
  the time grid to make it longer or shorter. A recurring event moves only that occurrence.
- The calendar has a day view and a three-day view next to the month and week, which suit phones
  better. The calendar remembers the view you picked last and opens it next time.
- Hiding a dashboard widget while customizing removes it from the grid right away instead of leaving
  it dimmed in place; "Sichtbare Widgets" brings it back. The arrows now skip hidden widgets, so
  every click visibly moves a widget.
- Fix the "Teilnehmer einladen" list in the calendar dialogs and the contact list for a new chat:
  each checkbox sits next to its name again instead of on a line of its own. The "Gruppenname" field
  of a new chat is styled like the other fields.
- Pages keep their scroll position when you page through them: switching calendar months or weeks,
  sorting or filtering no longer jumps back to the top. A link to a specific section still scrolls
  there.
- Recurring calendar events can be edited and deleted as "Nur dieser Termin", "Dieser und alle
  folgenden" or "Alle Termine der Serie". Following occurrences move along with the edited one and
  keep their time of day across daylight-saving changes; "Dieser und alle folgenden" splits them off
  into a series of their own. The edit dialog now has a delete button too.
- Drag a task in the calendar onto another day to move its due date (its time stays), or in the week
  view onto a time to set that time as well. A task moved or edited to a later due date reminds
  again at the new time.
- Quick add understands natural German: a new task like "Bericht abgeben Freitag 14 Uhr !hoch
  #arbeit" gets its due date, time, priority and an existing label from the title, and the calendar
  has a "Schnell eintragen" field that turns "Zahnarzt morgen 14 Uhr" into an event (all day when
  only a date is given). Dates like "morgen", "nächsten Montag", "in 2 Wochen", "15.3." and
  "5. Dezember" work.
- Notes can be shared through a public link: in the share dialog, the owner turns on a read-only
  address that anyone can open without signing in, can replace it with a new one, or turn it off.
  Formulas, images and attachments show up on the public page; administrators can disable public
  links in the administration.
- The calendar has a week view next to the month: a switch in the toolbar shows Monday to Sunday
  with a time grid, where overlapping events sit side by side and a line marks the current time.
  All-day and multi-day events, holidays, vacations and due tasks sit in a row above the grid.
  Right-click a time (tap it on a phone) or drag across a time span to create an event there.
- New "Weiß" color theme: a plain white page (neutral dark in dark mode) without tinted
  background waves.
- The weather map's popups, tooltips and zoom buttons, the dropdown lists in forms, and the paper of
  the notes editor now follow the color theme instead of staying sand-colored.
- Darstellung offers "System" next to the light and dark mode: the page then follows the device's
  setting and switches along when it changes. The browser toolbar on phones now takes the page's
  color instead of a fixed sand tone.
- New "Farbthema" setting under Darstellung (Sand, Terrakotta, Rosé, Lavendel, Ozean, Lagune, Wald,
  Schiefer) recolors the whole page (background, panels and text) in light and dark mode. The
  background waves are now drawn by the page itself and pick up the accent color too. A "Farbstärke"
  slider sets how strongly the theme tints the background and panels.
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
