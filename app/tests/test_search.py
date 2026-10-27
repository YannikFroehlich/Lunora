from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from app.models import (
    CalendarEvent,
    ChatMessage,
    Conversation,
    ConversationMember,
    Note,
    Profile,
    SystemSettings,
    Task,
    UmlDiagram,
    VacationPeriod,
    VocabularyCard,
    VocabularyList,
    VocabularyListShare,
)
from app.services.note_search import build_snippet, highlight_text, parse_search_query, search_notes
from app.services.notes import accessible_notes


class GlobalSearchTests(TestCase):
    def setUp(self):
        self.mira = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        self.lukas = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        Profile.objects.create(user=self.mira, display_name="Mira")
        Profile.objects.create(user=self.lukas, display_name="Lukas")

    @staticmethod
    def rendered_title(result):
        """Reassemble a note title from its highlight segments."""
        return "".join(part["text"] for part in result["title_segments"])

    def test_global_search_requires_login(self):
        response = self.client.get("/search/?q=Rakete")
        self.assertRedirects(response, "/login/?next=/search/%3Fq%3DRakete")

    def test_global_search_returns_matches_across_all_three_categories(self):
        Note.objects.create(owner=self.mira, title="Raketenstart planen", plain_text="Zündfolge prüfen")
        conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.mira)
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        ChatMessage.objects.create(conversation=conversation, sender=self.lukas, body="Rakete ist startklar")
        CalendarEvent.objects.create(
            user=self.mira,
            title="Raketenstart",
            start_at=timezone.now() + timedelta(days=5),
            end_at=timezone.now() + timedelta(days=5, hours=1),
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/search/?q=Rakete")

        self.assertEqual(response.status_code, 200)
        # The matching part of a note title is wrapped in a highlight, so assert on
        # the context rather than on a contiguous title string.
        self.assertEqual(
            [self.rendered_title(item) for item in response.context["notes_results"]], ["Raketenstart planen"]
        )
        self.assertContains(response, "Rakete ist startklar")
        self.assertContains(response, "Raketenstart")

    def test_global_search_empty_query_shows_no_results(self):
        Note.objects.create(owner=self.mira, title="Irgendeine Notiz")
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/search/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Wonach suchst du?")
        self.assertContains(response, "Notizen öffnen")
        self.assertContains(response, "Nachrichten öffnen")
        self.assertContains(response, "Kalender öffnen")
        self.assertNotContains(response, "Irgendeine Notiz")

    def test_global_search_no_results_offers_reset_and_shortcuts(self):
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/search/?q=Unauffindbar")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["has_search_results"])
        self.assertContains(response, "Keine Treffer für")
        self.assertContains(response, "Unauffindbar")
        self.assertContains(response, "Suche zurücksetzen")
        self.assertContains(response, "Notizen öffnen")
        self.assertContains(response, "Nachrichten öffnen")
        self.assertContains(response, "Kalender öffnen")

    def test_global_search_does_not_leak_other_users_private_data(self):
        Note.objects.create(owner=self.lukas, title="Raketengeheimnis")
        conversation = Conversation.objects.create(created_by=self.lukas)
        other_user = User.objects.create_user(
            username="anna@example.com", email="anna@example.com", password="secret-12345"
        )
        Profile.objects.create(user=other_user, display_name="Anna")
        ConversationMember.objects.create(conversation=conversation, user=self.lukas)
        ConversationMember.objects.create(conversation=conversation, user=other_user)
        ChatMessage.objects.create(
            conversation=conversation, sender=self.lukas, body="Rakete geheime Unterhaltung"
        )
        CalendarEvent.objects.create(
            user=self.lukas,
            title="Rakete privater Termin",
            start_at=timezone.now() + timedelta(days=5),
            end_at=timezone.now() + timedelta(days=5, hours=1),
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/search/?q=Rakete")

        self.assertEqual(response.status_code, 200)
        # Highlighting splits a title mid-string, so a plain assertNotContains on the
        # title would pass even if the note leaked. Assert on the context instead.
        self.assertEqual(response.context["notes_results"], [])
        self.assertNotContains(response, "Rakete geheime Unterhaltung")
        self.assertNotContains(response, "Rakete privater Termin")

    def test_global_search_returns_matches_across_tasks_and_vacation_periods(self):
        Task.objects.create(user=self.mira, title="Raketenstart vorbereiten")
        VacationPeriod.objects.create(
            user=self.mira,
            name=VacationPeriod.SONDERURLAUB,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 3),
            notes="Raketenmesse besuchen",
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/search/?q=Rakete")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["task_results"]), 1)
        self.assertEqual(response.context["task_results"][0]["title"], "Raketenstart vorbereiten")
        self.assertEqual(len(response.context["vacation_results"]), 1)
        self.assertContains(response, "Raketenstart vorbereiten")

    def test_global_search_hides_disabled_sections(self):
        SystemSettings.objects.create(notes_enabled=False, messages_enabled=False)
        Note.objects.create(owner=self.mira, title="Raketenidee")
        CalendarEvent.objects.create(
            user=self.mira,
            title="Raketenstart",
            start_at=timezone.now() + timedelta(days=5),
            end_at=timezone.now() + timedelta(days=5, hours=1),
        )
        self.client.login(username="mira@example.com", password="secret-12345")

        response = self.client.get("/search/?q=Rakete")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Notizen deaktiviert")
        self.assertContains(response, "Nachrichten deaktiviert")
        self.assertEqual(response.context["notes_results"], [])
        self.assertContains(response, "Raketenstart")


class GlobalSearchToolTests(TestCase):
    def setUp(self):
        self.mira = User.objects.create_user(username="mira", email="mira@example.com", password="pw-12345!")
        self.lukas = User.objects.create_user(
            username="lukas", email="lukas@example.com", password="pw-12345!"
        )
        self.client.login(username="mira", password="pw-12345!")

    def test_search_finds_own_and_shared_vocabulary_and_own_diagrams(self):
        own = VocabularyList.objects.create(owner=self.mira, title="Unit 5")
        VocabularyCard.objects.create(vocabulary_list=own, term="rocket", translation="Rakete")
        shared = VocabularyList.objects.create(owner=self.lukas, title="Raketen-Vokabeln")
        VocabularyListShare.objects.create(vocabulary_list=shared, user=self.mira)
        VocabularyList.objects.create(owner=self.lukas, title="Rakete privat")
        diagram = UmlDiagram.objects.create(owner=self.mira, title="Raketensteuerung")
        UmlDiagram.objects.create(owner=self.lukas, title="Rakete fremd")

        results = self.client.get("/search/?q=rakete").context["tool_results"]

        by_title = {item["title"]: item for item in results}
        self.assertEqual(set(by_title), {"Unit 5", "Raketen-Vokabeln", "Raketensteuerung"})
        self.assertEqual(by_title["Unit 5"]["url"], f"/tools/vocabulary/{own.pk}/")
        self.assertEqual(by_title["Raketen-Vokabeln"]["url"], "/tools/vocabulary/")
        self.assertEqual(by_title["Raketensteuerung"]["url"], f"/tools/uml/{diagram.pk}/")

    def test_search_skips_tools_when_flag_is_off(self):
        VocabularyList.objects.create(owner=self.mira, title="Rakete")
        SystemSettings.objects.update_or_create(pk=1, defaults={"tools_enabled": False})

        response = self.client.get("/search/?q=rakete")

        self.assertEqual(response.context["tool_results"], [])
        self.assertNotContains(response, "Werkzeuge öffnen")


class NoteSearchQueryParserTests(SimpleTestCase):
    def test_bare_words_become_required_terms(self):
        parsed = parse_search_query("Rakete Zündung")
        self.assertEqual(parsed.terms, ("Rakete", "Zündung"))
        self.assertEqual(parsed.phrases, ())
        self.assertEqual(parsed.exclusions, ())

    def test_quoted_input_becomes_a_phrase_and_dash_prefix_excludes(self):
        parsed = parse_search_query('"kalter Start" rakete -geheim')
        self.assertEqual(parsed.terms, ("rakete",))
        self.assertEqual(parsed.phrases, ("kalter Start",))
        self.assertEqual(parsed.exclusions, ("geheim",))

    def test_punctuation_splits_a_token_into_separate_terms(self):
        self.assertEqual(parse_search_query("start,zündung;test").terms, ("start", "zündung", "test"))

    def test_single_quoted_word_stays_a_term_rather_than_a_phrase(self):
        parsed = parse_search_query('"rakete"')
        self.assertEqual(parsed.terms, ("rakete",))
        self.assertEqual(parsed.phrases, ())

    def test_duplicate_terms_are_collapsed_and_term_count_is_capped(self):
        self.assertEqual(parse_search_query("rakete rakete").terms, ("rakete",))
        parsed = parse_search_query(" ".join(f"wort{index}" for index in range(30)))
        self.assertEqual(len(parsed.terms), 12)

    def test_blank_query_is_falsy_and_exclusion_only_query_has_no_terms(self):
        self.assertFalse(parse_search_query("   "))
        parsed = parse_search_query("-geheim")
        self.assertFalse(parsed)
        self.assertEqual(parsed.exclusions, ("geheim",))


class NoteSearchSnippetTests(SimpleTestCase):
    def test_snippet_marks_every_match_and_keeps_surrounding_text(self):
        parsed = parse_search_query("Zündung")
        segments = build_snippet("Vor dem Start muss die Zündung geprüft werden.", parsed)
        self.assertEqual([part["text"] for part in segments if part["match"]], ["Zündung"])
        self.assertEqual(
            "".join(part["text"] for part in segments), "Vor dem Start muss die Zündung geprüft werden."
        )

    def test_snippet_matches_case_insensitively_and_inside_compounds(self):
        parsed = parse_search_query("rakete")
        segments = build_snippet("Der Raketenstart ist geplant.", parsed)
        self.assertEqual([part["text"] for part in segments if part["match"]], ["Rakete"])

    def test_snippet_without_a_match_falls_back_to_truncated_text(self):
        segments = build_snippet("Kein Treffer hier drin", parse_search_query("rakete"))
        self.assertEqual(segments, [{"text": "Kein Treffer hier drin", "match": False}])

    def test_snippet_windows_around_a_late_match_instead_of_the_text_start(self):
        body = ("Fülltext " * 60) + "Zündschlüssel am Ende"
        segments = build_snippet("Anfangswort " + body, parse_search_query("Zündschlüssel"))
        rendered = "".join(part["text"] for part in segments)
        self.assertTrue(rendered.startswith("… "))
        self.assertIn("Zündschlüssel", rendered)
        self.assertNotIn("Anfangswort", rendered)

    def test_empty_text_yields_no_segments(self):
        self.assertEqual(build_snippet("", parse_search_query("rakete")), [])

    def test_highlight_text_marks_a_match_without_windowing(self):
        segments = highlight_text("Raketenstart planen", parse_search_query("rakete"))
        self.assertEqual(
            segments,
            [{"text": "Rakete", "match": True}, {"text": "nstart planen", "match": False}],
        )

    def test_highlight_text_keeps_a_long_title_intact(self):
        title = "Ein sehr langer Titel " * 8 + "mit Rakete am Ende"
        rendered = "".join(part["text"] for part in highlight_text(title, parse_search_query("rakete")))
        self.assertEqual(rendered, title)
        self.assertNotIn("…", rendered)

    def test_highlight_text_without_a_match_returns_one_plain_segment(self):
        self.assertEqual(
            highlight_text("Einkaufsliste", parse_search_query("rakete")),
            [{"text": "Einkaufsliste", "match": False}],
        )


class NoteSearchRankingTests(TestCase):
    def setUp(self):
        self.mira = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=self.mira, display_name="Mira")

    def search(self, query):
        return list(
            search_notes(accessible_notes(self.mira), query).order_by("-search_rank", "-updated_at", "-id")
        )

    def test_multiple_terms_are_combined_with_and(self):
        both = Note.objects.create(owner=self.mira, title="Start", plain_text="Rakete und Zündung geprüft")
        Note.objects.create(owner=self.mira, title="Nur Rakete", plain_text="ohne das zweite Wort")

        self.assertEqual(self.search("Rakete Zündung"), [both])

    def test_title_match_ranks_above_body_only_match(self):
        body_hit = Note.objects.create(
            owner=self.mira, title="Wochenplan", plain_text="Die Rakete ist fertig"
        )
        title_hit = Note.objects.create(owner=self.mira, title="Rakete", plain_text="ohne Treffer im Text")

        self.assertEqual(self.search("Rakete"), [title_hit, body_hit])

    def test_phrase_requires_adjacent_words(self):
        adjacent = Note.objects.create(
            owner=self.mira, title="Ablauf", plain_text="Ein kalter Start am Morgen"
        )
        Note.objects.create(owner=self.mira, title="Ablauf B", plain_text="Start war kalter als erwartet")

        self.assertEqual(self.search('"kalter Start"'), [adjacent])

    def test_excluded_term_removes_a_matching_note(self):
        keep = Note.objects.create(owner=self.mira, title="Rakete", plain_text="öffentlich")
        Note.objects.create(owner=self.mira, title="Rakete", plain_text="streng geheim")

        self.assertEqual(self.search("Rakete -geheim"), [keep])

    def test_german_compounds_match_as_prefix_and_infix(self):
        note = Note.objects.create(
            owner=self.mira, title="Raketenstart planen", plain_text="Zündfolge prüfen"
        )

        self.assertEqual(self.search("Rakete"), [note])
        self.assertEqual(self.search("start"), [note])

    def test_search_does_not_return_notes_the_user_cannot_access(self):
        other = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        Note.objects.create(owner=other, title="Raketengeheimnis", plain_text="privat")

        self.assertEqual(self.search("Rakete"), [])

    def test_empty_query_annotates_a_neutral_rank_without_filtering(self):
        Note.objects.create(owner=self.mira, title="Irgendeine Notiz")

        results = search_notes(accessible_notes(self.mira), "")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].search_rank, 0.0)


class NotePostgresSearchSqlTests(SimpleTestCase):
    """Compile the production search path without needing a PostgreSQL server.

    The suite runs on SQLite, so ``search_notes`` normally never takes its
    PostgreSQL branch. Building the SQL against a real PostgreSQL backend proves
    the tsquery/tsvector expressions are constructed correctly.
    """

    def compile_search(self, query):
        from django.db.backends.postgresql.base import DatabaseWrapper

        connection = DatabaseWrapper(
            {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": "lunora",
                "USER": "lunora",
                "PASSWORD": "",
                "HOST": "127.0.0.1",
                "PORT": "5432",
                "OPTIONS": {},
                "CONN_MAX_AGE": 0,
                "CONN_HEALTH_CHECKS": False,
                "AUTOCOMMIT": True,
                "ATOMIC_REQUESTS": False,
                "TIME_ZONE": None,
                "TEST": {"CHARSET": None, "COLLATION": None, "NAME": None, "MIRROR": None},
            },
            "postgres-sql-check",
        )
        self.assertEqual(connection.vendor, "postgresql")
        with patch("app.services.note_search.connection", connection):
            queryset = search_notes(accessible_notes(User(pk=1, username="mira@example.com")), query)
        sql, params = queryset.query.get_compiler(connection=connection).as_sql()
        return sql, [value for value in params if isinstance(value, str)]

    def test_terms_compile_to_a_prefix_tsquery_ranked_with_ts_rank(self):
        sql, params = self.compile_search("rakete")

        self.assertIn("to_tsquery", sql)
        self.assertIn("ts_rank", sql)
        self.assertIn("rakete:*", params)
        self.assertIn("german", params)

    def test_phrases_compile_to_phraseto_tsquery(self):
        sql, params = self.compile_search('"kalter Start"')

        self.assertIn("phraseto_tsquery", sql)
        self.assertIn("kalter Start", params)

    def test_substring_fallback_is_kept_alongside_the_tsquery(self):
        _sql, params = self.compile_search("rakete")

        self.assertIn("%rakete%", params)

    def test_terms_that_are_not_bare_words_are_dropped_from_the_raw_tsquery(self):
        # search_type="raw" is handed to to_tsquery(); a stray operator would be a
        # database error at query time, so the parser must never emit one.
        _sql, params = self.compile_search("rakete & !start")

        self.assertNotIn("&", "".join(value for value in params if ":*" in value))
        self.assertIn("rakete:*", params)
        self.assertIn("start:*", params)


class NoteSearchViewTests(TestCase):
    def setUp(self):
        self.mira = User.objects.create_user(
            username="mira@example.com", email="mira@example.com", password="secret-12345"
        )
        Profile.objects.create(user=self.mira, display_name="Mira")
        self.client.login(username="mira@example.com", password="secret-12345")

    def test_notes_page_ranks_by_relevance_when_searching(self):
        body_hit = Note.objects.create(
            owner=self.mira, title="Wochenplan", plain_text="Die Rakete ist fertig"
        )
        title_hit = Note.objects.create(owner=self.mira, title="Rakete", plain_text="ohne Treffer im Text")

        response = self.client.get("/notes/?q=Rakete")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "relevance")
        self.assertEqual([item["id"] for item in response.context["note_items"]], [title_hit.id, body_hit.id])

    def test_notes_page_keeps_custom_order_without_a_query(self):
        Note.objects.create(owner=self.mira, title="Wochenplan")

        response = self.client.get("/notes/")

        self.assertEqual(response.context["current_sort"], "custom")

    def test_notes_page_honours_an_explicit_sort_while_searching(self):
        Note.objects.create(owner=self.mira, title="Bravo Rakete")
        Note.objects.create(owner=self.mira, title="Alpha Rakete")

        response = self.client.get("/notes/?q=Rakete&sort=title")

        self.assertEqual(response.context["current_sort"], "title")
        self.assertEqual(
            [item["title"] for item in response.context["note_items"]], ["Alpha Rakete", "Bravo Rakete"]
        )

    def test_notes_page_falls_back_to_custom_order_for_relevance_without_a_query(self):
        Note.objects.create(owner=self.mira, title="Wochenplan")

        response = self.client.get("/notes/?sort=relevance")

        self.assertEqual(response.context["current_sort"], "custom")

    def test_notes_page_applies_multi_term_and_search(self):
        Note.objects.create(owner=self.mira, title="Start", plain_text="Rakete und Zündung geprüft")
        Note.objects.create(owner=self.mira, title="Nur Rakete", plain_text="ohne das zweite Wort")

        response = self.client.get("/notes/?q=Rakete+Z%C3%BCndung")

        self.assertEqual([item["title"] for item in response.context["note_items"]], ["Start"])

    def test_global_search_highlights_the_matching_fragment(self):
        Note.objects.create(
            owner=self.mira, title="Wochenplan", plain_text="Vor dem Start muss die Zündung geprüft werden"
        )

        response = self.client.get("/search/?q=Z%C3%BCndung")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<mark class="search-hit">Zündung</mark>', html=False)

    def test_global_search_highlights_the_matching_part_of_the_title(self):
        Note.objects.create(owner=self.mira, title="Raketenstart planen", plain_text="Zündfolge prüfen")

        response = self.client.get("/search/?q=Rakete")

        self.assertContains(response, '<mark class="search-hit">Rakete</mark>nstart planen', html=False)

    def test_global_search_falls_back_to_a_placeholder_title(self):
        Note.objects.create(owner=self.mira, title="", plain_text="Rakete im Text")

        response = self.client.get("/search/?q=Rakete")

        self.assertContains(response, "Unbenannte Notiz")

    def test_global_search_escapes_note_content_in_the_snippet(self):
        Note.objects.create(
            owner=self.mira, title="Wochenplan", plain_text="<script>alert(1)</script> Zündung"
        )

        response = self.client.get("/search/?q=Z%C3%BCndung")

        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, "&lt;script&gt;")
