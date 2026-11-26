import json
import zipfile
from io import BytesIO

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase

from app.models import Note, RetroBoard, RetroCard, RetroVote, SystemSettings, Task
from app.services.data_export import write_user_export
from app.services.retro import create_retro


class RetroTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(
            username="lea@example.com", email="lea@example.com", password="pw"
        )
        self.other = User.objects.create_user(
            username="tom@example.com", email="tom@example.com", password="pw"
        )
        self.board = create_retro(self.owner, "Sprint 47", "start_stop")
        self.start, self.stop, self.keep = self.board.columns.all()
        self.url = f"/tools/retro/{self.board.pk}/"
        self.join_url = f"/retro/join/{self.board.join_token}/"
        self.client.force_login(self.owner)
        self.guest = Client()

    def post(self, form_name, client=None, **data):
        return (client or self.client).post(self.url, {"form_name": form_name, **data})

    def join_guest(self, name="Kim", client=None):
        client = client or self.guest
        response = client.post(self.join_url, {"name": name})
        self.assertRedirects(response, self.url)
        return self.board.participants.get(guest_name__startswith=name, pk__in=self._new_ids())

    def _new_ids(self):
        return self.board.participants.order_by("-id").values_list("id", flat=True)[:1]

    def set_phase(self, phase):
        self.post("retro_phase", phase=phase)
        self.board.refresh_from_db()

    def test_new_retro_gets_template_columns_and_owner_participant(self):
        response = self.client.post(
            "/tools/retro/", {"form_name": "retro_create", "title": " Q4 ", "template": "four_l"}
        )
        board = RetroBoard.objects.get(title="Q4")
        self.assertRedirects(response, f"/tools/retro/{board.pk}/")
        self.assertEqual(
            list(board.columns.values_list("title", flat=True)),
            ["Gemocht", "Gelernt", "Gefehlt", "Gewünscht"],
        )
        self.assertEqual(board.participants.get().user, self.owner)
        self.assertContains(self.client.get("/tools/retro/"), "Q4")

    def test_guests_join_by_link_with_unique_names_and_others_need_it(self):
        anonymous = Client()
        self.assertRedirects(
            anonymous.get(self.url), f"/login/?next={self.url}", fetch_redirect_response=False
        )
        response = self.guest.get(self.join_url)
        self.assertContains(response, "Wie sollen dich die anderen nennen?")
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.join_guest("Kim")
        self.join_guest("Kim", client=Client())
        self.assertEqual(
            sorted(self.board.participants.exclude(guest_name="").values_list("guest_name", flat=True)),
            ["Kim", "Kim (2)"],
        )
        self.assertContains(self.guest.get(self.url), "Sprint 47")

        stranger = Client()
        stranger.force_login(self.other)
        self.assertEqual(stranger.get(self.url).status_code, 404)
        self.assertRedirects(stranger.get(self.join_url), self.url, fetch_redirect_response=False)
        self.assertTrue(self.board.participants.filter(user=self.other).exists())
        self.assertContains(stranger.get("/tools/retro/"), "Sprint 47")

    def test_disabled_or_renewed_link_stops_working(self):
        self.post("retro_link_renew")
        self.assertEqual(self.guest.get(self.join_url).status_code, 404)
        self.board.refresh_from_db()
        self.board.join_enabled = False
        self.board.save()
        self.assertEqual(self.guest.get(f"/retro/join/{self.board.join_token}/").status_code, 404)

    def test_hidden_cards_stay_server_side_until_collecting_ends(self):
        self.join_guest()
        self.post("retro_card_add", client=self.guest, column_id=self.start.pk, text="Geheime Idee")
        self.assertEqual(RetroCard.objects.get().text, "Geheime Idee")
        page = self.client.get(self.url)
        self.assertNotContains(page, "Geheime Idee")
        self.assertContains(page, "verdeckt")
        self.assertContains(self.guest.get(self.url), "Geheime Idee")
        self.set_phase("group")
        self.assertContains(self.client.get(self.url), "Geheime Idee")

    def test_card_limit_counts_across_columns(self):
        self.board.max_cards = 2
        self.board.save()
        for column, text in [(self.start, "Eins"), (self.stop, "Zwei"), (self.keep, "Drei")]:
            self.post("retro_card_add", column_id=column.pk, text=text)
        self.assertEqual(sorted(RetroCard.objects.values_list("text", flat=True)), ["Eins", "Zwei"])

    def test_only_the_owner_changes_phase_and_settings(self):
        self.join_guest()
        self.assertEqual(self.post("retro_phase", client=self.guest, phase="vote").status_code, 404)
        self.assertEqual(self.post("retro_settings", client=self.guest, title="X").status_code, 404)
        self.assertNotContains(self.guest.get(self.url), "retro-settings-dialog")
        self.post(
            "retro_settings",
            title="Neu",
            max_cards=0,
            votes_per_person=3,
            votes_per_card=9,
            timer_minutes=10,
            hide_cards="on",
            join_enabled="on",
        )
        self.board.refresh_from_db()
        self.assertEqual((self.board.title, self.board.max_cards, self.board.votes_per_card), ("Neu", 0, 3))
        self.assertFalse(self.board.anonymous)

    def test_votes_respect_limits_and_stay_hidden_until_discussion(self):
        self.board.votes_per_person, self.board.votes_per_card = 3, 2
        self.board.save()
        guest = self.join_guest()
        self.post("retro_card_add", column_id=self.start.pk, text="A")
        self.post("retro_card_add", column_id=self.stop.pk, text="B")
        first, second = RetroCard.objects.order_by("id")
        self.set_phase("vote")
        for _ in range(3):
            self.post("retro_vote", client=self.guest, card_id=first.pk)
        self.post("retro_vote", client=self.guest, card_id=second.pk)
        self.post("retro_vote", client=self.guest, card_id=second.pk)
        self.assertEqual(first.votes.count(), 2)
        self.assertEqual(second.votes.count(), 1)
        self.post("retro_vote", client=self.guest, card_id=first.pk, direction="down")
        self.assertEqual(guest.votes.count(), 2)

        board_context = self.client.get(self.url).context
        self.assertIsNone(board_context["columns"][0]["cards"][0]["votes"])
        self.set_phase("discuss")
        self.assertEqual(self.client.get(self.url).context["columns"][0]["cards"][0]["votes"], 1)

    def test_hovering_votes_names_voters_unless_hidden_or_anonymous(self):
        self.board.anonymous = False
        self.board.save()
        self.join_guest()
        self.post("retro_card_add", column_id=self.start.pk, text="A")
        card = RetroCard.objects.get()
        self.set_phase("vote")
        self.post("retro_vote", card_id=card.pk)
        self.post("retro_vote", client=self.guest, card_id=card.pk)
        self.post("retro_vote", client=self.guest, card_id=card.pk)
        self.assertEqual(self.client.get(self.url).context["columns"][0]["cards"][0]["voters"], "")

        self.set_phase("discuss")
        voters = self.client.get(self.url).context["columns"][0]["cards"][0]["voters"]
        self.assertIn("Kim ×2", voters)
        self.assertEqual(voters.count(","), 1)
        self.assertContains(self.client.get(self.url), f'title="{voters}"')

        self.board.anonymous = True
        self.board.save()
        self.assertEqual(self.client.get(self.url).context["columns"][0]["cards"][0]["voters"], "")

    def test_grouping_moves_votes_to_the_top_card_and_ungrouping_splits_again(self):
        self.post("retro_card_add", column_id=self.start.pk, text="A")
        self.post("retro_card_add", column_id=self.start.pk, text="B")
        first, second = RetroCard.objects.order_by("id")
        participant = self.board.participants.get()
        RetroVote.objects.create(card=second, participant=participant)
        self.post("retro_group", card_id=second.pk, target_id=first.pk)
        second.refresh_from_db()
        self.assertIsNone(second.group)  # Only while grouping.
        self.set_phase("group")
        self.post("retro_group", card_id=second.pk, target_id=first.pk)
        second.refresh_from_db()
        self.assertEqual(second.group, first)
        self.assertEqual(first.votes.count(), 1)
        self.post("retro_ungroup", card_id=first.pk)
        second.refresh_from_db()
        self.assertIsNone(second.group)
        self.assertEqual(second.votes.count(), 1)

    def test_presented_card_shows_for_everyone_and_owner_clears_it(self):
        self.join_guest()
        self.post("retro_card_add", column_id=self.start.pk, text="Thema")
        card = RetroCard.objects.get()
        self.post("retro_present", card_id=card.pk)
        self.board.refresh_from_db()
        self.assertIsNone(self.board.presented_card)  # Only while discussing.
        self.set_phase("discuss")
        self.post("retro_present", card_id=card.pk)
        self.assertContains(self.guest.get(self.url), "data-retro-present")
        self.post("retro_present", card_id="")
        self.assertNotContains(self.guest.get(self.url), "data-retro-present")

    def test_actions_become_tasks_only_for_lunora_users(self):
        guest = self.join_guest()
        self.set_phase("discuss")
        self.post("retro_action_add", client=self.guest, title="Tests schreiben", assignee_id=guest.pk)
        self.post("retro_action_add", title="Deploy prüfen", description="Logs\r\nund   Metriken")
        guest_action, open_action = self.board.actions.order_by("id")
        self.post("retro_action_task", client=self.guest, action_id=guest_action.pk)
        self.post("retro_action_task", action_id=guest_action.pk)
        self.assertFalse(Task.objects.exists())
        self.post("retro_action_task", action_id=open_action.pk)
        task = Task.objects.get()
        self.assertEqual(
            (task.user, task.title, task.description), (self.owner, "Deploy prüfen", "Logs\nund Metriken")
        )
        open_action.refresh_from_db()
        self.assertEqual(open_action.task, task)

    def test_actions_have_title_description_and_can_be_reassigned(self):
        guest = self.join_guest()
        self.set_phase("discuss")
        self.post("retro_action_add", title="  ", description="ohne Titel")
        self.assertFalse(self.board.actions.exists())
        self.post("retro_action_add", client=self.guest, title="Retro-Termin", description="Jeden Freitag")
        action = self.board.actions.get()
        self.assertIsNone(action.assignee)

        self.post(
            "retro_action_edit",
            action_id=action.pk,
            title="Retro-Termin festlegen",
            description="Freitags\n10 Uhr",
            assignee_id=guest.pk,
        )
        action.refresh_from_db()
        self.assertEqual(
            (action.title, action.description, action.assignee),
            ("Retro-Termin festlegen", "Freitags\n10 Uhr", guest),
        )
        page = self.client.get(self.url)
        self.assertContains(page, "Freitags<br>10 Uhr")
        self.assertContains(page, "Kim")

        other_board = create_retro(self.other, "Fremd", "start_stop")
        self.post(
            "retro_action_edit", action_id=action.pk, title="X", assignee_id=other_board.participants.get().pk
        )
        action.refresh_from_db()
        self.assertIsNone(action.assignee)

    def test_summary_saves_a_note(self):
        self.post("retro_card_add", column_id=self.start.pk, text="Pairing")
        self.set_phase("done")
        response = self.post("retro_note")
        note = Note.objects.get()
        self.assertRedirects(response, f"/notes/{note.pk}/", fetch_redirect_response=False)
        self.assertIn("Pairing", note.plain_text)

    def test_version_endpoint_and_feature_flag(self):
        version = self.client.get(f"{self.url}?version=1").json()["version"]
        self.post("retro_card_add", column_id=self.start.pk, text="A")
        self.assertNotEqual(self.client.get(f"{self.url}?version=1").json()["version"], version)
        settings = SystemSettings.objects.get_or_create(pk=1)[0]
        settings.tools_enabled = False
        settings.save()
        self.assertEqual(self.client.get(f"{self.url}?version=1").status_code, 503)
        self.assertEqual(self.guest.get(self.join_url).status_code, 503)

    def test_joining_is_throttled_per_address(self):
        for _ in range(30):
            Client().post(self.join_url, {"name": "Bot"})
        response = Client().post(self.join_url, {"name": "Bot"})
        self.assertContains(response, "Zu viele Versuche")
        self.assertEqual(self.board.participants.filter(guest_name__startswith="Bot").count(), 30)

    def test_export_holds_own_retros_without_the_token(self):
        self.post("retro_card_add", column_id=self.start.pk, text="Exportiert")
        buffer = BytesIO()
        write_user_export(self.owner, buffer)
        archive = zipfile.ZipFile(buffer)
        content = archive.read("retros.json").decode()
        self.assertIn("Exportiert", content)
        self.assertNotIn(self.board.join_token, content)
        self.assertEqual(json.loads(content)[0]["title"], "Sprint 47")
