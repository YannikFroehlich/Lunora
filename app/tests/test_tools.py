import json
from datetime import date, datetime, time, timedelta
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from app.models import (
    Habit,
    HabitCheck,
    MealPlanEntry,
    NotificationPreference,
    Profile,
    Recipe,
    ShoppingItem,
    ShoppingListShare,
    ShoppingProduct,
    SystemSettings,
    Task,
    TaskTimeEntry,
    UmlDiagram,
    UserNotification,
    VocabularyCard,
    VocabularyList,
    VocabularyListShare,
)
from app.services.habits import (
    STREAK_WINDOW_DAYS,
    current_streak,
    habit_items,
    habit_stats,
    is_done_now,
    longest_streak,
)
from app.services.notifications import materialize_due_habit_reminders, send_pending_user_notification_emails
from app.services.recipe_import import parse_recipe_page
from app.services.recipes import add_quantities, ingredient_rows, parse_ingredient, scale_quantity
from app.services.shopping import CATALOG, PRODUCTS, SUGGESTIONS, classify_item
from app.services.shopping_products import find_open_item, normalize_barcode, parse_open_food_facts
from app.services.uml_content import validate_uml_document
from app.services.user_preferences import localtime_for_user
from app.services.vocabulary import parse_bulk_cards, review_card
from app.tests.helpers import FakeHttpConnection, FakeJsonResponse


def _uml_class(class_id, name, kind="class", **overrides):
    item = {
        "id": class_id,
        "kind": kind,
        "name": name,
        "stereotype": "",
        "x": 0,
        "y": 0,
        "color": "default",
        "attributes": [],
        "methods": [],
        "literals": [],
    }
    item.update(overrides)
    return item


def _uml_relation(relation_id, kind, source, target):
    return {
        "id": relation_id,
        "kind": kind,
        "source": source,
        "target": target,
        "label": "",
        "source_multiplicity": "",
        "target_multiplicity": "",
    }


class UmlDiagramTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="lea@example.com", email="lea@example.com", password="pw-12345!"
        )
        self.other = User.objects.create_user(
            username="tom@example.com", email="tom@example.com", password="pw-12345!"
        )
        self.client.login(username="lea@example.com", password="pw-12345!")

    def _document(self, classes=(), relations=(), notes=()):
        return {"version": 1, "classes": list(classes), "relations": list(relations), "notes": list(notes)}

    def _put(self, diagram, payload):
        return self.client.put(
            f"/tools/uml/api/{diagram.pk}/", data=json.dumps(payload), content_type="application/json"
        )

    def test_tools_hub_lists_uml_editor(self):
        response = self.client.get("/tools/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "UML-Klassendiagramm")
        self.assertContains(response, 'href="/tools/uml/"')

    def test_disabled_tools_return_503_for_pages_and_api(self):
        diagram = UmlDiagram.objects.create(owner=self.user, title="Zoo")
        SystemSettings.objects.create(tools_enabled=False)

        self.assertEqual(self.client.get("/tools/").status_code, 503)
        self.assertEqual(self.client.get(f"/tools/uml/{diagram.pk}/").status_code, 503)
        response = self._put(diagram, {"title": "Zoo", "document": self._document()})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["ok"])

    def test_create_opens_editor_and_is_listed(self):
        response = self.client.post("/tools/uml/", {"form_name": "uml_create", "title": "  Bibliothek "})

        diagram = UmlDiagram.objects.get(owner=self.user)
        self.assertRedirects(response, f"/tools/uml/{diagram.pk}/")
        self.assertEqual(diagram.title, "Bibliothek")
        self.assertEqual(diagram.document, {"version": 1, "classes": [], "relations": [], "notes": []})
        self.assertContains(self.client.get("/tools/uml/"), "Bibliothek")

    def test_blank_title_is_rejected(self):
        self.client.post("/tools/uml/", {"form_name": "uml_create", "title": "   "})

        self.assertFalse(UmlDiagram.objects.exists())

    def test_save_validates_and_stores_document(self):
        diagram = UmlDiagram.objects.create(owner=self.user, title="Zoo")
        document = self._document(
            classes=[
                _uml_class(
                    "c1",
                    "Tier",
                    "abstract",
                    x=12.345,
                    attributes=[
                        {
                            "visibility": "-",
                            "name": "name",
                            "type": "String",
                            "default": "",
                            "is_static": False,
                        }
                    ],
                    methods=[
                        {
                            "visibility": "+",
                            "name": "laut",
                            "params": "",
                            "return_type": "String",
                            "is_static": False,
                            "is_abstract": True,
                        }
                    ],
                ),
                _uml_class("c2", "Hund"),
            ],
            relations=[_uml_relation("r1", "inheritance", "c2", "c1")],
            notes=[{"id": "n1", "text": "Oberklasse", "x": 0, "y": 200}],
        )
        document["relations"].append(_uml_relation("r2", "note_link", "n1", "c1"))

        response = self._put(diagram, {"title": "Zoo neu", "document": document})

        self.assertEqual(response.status_code, 200, response.content)
        diagram.refresh_from_db()
        self.assertEqual(diagram.title, "Zoo neu")
        self.assertEqual(diagram.document["classes"][0]["x"], 12.3)
        self.assertEqual([item["id"] for item in diagram.document["relations"]], ["r1", "r2"])

    def test_save_rejects_invalid_documents_without_overwriting(self):
        diagram = UmlDiagram.objects.create(owner=self.user, title="Zoo")
        invalid_documents = [
            self._document(classes=[_uml_class("c1", "A", "trait")]),
            self._document(classes=[_uml_class("c1", "A"), _uml_class("c1", "B")]),
            self._document(
                classes=[_uml_class("c1", "A")], relations=[_uml_relation("r1", "association", "c1", "c9")]
            ),
            self._document(
                classes=[_uml_class("c1", "A")], relations=[_uml_relation("r1", "inheritance", "c1", "c1")]
            ),
            self._document(classes=[_uml_class("c1", "A", x=True)]),
            self._document(classes=[_uml_class("c1", "A", onclick="x")]),
            self._document(
                classes=[_uml_class("c1", "A")],
                notes=[{"id": "n1", "text": "", "x": 0, "y": 0}],
                relations=[_uml_relation("r1", "association", "n1", "c1")],
            ),
            {"version": 2, "classes": [], "relations": [], "notes": []},
        ]

        for document in invalid_documents:
            with self.subTest(document=document):
                response = self._put(diagram, {"title": "Kaputt", "document": document})
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.json()["ok"])

        diagram.refresh_from_db()
        self.assertEqual(diagram.title, "Zoo")

    def test_other_users_cannot_open_save_duplicate_or_delete(self):
        diagram = UmlDiagram.objects.create(owner=self.other, title="Geheim")

        self.assertEqual(self.client.get(f"/tools/uml/{diagram.pk}/").status_code, 404)
        self.assertEqual(self._put(diagram, {"title": "X", "document": self._document()}).status_code, 404)
        self.assertEqual(
            self.client.post(
                "/tools/uml/", {"form_name": "uml_duplicate", "diagram_id": diagram.pk}
            ).status_code,
            404,
        )
        self.client.post("/tools/uml/", {"form_name": "uml_delete", "diagram_id": diagram.pk})
        self.client.post("/tools/uml/", {"form_name": "uml_delete", "diagram_id": "abc"})
        self.assertTrue(UmlDiagram.objects.filter(pk=diagram.pk).exists())
        self.assertNotContains(self.client.get("/tools/uml/"), "Geheim")

    def test_malformed_values_are_rejected_instead_of_crashing(self):
        from django.core.exceptions import ValidationError

        huge_position = {"id": "n1", "text": "", "x": 10**400, "y": 0}
        for document in (
            self._document(classes=[_uml_class("c1", "Auto", kind=[])]),
            self._document(notes=[huge_position]),
        ):
            with self.subTest(document=str(document)[:60]), self.assertRaises(ValidationError):
                validate_uml_document(document)

    def test_duplicate_and_delete_own_diagram(self):
        document = self._document(classes=[_uml_class("c1", "Auto")])
        diagram = UmlDiagram.objects.create(
            owner=self.user, title="Garage", document=validate_uml_document(document)
        )

        self.client.post("/tools/uml/", {"form_name": "uml_duplicate", "diagram_id": diagram.pk})
        copy = UmlDiagram.objects.exclude(pk=diagram.pk).get()
        self.assertEqual(copy.title, "Garage (Kopie)")
        self.assertEqual(copy.document, diagram.document)

        self.client.post("/tools/uml/", {"form_name": "uml_delete", "diagram_id": diagram.pk})
        self.assertEqual(list(UmlDiagram.objects.values_list("pk", flat=True)), [copy.pk])

    def test_editor_embeds_document_as_json_script(self):
        diagram = UmlDiagram.objects.create(
            owner=self.user,
            title="</script>",
            document=validate_uml_document(self._document(classes=[_uml_class("c1", "<b>Auto</b>")])),
        )

        response = self.client.get(f"/tools/uml/{diagram.pk}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="uml-diagram-data"')
        self.assertNotContains(response, "<b>Auto</b>")
        self.assertEqual(response.context["diagram_payload"]["document"]["classes"][0]["name"], "<b>Auto</b>")


class VocabularyTrainerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="lea@example.com", email="lea@example.com", password="pw-12345!"
        )
        self.other = User.objects.create_user(
            username="tom@example.com", email="tom@example.com", password="pw-12345!"
        )
        self.client.login(username="lea@example.com", password="pw-12345!")
        self.vocabulary_list = VocabularyList.objects.create(owner=self.user, title="Unit 3")

    def _review(self, card, payload):
        return self.client.post(
            f"/tools/vocabulary/api/cards/{card.pk}/review/",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_create_list_and_add_cards(self):
        response = self.client.post(
            "/tools/vocabulary/",
            {
                "form_name": "vocab_list_create",
                "title": "Französisch",
                "source_language": "fr",
                "target_language": "de",
            },
        )
        created = VocabularyList.objects.get(title="Französisch")
        self.assertRedirects(response, f"/tools/vocabulary/{created.pk}/")

        self.client.post(
            f"/tools/vocabulary/{created.pk}/",
            {"form_name": "vocab_card_add", "term": " la maison ", "translation": "das Haus", "note": ""},
        )
        card = created.cards.get()
        self.assertEqual((card.term, card.translation, card.box), ("la maison", "das Haus", 1))
        self.assertContains(self.client.get("/tools/vocabulary/"), "1 Vokabel(n)")

    def test_bulk_import_accepts_tab_semicolon_equals_and_reports_bad_lines(self):
        cards, errors = parse_bulk_cards(
            "﻿Begriff;Übersetzung;Notiz\nhouse ; Haus\nto run\trennen\nsun = Sonne\n"
        )
        self.assertEqual(errors, [])
        self.assertEqual(cards, [("house", "Haus", ""), ("to run", "rennen", ""), ("sun", "Sonne", "")])

        _cards, errors = parse_bulk_cards("ohne trennzeichen\n; leer")
        self.assertEqual(len(errors), 2)

        response = self.client.post(
            f"/tools/vocabulary/{self.vocabulary_list.pk}/",
            {"form_name": "vocab_card_bulk", "lines": "cat;Katze;Tier\ndog;Hund"},
            follow=True,
        )
        self.assertContains(response, "2 Vokabel(n) importiert.")
        self.assertEqual(list(self.vocabulary_list.cards.values_list("note", flat=True)), ["Tier", ""])

    def test_invalid_bulk_import_creates_nothing(self):
        self.client.post(
            f"/tools/vocabulary/{self.vocabulary_list.pk}/",
            {"form_name": "vocab_card_bulk", "lines": "cat;Katze\nkaputt"},
        )

        self.assertFalse(self.vocabulary_list.cards.exists())

    def test_review_moves_cards_through_leitner_boxes(self):
        card = VocabularyCard.objects.create(
            vocabulary_list=self.vocabulary_list, term="dog", translation="Hund"
        )

        before = timezone.now()
        self.assertEqual(
            self._review(card, {"correct": True}).json(), {"ok": True, "box": 2, "interval_days": 1}
        )
        card.refresh_from_db()
        self.assertEqual((card.box, card.correct_count), (2, 1))
        self.assertGreaterEqual(card.due_at, before + timedelta(days=1))

        self._review(card, {"correct": False})
        card.refresh_from_db()
        self.assertEqual((card.box, card.wrong_count), (1, 1))
        self.assertLessEqual(card.due_at, timezone.now())

        for _ in range(6):
            review_card(card, correct=True)
        self.assertEqual(card.box, 5)

        self.assertEqual(self._review(card, {"correct": "ja"}).status_code, 400)

    def test_other_users_cannot_see_or_review_cards(self):
        foreign_list = VocabularyList.objects.create(owner=self.other, title="Geheim")
        card = VocabularyCard.objects.create(vocabulary_list=foreign_list, term="a", translation="b")

        self.assertEqual(self.client.get(f"/tools/vocabulary/{foreign_list.pk}/").status_code, 404)
        self.assertEqual(self.client.get(f"/tools/vocabulary/{foreign_list.pk}/practice/").status_code, 404)
        self.assertEqual(self.client.get(f"/tools/vocabulary/{foreign_list.pk}/export/").status_code, 404)
        self.assertEqual(self._review(card, {"correct": True}).status_code, 404)
        self.client.post("/tools/vocabulary/", {"form_name": "vocab_list_delete", "list_id": foreign_list.pk})
        self.client.post(
            f"/tools/vocabulary/{self.vocabulary_list.pk}/",
            {"form_name": "vocab_card_delete", "card_id": card.pk},
        )
        self.assertTrue(VocabularyCard.objects.filter(pk=card.pk).exists())

    def test_edit_delete_reset_and_export(self):
        card = VocabularyCard.objects.create(
            vocabulary_list=self.vocabulary_list, term="Straße", translation="street", box=4, correct_count=3
        )
        url = f"/tools/vocabulary/{self.vocabulary_list.pk}/"

        self.client.post(url, {"form_name": "vocab_reset_progress"})
        card.refresh_from_db()
        self.assertEqual((card.box, card.correct_count), (1, 0))

        self.client.post(
            url, {"form_name": "vocab_card_edit", "card_id": card.pk, "term": "Weg", "translation": "way"}
        )
        card.refresh_from_db()
        self.assertEqual(card.term, "Weg")

        export = self.client.get(f"{url}export/")
        self.assertEqual(export["Content-Type"], "text/csv; charset=utf-8")
        self.assertEqual(export.content.decode("utf-8"), "﻿Begriff;Übersetzung;Notiz\r\nWeg;way;\r\n")

        self.client.post(url, {"form_name": "vocab_card_delete", "card_id": card.pk})
        self.assertFalse(self.vocabulary_list.cards.exists())

    def test_practice_page_embeds_due_cards(self):
        due = VocabularyCard.objects.create(vocabulary_list=self.vocabulary_list, term="a", translation="b")
        VocabularyCard.objects.create(
            vocabulary_list=self.vocabulary_list,
            term="c",
            translation="d",
            box=3,
            due_at=timezone.now() + timedelta(days=2),
        )

        response = self.client.get(f"/tools/vocabulary/{self.vocabulary_list.pk}/practice/")

        cards = response.context["practice_payload"]["cards"]
        self.assertEqual([card["due"] for card in cards], [True, False])
        self.assertEqual(cards[0]["id"], due.pk)
        self.assertContains(response, 'id="vocab-practice-data"')

    def test_tools_hub_links_vocabulary_and_respects_flag(self):
        self.assertContains(self.client.get("/tools/"), 'href="/tools/vocabulary/"')

        SystemSettings.objects.update_or_create(pk=1, defaults={"tools_enabled": False})
        self.assertEqual(self.client.get("/tools/vocabulary/").status_code, 503)
        card = VocabularyCard.objects.create(vocabulary_list=self.vocabulary_list, term="a", translation="b")
        self.assertEqual(self._review(card, {"correct": True}).status_code, 503)


class VocabularySharingTests(TestCase):
    def setUp(self):
        self.lea = User.objects.create_user(username="lea", email="lea@example.com", password="pw-12345!")
        self.tom = User.objects.create_user(username="tom", email="tom@example.com", password="pw-12345!")
        self.vocabulary_list = VocabularyList.objects.create(owner=self.lea, title="Unit 3")
        VocabularyCard.objects.create(
            vocabulary_list=self.vocabulary_list, term="house", translation="Haus", box=4, correct_count=3
        )
        self.detail_url = f"/tools/vocabulary/{self.vocabulary_list.pk}/"

    def test_owner_shares_by_email_or_username_and_removes_share(self):
        self.client.login(username="lea", password="pw-12345!")

        self.client.post(self.detail_url, {"form_name": "vocab_share_add", "recipient": "TOM@example.com"})
        self.assertTrue(
            VocabularyListShare.objects.filter(vocabulary_list=self.vocabulary_list, user=self.tom)
        )
        response = self.client.get(self.detail_url)
        self.assertEqual(response.context["shares"][0]["user_id"], self.tom.pk)

        self.client.post(self.detail_url, {"form_name": "vocab_share_remove", "user_id": self.tom.pk})
        self.assertFalse(VocabularyListShare.objects.exists())

    def test_share_rejects_self_unknown_and_duplicate_recipients(self):
        self.client.login(username="lea", password="pw-12345!")
        for recipient in ("lea", "niemand@example.com", ""):
            response = self.client.post(
                self.detail_url, {"form_name": "vocab_share_add", "recipient": recipient}, follow=True
            )
            self.assertEqual(list(response.context["messages"])[0].level_tag, "error")
        self.client.post(self.detail_url, {"form_name": "vocab_share_add", "recipient": "tom"})
        response = self.client.post(
            self.detail_url, {"form_name": "vocab_share_add", "recipient": "tom"}, follow=True
        )
        self.assertIn("bereits geteilt", str(list(response.context["messages"])[-1]))
        self.assertEqual(VocabularyListShare.objects.count(), 1)

    def test_recipient_sees_list_read_only_and_copies_it_with_fresh_progress(self):
        VocabularyListShare.objects.create(vocabulary_list=self.vocabulary_list, user=self.tom)
        self.client.login(username="tom", password="pw-12345!")

        overview = self.client.get("/tools/vocabulary/")
        self.assertEqual(overview.context["shared_lists"][0]["title"], "Unit 3")
        self.assertEqual(self.client.get(self.detail_url).status_code, 404)
        self.client.post(self.detail_url, {"form_name": "vocab_share_add", "recipient": "tom"})

        response = self.client.post(
            "/tools/vocabulary/", {"form_name": "vocab_list_copy", "list_id": self.vocabulary_list.pk}
        )
        copy = VocabularyList.objects.get(owner=self.tom)
        self.assertRedirects(response, f"/tools/vocabulary/{copy.pk}/")
        card = copy.cards.get()
        self.assertEqual((card.term, card.translation, card.box, card.correct_count), ("house", "Haus", 1, 0))
        self.assertEqual(self.vocabulary_list.cards.get().box, 4)

    def test_only_shared_lists_can_be_copied_and_recipient_can_leave(self):
        self.client.login(username="tom", password="pw-12345!")
        response = self.client.post(
            "/tools/vocabulary/", {"form_name": "vocab_list_copy", "list_id": self.vocabulary_list.pk}
        )
        self.assertEqual(response.status_code, 404)

        VocabularyListShare.objects.create(vocabulary_list=self.vocabulary_list, user=self.tom)
        self.client.post(
            "/tools/vocabulary/", {"form_name": "vocab_share_leave", "list_id": self.vocabulary_list.pk}
        )
        self.assertFalse(VocabularyListShare.objects.exists())
        self.assertTrue(VocabularyList.objects.filter(pk=self.vocabulary_list.pk).exists())

    def test_sharing_notifies_recipient_once_and_respects_tools_preference(self):
        Profile.objects.create(user=self.lea, display_name="Lea")
        Profile.objects.create(user=self.tom, display_name="Tom", notify_email=True)
        self.client.login(username="lea", password="pw-12345!")
        self.client.post(self.detail_url, {"form_name": "vocab_share_add", "recipient": "tom"})
        self.client.post(self.detail_url, {"form_name": "vocab_share_add", "recipient": "tom"})

        notification = UserNotification.objects.get(recipient=self.tom)
        self.assertEqual(notification.kind, UserNotification.KIND_VOCABULARY_SHARE)
        self.assertEqual(notification.title, "Lea hat eine Vokabelliste mit dir geteilt")
        self.assertEqual((notification.body, notification.url), ("Unit 3", "/tools/vocabulary/"))

        self.client.login(username="tom", password="pw-12345!")
        self.assertContains(self.client.get("/notifications/"), "Lea hat eine Vokabelliste mit dir geteilt")
        NotificationPreference.objects.create(
            user=self.tom,
            category=NotificationPreference.CATEGORY_TOOLS,
            inbox_enabled=False,
            email_enabled=False,
        )
        self.assertNotContains(
            self.client.get("/notifications/"), "Lea hat eine Vokabelliste mit dir geteilt"
        )
        self.assertEqual(send_pending_user_notification_emails()["sent"], 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_share_notification_email_and_backfill(self):
        Profile.objects.create(user=self.tom, display_name="Tom", notify_email=True)
        VocabularyListShare.objects.create(vocabulary_list=self.vocabulary_list, user=self.tom)
        self.client.login(username="tom", password="pw-12345!")

        self.client.get("/notifications/")
        self.assertEqual(
            UserNotification.objects.filter(
                recipient=self.tom, kind=UserNotification.KIND_VOCABULARY_SHARE
            ).count(),
            1,
        )
        self.assertEqual(send_pending_user_notification_emails(include_vocabulary_shares=False)["sent"], 0)
        self.assertEqual(send_pending_user_notification_emails()["sent"], 1)
        self.assertIn("Vokabelliste", mail.outbox[0].subject)


class ShoppingListTests(TestCase):
    url = "/tools/shopping/"

    def setUp(self):
        self.user = User.objects.create_user(username="lea", email="lea@example.com", password="pw-12345!")
        self.other = User.objects.create_user(username="tom", email="tom@example.com", password="pw-12345!")
        self.client.force_login(self.user)

    def _post(self, form_name, **data):
        return self.client.post(self.url, {"form_name": form_name, **data})

    def test_add_stores_the_item_for_the_user_and_refocuses_the_field(self):
        response = self._post("shopping_add", name="  Milch ", quantity="2 Liter")

        self.assertRedirects(response, f"{self.url}?add=1")
        item = ShoppingItem.objects.get()
        self.assertEqual((item.owner, item.name, item.quantity), (self.user, "Milch", "2 Liter"))
        self.assertContains(self.client.get(f"{self.url}?add=1"), "autofocus")
        self.assertNotContains(self.client.get(self.url), "autofocus")

    def test_blank_name_is_refused(self):
        response = self._post("shopping_add", name="   ", quantity="2")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ShoppingItem.objects.exists())

    def test_the_item_limit_is_enforced(self):
        ShoppingItem.objects.bulk_create(
            ShoppingItem(owner=self.user, name=f"Artikel {number}") for number in range(200)
        )

        response = self._post("shopping_add", name="Einer zu viel")

        self.assertContains(response, "höchstens 200 Artikel")
        self.assertEqual(ShoppingItem.objects.filter(owner=self.user).count(), 200)

    def test_toggle_returns_to_the_aisle_when_ticking_and_to_the_row_when_unticking(self):
        item = ShoppingItem.objects.create(owner=self.user, name="Brot")

        response = self._post("shopping_toggle", item_id=item.pk)
        self.assertRedirects(response, f"{self.url}#group-bakery", fetch_redirect_response=False)
        item.refresh_from_db()
        self.assertTrue(item.is_done)

        response = self._post("shopping_toggle", item_id=item.pk)
        self.assertRedirects(response, f"{self.url}#item-{item.pk}", fetch_redirect_response=False)
        item.refresh_from_db()
        self.assertFalse(item.is_done)

    def test_adding_an_item_already_on_the_list_reopens_it_instead_of_duplicating(self):
        item = ShoppingItem.objects.create(owner=self.user, name="Milch", quantity="1 Liter", is_done=True)

        self._post("shopping_add", name="milch", quantity="")
        item.refresh_from_db()
        self.assertEqual((item.is_done, item.quantity), (False, "1 Liter"))

        self._post("shopping_add", name="Milch", quantity="2 Liter")
        item.refresh_from_db()
        self.assertEqual(item.quantity, "2 Liter")
        self.assertEqual(ShoppingItem.objects.count(), 1)

    def test_open_items_are_grouped_by_aisle_in_store_order_and_ticked_ones_move_to_the_cart(self):
        for name, done in (("Duschgel", False), ("Vollmilch", False), ("Bananen", False), ("Brot", True)):
            ShoppingItem.objects.create(owner=self.user, name=name, is_done=done)

        response = self.client.get(self.url)

        groups = response.context["groups"]
        self.assertEqual([group["key"] for group in groups], ["produce", "dairy", "household"])
        self.assertEqual([item.emoji for group in groups for item in group["items"]], ["🍌", "🥛", "🧼"])
        self.assertEqual([item.name for item in response.context["done_items"]], ["Brot"])
        self.assertContains(response, "Im Wagen")
        self.assertContains(response, 'aria-controls="shopping-suggestions"')
        self.assertIn(
            {"name": "Toilettenpapier", "emoji": "🧻", "aisle": "Drogerie & Haushalt"},
            response.context["suggestions"],
        )

    def test_clear_done_keeps_open_items_and_other_users_items(self):
        ShoppingItem.objects.create(owner=self.user, name="Gekauft", is_done=True)
        ShoppingItem.objects.create(owner=self.user, name="Offen")
        ShoppingItem.objects.create(owner=self.other, name="Fremd", is_done=True)

        self._post("shopping_clear_done")

        self.assertCountEqual(ShoppingItem.objects.values_list("name", flat=True), ["Offen", "Fremd"])

    def test_items_of_others_stay_out_of_reach(self):
        foreign = ShoppingItem.objects.create(owner=self.other, name="Toms Kaffee")

        self.assertNotContains(self.client.get(self.url), "Toms Kaffee")
        self._post("shopping_toggle", item_id=foreign.pk)
        self._post("shopping_delete", item_id=foreign.pk)

        foreign.refresh_from_db()
        self.assertFalse(foreign.is_done)

    def test_tampered_item_ids_redirect_instead_of_failing(self):
        for item_id in ("abc", "9" * 5000, ""):
            with self.subTest(item_id=item_id[:10]):
                self.assertEqual(self._post("shopping_toggle", item_id=item_id).status_code, 302)
                self.assertEqual(self._post("shopping_delete", item_id=item_id).status_code, 302)

    def test_tools_page_links_the_list_and_counts_open_items(self):
        ShoppingItem.objects.create(owner=self.user, name="Brot")
        ShoppingItem.objects.create(owner=self.user, name="Butter", is_done=True)

        response = self.client.get("/tools/")

        self.assertContains(response, self.url)
        self.assertContains(response, "1 offen")

    def test_disabled_tools_flag_blocks_the_list(self):
        SystemSettings.objects.update_or_create(pk=1, defaults={"tools_enabled": False})

        self.assertEqual(self.client.get(self.url).status_code, 503)
        self.assertEqual(self._post("shopping_add", name="Milch").status_code, 503)
        self.assertFalse(ShoppingItem.objects.exists())


MILK = "4000417025005"


def off_milk_response():
    return FakeJsonResponse(
        {
            "status": 1,
            "product": {
                "product_name_de": "Frische Vollmilch  3,5 %",
                "brands": "Gut & Günstig, Edeka",
                "quantity": "1 l",
                "nutriscore_grade": "b",
                "nutriments": {"energy-kcal_100g": 64, "fat_100g": "3.5", "sugars_100g": 4.8},
            },
        }
    )


class ShoppingBarcodeTests(SimpleTestCase):
    def test_barcodes_are_normalized_and_checked(self):
        cases = {
            MILK: MILK,
            "4000 4170 2500 5": MILK,
            "96385074": "96385074",
            "036000291452": "0036000291452",
            "4000417025006": None,
            "12345": None,
            "40004170250x5": None,
            "": None,
            None: None,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_barcode(raw), expected)

    def test_open_food_facts_answers_are_reduced_to_checked_fields(self):
        product = parse_open_food_facts(
            {
                "status": 1,
                "product": {
                    "product_name": "Whole milk",
                    "product_name_de": "Frische   Vollmilch",
                    "brands": "Gut & Günstig, Edeka",
                    "quantity": "1 l ℮",
                    "nutriscore_grade": "not-applicable",
                    "nutriments": {
                        "energy-kcal_100g": 64,
                        "fat_100g": "3.54",
                        "sugars_100g": "viel",
                        "proteins_100g": -1,
                        "salt_100g": True,
                        "carbohydrates_100g": float("nan"),
                    },
                },
            }
        )

        self.assertEqual(
            product,
            {
                "name": "Frische Vollmilch",
                "brand": "Gut & Günstig",
                "package": "1 l",
                "nutri_score": "",
                "nutrition": {"energy-kcal": 64.0, "fat": 3.5},
            },
        )
        for data in (
            {"status": 0},
            {"status": 1, "product": {"brands": "X"}},
            [],
            {"status": 1, "product": "x"},
        ):
            with self.subTest(data=data):
                self.assertIsNone(parse_open_food_facts(data))


@patch("app.services.shopping_products.urlopen")
class ShoppingScanTests(TestCase):
    url = "/tools/shopping/scan/"

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="lea", email="lea@example.com", password="pw-12345!")
        self.other = User.objects.create_user(username="tom", email="tom@example.com", password="pw-12345!")
        self.client.force_login(self.user)

    def _scan(self, barcode=MILK, mode="shop", **data):
        return self.client.post(self.url, {"barcode": barcode, "mode": mode, **data})

    def test_scanning_in_the_store_ticks_off_the_matching_item_and_remembers_the_product(self, urlopen):
        urlopen.return_value = off_milk_response()
        milk = ShoppingItem.objects.create(owner=self.user, name="Milch")
        bread = ShoppingItem.objects.create(owner=self.user, name="Brot")

        response = self._scan()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ticked")
        self.assertEqual(response.json()["product"]["nutri_score"], "B")
        milk.refresh_from_db()
        bread.refresh_from_db()
        self.assertEqual((milk.is_done, milk.barcode, bread.is_done), (True, MILK, False))
        product = ShoppingProduct.objects.get(owner=self.user)
        self.assertEqual(
            (product.name, product.brand, product.package, product.nutrition["fat"]),
            ("Frische Vollmilch 3,5 %", "Gut & Günstig", "1 l", 3.5),
        )
        self.assertIn(f"/product/{MILK}.json", urlopen.call_args.args[0].full_url)

        # Scanned again it's already in the cart, and the stored product needs no lookup.
        urlopen.reset_mock()
        self.assertEqual(self._scan().json()["status"], "done")
        urlopen.assert_not_called()

    def test_items_match_by_word_stems_and_unlisted_products_are_reported(self, urlopen):
        urlopen.return_value = off_milk_response()
        ShoppingItem.objects.create(owner=self.user, name="Bananen")

        self.assertEqual(self._scan().json()["status"], "missing")
        self.assertFalse(ShoppingItem.objects.filter(is_done=True).exists())

        ShoppingItem.objects.create(owner=self.user, name="Vollmilch")
        self.assertEqual(self._scan().json()["status"], "ticked")
        self.assertTrue(ShoppingItem.objects.get(name="Vollmilch").is_done)

    def test_items_match_products_sold_under_a_synonym(self, urlopen):
        carrots = ShoppingItem.objects.create(owner=self.user, name="Möhren")
        jam = ShoppingItem.objects.create(owner=self.user, name="Marmelade")
        cabbage = ShoppingItem.objects.create(owner=self.user, name="Weißkohl")
        organic = ShoppingItem.objects.create(owner=self.user, name="Bio Tomaten")

        for product_name, expected in (
            ("Karotten 1 kg", carrots),
            ("Erdbeerkonfitüre", jam),
            ("Weißkraut frisch", cabbage),
            ("Bio Paradeiser", organic),
            # Every word of the item has to be covered.
            ("Paradeiser", None),
            ("Gurken", None),
        ):
            with self.subTest(product_name):
                product = ShoppingProduct(barcode=MILK, name=product_name)
                self.assertEqual(find_open_item(self.user, product), expected)

        self.assertEqual(self._scan(name="Karotten").json()["message"], "„Möhren“ abgehakt.")

    def test_compound_items_match_synonyms_of_their_last_part(self, urlopen):
        carrots = ShoppingItem.objects.create(owner=self.user, name="Speisemöhren")
        tomatoes = ShoppingItem.objects.create(owner=self.user, name="Kirschtomaten")

        for product_name, expected in (
            ("Karotten 1 kg", carrots),
            ("Speisemöhren lose", carrots),
            ("Paradeiser am Strauch", tomatoes),
            ("Gurken", None),
        ):
            with self.subTest(product_name):
                product = ShoppingProduct(barcode=MILK, name=product_name)
                self.assertEqual(find_open_item(self.user, product), expected)

    def test_scanning_at_home_puts_the_product_on_the_list_once(self, urlopen):
        urlopen.return_value = off_milk_response()

        self.assertEqual(self._scan(mode="add").json()["status"], "added")
        item = ShoppingItem.objects.get(owner=self.user)
        self.assertEqual((item.name, item.quantity, item.barcode), ("Frische Vollmilch 3,5 %", "1 l", MILK))

        item.is_done = True
        item.save()
        self._scan(mode="add")
        item.refresh_from_db()
        self.assertFalse(item.is_done)
        self.assertEqual(ShoppingItem.objects.count(), 1)

    def test_an_unknown_product_is_named_by_hand(self, urlopen):
        urlopen.side_effect = HTTPError("url", 404, "Not Found", {}, None)
        ShoppingItem.objects.create(owner=self.user, name="Hafermilch")

        response = self._scan()
        self.assertEqual((response.json()["status"], response.json()["barcode"]), ("unknown", MILK))
        self.assertFalse(ShoppingProduct.objects.exists())

        response = self._scan(name="  Oatly   Hafermilch ")
        self.assertEqual(response.json()["status"], "ticked")
        self.assertEqual(ShoppingProduct.objects.get().name, "Oatly Hafermilch")
        self.assertEqual(urlopen.call_count, 1)
        # Nothing but a name: no product line under the item.
        self.assertNotContains(self.client.get("/tools/shopping/"), "shopping-product")

    def test_a_failed_lookup_is_reported_and_not_cached(self, urlopen):
        urlopen.side_effect = URLError("offline")

        response = self._scan()

        self.assertEqual(response.status_code, 503)
        self.assertIn("nicht erreichbar", response.json()["message"])
        urlopen.side_effect = None
        urlopen.return_value = off_milk_response()
        self.assertEqual(self._scan(mode="add").json()["status"], "added")

    def test_lookups_are_cached_across_users_but_products_stay_private(self, urlopen):
        urlopen.return_value = off_milk_response()
        ShoppingProduct.objects.create(owner=self.other, barcode=MILK, name="Toms Milch")

        self._scan(mode="add")
        self.client.force_login(self.other)
        self._scan(mode="add")
        self.client.force_login(
            User.objects.create_user(username="ida", email="ida@example.com", password="x")
        )
        self._scan(mode="add")

        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(ShoppingProduct.objects.get(owner=self.user).name, "Frische Vollmilch 3,5 %")
        self.assertEqual(ShoppingItem.objects.get(owner=self.other).name, "Toms Milch")
        self.assertEqual(ShoppingProduct.objects.count(), 3)

    def test_new_lookups_are_throttled_per_user(self, urlopen):
        urlopen.return_value = FakeJsonResponse({"status": 0})
        with patch("app.services.shopping_products.LOOKUPS_PER_WINDOW", 1):
            self.assertEqual(self._scan(barcode="96385074").status_code, 200)
            response = self._scan()

        self.assertEqual(response.status_code, 503)
        self.assertIn("Zu viele", response.json()["message"])

    def test_invalid_requests_are_refused(self, urlopen):
        self.assertEqual(self._scan(barcode="4000417025006").status_code, 400)
        self.assertEqual(self._scan(mode="steal").status_code, 400)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        urlopen.assert_not_called()
        self.client.logout()
        self.assertEqual(self._scan().status_code, 302)

    def test_the_list_shows_the_scanned_product_with_its_nutrition(self, urlopen):
        urlopen.return_value = off_milk_response()
        self._scan(mode="add")

        response = self.client.get("/tools/shopping/")

        self.assertContains(response, "nutri-score-b")
        self.assertContains(response, "Gut &amp; Günstig · 1 l")
        self.assertContains(response, "<td>3,5 g</td>", html=True)
        self.assertContains(response, "<td>64 kcal</td>", html=True)
        self.assertContains(response, "data-shopping-scanner")

    def test_disabled_tools_flag_blocks_scanning(self, urlopen):
        SystemSettings.objects.update_or_create(pk=1, defaults={"tools_enabled": False})

        response = self._scan()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Content-Type"], "application/json")
        urlopen.assert_not_called()


class SharedShoppingListTests(TestCase):
    url = "/tools/shopping/"

    def setUp(self):
        cache.clear()
        self.lea = User.objects.create_user(username="lea", email="lea@example.com", password="pw-12345!")
        self.tom = User.objects.create_user(username="tom", email="tom@example.com", password="pw-12345!")
        self.ida = User.objects.create_user(username="ida", email="ida@example.com", password="pw-12345!")
        self.lea_url = f"{self.url}?list={self.lea.pk}"

    def _share_with_tom(self):
        self.client.force_login(self.lea)
        return self.client.post(self.url, {"form_name": "shopping_share", "identifier": "TOM@example.com"})

    def test_sharing_notifies_the_member_and_refuses_bad_recipients(self):
        self.assertRedirects(self._share_with_tom(), self.url)

        share = ShoppingListShare.objects.get()
        self.assertEqual((share.owner, share.user), (self.lea, self.tom))
        notification = UserNotification.objects.get(recipient=self.tom)
        self.assertEqual(
            (notification.kind, notification.url, notification.source_key),
            (UserNotification.KIND_SHOPPING_SHARE, self.lea_url, f"shopping-share:{share.pk}"),
        )
        Profile.objects.update_or_create(user=self.tom, defaults={"notify_email": True})
        self.assertEqual(send_pending_user_notification_emails(include_shopping_shares=False)["sent"], 0)
        self.assertEqual(send_pending_user_notification_emails()["sent"], 1)
        for identifier, error in (
            ("tom", "bereits geteilt"),
            ("lea@example.com", "nicht mit dir selbst"),
            ("niemand", "Kein eindeutiges Konto"),
        ):
            with self.subTest(identifier=identifier):
                response = self.client.post(
                    self.url, {"form_name": "shopping_share", "identifier": identifier}
                )
                self.assertContains(response, error)
        self.assertEqual(ShoppingListShare.objects.count(), 1)

    def test_members_work_on_the_owners_list(self):
        self._share_with_tom()
        milk = ShoppingItem.objects.create(owner=self.lea, name="Milch")
        ShoppingItem.objects.create(owner=self.tom, name="Toms eigener Kaffee")
        self.client.force_login(self.tom)

        response = self.client.get(self.lea_url)
        self.assertContains(response, "Milch")
        self.assertNotContains(response, "Toms eigener Kaffee")
        self.assertContains(response, f'href="{self.lea_url}" aria-current="page"')
        self.assertContains(response, f'data-scan-url="/tools/shopping/scan/?list={self.lea.pk}"')

        response = self.client.post(self.lea_url, {"form_name": "shopping_add", "name": "Brot"})
        self.assertRedirects(response, f"{self.lea_url}&add=1")
        self.assertTrue(ShoppingItem.objects.filter(owner=self.lea, name="Brot").exists())
        response = self.client.post(
            self.lea_url, {"form_name": "shopping_toggle", "item_id": milk.pk, "done": "1"}
        )
        self.assertRedirects(response, f"{self.lea_url}#group-dairy", fetch_redirect_response=False)
        milk.refresh_from_db()
        self.assertTrue(milk.is_done)
        self.client.post(self.lea_url, {"form_name": "shopping_clear_done"})
        self.assertFalse(ShoppingItem.objects.filter(pk=milk.pk).exists())

    def test_scanning_on_a_shared_list_ticks_the_owners_item(self):
        self._share_with_tom()
        milk = ShoppingItem.objects.create(owner=self.lea, name="Milch")
        self.client.force_login(self.tom)

        with patch("app.services.shopping_products.urlopen", return_value=off_milk_response()):
            response = self.client.post(
                f"/tools/shopping/scan/?list={self.lea.pk}", {"barcode": MILK, "mode": "shop"}
            )

        self.assertEqual(response.json()["status"], "ticked")
        milk.refresh_from_db()
        self.assertTrue(milk.is_done)
        # The product is the scanner's, and the owner sees its details on the item too.
        self.assertTrue(ShoppingProduct.objects.filter(owner=self.tom, barcode=MILK).exists())
        self.client.force_login(self.lea)
        self.assertContains(self.client.get(self.url), "nutri-score-b")

    def test_ticking_sends_the_wanted_state_so_two_people_ticking_agree(self):
        self.client.force_login(self.lea)
        item = ShoppingItem.objects.create(owner=self.lea, name="Eier")

        for _ in range(2):
            self.client.post(self.url, {"form_name": "shopping_toggle", "item_id": item.pk, "done": "1"})

        item.refresh_from_db()
        self.assertTrue(item.is_done)

    def test_lists_that_are_not_shared_stay_out_of_reach(self):
        foreign = ShoppingItem.objects.create(owner=self.lea, name="Leas Kaffee")
        self.client.force_login(self.ida)

        response = self.client.get(self.lea_url, follow=True)
        self.assertRedirects(response, self.url)
        self.assertContains(response, "nicht (mehr) mit dir geteilt")
        self.assertNotContains(response, "Leas Kaffee")
        for url in (self.lea_url, self.url):
            self.client.post(url, {"form_name": "shopping_toggle", "item_id": foreign.pk, "done": "1"})
            self.client.post(url, {"form_name": "shopping_delete", "item_id": foreign.pk})
        foreign.refresh_from_db()
        self.assertFalse(foreign.is_done)
        response = self.client.post(
            f"/tools/shopping/scan/?list={self.lea.pk}", {"barcode": MILK, "mode": "add"}
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get(f"{self.url}?list=abc").status_code, 302)

    def test_only_the_owner_manages_members_and_members_can_leave(self):
        self._share_with_tom()
        ShoppingListShare.objects.create(owner=self.lea, user=self.ida)
        self.client.force_login(self.tom)

        self.client.post(self.lea_url, {"form_name": "shopping_unshare", "user_id": self.ida.pk})
        self.client.post(self.lea_url, {"form_name": "shopping_share", "identifier": "tom@example.com"})
        self.assertEqual(ShoppingListShare.objects.count(), 2)

        response = self.client.post(self.lea_url, {"form_name": "shopping_leave"})
        self.assertRedirects(response, self.url)
        self.assertFalse(ShoppingListShare.objects.filter(user=self.tom).exists())
        self.assertFalse(UserNotification.objects.filter(recipient=self.tom).exists())

        self.client.force_login(self.lea)
        self.client.post(self.url, {"form_name": "shopping_unshare", "user_id": self.ida.pk})
        self.client.post(self.url, {"form_name": "shopping_unshare", "user_id": "x"})
        self.assertFalse(ShoppingListShare.objects.exists())

    def test_the_version_changes_with_the_list_and_only_shared_lists_poll(self):
        self.client.force_login(self.lea)
        page = self.client.get(self.url)
        self.assertNotContains(page, "data-poll-url")

        first = self.client.get(f"{self.url}?version=1").json()["version"]
        self.assertEqual(self.client.get(f"{self.url}?version=1").json()["version"], first)
        item = ShoppingItem.objects.create(owner=self.lea, name="Milch")
        second = self.client.get(f"{self.url}?version=1").json()["version"]
        self.assertNotEqual(second, first)
        item.is_done = True
        item.save()
        self.assertNotEqual(self.client.get(f"{self.url}?version=1").json()["version"], second)

        self._share_with_tom()
        self.assertContains(self.client.get(self.url), f'data-poll-url="{self.url}?version=1"')
        self.client.force_login(self.tom)
        self.assertContains(self.client.get(self.lea_url), f'data-poll-url="{self.lea_url}&amp;version=1"')

    def test_disabled_tools_flag_answers_the_poll_with_json(self):
        SystemSettings.objects.update_or_create(pk=1, defaults={"tools_enabled": False})
        self.client.force_login(self.lea)

        response = self.client.get(f"{self.url}?version=1")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Content-Type"], "application/json")


class ShoppingCatalogTests(SimpleTestCase):
    def test_the_last_word_of_a_compound_decides_the_aisle(self):
        cases = {
            "Erdbeermarmelade": ("pantry", "🍯"),
            "Schweinefleisch": ("meat", "🥩"),
            "Milchschokolade": ("sweets", "🍫"),
            "Kaffeebohnen": ("drinks", "☕"),
            "Kidneybohnen": ("pantry", "🥫"),
            "Salatgurke": ("produce", "🥒"),
            "Glühbirne": ("household", "💡"),
            "Apfelsaft": ("drinks", "🧃"),
            "Reis": ("pantry", "🍚"),
            "Vanilleeis": ("frozen", "🍨"),
            "Süßkartoffeln": ("produce", "🍠"),
            "EIER": ("dairy", "🥚"),
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(classify_item(name), expected)

    def test_emoji_fit_the_product_within_an_aisle(self):
        cases = {
            "Salami": "🥪",
            "Bratwurst": "🌭",
            "Himbeeren": "🍓",
            "Heidelbeeren": "🫐",
            "Zwieback": "🍞",
            "Prosecco": "🥂",
            "Alufolie": "📜",
            "Müllbeutel": "🗑️",
        }
        for name, emoji in cases.items():
            with self.subTest(name=name):
                self.assertEqual(classify_item(name)[1], emoji)

    def test_unknown_items_land_in_other(self):
        self.assertEqual(classify_item("Gartenstuhl"), ("other", "🛒"))

    def test_every_catalog_entry_classifies_into_its_own_aisle(self):
        for category, entries in CATALOG.items():
            for name, emoji, _stems in entries:
                with self.subTest(name=name):
                    self.assertEqual(classify_item(name), (category, emoji))

    def test_every_suggested_product_lands_in_the_aisle_it_is_listed_under(self):
        for category, names in PRODUCTS.items():
            for name in (name.strip() for name in names.split(",")):
                with self.subTest(name=name):
                    self.assertEqual(classify_item(name)[0], category)

    def test_suggestions_cover_compounds_with_their_own_emoji(self):
        by_name = {suggestion["name"]: suggestion for suggestion in SUGGESTIONS}

        self.assertEqual(by_name["Apfelsaft"], {"name": "Apfelsaft", "emoji": "🧃", "aisle": "Getränke"})
        self.assertEqual(by_name["Paulaner Spezi"]["aisle"], "Getränke")
        self.assertEqual(by_name["Haribo Goldbären"]["aisle"], "Süßes & Snacks")
        self.assertGreater(len(SUGGESTIONS), 1000)
        self.assertEqual(len(by_name), len(SUGGESTIONS))

    def test_multi_word_stems_match_only_as_a_whole(self):
        self.assertEqual(classify_item("Hohes C"), ("drinks", "🧃"))
        self.assertEqual(classify_item("Weintrauben")[0], "produce")  # not "ben" of "Ben & Jerry's"

    def test_leading_underscore_anchors_a_stem_at_a_word_start(self):
        self.assertEqual(classify_item("Gin"), ("drinks", "🍸"))
        self.assertEqual(classify_item("Dry Gin"), ("drinks", "🍸"))
        self.assertEqual(classify_item("Werther's Original")[0], "sweets")
        self.assertEqual(classify_item("Vita Malz"), ("drinks", "🍺"))


class HabitTrackerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="lea", email="lea@example.com", password="pw-12345!")
        Profile.objects.create(user=self.user, display_name="Lea", timezone_name="Europe/Berlin")
        self.other = User.objects.create_user(username="tom", email="tom@example.com", password="pw-12345!")
        self.client.force_login(self.user)
        self.today = localtime_for_user(profile_or_user=self.user).date()

    def test_streaks_count_back_from_today_without_breaking_on_an_open_today(self):
        today = date(2026, 10, 28)  # a Wednesday
        daily = Habit(cadence=Habit.CADENCE_DAILY)
        weekly = Habit(cadence=Habit.CADENCE_WEEKLY)
        checked = {today - timedelta(days=offset) for offset in (1, 2, 3, 5)}

        self.assertEqual(current_streak(daily, checked, today), 3)
        self.assertEqual(current_streak(daily, checked | {today}, today), 4)
        self.assertFalse(is_done_now(daily, checked, today))
        # Monday of this week, plus one day in each of the two previous weeks.
        weekly_checks = {date(2026, 10, 26), date(2026, 10, 22), date(2026, 10, 12)}
        self.assertEqual(current_streak(weekly, weekly_checks, today), 3)
        self.assertTrue(is_done_now(weekly, weekly_checks, today))
        self.assertEqual(current_streak(weekly, {date(2026, 10, 12)}, today), 0)

    def test_streaks_longer_than_the_loaded_window_are_counted_in_full(self):
        habit = Habit.objects.create(owner=self.user, title="Lesen")
        weekly = Habit.objects.create(owner=self.user, title="Sport", cadence=Habit.CADENCE_WEEKLY)
        long_streak = STREAK_WINDOW_DAYS + 50
        HabitCheck.objects.bulk_create(
            [
                HabitCheck(habit=habit, date=self.today - timedelta(days=offset))
                for offset in range(long_streak)
            ]
            + [HabitCheck(habit=weekly, date=self.today - timedelta(weeks=offset)) for offset in range(70)]
        )

        streaks = {item["title"]: item["streak"] for item in habit_items(self.user, self.today)}

        self.assertEqual(streaks, {"Lesen": long_streak, "Sport": 70})

    def test_statistics_count_the_last_year_best_streak_and_recent_rate(self):
        daily = Habit(cadence=Habit.CADENCE_DAILY)
        weekly = Habit(cadence=Habit.CADENCE_WEEKLY)
        today = date(2099, 3, 4)  # a Wednesday
        checks = {today - timedelta(days=offset) for offset in (0, 1, 2, 10, 11, 12, 13, 400)}

        self.assertEqual(longest_streak(daily, checks), 4)
        self.assertEqual(longest_streak(weekly, {date(2099, 3, 2), date(2099, 3, 10), date(2099, 3, 25)}), 2)

        stats = habit_stats(daily, checks, today, 3)
        self.assertEqual((stats["total"], stats["best_streak_label"], stats["rate"]), (7, "4 Tage", 23))
        self.assertEqual(len(stats["heatmap"]), 53 * 7)
        self.assertEqual(stats["heatmap"][0]["date"].weekday(), 0)
        self.assertEqual(
            [cell["date"] for cell in stats["heatmap"] if cell["is_future"]],
            [date(2099, 3, 5), date(2099, 3, 6), date(2099, 3, 7), date(2099, 3, 8)],
        )

        weekly_stats = habit_stats(weekly, {date(2099, 3, 2), date(2099, 2, 20)}, today, 2)
        self.assertEqual((weekly_stats["rate"], weekly_stats["rate_label"]), (17, "der letzten 12 Wochen"))

    def test_daily_reminder_fires_once_after_its_time_while_the_habit_is_open(self):
        berlin = ZoneInfo("Europe/Berlin")
        habit = Habit.objects.create(owner=self.user, title="Lesen", reminder_time=time(19))
        Habit.objects.create(owner=self.user, title="Ohne Erinnerung")

        before = materialize_due_habit_reminders(now=datetime(2099, 3, 4, 18, 59, tzinfo=berlin))
        after = materialize_due_habit_reminders(now=datetime(2099, 3, 4, 19, 30, tzinfo=berlin))
        again = materialize_due_habit_reminders(now=datetime(2099, 3, 4, 21, 0, tzinfo=berlin))

        self.assertEqual((before, after, again), (0, 1, 0))
        reminder = UserNotification.objects.get(recipient=self.user)
        self.assertEqual(reminder.kind, UserNotification.KIND_HABIT_REMINDER)
        self.assertEqual(reminder.title, "Gewohnheit offen: Lesen")

        HabitCheck.objects.create(habit=habit, date=date(2099, 3, 5))
        self.assertEqual(materialize_due_habit_reminders(now=datetime(2099, 3, 5, 20, tzinfo=berlin)), 0)

    def test_checking_a_habit_removes_its_unread_reminder(self):
        habit = Habit.objects.create(owner=self.user, title="Lesen", reminder_time=time(0))
        materialize_due_habit_reminders()
        self.assertTrue(UserNotification.objects.filter(recipient=self.user).exists())

        self.client.post(
            "/tools/habits/",
            {"form_name": "habit_toggle", "habit_id": habit.id, "date": self.today.isoformat()},
        )

        self.assertFalse(UserNotification.objects.filter(recipient=self.user).exists())

    def test_weekly_reminder_only_on_sunday_of_an_open_week(self):
        berlin = ZoneInfo("Europe/Berlin")
        habit = Habit.objects.create(
            owner=self.user, title="Sport", cadence=Habit.CADENCE_WEEKLY, reminder_time=time(10)
        )
        other = Habit.objects.create(
            owner=self.user, title="Putzen", cadence=Habit.CADENCE_WEEKLY, reminder_time=time(10)
        )
        HabitCheck.objects.create(habit=other, date=date(2099, 3, 3))

        saturday = materialize_due_habit_reminders(now=datetime(2099, 3, 7, 12, tzinfo=berlin))
        sunday = materialize_due_habit_reminders(now=datetime(2099, 3, 8, 12, tzinfo=berlin))

        self.assertEqual((saturday, sunday), (0, 1))
        self.assertEqual(UserNotification.objects.get().source_key, f"habit:{habit.pk}:2099-03-08")

    def test_reminder_time_is_set_and_cleared_only_on_own_habits(self):
        habit = Habit.objects.create(owner=self.user, title="Lesen")
        foreign = Habit.objects.create(owner=self.other, title="Fremd")

        self.client.post(
            "/tools/habits/", {"form_name": "habit_reminder", "habit_id": habit.id, "reminder_time": "07:30"}
        )
        self.client.post(
            "/tools/habits/",
            {"form_name": "habit_reminder", "habit_id": foreign.id, "reminder_time": "07:30"},
        )
        habit.refresh_from_db()
        foreign.refresh_from_db()
        self.assertEqual((habit.reminder_time, foreign.reminder_time), (time(7, 30), None))
        self.assertContains(self.client.get("/tools/habits/"), 'value="07:30"')

        self.client.post(
            "/tools/habits/", {"form_name": "habit_reminder", "habit_id": habit.id, "reminder_time": ""}
        )
        habit.refresh_from_db()
        self.assertIsNone(habit.reminder_time)

    def test_habit_reminder_is_mailed_once(self):
        Profile.objects.filter(user=self.user).update(notify_email=True)
        Habit.objects.create(owner=self.user, title="Lesen", reminder_time=time(0))
        materialize_due_habit_reminders()

        send_pending_user_notification_emails()
        send_pending_user_notification_emails()

        self.assertEqual([message.subject for message in mail.outbox], ["Lunora: Gewohnheit offen: Lesen"])

    def test_page_creates_toggles_and_deletes_only_own_habits(self):
        response = self.client.post(
            "/tools/habits/",
            {"form_name": "habit_add", "title": "Lesen", "cadence": "daily", "color": "blue"},
        )
        self.assertRedirects(response, "/tools/habits/")
        habit = Habit.objects.get(owner=self.user)
        foreign = Habit.objects.create(owner=self.other, title="Fremd")

        for day in (self.today, self.today - timedelta(days=6), self.today - timedelta(days=7)):
            self.client.post(
                "/tools/habits/", {"form_name": "habit_toggle", "habit_id": habit.id, "date": day.isoformat()}
            )
        self.client.post(
            "/tools/habits/",
            {"form_name": "habit_toggle", "habit_id": foreign.id, "date": self.today.isoformat()},
        )

        self.assertEqual(
            set(habit.checks.values_list("date", flat=True)), {self.today, self.today - timedelta(days=6)}
        )
        self.assertFalse(foreign.checks.exists())
        response = self.client.get("/tools/habits/")
        self.assertContains(response, "1 Tag in Folge")
        self.assertNotContains(response, "Fremd")

        self.client.post(
            "/tools/habits/", {"form_name": "habit_toggle", "habit_id": habit.id, "date": "kaputt"}
        )
        self.client.post("/tools/habits/", {"form_name": "habit_delete", "habit_id": foreign.id})
        self.client.post("/tools/habits/", {"form_name": "habit_delete", "habit_id": habit.id})
        self.assertFalse(Habit.objects.filter(owner=self.user).exists())
        self.assertTrue(Habit.objects.filter(pk=foreign.pk).exists())

    def test_dashboard_widget_ticks_today_off_and_returns_home(self):
        habit = Habit.objects.create(owner=self.user, title="Wasser trinken")

        response = self.client.get("/home/")
        self.assertContains(response, "Wasser trinken")
        self.assertContains(response, 'aria-label="„Wasser trinken“ heute erledigt"')

        response = self.client.post(
            "/tools/habits/",
            {
                "form_name": "habit_toggle",
                "habit_id": habit.id,
                "date": self.today.isoformat(),
                "return_to": "/home/",
            },
        )

        self.assertRedirects(response, "/home/", fetch_redirect_response=False)
        self.assertTrue(habit.checks.filter(date=self.today).exists())

    def test_tools_hub_lists_habits_and_focus_timer_and_flag_blocks_both(self):
        response = self.client.get("/tools/")
        self.assertContains(response, "Gewohnheiten")
        self.assertContains(response, "Fokus-Timer")
        self.assertNotContains(response, "Schule")
        response = self.client.get("/tools/focus/")
        self.assertContains(response, "data-focus-timer")

        SystemSettings.objects.update_or_create(pk=1, defaults={"tools_enabled": False})

        self.assertEqual(self.client.get("/tools/habits/").status_code, 503)
        self.assertEqual(self.client.get("/tools/focus/").status_code, 503)
        self.assertNotContains(self.client.get("/home/"), "Gewohnheiten")


class FocusTimerTrackingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="lea", email="lea@example.com", password="pw-12345!")
        Profile.objects.create(user=self.user, display_name="Lea", timezone_name="Europe/Berlin")
        self.task = Task.objects.create(user=self.user, title="Bericht schreiben")
        self.client.force_login(self.user)

    def _track(self, action, task):
        return self.client.post("/tools/focus/", {"action": action, "task_id": task.pk})

    def test_page_offers_open_tasks_to_book_focus_time_on(self):
        Task.objects.create(user=self.user, title="Schon erledigt", is_done=True)

        response = self.client.get("/tools/focus/")

        self.assertContains(response, f'<option value="{self.task.pk}">Bericht schreiben</option>', html=True)
        self.assertNotContains(response, "Schon erledigt")

    def test_a_focus_block_starts_and_stops_the_task_timer(self):
        self.assertEqual(self._track("start", self.task).json(), {"ok": True})
        entry = TaskTimeEntry.objects.get(task=self.task)
        self.assertIsNone(entry.ended_at)

        TaskTimeEntry.objects.filter(pk=entry.pk).update(started_at=timezone.now() - timedelta(minutes=25))
        self._track("stop", self.task)

        entry.refresh_from_db()
        self.assertIsNotNone(entry.ended_at)

    def test_stop_leaves_a_timer_on_another_task_running(self):
        other_task = Task.objects.create(user=self.user, title="Anderes")
        self._track("start", other_task)

        self._track("stop", self.task)

        self.assertIsNone(TaskTimeEntry.objects.get(task=other_task).ended_at)

    def test_foreign_tasks_and_disabled_tasks_are_refused(self):
        stranger = User.objects.create_user(username="tom", email="tom@example.com", password="pw-12345!")
        foreign = Task.objects.create(user=stranger, title="Fremd")
        self.assertEqual(self._track("start", foreign).status_code, 404)

        SystemSettings.objects.update_or_create(pk=1, defaults={"tasks_enabled": False})

        self.assertEqual(self._track("start", self.task).status_code, 503)
        self.assertNotContains(self.client.get("/tools/focus/"), "data-focus-task")
        self.assertFalse(TaskTimeEntry.objects.exists())


class RecipeIngredientParsingTests(SimpleTestCase):
    def test_quantity_and_unit_are_split_from_the_name(self):
        cases = {
            "200 g Mehl": ("200 g", "Mehl"),
            "500ml Milch": ("500ml", "Milch"),
            "1 1/2 TL Zimt": ("1 1/2 TL", "Zimt"),
            "2-3 Zehen Knoblauch": ("2-3 Zehen", "Knoblauch"),
            "½ Bund Petersilie": ("½ Bund", "Petersilie"),
            "1 Pck. Vanillezucker": ("1 Pck.", "Vanillezucker"),
            "2 Liter Wasser": ("2 Liter", "Wasser"),
            "2 Eier": ("2", "Eier"),
            "- 3 Glasnudeln": ("3", "Glasnudeln"),
            "Salz und Pfeffer": ("", "Salz und Pfeffer"),
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.assertEqual(parse_ingredient(line), expected)

    def test_rows_keep_headings_and_drop_preparation_notes_from_the_list_name(self):
        rows = ingredient_rows("Für den Teig:\n\n2 Zwiebeln, gewürfelt\nSahne (30 %)")

        self.assertEqual(rows[0], {"index": 0, "heading": "Für den Teig"})
        self.assertEqual((rows[1]["index"], rows[1]["name"], rows[1]["quantity"]), (2, "Zwiebeln", "2"))
        self.assertEqual(rows[1]["text"], "2 Zwiebeln, gewürfelt")
        self.assertEqual(rows[2]["name"], "Sahne")


class RecipeTests(TestCase):
    def setUp(self):
        self.lea = User.objects.create_user(username="lea", email="lea@example.com", password="pw-12345!")
        self.tom = User.objects.create_user(username="tom", email="tom@example.com", password="pw-12345!")
        self.recipe = Recipe.objects.create(
            owner=self.lea, title="Pfannkuchen", ingredients="Teig:\n200 g Mehl\n2 Eier\n1 Prise Salz"
        )
        self.client.force_login(self.lea)
        self.url = f"/tools/recipes/{self.recipe.pk}/"

    def test_create_opens_the_new_recipe_with_the_editor_open(self):
        response = self.client.post("/tools/recipes/", {"form_name": "recipe_create", "title": " Lasagne "})

        recipe = Recipe.objects.get(title="Lasagne")
        self.assertRedirects(response, f"/tools/recipes/{recipe.pk}/")
        self.assertEqual(recipe.owner, self.lea)
        self.assertContains(
            self.client.get(f"/tools/recipes/{recipe.pk}/"), '<details class="vocab-details" open>'
        )

    def test_list_shows_only_own_recipes_with_ingredient_count(self):
        Recipe.objects.create(owner=self.tom, title="Toms Chili")

        response = self.client.get("/tools/recipes/")

        self.assertContains(response, "Pfannkuchen")
        self.assertContains(response, "3 Zutat(en)")
        self.assertNotContains(response, "Toms Chili")

    def test_other_users_recipes_are_not_found(self):
        foreign = Recipe.objects.create(owner=self.tom, title="Toms Chili")

        self.assertEqual(self.client.get(f"/tools/recipes/{foreign.pk}/").status_code, 404)
        response = self.client.post(f"/tools/recipes/{foreign.pk}/", {"form_name": "recipe_delete"})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Recipe.objects.filter(pk=foreign.pk).exists())

    def test_update_and_delete(self):
        response = self.client.post(
            self.url,
            {
                "form_name": "recipe_update",
                "title": "Crêpes",
                "servings": "4",
                "ingredients": "Mehl",
                "instructions": "",
            },
        )
        self.assertRedirects(response, self.url)
        self.recipe.refresh_from_db()
        self.assertEqual((self.recipe.title, self.recipe.servings), ("Crêpes", 4))

        response = self.client.post(self.url, {"form_name": "recipe_delete"})
        self.assertRedirects(response, "/tools/recipes/")
        self.assertFalse(Recipe.objects.exists())

    def test_chosen_ingredients_go_on_the_own_list_and_reopen_existing_rows(self):
        ShoppingItem.objects.create(owner=self.lea, name="mehl", is_done=True)

        # Line 0 is the heading, 3 (the salt) was unticked; a tampered value is ignored.
        response = self.client.post(
            self.url, {"form_name": "recipe_to_shopping", "ingredient": ["0", "1", "2", "x"]}
        )

        self.assertRedirects(response, self.url)
        items = {
            item.name: (item.quantity, item.is_done) for item in ShoppingItem.objects.filter(owner=self.lea)
        }
        self.assertEqual(items, {"mehl": ("200 g", False), "Eier": ("2", False)})

    def test_open_items_add_quantities_up_but_ticked_ones_start_over(self):
        ShoppingItem.objects.create(owner=self.lea, name="Mehl", quantity="300 g")
        ShoppingItem.objects.create(owner=self.lea, name="Eier", quantity="6", is_done=True)

        self.client.post(self.url, {"form_name": "recipe_to_shopping", "ingredient": ["1", "2"]})

        self.assertEqual(
            sorted(ShoppingItem.objects.values_list("name", "quantity", "is_done")),
            [("Eier", "2", False), ("Mehl", "500 g", False)],
        )

    def test_recipe_is_shown_and_shopped_for_other_servings(self):
        self.recipe.servings = 2
        self.recipe.save(update_fields=["servings"])

        response = self.client.get(self.url, {"servings": "3"})
        self.assertContains(response, "<span>300 g Mehl</span>", html=True)
        self.assertContains(response, "<span>3 Eier</span>", html=True)
        self.assertContains(response, 'name="servings" value="3"')
        # Out of range or the recipe's own number: the recipe as written.
        self.assertContains(
            self.client.get(self.url, {"servings": "500"}), "<span>200 g Mehl</span>", html=True
        )

        response = self.client.post(
            self.url, {"form_name": "recipe_to_shopping", "ingredient": ["1", "2"], "servings": "3"}
        )

        self.assertRedirects(response, f"{self.url}?servings=3")
        self.assertEqual(
            sorted(ShoppingItem.objects.values_list("name", "quantity")), [("Eier", "3"), ("Mehl", "300 g")]
        )

    def test_ingredients_go_on_a_shared_list_only_when_it_is_shared(self):
        data = {"form_name": "recipe_to_shopping", "ingredient": ["2"], "list": str(self.tom.pk)}

        self.client.post(self.url, data)
        self.assertFalse(ShoppingItem.objects.exists())

        ShoppingListShare.objects.create(owner=self.tom, user=self.lea)
        self.assertContains(self.client.get(self.url), "Zettel von")
        self.client.post(self.url, data)
        self.assertEqual(list(ShoppingItem.objects.values_list("owner", "name")), [(self.tom.pk, "Eier")])

    def test_a_full_list_stops_with_a_message(self):
        ShoppingItem.objects.bulk_create(
            ShoppingItem(owner=self.lea, name=f"Artikel {n}") for n in range(199)
        )

        response = self.client.post(
            self.url, {"form_name": "recipe_to_shopping", "ingredient": ["1", "2", "3"]}, follow=True
        )

        self.assertContains(response, "1 Zutat(en) auf den Einkaufszettel gesetzt.")
        self.assertContains(response, "höchstens 200 Artikel")
        self.assertEqual(ShoppingItem.objects.filter(owner=self.lea).count(), 200)

    def test_disabled_tools_flag_returns_503(self):
        SystemSettings.objects.update_or_create(pk=1, defaults={"tools_enabled": False})

        self.assertEqual(self.client.get("/tools/recipes/").status_code, 503)
        self.assertEqual(self.client.get(self.url).status_code, 503)


class ScaleQuantityTests(SimpleTestCase):
    def test_every_number_is_scaled_and_written_the_german_way(self):
        self.assertEqual(scale_quantity("1 1/2 TL", 2), "3 TL")
        self.assertEqual(scale_quantity("2-3 Zehen", 2), "4-6 Zehen")
        self.assertEqual(scale_quantity("ca. 500ml", 0.25), "ca. 125ml")
        self.assertEqual(scale_quantity("3", 0.5), "1,5")
        self.assertEqual(scale_quantity("25 g", 0.5), "13 g")
        self.assertEqual(scale_quantity("", 2), "")


class AddQuantitiesTests(SimpleTestCase):
    def test_same_units_add_up_and_others_stay_side_by_side(self):
        self.assertEqual(add_quantities("200 g", "300 g"), "500 g")
        self.assertEqual(add_quantities("800 ml", "0,5 l"), "1,3 l")
        self.assertEqual(add_quantities("600 g", "0,5 kg"), "1,1 kg")
        self.assertEqual(add_quantities("2 EL", "1 EL"), "3 EL")
        self.assertEqual(add_quantities("1 1/2 TL", "1/2 TL"), "2 TL")
        self.assertEqual(add_quantities("1 Zehe", "2 Zehen"), "1 Zehe + 2 Zehen")
        self.assertEqual(add_quantities("2-3", "1"), "2-3 + 1")
        self.assertEqual(add_quantities("1 Packung Bio-Hafermilch, ungesüßt", "2 Liter"), "2 Liter")


class MealPlanTests(TestCase):
    def setUp(self):
        self.lea = User.objects.create_user(username="lea", email="lea@example.com", password="pw-12345!")
        self.tom = User.objects.create_user(username="tom", email="tom@example.com", password="pw-12345!")
        self.pancakes = Recipe.objects.create(
            owner=self.lea, title="Pfannkuchen", ingredients="200 g Mehl\n2 Eier\n500 ml Milch"
        )
        self.soup = Recipe.objects.create(owner=self.lea, title="Suppe", ingredients="Teig:\n2 Eier\n1 Lauch")
        self.client.force_login(self.lea)
        self.monday = date(2026, 11, 9)

    def _post(self, data):
        return self.client.post("/tools/recipes/", {"week": "2026-W46", **data})

    def test_planning_shows_the_recipe_on_its_day_and_redirects_to_the_week(self):
        response = self._post(
            {"form_name": "meal_plan_add", "recipe_id": self.pancakes.pk, "date": "2026-11-11"}
        )

        self.assertRedirects(response, "/tools/recipes/?week=2026-W46")
        page = self.client.get("/tools/recipes/?week=2026-W46")
        wednesday = page.context["plan_days"][2]
        self.assertEqual(wednesday["name"], "Mittwoch")
        self.assertEqual([entry.recipe for entry in wednesday["entries"]], [self.pancakes])
        self.assertEqual(page.context["week_label"], "KW 46")

    def test_foreign_recipes_cannot_be_planned_and_foreign_entries_not_removed(self):
        foreign = Recipe.objects.create(owner=self.tom, title="Toms Chili")
        toms_entry = MealPlanEntry.objects.create(owner=self.tom, recipe=foreign, date=self.monday)

        self._post({"form_name": "meal_plan_add", "recipe_id": foreign.pk, "date": "2026-11-09"})
        self._post({"form_name": "meal_plan_remove", "entry_id": toms_entry.pk})

        self.assertFalse(MealPlanEntry.objects.filter(owner=self.lea).exists())
        self.assertTrue(MealPlanEntry.objects.filter(pk=toms_entry.pk).exists())
        self.assertNotContains(self.client.get("/tools/recipes/?week=2026-W46"), "Toms Chili")

    def test_a_day_holds_at_most_six_recipes(self):
        for _ in range(6):
            MealPlanEntry.objects.create(owner=self.lea, recipe=self.soup, date=self.monday)

        self._post({"form_name": "meal_plan_add", "recipe_id": self.pancakes.pk, "date": "2026-11-09"})

        self.assertEqual(MealPlanEntry.objects.filter(owner=self.lea).count(), 6)

    def test_week_goes_on_the_shopping_list_once_per_ingredient_with_quantities_added_up(self):
        MealPlanEntry.objects.create(owner=self.lea, recipe=self.pancakes, date=self.monday)
        MealPlanEntry.objects.create(
            owner=self.lea, recipe=self.pancakes, date=self.monday + timedelta(days=3)
        )
        MealPlanEntry.objects.create(owner=self.lea, recipe=self.soup, date=self.monday + timedelta(days=6))
        # Next week's plan stays out.
        MealPlanEntry.objects.create(
            owner=self.lea,
            recipe=Recipe.objects.create(owner=self.lea, title="Reis", ingredients="Reis"),
            date=self.monday + timedelta(days=7),
        )

        response = self._post({"form_name": "meal_plan_to_shopping"})

        self.assertRedirects(response, "/tools/recipes/?week=2026-W46")
        self.assertEqual(
            sorted(ShoppingItem.objects.filter(owner=self.lea).values_list("name", "quantity")),
            [("Eier", "4"), ("Lauch", "1"), ("Mehl", "200 g"), ("Milch", "500 ml")],
        )

    def test_planned_servings_scale_the_quantities_over_the_week(self):
        stew = Recipe.objects.create(
            owner=self.lea, title="Eintopf", servings=2, ingredients="200 g Linsen\n1 1/2 Zwiebeln\nSalz"
        )
        monday = MealPlanEntry.objects.create(owner=self.lea, recipe=stew, date=self.monday)
        MealPlanEntry.objects.create(owner=self.lea, recipe=stew, date=self.monday + timedelta(days=3))

        self._post({"form_name": "meal_plan_servings", "entry_id": monday.pk, "servings": "3"})
        self._post({"form_name": "meal_plan_servings", "entry_id": monday.pk, "servings": "0"})
        self._post({"form_name": "meal_plan_to_shopping"})

        monday.refresh_from_db()
        self.assertEqual(monday.servings, 3)
        self.assertEqual(
            sorted(ShoppingItem.objects.filter(owner=self.lea).values_list("name", "quantity")),
            [("Linsen", "500 g"), ("Salz", ""), ("Zwiebeln", "3,75")],
        )

    def test_week_goes_on_a_shared_list_but_not_a_foreign_one(self):
        MealPlanEntry.objects.create(owner=self.lea, recipe=self.soup, date=self.monday)
        ShoppingListShare.objects.create(owner=self.tom, user=self.lea)

        self._post({"form_name": "meal_plan_to_shopping", "list": self.tom.pk})
        self.assertEqual(ShoppingItem.objects.filter(owner=self.tom).count(), 2)

        stranger = User.objects.create_user(username="sam", email="sam@example.com", password="pw-12345!")
        self._post({"form_name": "meal_plan_to_shopping", "list": stranger.pk})
        self.assertFalse(ShoppingItem.objects.filter(owner=stranger).exists())

    def test_invalid_week_falls_back_to_the_current_week(self):
        response = self.client.get("/tools/recipes/?week=2026-W99")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["plan_days"]), 7)


RECIPE_PAGE = """<html><head><title>Lasagne</title>
<script type="application/ld+json">{"@context": "https://schema.org", "@type": "WebSite", "name": "Kochseite"}</script>
<script type="application/ld+json">
{"@context": "https://schema.org", "@graph": [
  {"@type": "BreadcrumbList"},
  {"@type": ["Recipe", "NewsArticle"], "name": "Gem&uuml;se&shy;lasagne",
   "recipeYield": ["4 Portionen", "4"],
   "recipeIngredient": ["500 g Zucchini", "1 <b>Dose</b> Tomaten", ""],
   "recipeInstructions": [
     {"@type": "HowToSection", "name": "Soße", "itemListElement": [
       {"@type": "HowToStep", "text": "Tomaten <i>einkochen</i>."}]},
     {"@type": "HowToStep", "text": "Schichten &amp; backen."}]}
]}
</script></head><body>…</body></html>"""


class RecipeImportParsingTests(SimpleTestCase):
    def test_reads_the_recipe_out_of_a_json_ld_graph(self):
        self.assertEqual(
            parse_recipe_page(RECIPE_PAGE),
            {
                "title": "Gemüse\xadlasagne",
                "servings": 4,
                "ingredients": "500 g Zucchini\n1 Dose Tomaten",
                "instructions": "Soße:\n\nTomaten einkochen.\n\nSchichten & backen.",
            },
        )

    def test_plain_text_instructions_and_single_yield(self):
        page = (
            '<script type="application/ld+json">{"@type": "Recipe", "name": "Brot", "recipeYield": 1,'
            ' "recipeIngredient": "500 g Mehl", "recipeInstructions": "Kneten.<br>Backen."}</script>'
        )

        self.assertEqual(
            parse_recipe_page(page),
            {
                "title": "Brot",
                "servings": 1,
                "ingredients": "500 g Mehl",
                "instructions": "Kneten.\n\nBacken.",
            },
        )

    def test_pages_without_recipe_data_or_with_broken_json_give_none(self):
        self.assertIsNone(parse_recipe_page("<html><h1>Kuchen</h1></html>"))
        self.assertIsNone(
            parse_recipe_page('<script type="application/ld+json">{"@type": "Recipe",</script>')
        )
        self.assertIsNone(
            parse_recipe_page('<script type="application/ld+json">{"@type": "Recipe"}</script>')
        )


class RecipeImportViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.lea = User.objects.create_user(username="lea", email="lea@example.com", password="pw-12345!")
        self.client.force_login(self.lea)
        FakeHttpConnection.responses = []
        FakeHttpConnection.requests = []
        patcher = patch.multiple(
            "app.services.safe_fetch.http.client",
            HTTPSConnection=FakeHttpConnection,
            HTTPConnection=FakeHttpConnection,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _import(self, url="https://rezepte.example.com/lasagne"):
        return self.client.post("/tools/recipes/", {"form_name": "recipe_import", "url": url})

    def test_import_creates_the_recipe_with_its_source_and_opens_it(self):
        FakeHttpConnection.responses = [(200, {}, RECIPE_PAGE.encode())]

        response = self._import()

        recipe = Recipe.objects.get(owner=self.lea)
        self.assertRedirects(response, f"/tools/recipes/{recipe.pk}/")
        self.assertEqual(recipe.title, "Gemüse\xadlasagne")
        self.assertEqual(recipe.servings, 4)
        self.assertEqual(recipe.source_url, "https://rezepte.example.com/lasagne")
        self.assertContains(self.client.get(f"/tools/recipes/{recipe.pk}/"), "Quelle: rezepte.example.com")

    def test_internal_addresses_are_refused_without_a_request(self):
        for url in ["http://127.0.0.1/rezept", "http://intranet/rezept", "file:///etc/passwd"]:
            with self.subTest(url=url):
                response = self._import(url)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["import_error"])
        self.assertEqual(FakeHttpConnection.requests, [])
        self.assertFalse(Recipe.objects.exists())

    def test_page_without_recipe_shows_an_error_and_keeps_the_address(self):
        FakeHttpConnection.responses = [(200, {}, b"<html><h1>Nur Text</h1></html>")]

        response = self._import()

        self.assertContains(response, "kein Rezept gefunden")
        self.assertContains(response, 'value="https://rezepte.example.com/lasagne"')
        self.assertFalse(Recipe.objects.exists())

    def test_imports_are_throttled_per_user(self):
        FakeHttpConnection.responses = [(404, {}, b"")] * 11

        for _ in range(10):
            self._import()
        response = self._import()

        self.assertContains(response, "Zu viele Importe")
        self.assertEqual(len(FakeHttpConnection.requests), 10)
