from django.contrib.auth.models import User
from django.test import TestCase

from app.models import Note, NoteFolder, NoteFolderShare, NoteShare, NoteUserState, UserNotification
from app.services.note_folder_shares import end_folder_share, set_folder_share_role, share_folder
from app.services.notes import (
    accessible_notes,
    create_note,
    create_note_folder,
    delete_note_folder,
    move_note_tree_item,
    set_note_folder,
    share_note,
)


class NoteFolderShareTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="lea@example.com", email="lea@example.com", password="pw"
        )
        self.member = User.objects.create_user(
            username="tom@example.com", email="tom@example.com", password="pw"
        )
        self.reader = User.objects.create_user(
            username="max@example.com", email="max@example.com", password="pw"
        )
        self.folder = create_note_folder(self.owner, name="Projekt")
        self.subfolder = create_note_folder(self.owner, name="Entwürfe", parent_id=self.folder.pk)
        self.plan = create_note(self.owner, title="Plan", folder_id=self.folder.pk)
        self.draft = create_note(self.owner, title="Entwurf", folder_id=self.subfolder.pk)
        self.private = create_note(self.owner, title="Privat")

    def visible(self, user):
        return set(accessible_notes(user).values_list("title", flat=True))

    def share(self, user, role=NoteShare.ROLE_READER):
        self.assertIsNone(share_folder(self.owner, self.folder.pk, user.email, role))
        return NoteFolderShare.objects.get(folder=self.folder, user=user)

    def test_sharing_a_folder_shares_its_notes_into_the_members_own_folder(self):
        share = self.share(self.member)

        self.assertEqual(self.visible(self.member), {"Plan", "Entwurf"})
        self.assertEqual(share.mirror.owner, self.member)
        self.assertEqual(share.mirror.name, "Projekt")
        self.assertEqual(
            set(NoteUserState.objects.filter(user=self.member).values_list("folder_id", flat=True)),
            {share.mirror_id},
        )
        self.assertEqual(
            set(NoteShare.objects.filter(user=self.member).values_list("role", "via_folder")),
            {(NoteShare.ROLE_READER, self.folder.pk)},
        )
        notification = UserNotification.objects.get(recipient=self.member)
        self.assertEqual(notification.kind, UserNotification.KIND_NOTE_FOLDER_SHARE)

    def test_notes_follow_the_owners_moves_in_and_out_of_the_folder(self):
        self.share(self.member)
        new = create_note(self.owner, title="Neu", folder_id=self.subfolder.pk)
        self.assertIn("Neu", self.visible(self.member))

        set_note_folder(self.owner, new.pk, None)
        self.assertNotIn("Neu", self.visible(self.member))
        self.assertFalse(NoteUserState.objects.filter(note=new, user=self.member).exists())

        move_note_tree_item(
            self.owner,
            item_type="note",
            item_id=self.private.pk,
            placement="inside",
            target_type="folder",
            target_id=self.folder.pk,
        )
        self.assertIn("Privat", self.visible(self.member))

        # A whole subfolder moving out takes its notes along.
        move_note_tree_item(self.owner, item_type="folder", item_id=self.subfolder.pk, placement="root")
        self.assertNotIn("Entwurf", self.visible(self.member))
        move_note_tree_item(
            self.owner,
            item_type="folder",
            item_id=self.subfolder.pk,
            placement="inside",
            target_type="folder",
            target_id=self.folder.pk,
        )
        self.assertIn("Entwurf", self.visible(self.member))

    def test_a_member_moving_a_shared_note_in_their_own_tree_changes_nothing(self):
        self.share(self.member)
        set_note_folder(self.member, self.plan.pk, None)
        self.assertIn("Plan", self.visible(self.member))

    def test_editors_notes_in_their_folder_reach_the_owner_and_other_members(self):
        editor_share = self.share(self.member, NoteShare.ROLE_EDITOR)
        reader_share = self.share(self.reader)

        idea = create_note(self.member, title="Idee", folder_id=editor_share.mirror_id)
        self.assertIn("Idee", self.visible(self.owner))
        self.assertIn("Idee", self.visible(self.reader))
        self.assertEqual(NoteShare.objects.get(note=idea, user=self.owner).role, NoteShare.ROLE_EDITOR)
        self.assertEqual(NoteUserState.objects.get(note=idea, user=self.owner).folder, self.folder)
        self.assertEqual(
            NoteUserState.objects.get(note=idea, user=self.reader).folder_id, reader_share.mirror_id
        )

        # A reader's own notes there stay private.
        create_note(self.reader, title="Notiz von Max", folder_id=reader_share.mirror_id)
        self.assertNotIn("Notiz von Max", self.visible(self.owner) | self.visible(self.member))

        # Losing the editor role takes the editor's notes out of the group again.
        set_folder_share_role(self.owner, editor_share.pk, NoteShare.ROLE_READER)
        self.assertNotIn("Idee", self.visible(self.owner))
        self.assertEqual(
            set(NoteShare.objects.filter(user=self.member).values_list("role", flat=True)),
            {NoteShare.ROLE_READER},
        )

    def test_shares_made_by_hand_are_left_alone(self):
        self.share(self.member)
        share_note(self.owner, self.plan.pk, self.member.pk, NoteShare.ROLE_EDITOR)
        set_note_folder(self.owner, self.plan.pk, None)
        self.assertEqual(NoteShare.objects.get(note=self.plan, user=self.member).role, NoteShare.ROLE_EDITOR)

        share_note(self.owner, self.private.pk, self.reader.pk, NoteShare.ROLE_READER)
        self.share(self.reader)
        end_folder_share(NoteFolderShare.objects.get(user=self.reader))
        self.assertEqual(self.visible(self.reader), {"Privat"})

    def test_leaving_removes_the_notes_and_an_empty_folder_but_keeps_own_notes(self):
        share = self.share(self.member, NoteShare.ROLE_EDITOR)
        mirror_id = share.mirror_id
        create_note(self.member, title="Idee", folder_id=mirror_id)

        self.client.force_login(self.member)
        response = self.client.post(f"/notes/folders/{mirror_id}/share/", {"form_name": "folder_leave"})
        self.assertRedirects(response, "/notes/")
        self.assertEqual(self.visible(self.member), {"Idee"})
        self.assertNotIn("Idee", self.visible(self.owner))
        self.assertTrue(NoteFolder.objects.filter(pk=mirror_id).exists())
        self.assertFalse(UserNotification.objects.filter(recipient=self.member).exists())

        reader_mirror = self.share(self.reader).mirror_id
        end_folder_share(NoteFolderShare.objects.get(user=self.reader))
        self.assertFalse(NoteFolder.objects.filter(pk=reader_mirror).exists())

    def test_deleting_the_folder_or_the_members_copy_ends_the_share(self):
        mirror_id = self.share(self.member).mirror_id
        delete_note_folder(self.member, mirror_id)
        self.assertEqual(self.visible(self.member), set())
        self.assertFalse(NoteFolderShare.objects.exists())

        self.share(self.reader)
        delete_note_folder(self.owner, self.folder.pk)
        self.assertEqual(self.visible(self.reader), set())
        self.assertFalse(NoteShare.objects.exists())
        self.assertEqual(Note.objects.filter(owner=self.owner).count(), 3)

    def test_mirror_names_are_unique_and_mirrors_cannot_be_shared_on(self):
        create_note_folder(self.member, name="Projekt")
        share = self.share(self.member)
        self.assertEqual(share.mirror.name, "Projekt (lea@example.com)")
        self.assertIsNotNone(
            share_folder(self.member, share.mirror_id, "max@example.com", NoteShare.ROLE_READER)
        )
        self.assertIsNotNone(
            share_folder(self.owner, self.folder.pk, "lea@example.com", NoteShare.ROLE_READER)
        )
        self.assertIsNotNone(
            share_folder(self.owner, self.folder.pk, "tom@example.com", NoteShare.ROLE_READER)
        )

    def test_share_page_and_tree_for_owner_member_and_stranger(self):
        self.client.force_login(self.owner)
        url = f"/notes/folders/{self.folder.pk}/share/"
        response = self.client.post(
            url, {"form_name": "folder_share", "identifier": "tom@example.com", "role": "editor"}
        )
        self.assertRedirects(response, url)
        self.assertContains(self.client.get(url), "tom@example.com")
        self.assertContains(self.client.get("/notes/"), "Geteilt mit 1 Person(en)")
        share = NoteFolderShare.objects.get()
        self.client.post(url, {"form_name": "folder_share_role", "share_id": share.pk, "role": "reader"})
        share.refresh_from_db()
        self.assertEqual(share.role, NoteShare.ROLE_READER)

        self.client.force_login(self.member)
        self.assertContains(self.client.get(f"/notes/folders/{share.mirror_id}/share/"), "Ordner verlassen")
        self.assertContains(self.client.get("/notes/"), "Geteilt von lea@example.com")
        self.assertEqual(self.client.get(url).status_code, 404)

        self.client.force_login(self.owner)
        self.client.post(url, {"form_name": "folder_unshare", "share_id": share.pk})
        self.assertFalse(NoteFolderShare.objects.exists())
        self.assertEqual(self.visible(self.member), set())
