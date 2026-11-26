from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from app.models import Bookmark, Contract, Expense, KanbanCard, Recipe, SystemSettings, WishList, WishListItem
from app.services import bookmarks as bookmark_service
from app.services.bookmarks import normalize_tags, parse_page_preview
from app.services.expenses import create_group
from app.services.kanban import create_board
from app.services.safe_fetch import FetchError

PAGE = """<html><head>
<title>Fallback-Titel</title>
<meta property="og:title" content="Online-Banking  der Bank">
<meta name="description" content="Konto, Karten &amp; Überweisungen">
</head><body><meta name="description" content="nicht aus dem Body"></body></html>"""


class BookmarkHelperTests(SimpleTestCase):
    def test_tags_are_lower_case_unique_and_free_of_punctuation(self):
        self.assertEqual(normalize_tags("#Arbeit, finanzen;Arbeit  re-ise! "), "arbeit finanzen re-ise")
        self.assertEqual(normalize_tags(""), "")
        self.assertEqual(len(normalize_tags(" ".join(f"t{i}" for i in range(20))).split()), 10)
        self.assertLessEqual(len(normalize_tags(" ".join("x" * 30 + str(i) for i in range(10)))), 200)

    def test_preview_prefers_open_graph_and_stops_at_the_head(self):
        preview = parse_page_preview(PAGE)
        self.assertEqual(
            preview, {"title": "Online-Banking der Bank", "description": "Konto, Karten & Überweisungen"}
        )
        self.assertEqual(
            parse_page_preview("<title>Nur Titel</title>"), {"title": "Nur Titel", "description": ""}
        )
        self.assertEqual(parse_page_preview("<<<"), {"title": "", "description": ""})

    def test_preview_texts_are_capped_to_the_model_fields(self):
        preview = parse_page_preview(
            f'<title>{"a" * 500}</title><meta name="description" content="{"b" * 900}">'
        )
        self.assertEqual(len(preview["title"]), 80)
        self.assertEqual(len(preview["description"]), 300)


class BookmarkPageTests(TestCase):
    def setUp(self):
        self.mira = User.objects.create_user(username="mira", email="mira@example.com", password="pw-12345!")
        self.lukas = User.objects.create_user(
            username="lukas", email="lukas@example.com", password="pw-12345!"
        )
        self.client.force_login(self.mira)
        # The preview throttle counts in the cache, which outlives a test's database rollback.
        cache.clear()

    def add(self, **data):
        return self.client.post("/tools/bookmarks/", {"form_name": "bookmark_add", **data})

    @patch("app.services.bookmarks.safe_fetch.fetch", return_value=PAGE)
    def test_adding_reads_title_and_description_from_the_page(self, fetch):
        response = self.add(url="bank.example.com", title="", tags="#Finanzen arbeit")

        self.assertRedirects(response, "/tools/bookmarks/", fetch_redirect_response=False)
        bookmark = Bookmark.objects.get(owner=self.mira)
        self.assertEqual(bookmark.title, "Online-Banking der Bank")
        self.assertEqual(bookmark.description, "Konto, Karten & Überweisungen")
        self.assertEqual(bookmark.tags, "finanzen arbeit")
        self.assertEqual(fetch.call_args.args[0], "https://bank.example.com")

    @patch("app.services.bookmarks.safe_fetch.fetch", side_effect=FetchError("weg"))
    def test_an_unreadable_page_still_saves_with_the_host_as_name(self, _fetch):
        self.add(url="https://intern.example.com/pfad", title="")
        self.add(url="https://example.org", title="Eigener Name")

        self.assertEqual(
            sorted(Bookmark.objects.values_list("title", "description")),
            [("Eigener Name", ""), ("intern.example.com", "")],
        )

    def test_internal_addresses_are_never_fetched(self):
        with patch("app.services.bookmarks.safe_fetch.fetch") as fetch:
            self.add(url="http://localhost/admin", title="Lokal")
        fetch.assert_not_called()
        self.assertTrue(Bookmark.objects.filter(title="Lokal").exists())

    @patch("app.services.bookmarks.safe_fetch.fetch", return_value=PAGE)
    def test_previews_are_throttled_per_user(self, fetch):
        with patch.object(bookmark_service, "PREVIEWS_PER_WINDOW", 1):
            self.add(url="https://a.example.com")
            self.add(url="https://b.example.com")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(Bookmark.objects.get(url="https://b.example.com").title, "b.example.com")

    @patch("app.services.bookmarks.fetch_page_preview", new=lambda user, url: {})
    def test_list_filters_by_text_and_tag_and_shows_only_own_bookmarks(self):
        Bookmark.objects.create(
            owner=self.mira, title="Bank", url="https://bank.example.com", tags="finanzen"
        )
        Bookmark.objects.create(
            owner=self.mira,
            title="Wiki",
            url="https://wiki.example.com",
            description="Team-Doku",
            tags="arbeit",
        )
        Bookmark.objects.create(
            owner=self.mira, title="Steuer", url="https://steuer.example.com", tags="finanzen2"
        )
        Bookmark.objects.create(
            owner=self.lukas, title="Fremdbank", url="https://x.example.com", tags="finanzen"
        )

        def titles(params):
            response = self.client.get("/tools/bookmarks/", params)
            return sorted(item["bookmark"].title for item in response.context["bookmark_items"])

        self.assertEqual(titles({}), ["Bank", "Steuer", "Wiki"])
        self.assertEqual(titles({"tag": "finanzen"}), ["Bank"])
        self.assertEqual(titles({"q": "doku"}), ["Wiki"])
        self.assertEqual(titles({"q": "example -bank"}), ["Steuer", "Wiki"])
        response = self.client.get("/tools/bookmarks/")
        self.assertEqual(response.context["tags"], [("arbeit", 1), ("finanzen", 1), ("finanzen2", 1)])
        self.assertNotContains(response, "Fremdbank")

    @patch("app.services.bookmarks.fetch_page_preview", new=lambda user, url: {"description": "Neu"})
    def test_edit_refresh_and_delete_only_touch_own_bookmarks(self):
        own = Bookmark.objects.create(
            owner=self.mira, title="Alt", url="https://a.example.com", description="x"
        )
        foreign = Bookmark.objects.create(owner=self.lukas, title="Fremd", url="https://f.example.com")

        self.client.post(
            "/tools/bookmarks/",
            {
                "form_name": "bookmark_edit",
                "bookmark_id": own.pk,
                "title": "",
                "url": "b.example.com",
                "tags": "A",
            },
        )
        own.refresh_from_db()
        self.assertEqual(
            (own.title, own.url, own.tags, own.description),
            ("b.example.com", "https://b.example.com", "a", ""),
        )

        self.client.post("/tools/bookmarks/", {"form_name": "bookmark_refresh", "bookmark_id": own.pk})
        own.refresh_from_db()
        self.assertEqual(own.description, "Neu")

        for form_name in ("bookmark_edit", "bookmark_refresh", "bookmark_delete"):
            self.client.post(
                "/tools/bookmarks/",
                {
                    "form_name": form_name,
                    "bookmark_id": foreign.pk,
                    "title": "Gekapert",
                    "url": "https://evil.example",
                },
            )
        foreign.refresh_from_db()
        self.assertEqual((foreign.title, foreign.description), ("Fremd", ""))

        self.client.post("/tools/bookmarks/", {"form_name": "bookmark_delete", "bookmark_id": own.pk})
        self.assertFalse(Bookmark.objects.filter(pk=own.pk).exists())

    @patch("app.services.bookmarks.fetch_page_preview", new=lambda user, url: {})
    def test_limit_and_dashboard_cap(self):
        with patch.object(bookmark_service, "MAX_BOOKMARKS", 2):
            for index in range(3):
                self.add(url=f"https://{index}.example.com", title=f"L{index}")
        self.assertEqual(Bookmark.objects.filter(owner=self.mira).count(), 2)

        Bookmark.objects.bulk_create(
            Bookmark(owner=self.mira, title=f"B{index}", url="https://example.com") for index in range(30)
        )
        response = self.client.get("/home/")
        self.assertEqual(len(response.context["dashboard_bookmarks"]), bookmark_service.DASHBOARD_BOOKMARKS)
        self.assertContains(response, 'href="/tools/bookmarks/" aria-label="Alle Lesezeichen"')

    def test_page_follows_the_tools_flag(self):
        SystemSettings.objects.create(tools_enabled=False)
        self.assertEqual(self.client.get("/tools/bookmarks/").status_code, 503)


class GlobalSearchAreaTests(TestCase):
    def setUp(self):
        self.mira = User.objects.create_user(username="mira", email="mira@example.com", password="pw-12345!")
        self.lukas = User.objects.create_user(
            username="lukas", email="lukas@example.com", password="pw-12345!"
        )
        self.client.force_login(self.mira)

    def search(self, query, **params):
        return self.client.get("/search/", {"q": query, **params})

    def titles(self, response, key):
        return sorted(item["title"] for item in response.context[key])

    def test_finds_bookmarks_recipes_contracts_boards_wish_lists_and_expense_groups(self):
        Bookmark.objects.create(
            owner=self.mira,
            title="Shop",
            url="https://shop.example.com",
            description="Kaffee online",
            tags="einkauf",
        )
        Recipe.objects.create(owner=self.mira, title="Frühstück", ingredients="200 g Kaffee")
        Contract.objects.create(
            owner=self.mira, name="Kaffee-Abo", amount=Decimal("9.99"), start_date=date(2026, 1, 1)
        )
        board = create_board(self.mira, "Haushalt")
        KanbanCard.objects.create(
            board=board, column=board.columns.first(), lane=board.lanes.first(), title="Kaffee kaufen"
        )
        wish_list = WishList.objects.create(owner=self.mira, title="Geburtstag")
        WishListItem.objects.create(wish_list=wish_list, url="https://example.com/p", title="Kaffeemühle")
        group, _error = create_group(self.mira, "WG")
        Expense.objects.create(
            group=group,
            title="Kaffee",
            amount=Decimal("5"),
            paid_by=self.mira,
            spent_on=date(2026, 1, 2),
            created_by=self.mira,
        )
        # The same word in someone else's things stays hidden.
        Bookmark.objects.create(owner=self.lukas, title="Kaffee fremd", url="https://x.example.com")
        Recipe.objects.create(owner=self.lukas, title="Kaffee fremd")
        create_board(self.lukas, "Kaffee fremd")

        response = self.search("kaffee")

        self.assertEqual(self.titles(response, "bookmark_results"), ["Shop"])
        self.assertEqual(self.titles(response, "recipe_results"), ["Frühstück"])
        self.assertEqual(self.titles(response, "contract_results"), ["Kaffee-Abo"])
        self.assertEqual(self.titles(response, "kanban_results"), ["Haushalt"])
        self.assertEqual(self.titles(response, "wishlist_results"), ["Geburtstag"])
        self.assertEqual(self.titles(response, "expense_results"), ["WG"])
        [board_row] = response.context["kanban_results"]
        self.assertIn("Kaffee kaufen", "".join(part["text"] for part in board_row["snippet"]))
        self.assertNotContains(response, "fremd")

    def test_every_word_must_match_and_minus_excludes(self):
        Recipe.objects.create(owner=self.mira, title="Pfannkuchen", ingredients="Mehl Milch Eier")
        Recipe.objects.create(owner=self.mira, title="Brot", ingredients="Mehl Hefe")

        self.assertEqual(self.titles(self.search("mehl milch"), "recipe_results"), ["Pfannkuchen"])
        self.assertEqual(self.titles(self.search("mehl -milch"), "recipe_results"), ["Brot"])
        self.assertEqual(self.titles(self.search('"milch eier"'), "recipe_results"), ["Pfannkuchen"])

    def test_tag_search_finds_tagged_bookmarks_but_matches_nothing_else_by_text(self):
        Bookmark.objects.create(owner=self.mira, title="Wiki", url="https://wiki.example.com", tags="arbeit")
        Recipe.objects.create(owner=self.mira, title="Arbeit-Snack")

        response = self.search("#arbeit")

        self.assertEqual(self.titles(response, "bookmark_results"), ["Wiki"])
        self.assertEqual(response.context["recipe_results"], [])

    def test_area_filter_shows_one_area_and_chips_count_all(self):
        for index in range(25):
            Recipe.objects.create(owner=self.mira, title=f"Suppe {index}")
        Bookmark.objects.create(owner=self.mira, title="Suppenblog", url="https://s.example.com")

        response = self.search("suppe")
        self.assertEqual(
            {section["key"]: section["count_label"] for section in response.context["hit_sections"]},
            {"bookmarks": "1", "recipes": "20+"},
        )
        self.assertContains(response, "Mehr in Rezepte")

        response = self.search("suppe", **{"in": "recipes"})
        self.assertEqual([section["key"] for section in response.context["shown_sections"]], ["recipes"])
        self.assertEqual(len(response.context["recipe_results"]), 25)
        self.assertContains(response, 'aria-current="page"><i class="fa-solid fa-utensils"', html=False)

    def test_unknown_area_falls_back_to_all(self):
        Recipe.objects.create(owner=self.mira, title="Suppe")
        response = self.search("suppe", **{"in": "<script>"})
        self.assertEqual(response.context["selected_area"], "")
        self.assertEqual(self.titles(response, "recipe_results"), ["Suppe"])
