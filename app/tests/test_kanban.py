from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase

from app.models import KanbanBoard, KanbanCard, SystemSettings, UserNotification
from app.services.kanban import MAX_COLUMNS, add_card, add_lane, create_board


class KanbanBoardTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="lea@example.com", email="lea@example.com", password="pw"
        )
        self.member = User.objects.create_user(
            username="tom@example.com", email="tom@example.com", password="pw"
        )
        self.stranger = User.objects.create_user(
            username="max@example.com", email="max@example.com", password="pw"
        )
        self.board = create_board(self.owner, "Umzug")
        self.todo, self.doing, self.done = self.board.columns.all()
        self.lane = self.board.lanes.get()
        self.url = f"/tools/kanban/{self.board.pk}/"
        self.client.force_login(self.owner)

    def post(self, form_name, **data):
        return self.client.post(self.url, {"form_name": form_name, **data})

    def cell(self, column, lane=None):
        cards = KanbanCard.objects.filter(column=column, lane=lane or self.lane)
        return list(cards.values_list("title", flat=True))

    def card(self, title, column=None, lane=None):
        add_card(self.board, self.owner, column or self.todo, lane or self.lane, title)
        return KanbanCard.objects.get(title=title)

    def test_new_board_starts_with_three_columns_and_one_lane(self):
        response = self.client.post(
            "/tools/kanban/", {"form_name": "kanban_board_create", "title": "  Sprint  "}
        )
        board = KanbanBoard.objects.get(title="Sprint")
        self.assertRedirects(response, f"/tools/kanban/{board.pk}/")
        self.assertEqual(
            list(board.columns.values_list("title", flat=True)), ["Offen", "In Arbeit", "Erledigt"]
        )
        self.assertEqual(list(board.lanes.values_list("title", flat=True)), ["Allgemein"])
        self.assertContains(self.client.get("/tools/kanban/"), "Sprint")

    def test_cards_are_added_moved_before_others_and_reordered(self):
        self.post("kanban_card_add", column_id=self.todo.pk, lane_id=self.lane.pk, title="Kartons")
        self.post("kanban_card_add", column_id=self.todo.pk, lane_id=self.lane.pk, title="Wagen")
        self.assertEqual(self.cell(self.todo), ["Kartons", "Wagen"])
        wagen = KanbanCard.objects.get(title="Wagen")
        kartons = KanbanCard.objects.get(title="Kartons")

        self.post(
            "kanban_card_move",
            card_id=wagen.pk,
            column_id=self.todo.pk,
            lane_id=self.lane.pk,
            before_id=kartons.pk,
        )
        self.assertEqual(self.cell(self.todo), ["Wagen", "Kartons"])

        self.post("kanban_card_move", card_id=wagen.pk, column_id=self.doing.pk, lane_id=self.lane.pk)
        self.assertEqual(self.cell(self.todo), ["Kartons"])
        self.assertEqual(self.cell(self.doing), ["Wagen"])
        self.assertContains(self.client.get(self.url), f'id="card-{wagen.pk}"')

    def test_moves_stay_inside_the_board(self):
        other = create_board(self.stranger, "Fremd")
        card = self.card("Kartons")
        response = self.post(
            "kanban_card_move", card_id=card.pk, column_id=other.columns.first().pk, lane_id=self.lane.pk
        )
        self.assertEqual(response.status_code, 404)
        card.refresh_from_db()
        self.assertEqual(card.column, self.todo)

    def test_editing_a_card_moves_it_and_validates_column_and_assignee(self):
        card = self.card("Kartons")
        add_lane(self.board, "Küche")
        kitchen = self.board.lanes.get(title="Küche")
        response = self.post(
            "kanban_card_update",
            card_id=card.pk,
            title="Kartons kaufen",
            description="30 Stück",
            color="green",
            due_date="2026-11-01",
            column=self.doing.pk,
            lane=kitchen.pk,
            assignee="",
        )
        self.assertRedirects(response, self.url)
        card.refresh_from_db()
        self.assertEqual(
            (card.title, card.description, card.color, card.due_date, card.column, card.lane),
            ("Kartons kaufen", "30 Stück", "green", date(2026, 11, 1), self.doing, kitchen),
        )

        # Someone who isn't on the board can't be made responsible.
        self.post(
            "kanban_card_update",
            card_id=card.pk,
            title="Kartons",
            column=self.doing.pk,
            lane=kitchen.pk,
            assignee=self.stranger.pk,
        )
        card.refresh_from_db()
        self.assertEqual((card.title, card.assignee), ("Kartons kaufen", None))

    def test_columns_and_lanes_are_renamed_moved_limited_and_deleted(self):
        self.post("kanban_column_add", title="Review")
        review = self.board.columns.get(title="Review")
        self.post("kanban_column_rename", item_id=review.pk, title="Prüfen", wip_limit="1")
        self.post("kanban_column_move", item_id=review.pk, direction="back")
        self.assertEqual(
            list(self.board.columns.values_list("title", flat=True)),
            ["Offen", "In Arbeit", "Prüfen", "Erledigt"],
        )
        review.refresh_from_db()
        self.assertEqual(review.wip_limit, 1)
        self.card("A", column=review)
        self.card("B", column=review)
        self.assertContains(self.client.get(self.url), "is-over-limit")

        self.post("kanban_column_delete", item_id=review.pk)
        self.assertFalse(KanbanCard.objects.filter(title__in=["A", "B"]).exists())

        self.post("kanban_lane_delete", item_id=self.lane.pk)
        self.assertEqual(self.board.lanes.count(), 1)
        self.post("kanban_lane_add", title="Küche")
        self.post("kanban_lane_move", item_id=self.lane.pk, direction="forward")
        self.assertEqual(list(self.board.lanes.values_list("title", flat=True)), ["Küche", "Allgemein"])

    def test_column_limit(self):
        for index in range(MAX_COLUMNS):
            self.post("kanban_column_add", title=f"S{index}")
        self.assertEqual(self.board.columns.count(), MAX_COLUMNS)

    def test_overdue_cards_are_marked_except_in_the_last_column(self):
        late = self.card("Spät")
        finished = self.card("Fertig", column=self.done)
        KanbanCard.objects.filter(pk__in=[late.pk, finished.pk]).update(due_date=date(2020, 1, 1))
        page = self.client.get(self.url).content.decode()
        self.assertEqual(page.count("is-overdue"), 1)

    def test_owner_shares_member_edits_assignments_notify_and_leaving_clears_them(self):
        self.post("kanban_share", identifier="tom@example.com")
        notification = UserNotification.objects.get(recipient=self.member)
        self.assertEqual(
            (notification.kind, notification.url), (UserNotification.KIND_KANBAN_SHARE, self.url)
        )

        card = self.card("Kartons")
        self.client.force_login(self.member)
        self.assertContains(self.client.get("/tools/kanban/"), "geteilt von")
        self.post(
            "kanban_card_update",
            card_id=card.pk,
            title="Kartons",
            column=self.todo.pk,
            lane=self.lane.pk,
            assignee=self.owner.pk,
        )
        card.refresh_from_db()
        self.assertEqual(card.assignee, self.owner)
        assignment = UserNotification.objects.get(recipient=self.owner)
        self.assertEqual(assignment.kind, UserNotification.KIND_KANBAN_ASSIGNMENT)

        # Members can't share, delete or remove others.
        self.assertEqual(self.post("kanban_share", identifier="max@example.com").status_code, 404)
        self.assertEqual(self.post("kanban_delete").status_code, 404)
        self.assertTrue(KanbanBoard.objects.filter(pk=self.board.pk).exists())

        self.client.force_login(self.owner)
        self.post(
            "kanban_card_update",
            card_id=card.pk,
            title="Kartons",
            column=self.todo.pk,
            lane=self.lane.pk,
            assignee=self.member.pk,
        )
        self.assertFalse(UserNotification.objects.filter(pk=assignment.pk).exists())
        self.assertTrue(
            UserNotification.objects.filter(
                recipient=self.member, kind=UserNotification.KIND_KANBAN_ASSIGNMENT
            )
        )

        self.client.force_login(self.member)
        self.assertRedirects(self.post("kanban_leave"), "/tools/kanban/")
        card.refresh_from_db()
        self.assertIsNone(card.assignee)
        self.assertFalse(UserNotification.objects.filter(recipient=self.member).exists())
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_strangers_get_nothing(self):
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(
            self.post("kanban_card_add", column_id=self.todo.pk, lane_id=self.lane.pk, title="x").status_code,
            404,
        )
        self.assertNotContains(self.client.get("/tools/kanban/"), f'href="{self.url}"')

    def test_version_changes_with_every_change(self):
        version = self.client.get(f"{self.url}?version=1").json()["version"]
        self.post("kanban_card_add", column_id=self.todo.pk, lane_id=self.lane.pk, title="Kartons")
        self.assertNotEqual(self.client.get(f"{self.url}?version=1").json()["version"], version)

    def test_disabled_tools_flag_returns_503(self):
        settings = SystemSettings.objects.get_or_create(pk=1)[0]
        settings.tools_enabled = False
        settings.save()
        self.assertEqual(self.client.get(self.url).status_code, 503)
        self.assertEqual(self.client.get(f"{self.url}?version=1").status_code, 503)
