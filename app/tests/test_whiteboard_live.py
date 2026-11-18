import json
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase

from app.models import UserNotification, Whiteboard, WhiteboardComment, WhiteboardOp, WhiteboardShare
from app.services import whiteboards
from app.services.whiteboard_content import (
    apply_whiteboard_ops,
    validate_whiteboard_document,
    whiteboard_hash,
)


def rect(element_id, x=0):
    return {
        "id": element_id,
        "type": "rect",
        "color": "default",
        "width": 2,
        "x": x,
        "y": 0,
        "w": 10,
        "h": 10,
        "fill": False,
    }


class WhiteboardLiveTests(TestCase):
    def setUp(self):
        cache.clear()  # peers and hashes live in the cache, which outlasts each test
        self.owner = User.objects.create_user(username="wb-owner@example.com", password="pw-12345!")
        self.member = User.objects.create_user(username="wb-member@example.com", password="pw-12345!")
        self.stranger = User.objects.create_user(username="wb-stranger@example.com", password="pw-12345!")
        self.board = Whiteboard.objects.create(
            owner=self.owner, title="Tafel", document={"version": 1, "elements": [rect("a"), rect("b")]}
        )
        WhiteboardShare.objects.create(board=self.board, user=self.member)
        self.clients = {}
        for user in (self.owner, self.member, self.stranger):
            client = Client()
            client.force_login(user)
            self.clients[user] = client

    def sync(self, user, version, ops=None, tab="tab-aaaaaaaa", pointer=None, drawing=False):
        body = {"version": version, "tab": tab, "pointer": pointer, "drawing": drawing}
        if ops is not None:
            body["ops"] = ops
        return self.clients[user].post(
            f"/tools/whiteboard/api/{self.board.pk}/sync/",
            data=json.dumps(body),
            content_type="application/json",
        )

    def stored(self):
        self.board.refresh_from_db()
        return self.board.document["elements"]

    def test_pushed_operations_reach_the_others_as_the_server_cleaned_them(self):
        new = rect("c", x=1.26)
        response = self.sync(self.owner, 1, [{"op": "put", "element": new, "after": "a", "seq": 1}])
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data["version"], 2)
        self.assertEqual(data["acked"], 1)
        self.assertEqual([item["id"] for item in self.stored()], ["a", "c", "b"])
        self.assertEqual(data["hash"], whiteboard_hash(self.stored()))

        data = self.sync(self.member, 1).json()
        self.assertEqual(
            data["ops"],
            [{"op": "put", "element": {**new, "x": 1.3, "y": 0.0, "w": 10.0, "h": 10.0}, "after": "a"}],
        )
        self.assertEqual(apply_whiteboard_ops([rect("a"), rect("b")], data["ops"]), self.stored())
        self.assertEqual(data["acked"], 0)
        self.assertEqual(self.sync(self.stranger, 1).status_code, 404)

    def test_concurrent_pushes_are_applied_in_arrival_order(self):
        self.sync(self.owner, 1, [{"op": "delete", "id": "a", "seq": 1}])
        data = self.sync(
            self.member,
            1,
            [
                {"op": "put", "element": rect("a", x=5), "seq": 1},
                {"op": "move", "id": "b", "after": None, "seq": 2},
            ],
            tab="tab-bbbbbbbb",
        ).json()
        self.assertEqual(data["version"], 4)
        self.assertEqual([op["op"] for op in data["ops"]], ["delete", "put", "move"])
        self.assertEqual(data["acked"], 2)
        # The later change to "a" wins over the earlier delete, for everyone.
        self.assertEqual([(item["id"], item["x"]) for item in self.stored()], [("b", 0.0), ("a", 5.0)])

    def test_a_resent_push_is_not_applied_twice(self):
        ops = [{"op": "put", "element": rect("c"), "after": None, "seq": 1}]
        self.sync(self.owner, 1, ops)
        data = self.sync(self.owner, 1, ops).json()
        self.assertEqual(data["version"], 2)
        self.assertEqual(WhiteboardOp.objects.filter(board=self.board).count(), 1)
        self.assertEqual([item["id"] for item in self.stored()], ["c", "a", "b"])

    def test_invalid_operations_change_nothing(self):
        bad = [
            [{"op": "put", "element": {**rect("c"), "color": "pink"}, "seq": 1}],
            [{"op": "put", "element": rect("c"), "after": "c", "seq": 1}],
            [{"op": "drop", "id": "a", "seq": 1}],
            [{"op": "delete", "id": "a", "seq": 0}],
            [{"op": "delete", "id": "a", "seq": 2}, {"op": "delete", "id": "b", "seq": 1}],
            [{"op": "delete", "id": "a", "seq": 1, "extra": 1}],
            [{"op": "title", "title": "   ", "seq": 1}],
            "nope",
        ]
        for ops in bad:
            with self.subTest(ops=ops):
                self.assertEqual(self.sync(self.owner, 1, ops).status_code, 400)
        with mock.patch("app.services.whiteboard_content.MAX_ELEMENTS", 2):
            response = self.sync(self.owner, 1, [{"op": "put", "element": rect("c"), "seq": 1}])
        self.assertEqual(response.status_code, 400)
        self.assertEqual([item["id"] for item in self.stored()], ["a", "b"])
        self.assertEqual(self.board.revision, 1)
        self.assertFalse(WhiteboardOp.objects.exists())

    def test_title_changes_travel_as_operations(self):
        data = self.sync(self.member, 1, [{"op": "title", "title": "  Neue Tafel ", "seq": 1}]).json()
        self.assertEqual(data["title"], "Neue Tafel")
        self.board.refresh_from_db()
        self.assertEqual(self.board.title, "Neue Tafel")

    def test_whole_board_saves_and_pruned_logs_make_editors_reload(self):
        response = self.clients[self.owner].put(
            f"/tools/whiteboard/api/{self.board.pk}/",
            data=json.dumps(
                {"title": "Tafel", "document": {"version": 1, "elements": []}, "base_revision": 1}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = self.sync(self.member, 1).json()
        self.assertTrue(data["resync"])
        self.assertEqual(data["ops"], [])
        self.assertFalse(self.sync(self.member, 2).json()["resync"])

        with mock.patch.object(whiteboards, "OP_HISTORY", 2):
            for seq in range(1, 4):
                self.sync(self.owner, 2, [{"op": "put", "element": rect(f"n{seq}"), "seq": seq}])
        self.assertTrue(self.sync(self.member, 2).json()["resync"])
        self.assertFalse(self.sync(self.member, 3).json()["resync"])

    def test_apply_rules(self):
        a, b, c = rect("a"), rect("b"), rect("c")
        ids = lambda elements: [item["id"] for item in elements]  # noqa: E731
        self.assertEqual(
            ids(apply_whiteboard_ops([a, b], [{"op": "put", "element": c, "after": "gone"}])), ["a", "b", "c"]
        )
        self.assertEqual(
            ids(apply_whiteboard_ops([a, b], [{"op": "put", "element": c, "after": None}])), ["c", "a", "b"]
        )
        self.assertEqual(
            ids(apply_whiteboard_ops([a, b, c], [{"op": "put", "element": {**b, "x": 3}}])), ["a", "b", "c"]
        )
        self.assertEqual(
            ids(apply_whiteboard_ops([a, b, c], [{"op": "move", "id": "a", "after": "c"}])), ["b", "c", "a"]
        )
        self.assertEqual(
            ids(apply_whiteboard_ops([a, b], [{"op": "move", "id": "gone", "after": None}])), ["a", "b"]
        )
        self.assertEqual(
            ids(apply_whiteboard_ops([a, b], [{"op": "delete", "id": "a"}, {"op": "delete", "id": "a"}])),
            ["b"],
        )

    def test_hash_matches_the_editor(self):
        # frontend/whiteboard_live.test.js hashes the same board in the browser's way.
        elements = validate_whiteboard_document(
            {
                "version": 1,
                "elements": [
                    {
                        "id": "a",
                        "type": "text",
                        "color": "blue",
                        "x": 10,
                        "y": -0.04,
                        "size": "m",
                        "text": 'Grüße "Ü"   😀',
                    },
                    {
                        "id": "b",
                        "type": "stroke",
                        "color": "default",
                        "width": 4,
                        "points": [0, 0, 12.34, 5.5],
                    },
                    {
                        "id": "c",
                        "type": "rect",
                        "color": "red",
                        "width": 2,
                        "x": 1,
                        "y": 2,
                        "w": 30,
                        "h": 40,
                        "fill": True,
                    },
                ],
            }
        )["elements"]
        self.assertEqual(
            whiteboard_hash(elements), "a36b2bb342f000808b34ec4ec8fa7f9430d6ce7654e733a156b1c745d40cf3d6"
        )

    def test_editor_page_points_to_the_sync_endpoint(self):
        response = self.clients[self.member].get(f"/tools/whiteboard/{self.board.pk}/")
        self.assertContains(response, f'data-sync-url="/tools/whiteboard/api/{self.board.pk}/sync/"')

    def test_peers_see_each_other_with_their_pointer(self):
        self.board.owner.first_name = "Lea"
        self.board.owner.save()
        self.sync(self.owner, 1, pointer={"x": 12.34, "y": -5}, drawing=True)
        peers = self.sync(self.member, 1, tab="tab-bbbbbbbb").json()["peers"]
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0]["user_id"], self.owner.pk)
        self.assertEqual(peers[0]["pointer"], {"x": 12.3, "y": -5.0})
        self.assertTrue(peers[0]["drawing"])
        self.assertEqual(peers[0]["initials"], "LE")
        # Own tabs are not listed, the other one is.
        self.assertEqual(
            self.sync(self.owner, 1, tab="tab-cccccccc").json()["peers"][0]["user_id"], self.member.pk
        )

    def test_peers_expire_and_former_members_drop_out(self):
        with mock.patch("app.services.whiteboards.time.time", return_value=1000.0):
            self.sync(self.member, 1)
        with mock.patch(
            "app.services.whiteboards.time.time", return_value=1000.0 + whiteboards.PEER_TTL_SECONDS + 1
        ):
            self.assertEqual(self.sync(self.owner, 1).json()["peers"], [])
        self.sync(self.member, 1)
        WhiteboardShare.objects.filter(board=self.board, user=self.member).delete()
        self.assertEqual(self.sync(self.owner, 1).json()["peers"], [])

    def test_bad_pointers_are_refused(self):
        for pointer in (
            {"x": 1},
            {"x": "1", "y": 2},
            {"x": True, "y": 2},
            [1, 2],
            {"x": 1, "y": float("nan")},
        ):
            with self.subTest(pointer=pointer):
                body = json.dumps({"version": 1, "tab": "tab-aaaaaaaa", "pointer": pointer})
                response = self.clients[self.owner].post(
                    f"/tools/whiteboard/api/{self.board.pk}/sync/", data=body, content_type="application/json"
                )
                self.assertEqual(response.status_code, 400)


class WhiteboardCommentTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(
            username="c-owner@example.com", password="pw-12345!", first_name="Lea"
        )
        self.member = User.objects.create_user(username="c-member@example.com", password="pw-12345!")
        self.stranger = User.objects.create_user(username="c-stranger@example.com", password="pw-12345!")
        self.board = Whiteboard.objects.create(owner=self.owner, title="Tafel")
        WhiteboardShare.objects.create(board=self.board, user=self.member)
        self.url = f"/tools/whiteboard/api/{self.board.pk}/comments/"
        self.clients = {}
        for user in (self.owner, self.member, self.stranger):
            client = Client()
            client.force_login(user)
            self.clients[user] = client

    def post(self, user, **payload):
        return self.clients[user].post(self.url, data=json.dumps(payload), content_type="application/json")

    def test_threads_replies_and_inbox_rows(self):
        data = self.post(self.owner, action="add", x=10.04, y=-5, body="  Hier fehlt was  ").json()
        thread_id = data["thread_id"]
        self.assertEqual(data["threads"][0]["x"], 10.0)
        self.assertEqual(data["threads"][0]["comments"][0]["body"], "Hier fehlt was")
        inbox = UserNotification.objects.get(recipient=self.member, kind="whiteboard_comment")
        self.assertEqual(inbox.url, f"/tools/whiteboard/{self.board.pk}/?comment={thread_id}")
        self.assertFalse(UserNotification.objects.filter(recipient=self.owner).exists())

        data = self.post(self.member, action="add", thread_id=thread_id, body="Stimmt").json()
        self.assertEqual(data["thread_id"], thread_id)
        self.assertEqual([c["body"] for c in data["threads"][0]["comments"]], ["Hier fehlt was", "Stimmt"])
        self.assertTrue(
            UserNotification.objects.filter(recipient=self.owner, kind="whiteboard_comment").exists()
        )

        self.assertEqual(self.clients[self.stranger].get(self.url).status_code, 404)
        self.assertEqual(self.post(self.stranger, action="add", x=0, y=0, body="x").status_code, 404)

    def test_resolve_reopen_and_reply_opens_again(self):
        thread_id = self.post(self.owner, action="add", x=0, y=0, body="Prüfen").json()["thread_id"]
        self.assertTrue(
            self.post(self.member, action="resolve", thread_id=thread_id).json()["threads"][0]["resolved"]
        )
        self.assertFalse(
            self.post(self.member, action="reopen", thread_id=thread_id).json()["threads"][0]["resolved"]
        )
        self.post(self.member, action="resolve", thread_id=thread_id)
        data = self.post(self.owner, action="add", thread_id=thread_id, body="Doch nicht").json()
        self.assertFalse(data["threads"][0]["resolved"])

    def test_only_authors_delete_and_a_thread_goes_with_its_inbox_rows(self):
        thread_id = self.post(self.owner, action="add", x=0, y=0, body="Eins").json()["thread_id"]
        self.post(self.member, action="add", thread_id=thread_id, body="Zwei")
        self.assertEqual(self.post(self.member, action="delete", comment_id=thread_id).status_code, 403)
        self.assertEqual(self.post(self.owner, action="delete", comment_id=thread_id).json()["threads"], [])
        self.assertFalse(WhiteboardComment.objects.exists())
        self.assertFalse(UserNotification.objects.filter(kind="whiteboard_comment").exists())

    def test_invalid_comments_are_refused(self):
        for payload in (
            {"action": "add", "x": 0, "y": 0, "body": "   "},
            {"action": "add", "x": 0, "y": 0, "body": "x" * 2001},
            {"action": "add", "x": "1", "y": 0, "body": "x"},
            {"action": "add", "thread_id": 999, "body": "x"},
            {"action": "shout"},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(self.post(self.owner, **payload).status_code, 400)
        self.assertFalse(WhiteboardComment.objects.exists())

    def test_live_poll_signals_comment_changes(self):
        def signature():
            body = json.dumps({"version": 1, "tab": "tab-aaaaaaaa"})
            response = self.clients[self.member].post(
                f"/tools/whiteboard/api/{self.board.pk}/sync/", data=body, content_type="application/json"
            )
            return response.json()["comments"]

        before = signature()
        thread_id = self.post(self.owner, action="add", x=0, y=0, body="Neu").json()["thread_id"]
        added = signature()
        self.post(self.owner, action="resolve", thread_id=thread_id)
        self.assertEqual(len({before, added, signature()}), 3)
