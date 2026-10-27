import json
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.utils import timezone

from app.models import (
    Habit,
    NotificationPreference,
    Profile,
    SystemSettings,
    UmlDiagram,
    UserNotification,
    VocabularyCard,
    VocabularyList,
    VocabularyListShare,
)
from app.services.habits import current_streak, is_done_now
from app.services.notifications import send_pending_user_notification_emails
from app.services.uml_content import validate_uml_document
from app.services.user_preferences import localtime_for_user
from app.services.vocabulary import parse_bulk_cards, review_card


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
