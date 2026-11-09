import time
from datetime import datetime
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from app.models import CalendarEvent, Note, Profile, SystemSettings
from app.services.data_import import import_ics_events, markdown_to_note
from app.services.note_content import validate_note_document
from app.services.note_markdown import render_note_markdown

BERLIN = ZoneInfo("Europe/Berlin")

MARKDOWN = """# Wochenplan

Ein **fetter**, _kursiver_ und `code`-Text mit [Link](https://example.com), \\*Stern\\* und
zweiter Zeile.\\
Nach dem Umbruch.

## Liste
- Eins
- Zwei
  - Unterpunkt ~~alt~~
3. Drei

- [x] erledigt
- [ ] offen

> Zitat

```python
print("hi")
```

| Tag | Plan |
| --- | --- |
| Mo | Sport |

---
"""


class MarkdownHostileInputTests(TestCase):
    def test_lines_built_to_make_the_parser_backtrack_stay_fast(self):
        byline = "# Titel\n\n*Zuletzt bearbeitet am" + " von" * 5000
        for text in (
            "# a" + " " * 1500 + "b",
            "- a\n" + "\n" * 20000 + "  x",
            "| a |\n" + " " * 20000 + "x",
            "| a |\n---" + " " * 20000 + "x",
            byline,
            "a\n" * 20000,
            "\n" * 20000 + "a",
            "*a " * 4000,
        ):
            with self.subTest(text=text[:20]):
                started = time.monotonic()
                markdown_to_note(text, "datei")
                self.assertLess(time.monotonic() - started, 1)

    def test_too_many_long_lines_are_refused(self):
        with self.assertRaisesMessage(ValidationError, "sehr lange Zeilen"):
            markdown_to_note(("*a " * 3000 + "\n") * 200, "datei")

    def test_a_line_too_long_for_inline_parsing_stays_plain_text(self):
        line = "**fett** " + "x" * 10_000

        _title, document = markdown_to_note(line, "datei")

        self.assertEqual(document["content"][0]["content"], [{"type": "text", "text": line}])

    def test_deep_nesting_is_refused_instead_of_crashing(self):
        for text in (">" * 5000, "\n".join("  " * depth + "- x" for depth in range(600))):
            with self.subTest(text=text[:10]), self.assertRaisesMessage(ValidationError, "verschachtelt"):
                markdown_to_note(text, "datei")

    def test_nested_emphasis_stops_at_the_depth_limit(self):
        _title, document = markdown_to_note("*_" * 40 + "x" + "_*" * 40, "datei")

        validate_note_document(document)

    def test_headings_and_table_separators_keep_their_meaning(self):
        self.assertEqual(markdown_to_note("# Titel ##  ", "datei")[0], "Titel")
        self.assertEqual(markdown_to_note("# C#", "datei")[0], "C")
        heading = markdown_to_note("text\n\n## Plan ##", "datei")[1]["content"][1]
        self.assertEqual(heading["content"], [{"type": "text", "text": "Plan"}])
        for separator in ("| --- | :---: |", "---|---", ":---"):
            document = markdown_to_note(f"| a | b |\n{separator}\n| c | d |", "datei")[1]
            self.assertEqual(document["content"][0]["type"], "table", separator)
        document = markdown_to_note("| a | b |\n| c | d |", "datei")[1]
        self.assertEqual(document["content"][0]["type"], "paragraph")

    def test_paragraph_lines_are_joined_and_breaks_stay_separate(self):
        document = markdown_to_note("eins\nzwei  \n\\\ndrei", "datei")[1]

        types = [node["type"] for node in document["content"][0]["content"]]
        self.assertIn("hardBreak", types)
        self.assertEqual(document["content"][0]["content"][0], {"type": "text", "text": "eins zwei"})


class MarkdownImportTests(TestCase):
    def test_markdown_becomes_a_valid_document(self):
        title, document = markdown_to_note(MARKDOWN, "datei")

        validate_note_document(document)
        self.assertEqual(title, "Wochenplan")
        types = [block["type"] for block in document["content"]]
        self.assertEqual(
            types,
            [
                "paragraph",
                "heading",
                "bulletList",
                "orderedList",
                "taskList",
                "blockquote",
                "codeBlock",
                "table",
                "horizontalRule",
            ],
        )
        paragraph = document["content"][0]["content"]
        self.assertIn({"type": "text", "text": "fetter", "marks": [{"type": "bold"}]}, paragraph)
        self.assertIn(
            {
                "type": "text",
                "text": "Link",
                "marks": [{"type": "link", "attrs": {"href": "https://example.com"}}],
            },
            paragraph,
        )
        self.assertIn({"type": "hardBreak"}, paragraph)
        self.assertEqual(paragraph[-1]["text"], "Nach dem Umbruch.")
        nested = document["content"][2]["content"][1]["content"][1]
        self.assertEqual(nested["type"], "bulletList")
        self.assertEqual(document["content"][3]["attrs"], {"start": 3})
        self.assertEqual(
            [item["attrs"]["checked"] for item in document["content"][4]["content"]],
            [True, False],
        )
        self.assertEqual(document["content"][6]["attrs"], {"language": "python"})

    def test_unsafe_links_lose_their_link_and_plain_files_keep_their_name(self):
        title, document = markdown_to_note("[klick](javascript:alert(1)) und [intern](/notes/3/)", "Notiz 1")

        validate_note_document(document)
        self.assertEqual(title, "Notiz 1")
        texts = document["content"][0]["content"]
        self.assertTrue(all("marks" not in node for node in texts))

    def test_exported_note_imports_back_to_the_same_document(self):
        user = User.objects.create_user(username="mira", password="secret-12345")
        _title, document = markdown_to_note(MARKDOWN, "datei")
        note = Note.objects.create(owner=user, title="Wochenplan", document=document)

        title, reimported = markdown_to_note(render_note_markdown(note), "datei")

        self.assertEqual(title, "Wochenplan")
        self.assertEqual(reimported, document)


ICS = (
    "BEGIN:VCALENDAR\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:zahnarzt-1\r\n"
    "DTSTART;TZID=Europe/Berlin:20991103T090000\r\n"
    "DTEND;TZID=Europe/Berlin:20991103T100000\r\n"
    "SUMMARY:Zahnarzt\\, Kontrolle\r\n"
    "LOCATION:Praxis\r\n"
    "DESCRIPTION:Zeile eins\\nZeile zwei mit einem sehr langen Text\\, der gefa\r\n"
    " ltet wurde\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:geburtstag-1\r\n"
    "DTSTART;VALUE=DATE:20991224\r\n"
    "RRULE:FREQ=YEARLY;COUNT=3\r\n"
    "SUMMARY:Heiligabend\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:utc-1\r\n"
    "DTSTART:20991105T120000Z\r\n"
    "DURATION:PT30M\r\n"
    "SUMMARY:Call\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:team-1\r\n"
    "DTSTART:20991106T080000Z\r\n"
    "DTEND:20991106T090000Z\r\n"
    "RRULE:FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20991120T235959Z\r\n"
    "SUMMARY:Team\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:letzter-montag-1\r\n"
    "DTSTART:20991130T080000Z\r\n"
    "RRULE:FREQ=MONTHLY;BYDAY=MO;BYSETPOS=-1\r\n"
    "SUMMARY:Monatsabschluss\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "UID:abgesagt-1\r\n"
    "DTSTART:20991107T080000Z\r\n"
    "STATUS:CANCELLED\r\n"
    "SUMMARY:Abgesagt\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


class IcsImportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mira", password="secret-12345")
        Profile.objects.create(user=self.user, display_name="Mira", timezone_name="Europe/Berlin")

    def test_events_are_imported_once_with_times_series_and_text(self):
        counts = import_ics_events(self.user, ICS)

        self.assertEqual(counts, {"created": 5, "duplicates": 0, "skipped": 1, "simplified": 1})
        dentist = CalendarEvent.objects.get(title="Zahnarzt, Kontrolle")
        self.assertEqual(dentist.start_at, datetime(2099, 11, 3, 9, tzinfo=BERLIN))
        self.assertEqual(dentist.end_at, datetime(2099, 11, 3, 10, tzinfo=BERLIN))
        self.assertEqual(dentist.location, "Praxis")
        self.assertEqual(
            dentist.description, "Zeile eins\nZeile zwei mit einem sehr langen Text, der gefaltet wurde"
        )
        self.assertEqual(dentist.category, CalendarEvent.CATEGORY_APPOINTMENT)
        christmas = CalendarEvent.objects.filter(title="Heiligabend").order_by("start_at")
        self.assertEqual(
            [event.start_at.astimezone(BERLIN).date().isoformat() for event in christmas],
            ["2099-12-24", "2100-12-24", "2101-12-24"],
        )
        self.assertTrue(all(event.is_all_day and event.recurrence_rule == "YEARLY" for event in christmas))
        self.assertEqual(len({event.recurrence_id for event in christmas}), 1)
        call = CalendarEvent.objects.get(title="Call")
        self.assertEqual((call.end_at - call.start_at).total_seconds(), 1800)
        team = CalendarEvent.objects.filter(title="Team").order_by("start_at")
        self.assertEqual(
            [event.start_at.date().isoformat() for event in team],
            ["2099-11-06", "2099-11-09", "2099-11-11", "2099-11-16", "2099-11-18"],
        )
        self.assertEqual(CalendarEvent.objects.filter(title="Monatsabschluss").count(), 1)
        self.assertFalse(CalendarEvent.objects.filter(title="Abgesagt").exists())

        counts = import_ics_events(self.user, ICS)

        self.assertEqual(counts["created"], 0)
        self.assertEqual(counts["duplicates"], 5)
        self.assertEqual(CalendarEvent.objects.count(), 11)

    def _series(self, *lines):
        body = "".join(f"{line}\r\n" for line in lines)
        import_ics_events(self.user, f"BEGIN:VCALENDAR\r\n{body}END:VCALENDAR\r\n")
        return [
            event.start_at.astimezone(BERLIN).strftime("%Y-%m-%d %H:%M")
            for event in CalendarEvent.objects.order_by("start_at")
        ]

    def test_interval_and_monthly_weekday_ordinals(self):
        starts = self._series(
            "BEGIN:VEVENT",
            "UID:sprint",
            "DTSTART;TZID=Europe/Berlin:20990105T100000",
            "RRULE:FREQ=WEEKLY;INTERVAL=2;COUNT=3",
            "SUMMARY:Sprint",
            "END:VEVENT",
            "BEGIN:VEVENT",
            "UID:stammtisch",
            "DTSTART;TZID=Europe/Berlin:20990130T190000",
            "RRULE:FREQ=MONTHLY;BYDAY=-1FR;COUNT=3",
            "SUMMARY:Stammtisch",
            "END:VEVENT",
        )

        self.assertEqual(
            starts,
            [
                "2099-01-05 10:00",
                "2099-01-19 10:00",
                "2099-01-30 19:00",
                "2099-02-02 10:00",
                "2099-02-27 19:00",
                "2099-03-27 19:00",
            ],
        )

    def test_monthly_day_31_skips_short_months_and_keeps_wall_clock_time_over_dst(self):
        starts = self._series(
            "BEGIN:VEVENT",
            "UID:monatsende",
            "DTSTART;TZID=Europe/Berlin:20990131T090000",
            "RRULE:FREQ=MONTHLY;UNTIL=20990601T000000Z",
            "SUMMARY:Monatsende",
            "END:VEVENT",
        )

        self.assertEqual(starts, ["2099-01-31 09:00", "2099-03-31 09:00", "2099-05-31 09:00"])

    def test_exdates_and_changed_or_cancelled_occurrences(self):
        starts = self._series(
            "BEGIN:VEVENT",
            "UID:yoga",
            "DTSTART;TZID=Europe/Berlin:20990105T180000",
            "DTEND;TZID=Europe/Berlin:20990105T190000",
            "RRULE:FREQ=WEEKLY;COUNT=5",
            "EXDATE;TZID=Europe/Berlin:20990112T180000",
            "SUMMARY:Yoga",
            "END:VEVENT",
            "BEGIN:VEVENT",
            "UID:yoga",
            "RECURRENCE-ID;TZID=Europe/Berlin:20990119T180000",
            "DTSTART;TZID=Europe/Berlin:20990120T200000",
            "DTEND;TZID=Europe/Berlin:20990120T213000",
            "SUMMARY:Yoga (verschoben)",
            "END:VEVENT",
            "BEGIN:VEVENT",
            "UID:yoga",
            "RECURRENCE-ID;TZID=Europe/Berlin:20990126T180000",
            "DTSTART;TZID=Europe/Berlin:20990126T180000",
            "STATUS:CANCELLED",
            "END:VEVENT",
        )

        self.assertEqual(starts, ["2099-01-05 18:00", "2099-01-20 20:00", "2099-02-02 18:00"])
        moved = CalendarEvent.objects.get(title="Yoga (verschoben)")
        self.assertEqual((moved.end_at - moved.start_at).total_seconds(), 5400)
        self.assertEqual(len(set(CalendarEvent.objects.values_list("recurrence_id", flat=True))), 1)

        import_ics_events(self.user, "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
        self.assertEqual(CalendarEvent.objects.count(), 3)


class DataImportViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mira", password="secret-12345")
        Profile.objects.create(user=self.user, display_name="Mira", timezone_name="Europe/Berlin")
        self.client.force_login(self.user)

    def _post(self, *files):
        return self.client.post("/settings/", {"form_name": "data_import", "files": list(files)}, follow=True)

    def test_settings_import_creates_notes_and_events_and_reports_problems(self):
        response = self._post(
            SimpleUploadedFile("Einkauf.md", b"- Milch\n- Brot\n"),
            SimpleUploadedFile("kalender.ics", ICS.encode(), content_type="text/calendar"),
            SimpleUploadedFile("bild.png", b"\x89PNG"),
            SimpleUploadedFile("kaputt.md", b"\xff\xfe\xfa"),
        )

        note = Note.objects.get(owner=self.user)
        self.assertEqual(note.title, "Einkauf")
        self.assertEqual(note.plain_text.split(), ["Milch", "Brot"])
        self.assertEqual(CalendarEvent.objects.filter(user=self.user).count(), 11)
        self.assertContains(response, "1 Notiz(en) importiert.")
        self.assertContains(response, "„kalender.ics“: 5 Termin(e) importiert")
        self.assertContains(response, "„bild.png“: Nur .md-, .txt-, .docx- und .ics-Dateien")
        self.assertContains(response, "„kaputt.md“ ist keine UTF-8-Textdatei.")

    def test_disabled_areas_are_not_imported(self):
        SystemSettings.objects.update_or_create(
            pk=1, defaults={"notes_enabled": False, "calendar_event_creation_enabled": False}
        )

        response = self._post(
            SimpleUploadedFile("notiz.md", b"Hallo"),
            SimpleUploadedFile("kalender.ics", ICS.encode()),
        )

        self.assertFalse(Note.objects.exists())
        self.assertFalse(CalendarEvent.objects.exists())
        self.assertContains(response, "Notizen sind gerade deaktiviert.")
        self.assertContains(response, "Termine anlegen ist gerade deaktiviert.")
