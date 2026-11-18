import json

from django.contrib.auth.models import User
from django.test import Client, TestCase

from app.models import Note, NoteActivityNotification, NoteShare, NoteVersion
from app.tests.helpers import note_document


def insert_step(text, pos=1, marks=None):
    node = {"type": "text", "text": text}
    if marks:
        node["marks"] = marks
    return {"stepType": "replace", "from": pos, "to": pos, "slice": {"content": [node]}}


class NoteCollabTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="collab-owner@example.com", password="secret-12345")
        self.editor = User.objects.create_user(username="collab-editor@example.com", password="secret-12345")
        self.reader = User.objects.create_user(username="collab-reader@example.com", password="secret-12345")
        self.outsider = User.objects.create_user(
            username="collab-outsider@example.com", password="secret-12345"
        )
        self.note = Note.objects.create(owner=self.owner, title="Gemeinsam", document=note_document("Start"))
        NoteShare.objects.create(note=self.note, user=self.editor, role=NoteShare.ROLE_EDITOR)
        NoteShare.objects.create(note=self.note, user=self.reader, role=NoteShare.ROLE_READER)
        self.clients = {}
        for user in (self.owner, self.editor, self.reader, self.outsider):
            client = Client()
            client.force_login(user)
            self.clients[user] = client

    def sync(self, user, version, steps=None, selection=None, tab="tab-aaaaaaaa"):
        body = {"version": version, "tab": tab, "selection": selection}
        if steps is not None:
            body["steps"] = steps
        return self.clients[user].post(
            f"/notes/api/{self.note.id}/collab/", data=json.dumps(body), content_type="application/json"
        )

    def save(self, user, document, *, collab_version=None, title="Gemeinsam"):
        self.note.refresh_from_db()
        payload = {"title": title, "document": document, "base_revision": self.note.revision}
        if collab_version is not None:
            payload["collab_version"] = collab_version
        return self.clients[user].patch(
            f"/notes/api/{self.note.id}/", data=json.dumps(payload), content_type="application/json"
        )

    def test_accepted_steps_reach_the_other_editors_and_readers(self):
        response = self.sync(self.owner, 0, [insert_step("Hallo ")])
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertTrue(data["accepted"])
        self.assertEqual(data["version"], 1)
        own_id = f"{self.owner.id}-tab-aaaaaaaa"
        self.assertEqual(data["client_id"], own_id)

        for user in (self.editor, self.reader):
            data = self.sync(user, 0).json()
            self.assertEqual(data["version"], 1)
            self.assertEqual(data["steps"], [{"step": insert_step("Hallo "), "client_id": own_id}])
        self.assertEqual(self.sync(self.outsider, 0).status_code, 404)

    def test_stale_push_is_refused_with_the_missing_steps(self):
        self.sync(self.owner, 0, [insert_step("A")])
        data = self.sync(self.editor, 0, [insert_step("B")]).json()
        self.assertFalse(data["accepted"])
        self.assertEqual([item["step"] for item in data["steps"]], [insert_step("A")])
        self.assertEqual(self.note.collab_steps.count(), 1)
        self.assertTrue(self.sync(self.editor, 1, [insert_step("B", pos=2)]).json()["accepted"])

    def test_readers_cannot_push_steps(self):
        self.assertEqual(self.sync(self.reader, 0, [insert_step("x")]).status_code, 403)
        self.assertFalse(self.note.collab_steps.exists())

    def test_steps_are_checked_against_the_document_allowlists(self):
        bad_steps = [
            {"stepType": "replace", "from": 1, "to": 1, "slice": {"content": [{"type": "script"}]}},
            insert_step("x", marks=[{"type": "link", "attrs": {"href": "javascript:alert(1)"}}]),
            {
                "stepType": "addMark",
                "from": 1,
                "to": 2,
                "mark": {"type": "textStyle", "attrs": {"color": "red"}},
            },
            {"stepType": "docAttr", "attr": "x", "value": 1},
            {"stepType": "replace", "from": 5, "to": 2},
            {"stepType": "replace", "from": -1, "to": 2},
            {"stepType": "attr", "pos": 0, "attr": "userId", "value": 1},
            {"stepType": "attr", "pos": 0, "attr": "onclick", "value": "x"},
            insert_step("x") | {"extra": True},
        ]
        for step in bad_steps:
            with self.subTest(step=step):
                self.assertEqual(self.sync(self.owner, 0, [step]).status_code, 400)
        self.assertFalse(self.note.collab_steps.exists())

    def test_attribute_steps_accept_valid_values(self):
        steps = [
            {"stepType": "attr", "pos": 0, "attr": "value", "value": "Ausgefüllt"},
            {"stepType": "attr", "pos": 0, "attr": "float", "value": True},
            {"stepType": "attr", "pos": 0, "attr": "textAlign", "value": None},
        ]
        self.assertTrue(self.sync(self.owner, 0, steps).json()["accepted"])
        bad = {"stepType": "attr", "pos": 0, "attr": "level", "value": 7}
        self.assertEqual(self.sync(self.owner, 3, [bad]).status_code, 400)

    def test_mentions_need_access_and_notify_their_author_once(self):
        mention = {"type": "mention", "attrs": {"userId": self.outsider.id, "label": "Outsider"}}
        step = {"stepType": "replace", "from": 1, "to": 1, "slice": {"content": [mention]}}
        self.assertEqual(self.sync(self.owner, 0, [step]).status_code, 400)

        mention["attrs"]["userId"] = self.editor.id
        self.assertTrue(self.sync(self.owner, 0, [step]).json()["accepted"])
        notifications = NoteActivityNotification.objects.filter(recipient=self.editor, kind="mention")
        self.assertEqual([item.actor_id for item in notifications], [self.owner.id])

        # The snapshot of another editor doesn't notify again.
        document = note_document("Start")
        document["content"][0]["content"].insert(0, mention)
        self.assertEqual(self.save(self.reader, document, collab_version=1).status_code, 403)
        mention["attrs"]["userId"] = self.reader.id
        self.assertEqual(self.save(self.editor, document, collab_version=1).status_code, 200)
        self.assertFalse(NoteActivityNotification.objects.filter(recipient=self.reader).exists())

    def test_snapshot_save_skips_the_revision_check_and_keeps_newer_snapshots(self):
        self.sync(self.owner, 0, [insert_step("A"), insert_step("B", pos=2)])
        payload = {
            "title": "Gemeinsam",
            "document": note_document("ABStart"),
            "base_revision": 0,
            "collab_version": 2,
        }
        response = self.clients[self.editor].patch(
            f"/notes/api/{self.note.id}/", data=json.dumps(payload), content_type="application/json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.note.refresh_from_db()
        self.assertEqual(self.note.collab_version, 2)
        self.assertEqual(self.note.plain_text, "ABStart")

        response = self.save(self.owner, note_document("AStart"), collab_version=1, title="Neuer Titel")
        self.assertEqual(response.status_code, 200, response.content)
        self.note.refresh_from_db()
        self.assertEqual(self.note.plain_text, "ABStart")
        self.assertEqual(self.note.title, "Neuer Titel")
        self.assertEqual(self.save(self.owner, note_document("?"), collab_version=9).status_code, 400)

    def test_plain_save_and_restore_reset_the_step_log(self):
        self.sync(self.owner, 0, [insert_step("A")])
        response = self.save(self.editor, note_document("Neu"))
        self.assertEqual(response.status_code, 200, response.content)
        self.note.refresh_from_db()
        self.assertFalse(self.note.collab_steps.exists())
        self.assertEqual(self.note.collab_version, 2)
        self.assertEqual(response.json()["note"]["collab_version"], 2)
        self.assertTrue(self.sync(self.owner, 1, [insert_step("B")]).json()["resync"])
        self.assertTrue(self.sync(self.owner, 2, [insert_step("B")]).json()["accepted"])

        version = NoteVersion.objects.create(
            note=self.note,
            created_by=self.owner,
            source_revision=1,
            title="Alt",
            document=note_document("Alt"),
        )
        self.note.refresh_from_db()
        response = self.clients[self.owner].post(
            f"/notes/api/{self.note.id}/versions/{version.id}/restore/",
            data=json.dumps({"base_revision": self.note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.note.refresh_from_db()
        self.assertEqual(self.note.collab_version, 4)
        self.assertTrue(self.sync(self.editor, 3).json()["resync"])

    def test_cursors_are_shown_to_the_others_only(self):
        self.sync(self.owner, 0, selection={"anchor": 2, "head": 4})
        self.sync(self.reader, 0, selection={"anchor": 1, "head": 1})
        self.assertEqual(self.sync(self.owner, 0).json()["cursors"], [])
        self.sync(self.owner, 0, selection={"anchor": 2, "head": 4})
        cursors = self.sync(self.editor, 0, tab="tab-bbbbbbbb").json()["cursors"]
        self.assertEqual(
            [(item["user_id"], item["anchor"], item["head"]) for item in cursors], [(self.owner.id, 2, 4)]
        )
        self.sync(self.owner, 0, selection=None)
        self.assertEqual(self.sync(self.editor, 0, tab="tab-bbbbbbbb").json()["cursors"], [])

    def test_editor_can_store_a_link_someone_else_added(self):
        private = Note.objects.create(owner=self.owner, title="Privat")
        document = note_document("Start")
        document["content"][0]["content"].append(
            {"type": "noteLink", "attrs": {"noteId": private.id, "label": "Privat"}}
        )
        self.assertEqual(self.save(self.editor, document).status_code, 400)
        self.assertEqual(self.save(self.owner, document).status_code, 200)
        self.assertEqual(self.save(self.editor, document).status_code, 200)
