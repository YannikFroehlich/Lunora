import json
import zipfile
from io import BytesIO

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase

from app.models import Note, RetroAction, RetroBoard, RetroCard, RetroVote, SystemSettings, Task
from app.services.data_export import write_user_export
from app.services.retro import create_retro, next_title


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

    def test_cards_change_only_while_collecting_and_hidden_cards_stay_untouchable(self):
        self.join_guest()
        self.post("retro_card_add", client=self.guest, column_id=self.start.pk, text="Gast")
        self.post("retro_card_add", column_id=self.start.pk, text="Eigene")
        guest_card, own_card = RetroCard.objects.order_by("id")
        # Hidden while collecting: the owner can neither overwrite nor delete it unseen.
        self.assertEqual(self.post("retro_card_edit", card_id=guest_card.pk, text="X").status_code, 404)
        self.assertEqual(self.post("retro_card_delete", card_id=guest_card.pk).status_code, 404)
        self.board.hide_cards = False
        self.board.save()
        self.post("retro_card_edit", card_id=guest_card.pk, text="Moderiert")
        guest_card.refresh_from_db()
        self.assertEqual(guest_card.text, "Moderiert")

        self.set_phase("vote")
        self.post("retro_card_edit", card_id=own_card.pk, text="Nachträglich")
        self.post("retro_card_delete", client=self.guest, card_id=guest_card.pk)
        own_card.refresh_from_db()
        self.assertEqual(own_card.text, "Eigene")
        self.assertTrue(RetroCard.objects.filter(pk=guest_card.pk).exists())

    def test_ungrouping_only_while_grouping(self):
        self.post("retro_card_add", column_id=self.start.pk, text="A")
        self.post("retro_card_add", column_id=self.start.pk, text="B")
        first, second = RetroCard.objects.order_by("id")
        self.set_phase("group")
        self.post("retro_group", card_id=second.pk, target_id=first.pk)
        self.set_phase("discuss")
        self.post("retro_ungroup", card_id=first.pk)
        second.refresh_from_db()
        self.assertEqual(second.group, first)

    def test_new_retro_follows_up_on_own_last_retro_and_reviews_its_actions(self):
        old_action = RetroAction.objects.create(board=self.board, title="Daily kürzen")
        task = Task.objects.create(user=self.other, title="Doku")
        task_action = RetroAction.objects.create(board=self.board, title="Doku", task=task)
        foreign = create_retro(self.other, "Fremd", "blank")
        page = self.client.get("/tools/retro/")
        self.assertEqual([choice["id"] for choice in page.context["previous_choices"]], [self.board.pk])

        # Someone else's retro can't be picked; the own one can.
        self.client.post(
            "/tools/retro/",
            {"form_name": "retro_create", "title": "Fremd-Folge", "previous_id": foreign.pk},
        )
        self.assertIsNone(RetroBoard.objects.get(title="Fremd-Folge").previous)
        self.client.post(
            "/tools/retro/",
            {"form_name": "retro_create", "title": "Sprint 48", "previous_id": self.board.pk},
        )
        follow_up = RetroBoard.objects.get(title="Sprint 48")
        self.assertEqual(follow_up.previous, self.board)

        # A guest of the new retro sees the old actions while collecting and ticks them off.
        url = f"/tools/retro/{follow_up.pk}/"
        guest = Client()
        guest.post(f"/retro/join/{follow_up.join_token}/", {"name": "Kim"})
        self.assertContains(guest.get(url), "Daily kürzen")
        guest.post(url, {"form_name": "retro_action_done", "action_id": old_action.pk, "done": "1"})
        old_action.refresh_from_db()
        self.assertTrue(old_action.done)
        # A task's state wins and can't be overridden from the retro.
        guest.post(url, {"form_name": "retro_action_done", "action_id": task_action.pk, "done": "1"})
        task_action.refresh_from_db()
        self.assertFalse(task_action.done)
        task.is_done = True
        task.save()
        previous = {item["id"]: item["done"] for item in guest.get(url).context["previous_actions"]}
        self.assertEqual(previous, {old_action.pk: True, task_action.pk: True})

        # Unrelated boards' actions stay out of reach, and the panel leaves after collecting.
        stranger = RetroAction.objects.create(board=foreign, title="X")
        response = guest.post(url, {"form_name": "retro_action_done", "action_id": stranger.pk, "done": "1"})
        self.assertEqual(response.status_code, 404)
        self.client.post(url, {"form_name": "retro_phase", "phase": "group"})
        self.assertIsNone(guest.get(url).context["previous_actions"])

    def test_moving_on_from_a_presented_topic_ticks_it_off_and_owner_can_untick(self):
        self.join_guest()
        self.post("retro_card_add", column_id=self.start.pk, text="A")
        self.post("retro_card_add", column_id=self.start.pk, text="B")
        first, second = RetroCard.objects.order_by("id")
        self.set_phase("group")
        self.post("retro_group", card_id=second.pk, target_id=first.pk)
        self.post("retro_discussed", card_id=first.pk, discussed="1")
        first.refresh_from_db()
        self.assertFalse(first.discussed)  # Only while discussing.

        self.set_phase("discuss")
        self.post("retro_present", card_id=first.pk)
        first.refresh_from_db()
        self.assertFalse(first.discussed)  # Not until moving on.
        self.post("retro_present", card_id="")
        first.refresh_from_db()
        self.assertTrue(first.discussed)
        context = self.guest.get(self.url).context
        self.assertEqual((context["discussed_count"], context["topic_count"]), (1, 1))

        # Ticking a grouped card means its group; guests can't tick at all.
        self.post("retro_discussed", card_id=second.pk, discussed="0")
        first.refresh_from_db()
        self.assertFalse(first.discussed)
        response = self.post("retro_discussed", client=self.guest, card_id=first.pk, discussed="1")
        self.assertEqual(response.status_code, 404)

    def test_owner_renames_moves_inserts_and_deletes_columns(self):
        def titles():
            return list(self.board.columns.values_list("title", "tone"))

        self.post("retro_column_edit", column_id=self.keep.pk, title=" Weiter so ", tone="blue")
        self.post("retro_column_edit", column_id=self.stop.pk, title="Halt", tone="pink")  # Unknown tone.
        self.post("retro_column_move", column_id=self.keep.pk, direction="left")
        self.post("retro_column_move", column_id=self.start.pk, direction="left")  # Already first.
        self.post("retro_column_add", column_id=self.start.pk, title="Fragen", tone="violet")
        self.assertEqual(
            titles(), [("Start", "green"), ("Fragen", "violet"), ("Weiter so", "blue"), ("Halt", "sand")]
        )
        self.assertEqual(list(self.board.columns.values_list("position", flat=True)), [0, 1, 2, 3])

        self.join_guest()
        self.post("retro_card_add", client=self.guest, column_id=self.stop.pk, text="Weg damit")
        for form_name in (
            "retro_column_add",
            "retro_column_edit",
            "retro_column_move",
            "retro_column_delete",
        ):
            response = self.post(form_name, client=self.guest, column_id=self.start.pk, title="X")
            self.assertEqual(response.status_code, 404)
        self.post("retro_column_delete", column_id=self.stop.pk)
        self.assertFalse(RetroCard.objects.exists())

        for title in ("E", "F", "G", "H"):  # H is one too many.
            self.post("retro_column_add", column_id=self.start.pk, title=title)
        self.assertEqual(self.board.columns.count(), 6)  # MAX_COLUMNS
        for column in list(self.board.columns.all())[1:]:
            self.post("retro_column_delete", column_id=column.pk)
        self.post("retro_column_delete", column_id=self.start.pk)
        self.assertEqual(titles(), [("Start", "green")])
        self.assertContains(self.client.get(self.url), "Spalte „Start“ bearbeiten")
        self.assertNotContains(self.guest.get(self.url), "Spalte „Start“ bearbeiten")

    def test_follow_up_retro_copies_columns_and_settings_but_not_content(self):
        self.post("retro_column_edit", column_id=self.keep.pk, title="Weiter so", tone="blue")
        self.board.max_cards, self.board.anonymous, self.board.phase = 9, False, RetroBoard.PHASE_DONE
        self.board.save()
        self.join_guest()
        self.post("retro_card_add", client=self.guest, column_id=self.start.pk, text="Karte")
        RetroAction.objects.create(board=self.board, title="Daily kürzen")
        self.assertContains(self.client.get(self.url), 'value="Sprint 48"')
        self.assertEqual(self.post("retro_duplicate", client=self.guest).status_code, 404)

        response = self.post("retro_duplicate", title="")  # Empty: the suggested title.
        copy = RetroBoard.objects.get(title="Sprint 48")
        self.assertRedirects(response, f"/tools/retro/{copy.pk}/")
        self.assertEqual(
            list(copy.columns.values_list("title", "tone", "position")),
            [("Start", "green", 0), ("Stop", "red", 1), ("Weiter so", "blue", 2)],
        )
        self.assertEqual((copy.max_cards, copy.anonymous, copy.phase), (9, False, RetroBoard.PHASE_COLLECT))
        self.assertEqual(copy.previous, self.board)
        self.assertNotEqual(copy.join_token, self.board.join_token)
        self.assertEqual(list(copy.participants.values_list("user", flat=True)), [self.owner.pk])
        self.assertFalse(copy.cards.exists() or copy.actions.exists())
        self.assertContains(self.client.get(f"/tools/retro/{copy.pk}/"), "Daily kürzen")

    def test_next_title_bumps_the_last_number(self):
        self.assertEqual(next_title("Sprint 09 Retro"), "Sprint 10 Retro")
        self.assertEqual(next_title("Q4 Team 7"), "Q4 Team 8")
        self.assertEqual(next_title("Teamretro"), "Teamretro (Kopie)")

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

    def test_authors_show_from_grouping_on_unless_anonymous(self):
        self.board.anonymous, self.board.hide_cards = False, False
        self.board.save()
        self.join_guest()
        self.post("retro_card_add", client=self.guest, column_id=self.start.pk, text="A")
        self.post("retro_card_add", column_id=self.start.pk, text="B")
        first, second = RetroCard.objects.order_by("id")

        def authors():
            return [card["author"] for card in self.client.get(self.url).context["columns"][0]["cards"]]

        self.assertEqual(authors(), ["", ""])
        self.set_phase("group")
        self.assertEqual(authors(), ["Kim", "lea@example.com"])
        self.post("retro_group", card_id=second.pk, target_id=first.pk)
        card = self.client.get(self.url).context["columns"][0]["cards"][0]
        self.assertEqual((card["author"], card["children"][0]["author"]), ("Kim", "lea@example.com"))
        self.assertContains(
            self.client.get(self.url), '<span class="retro-card-author">lea@example.com</span>'
        )

        self.board.anonymous = True
        self.board.save()
        self.assertEqual(authors(), [""])

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
