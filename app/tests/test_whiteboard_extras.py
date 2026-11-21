"""Whiteboard templates, pictures, bound arrows and the laser pointer."""

import io
import json

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from PIL import Image

from app.models import Whiteboard, WhiteboardImage, WhiteboardShare
from app.services.storage_quota import user_storage_used
from app.services.whiteboard_content import validate_whiteboard_document
from app.services.whiteboard_preview import whiteboard_preview_svg
from app.services.whiteboard_templates import WHITEBOARD_TEMPLATES, whiteboard_template_document

FILE_ID = "0b8f6a3e-5d1c-4a27-9f0e-2c4d6e8a1b3f"


def document(*elements):
    return {"version": 1, "elements": list(elements)}


def rect(element_id):
    return {
        "id": element_id,
        "type": "rect",
        "color": "default",
        "width": 2,
        "x": 0,
        "y": 0,
        "w": 100,
        "h": 50,
        "fill": False,
    }


def arrow(element_id, **extra):
    return {
        "id": element_id,
        "type": "arrow",
        "color": "default",
        "width": 2,
        "x1": 0,
        "y1": 0,
        "x2": 10,
        "y2": 10,
        **extra,
    }


def picture(element_id, file_id=FILE_ID):
    return {
        "id": element_id,
        "type": "image",
        "color": "default",
        "x": 1,
        "y": 2,
        "w": 300,
        "h": 200,
        "file": file_id,
    }


def png_upload(name="foto.png", size=(40, 30)):
    output = io.BytesIO()
    Image.new("RGB", size, (200, 120, 40)).save(output, "PNG")
    return SimpleUploadedFile(name, output.getvalue(), content_type="image/png")


class WhiteboardDocumentTests(TestCase):
    def test_pictures_and_bound_arrows_are_valid_elements(self):
        cleaned = validate_whiteboard_document(
            document(rect("r"), picture("p"), arrow("a", **{"from": "r", "to": "p"}))
        )

        self.assertEqual(cleaned["elements"][1]["file"], FILE_ID)
        self.assertEqual((cleaned["elements"][2]["from"], cleaned["elements"][2]["to"]), ("r", "p"))
        # A binding without its partner is fine, and so is one whose shape no longer exists.
        validate_whiteboard_document(document(arrow("a", to="gone")))

    def test_broken_pictures_and_bindings_are_refused(self):
        for bad in (
            picture("p", file_id="../../etc/passwd"),
            arrow("a", **{"from": "a"}),
            arrow("a", to=5),
            {**rect("r"), "from": "x"},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                validate_whiteboard_document(document(bad))

    def test_preview_shows_a_placeholder_for_pictures(self):
        self.assertIn('fill="#e6dfd4"', str(whiteboard_preview_svg(document(picture("p")))))


class WhiteboardTemplateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="lea@example.com", password="pw")
        self.client.force_login(self.user)

    def test_every_template_is_a_valid_board(self):
        for key in WHITEBOARD_TEMPLATES:
            with self.subTest(key=key):
                whiteboard_template_document(key)

    def test_a_new_board_starts_from_the_chosen_template(self):
        self.client.post(
            "/tools/whiteboard/", {"form_name": "whiteboard_create", "title": "Ideen", "template": "mindmap"}
        )
        self.client.post(
            "/tools/whiteboard/", {"form_name": "whiteboard_create", "title": "Leer", "template": "nope"}
        )

        mindmap = Whiteboard.objects.get(title="Ideen").document["elements"]
        arrows = [item for item in mindmap if item["type"] == "arrow"]
        self.assertEqual(len(arrows), 4)
        self.assertTrue(all(item["from"] == "center" for item in arrows))
        self.assertEqual(Whiteboard.objects.get(title="Leer").document["elements"], [])
        self.assertContains(self.client.get("/tools/whiteboard/"), "Vorlage: Retrospektive")


class WhiteboardImageTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner@example.com", password="pw")
        self.member = User.objects.create_user(username="member@example.com", password="pw")
        self.stranger = User.objects.create_user(username="stranger@example.com", password="pw")
        self.board = Whiteboard.objects.create(owner=self.owner, title="Tafel")
        WhiteboardShare.objects.create(board=self.board, user=self.member)
        self.clients = {}
        for user in (self.owner, self.member, self.stranger):
            self.clients[user] = Client()
            self.clients[user].force_login(user)

    def upload(self, user, upload=None):
        return self.clients[user].post(
            f"/tools/whiteboard/api/{self.board.pk}/images/", {"file": upload or png_upload()}
        )

    def image_url(self, file_id):
        return f"/tools/whiteboard/{self.board.pk}/images/{file_id}/"

    def test_members_upload_and_see_pictures_strangers_do_not(self):
        response = self.upload(self.member)

        self.assertEqual(response.status_code, 201)
        image = WhiteboardImage.objects.get()
        self.assertEqual(response.json()["file"], str(image.file_id))
        self.assertEqual((image.uploaded_by, image.content_type), (self.member, "image/png"))
        self.assertEqual(user_storage_used(self.member), image.size)
        served = self.clients[self.owner].get(self.image_url(image.file_id))
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served["Content-Type"], "image/png")
        served.close()
        self.assertEqual(self.clients[self.stranger].get(self.image_url(image.file_id)).status_code, 404)
        self.assertEqual(self.upload(self.stranger).status_code, 404)

    def test_files_that_are_no_pictures_are_refused(self):
        fake = SimpleUploadedFile("foto.png", b"not really a png", content_type="image/png")

        response = self.upload(self.owner, fake)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(WhiteboardImage.objects.exists())

    def test_duplicating_copies_the_pictures_and_deleting_removes_the_files(self):
        file_id = self.upload(self.owner).json()["file"]
        self.board.document = document(picture("p", file_id))
        self.board.save()

        self.clients[self.owner].post(
            "/tools/whiteboard/", {"form_name": "whiteboard_duplicate", "board_id": self.board.pk}
        )

        copy = Whiteboard.objects.exclude(pk=self.board.pk).get()
        copied_image = copy.images.get()
        self.assertNotEqual(str(copied_image.file_id), file_id)
        self.assertEqual(copy.document["elements"][0]["file"], str(copied_image.file_id))
        storage = copied_image.file.storage
        name = copied_image.file.name
        self.assertTrue(storage.exists(name))

        with self.captureOnCommitCallbacks(execute=True):
            self.clients[self.owner].post(
                "/tools/whiteboard/", {"form_name": "whiteboard_delete", "board_id": copy.pk}
            )

        self.assertFalse(storage.exists(name))
        self.assertTrue(self.board.images.exists())


class WhiteboardLaserTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username="owner@example.com", password="pw")
        self.member = User.objects.create_user(username="member@example.com", password="pw")
        self.board = Whiteboard.objects.create(owner=self.owner, title="Tafel")
        WhiteboardShare.objects.create(board=self.board, user=self.member)
        self.owner_client, self.member_client = Client(), Client()
        self.owner_client.force_login(self.owner)
        self.member_client.force_login(self.member)

    def sync(self, client, tab, **extra):
        return client.post(
            f"/tools/whiteboard/api/{self.board.pk}/sync/",
            data=json.dumps({"version": 0, "tab": tab, **extra}),
            content_type="application/json",
        )

    def test_the_laser_trail_reaches_the_others(self):
        self.sync(self.owner_client, "owner-tab-1", laser=[1.234, 2, 3, 4])

        peers = self.sync(self.member_client, "member-tab-1").json()["peers"]

        self.assertEqual(peers[0]["laser"], [1.2, 2.0, 3.0, 4.0])

    def test_broken_laser_trails_are_refused(self):
        for laser in ([1, 2, 3], ["x", 1], [True, 1], list(range(122))):
            with self.subTest(laser=laser):
                self.assertEqual(self.sync(self.owner_client, "owner-tab-1", laser=laser).status_code, 400)
