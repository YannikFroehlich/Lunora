import json
import uuid
from datetime import timedelta
from tempfile import TemporaryDirectory

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from app.models import (
    Note,
    NoteActivityNotification,
    NoteAttachment,
    NoteCommentThread,
    NoteFolder,
    NoteLink,
    NoteShare,
    NoteTemplate,
    NoteUserState,
    NoteVersion,
    NoteViewerPresence,
    Profile,
    SystemSettings,
    UserNotification,
)
from app.services.note_content import NOTE_TEMPLATES, empty_note_document, validate_note_document
from app.services.notes import prune_note_versions, purge_expired_notes
from app.tests.helpers import (
    PNG_1X1_BYTES,
    note_document,
    postgres_connection,
)


class NotesTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner@example.com",
            email="owner@example.com",
            password="secret-12345",
            first_name="Owner",
        )
        self.reader = User.objects.create_user(
            username="reader@example.com",
            email="reader@example.com",
            password="secret-12345",
            first_name="Reader",
        )
        self.editor = User.objects.create_user(
            username="editor@example.com",
            email="editor@example.com",
            password="secret-12345",
            first_name="Editor",
        )
        Profile.objects.create(user=self.owner, display_name="Owner")
        Profile.objects.create(user=self.reader, display_name="Reader")
        Profile.objects.create(user=self.editor, display_name="Editor")
        self.client.login(username="owner@example.com", password="secret-12345")

    def create_note(self, title="Projektidee", client=None):
        response = (client or self.client).post(
            "/notes/api/create/",
            data=json.dumps({"title": title}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return Note.objects.get(pk=response.json()["note"]["id"])

    def create_folder(self, name="Projekte", parent=None, client=None):
        response = (client or self.client).post(
            "/notes/api/folders/",
            data=json.dumps({"name": name, "parent_id": parent.id if parent else None}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return NoteFolder.objects.get(pk=response.json()["folder"]["id"])

    def save_note(self, note, *, text="Erster Inhalt", revision=None, client=None):
        return (client or self.client).patch(
            f"/notes/api/{note.id}/",
            data=json.dumps(
                {
                    "title": note.title,
                    "document": note_document(text),
                    "base_revision": revision or note.revision,
                }
            ),
            content_type="application/json",
        )

    def test_notes_require_login_and_render_editor(self):
        self.client.logout()
        self.assertRedirects(self.client.get("/notes/"), "/login/?next=/notes/")
        self.client.login(username="owner@example.com", password="secret-12345")
        note = self.create_note()
        response = self.client.get(f"/notes/{note.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Textformatierung")
        self.assertContains(response, "Versionsverlauf")
        self.assertContains(response, "Projektidee")
        self.assertContains(response, "data-table-dialog-open")
        self.assertContains(response, "data-editor-context-menu")
        self.assertContains(response, "data-table-only")
        self.assertContains(response, f'data-note-card="{note.id}"')
        self.assertContains(response, "data-note-card-title")
        self.assertContains(response, "data-note-title", count=1)
        self.assertContains(response, "data-note-card-name")
        self.assertContains(response, 'class="note-list-card-link"')
        self.assertNotContains(response, "data-note-card-preview")
        self.assertNotContains(response, "data-note-card-updated")
        self.assertNotContains(response, "data-note-card-tags")
        self.assertContains(response, "data-note-context-menu")
        self.assertContains(response, "data-folder-context-menu")
        self.assertContains(response, 'data-note-context-action="rename"')
        self.assertContains(response, 'data-folder-context-action="create"')

    def test_notes_overview_does_not_automatically_open_first_note(self):
        note = self.create_note()
        response = self.client.get("/notes/")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["selected_note_data"])
        # Notes exist but none is selected, so the prompt is "pick one" rather
        # than "create your first note" — see test_notes_overview_empty_state_*.
        self.assertContains(response, "Wähle eine Notiz aus")
        self.assertContains(response, '<a class="notes-mobile-back" href="/notes/"', count=0)

        detail = self.client.get(f"/notes/{note.id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.context["selected_note_data"]["id"], note.id)
        self.assertContains(detail, '<a class="notes-mobile-back" href="/notes/"')

    def test_notes_overview_empty_state_invites_first_note_when_none_exist(self):
        response = self.client.get("/notes/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dein Platz für Gedanken")
        self.assertNotContains(response, "Wähle eine Notiz aus")

    def test_notes_overview_empty_state_reflects_the_current_filter_not_the_whole_account(self):
        self.create_note()

        response = self.client.get("/notes/?status=pinned")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["note_items"], [])
        # The account isn't empty, but nothing matches "Pins", so note_items is
        # empty here too — the main pane falls back to the create-a-note prompt.
        # The sidebar's own "Keine Notizen gefunden" card is what actually
        # communicates "no match" for this case.
        self.assertContains(response, "Dein Platz für Gedanken")

    def test_notes_filter_form_action_points_at_the_open_note_so_search_keeps_it_selected(self):
        note = self.create_note()

        response = self.client.get(f"/notes/{note.id}/?status=archived")

        self.assertContains(response, f'action="/notes/{note.id}/"')

    def test_notes_filter_form_action_points_at_the_overview_without_a_selected_note(self):
        response = self.client.get("/notes/")

        self.assertContains(response, 'action="/notes/"')

    def test_notes_filter_form_carries_a_hidden_status_field_for_implicit_submission(self):
        # Pressing Enter in the search field implicitly submits via the form's first
        # submit control. Without a hidden field holding the current status, that
        # control used to be the "Alle" tab button, silently resetting any other
        # active status filter whenever a search was submitted this way.
        response = self.client.get("/notes/?status=archived")

        self.assertContains(response, '<input type="hidden" name="status" value="archived">')

    def test_note_save_derives_plain_text_and_search(self):
        note = self.create_note()
        response = self.save_note(note, text="Mondlicht Planung")
        self.assertEqual(response.status_code, 200, response.content)
        saved_note = response.json()["note"]
        self.assertEqual(saved_note["preview"], "Mondlicht Planung")
        self.assertTrue(saved_note["updated_at"])
        note.refresh_from_db()
        self.assertEqual(note.plain_text, "Mondlicht Planung")
        self.assertEqual(note.revision, 2)
        response = self.client.get("/notes/?q=Mondlicht")
        self.assertContains(response, "Projektidee")

    def test_note_title_change_persists_when_note_is_reopened(self):
        note = self.create_note()
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps(
                {
                    "title": "Neuer dauerhafter Titel",
                    "document": note_document("Titeltest"),
                    "base_revision": note.revision,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["note"]["title"], "Neuer dauerhafter Titel")

        note.refresh_from_db()
        self.assertEqual(note.title, "Neuer dauerhafter Titel")
        reopened = self.client.get(f"/notes/{note.id}/")
        self.assertEqual(reopened.context["selected_note_data"]["title"], "Neuer dauerhafter Titel")
        self.assertContains(reopened, 'value="Neuer dauerhafter Titel"')

    def test_note_templates_pass_document_validation(self):
        for factory in NOTE_TEMPLATES.values():
            validate_note_document(factory())

    def test_create_note_with_template_populates_plain_text(self):
        response = self.client.post(
            "/notes/api/create/",
            data=json.dumps({"title": "Sprint-Meeting", "template": "meeting"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        note = Note.objects.get(pk=response.json()["note"]["id"])
        self.assertIn("Teilnehmer", note.plain_text)
        self.assertIn("Agenda", note.plain_text)
        self.assertIn("Aufgaben", note.plain_text)

    def test_create_note_with_unknown_template_is_rejected(self):
        response = self.client.post(
            "/notes/api/create/",
            data=json.dumps({"title": "Sprint-Meeting", "template": "does-not-exist"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_save_note_as_custom_template_strips_hazards_and_reuses_structure(self):
        note = self.create_note("Vorlagen-Quelle")
        note.document = {
            "type": "doc",
            "content": [
                {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Agenda"}]},
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "Wichtig",
                            "marks": [
                                {"type": "bold"},
                                {"type": "commentThread", "attrs": {"threadId": str(uuid.uuid4())}},
                            ],
                        },
                        {"type": "mention", "attrs": {"userId": self.editor.id, "label": "Editor"}},
                    ],
                },
                {
                    "type": "noteImage",
                    "attrs": {"attachmentId": str(uuid.uuid4()), "alt": "Bild", "width": 400},
                },
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {"type": "paragraph", "content": [{"type": "text", "text": "Punkt"}]}
                            ],
                        },
                    ],
                },
            ],
        }
        note.plain_text = "Agenda Wichtig Punkt"
        note.save(update_fields=["document", "plain_text"])

        response = self.client.post(
            "/notes/api/templates/",
            data=json.dumps({"note_id": note.id, "name": "Mein Meeting"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        template_id = response.json()["template"]["id"]
        self.assertEqual(response.json()["template"]["name"], "Mein Meeting")

        template = NoteTemplate.objects.get(pk=template_id, owner=self.owner)
        document_json = json.dumps(template.document)
        self.assertNotIn("noteImage", document_json)
        self.assertNotIn("mention", document_json)
        self.assertNotIn("commentThread", document_json)
        self.assertIn("@Editor", document_json)

        create_response = self.client.post(
            "/notes/api/create/",
            data=json.dumps({"title": "Aus Vorlage", "custom_template_id": template_id}),
            content_type="application/json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.content)
        created_note = Note.objects.get(pk=create_response.json()["note"]["id"])
        self.assertIn("Agenda", created_note.plain_text)
        self.assertIn("@Editor", created_note.plain_text)
        self.assertIn("Punkt", created_note.plain_text)

    def test_custom_template_name_must_be_unique_per_owner(self):
        note = self.create_note()
        first = self.client.post(
            "/notes/api/templates/",
            data=json.dumps({"note_id": note.id, "name": "Duplikat"}),
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 201, first.content)
        second = self.client.post(
            "/notes/api/templates/",
            data=json.dumps({"note_id": note.id, "name": "Duplikat"}),
            content_type="application/json",
        )
        self.assertEqual(second.status_code, 400)

    def test_custom_template_deletion_is_owner_scoped(self):
        note = self.create_note()
        response = self.client.post(
            "/notes/api/templates/",
            data=json.dumps({"note_id": note.id, "name": "Meine Vorlage"}),
            content_type="application/json",
        )
        template_id = response.json()["template"]["id"]
        editor_client = Client()
        editor_client.login(username="editor@example.com", password="secret-12345")
        denied = editor_client.delete(f"/notes/api/templates/{template_id}/")
        self.assertEqual(denied.status_code, 404)
        self.assertTrue(NoteTemplate.objects.filter(pk=template_id).exists())
        allowed = self.client.delete(f"/notes/api/templates/{template_id}/")
        self.assertEqual(allowed.status_code, 200)
        self.assertFalse(NoteTemplate.objects.filter(pk=template_id).exists())

    def test_create_note_with_missing_custom_template_returns_404(self):
        response = self.client.post(
            "/notes/api/create/",
            data=json.dumps({"title": "Fehlt", "custom_template_id": 999999}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_custom_template_limit_is_enforced(self):
        note = self.create_note()
        NoteTemplate.objects.bulk_create(
            [
                NoteTemplate(owner=self.owner, name=f"Vorlage {i}", document=empty_note_document())
                for i in range(30)
            ]
        )
        response = self.client.post(
            "/notes/api/templates/",
            data=json.dumps({"note_id": note.id, "name": "Zu viel"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_nested_folders_render_notes_in_the_sidebar_tree(self):
        projects = self.create_folder("Projekte")
        lunora = self.create_folder("Lunora", parent=projects)
        response = self.client.post(
            "/notes/api/create/",
            data=json.dumps({"title": "Ordnernotiz", "folder_id": lunora.id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        note = Note.objects.get(pk=response.json()["note"]["id"])
        self.assertEqual(response.json()["note"]["folder_id"], lunora.id)
        self.assertEqual(NoteUserState.objects.get(note=note, user=self.owner).folder, lunora)

        page = self.client.get(f"/notes/{note.id}/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f'data-note-folder="{projects.id}"')
        self.assertContains(page, f'data-note-folder="{lunora.id}"')
        self.assertContains(page, f'data-note-card="{note.id}"')
        self.assertContains(page, "Ordnernotiz")
        self.assertContains(page, "Neuer Unterordner")

    def test_folder_names_are_unique_within_the_same_parent(self):
        self.create_folder("Projekte")
        response = self.client.post(
            "/notes/api/folders/",
            data=json.dumps({"name": "projekte"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(NoteFolder.objects.filter(owner=self.owner).count(), 1)

    def test_shared_note_folder_assignment_is_personal(self):
        note = self.create_note()
        NoteShare.objects.create(note=note, user=self.reader, role=NoteShare.ROLE_READER)
        owner_folder = self.create_folder("Privat")
        reader_client = Client()
        reader_client.login(username="reader@example.com", password="secret-12345")
        reader_folder = self.create_folder("Geteilt", client=reader_client)

        response = reader_client.patch(
            f"/notes/api/{note.id}/folder/",
            data=json.dumps({"folder_id": reader_folder.id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["note"]["folder_id"], reader_folder.id)
        self.assertEqual(NoteUserState.objects.get(note=note, user=self.reader).folder, reader_folder)
        self.assertIsNone(NoteUserState.objects.get(note=note, user=self.owner).folder)

        denied = reader_client.patch(
            f"/notes/api/{note.id}/folder/",
            data=json.dumps({"folder_id": owner_folder.id}),
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 404)

    def test_deleting_folder_preserves_notes_and_reparents_children(self):
        parent = self.create_folder("Arbeit")
        child = self.create_folder("Lunora", parent=parent)
        note = self.create_note()
        state = NoteUserState.objects.get(note=note, user=self.owner)
        state.folder = parent
        state.save(update_fields=["folder"])

        response = self.client.delete(f"/notes/api/folders/{parent.id}/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(NoteFolder.objects.filter(pk=parent.id).exists())
        child.refresh_from_db()
        state.refresh_from_db()
        self.assertIsNone(child.parent)
        self.assertIsNone(state.folder)
        self.assertTrue(Note.objects.filter(pk=note.id).exists())

    def test_moving_and_duplicating_note_keeps_folder_context(self):
        folder = self.create_folder("Ideen")
        note = self.create_note()
        moved = self.client.patch(
            f"/notes/api/{note.id}/folder/",
            data=json.dumps({"folder_id": folder.id}),
            content_type="application/json",
        )
        self.assertEqual(moved.status_code, 200, moved.content)

        duplicate = self.client.post(
            f"/notes/api/{note.id}/actions/",
            data=json.dumps({"action": "duplicate"}),
            content_type="application/json",
        )
        self.assertEqual(duplicate.status_code, 201, duplicate.content)
        duplicate_note = Note.objects.get(pk=duplicate.json()["note"]["id"])
        self.assertEqual(NoteUserState.objects.get(note=duplicate_note, user=self.owner).folder, folder)

    def test_drag_move_reorders_notes_before_and_after_each_other(self):
        first = self.create_note("Erste")
        second = self.create_note("Zweite")
        third = self.create_note("Dritte")

        response = self.client.patch(
            "/notes/api/tree/move/",
            data=json.dumps(
                {
                    "item_type": "note",
                    "item_id": third.id,
                    "placement": "before",
                    "target_type": "note",
                    "target_id": first.id,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        ordered_ids = list(
            NoteUserState.objects.filter(user=self.owner, folder__isnull=True)
            .order_by("position")
            .values_list("note_id", flat=True)
        )
        self.assertEqual(ordered_ids, [third.id, first.id, second.id])

        response = self.client.patch(
            "/notes/api/tree/move/",
            data=json.dumps(
                {
                    "item_type": "note",
                    "item_id": third.id,
                    "placement": "after",
                    "target_type": "note",
                    "target_id": second.id,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        ordered_ids = list(
            NoteUserState.objects.filter(user=self.owner, folder__isnull=True)
            .order_by("position")
            .values_list("note_id", flat=True)
        )
        self.assertEqual(ordered_ids, [first.id, second.id, third.id])

    def test_drag_move_places_note_at_bottom_of_folder(self):
        folder = self.create_folder("Projekte")
        existing = self.create_note("Vorhanden")
        moved = self.create_note("Verschoben")
        existing_state = NoteUserState.objects.get(note=existing, user=self.owner)
        existing_state.folder = folder
        existing_state.position = 1000
        existing_state.save(update_fields=["folder", "position"])

        response = self.client.patch(
            "/notes/api/tree/move/",
            data=json.dumps(
                {
                    "item_type": "note",
                    "item_id": moved.id,
                    "placement": "inside",
                    "target_type": "folder",
                    "target_id": folder.id,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        moved_state = NoteUserState.objects.get(note=moved, user=self.owner)
        existing_state.refresh_from_db()
        self.assertEqual(moved_state.folder, folder)
        self.assertGreater(moved_state.position, existing_state.position)

    def test_drag_move_prevents_folder_cycles(self):
        parent = self.create_folder("Eltern")
        child = self.create_folder("Kind", parent=parent)

        response = self.client.patch(
            "/notes/api/tree/move/",
            data=json.dumps(
                {
                    "item_type": "folder",
                    "item_id": parent.id,
                    "placement": "inside",
                    "target_type": "folder",
                    "target_id": child.id,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        parent.refresh_from_db()
        child.refresh_from_db()
        self.assertIsNone(parent.parent)
        self.assertEqual(child.parent, parent)

    def test_drag_move_renders_persistent_tree_metadata(self):
        folder = self.create_folder("Ablage")
        note = self.create_note("Ziehbar")
        response = self.client.get(f"/notes/{note.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "custom")
        self.assertContains(response, "data-root-drop-target")
        self.assertContains(response, 'data-item-type="note"')
        self.assertContains(response, 'data-item-type="folder"')
        self.assertContains(response, f'data-item-id="{folder.id}"')
        self.assertContains(response, 'data-tree-draggable="true"')

    def test_stale_revision_returns_conflict_without_overwriting(self):
        note = self.create_note()
        first = self.save_note(note, text="Serverstand", revision=1)
        self.assertEqual(first.status_code, 200)
        stale = self.save_note(note, text="Veralteter Stand", revision=1)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"], "revision_conflict")
        note.refresh_from_db()
        self.assertEqual(note.plain_text, "Serverstand")

    def test_share_roles_and_personal_pin_are_enforced(self):
        note = self.create_note()
        NoteShare.objects.create(note=note, user=self.reader, role=NoteShare.ROLE_READER)
        NoteShare.objects.create(note=note, user=self.editor, role=NoteShare.ROLE_EDITOR)
        reader_client = Client()
        editor_client = Client()
        reader_client.login(username="reader@example.com", password="secret-12345")
        editor_client.login(username="editor@example.com", password="secret-12345")

        denied = self.save_note(note, client=reader_client)
        self.assertEqual(denied.status_code, 403)
        pin = reader_client.post(
            f"/notes/api/{note.id}/actions/",
            data=json.dumps({"action": "pin"}),
            content_type="application/json",
        )
        self.assertEqual(pin.status_code, 200)
        self.assertTrue(NoteUserState.objects.get(note=note, user=self.reader).is_pinned)
        self.assertFalse(NoteUserState.objects.get(note=note, user=self.owner).is_pinned)

        changed = self.save_note(note, text="Vom Bearbeiter", client=editor_client)
        self.assertEqual(changed.status_code, 200, changed.content)
        note.refresh_from_db()
        self.assertEqual(note.plain_text, "Vom Bearbeiter")

    def test_note_style_is_personal_and_validated(self):
        note = self.create_note()
        NoteShare.objects.create(note=note, user=self.reader, role=NoteShare.ROLE_READER)
        reader_client = Client()
        reader_client.login(username="reader@example.com", password="secret-12345")

        styled = reader_client.post(
            f"/notes/api/{note.id}/actions/",
            data=json.dumps({"action": "style", "color": "violet", "icon": "rocket"}),
            content_type="application/json",
        )
        self.assertEqual(styled.status_code, 200, styled.content)
        self.assertEqual(styled.json()["note"]["color"], "violet")
        self.assertEqual(styled.json()["note"]["icon"], "rocket")
        reader_state = NoteUserState.objects.get(note=note, user=self.reader)
        self.assertEqual(reader_state.color, "violet")
        self.assertEqual(reader_state.icon, "rocket")
        owner_state, _created = NoteUserState.objects.get_or_create(note=note, user=self.owner)
        self.assertEqual(owner_state.color, "")
        self.assertEqual(owner_state.icon, "")

        invalid = self.client.post(
            f"/notes/api/{note.id}/actions/",
            data=json.dumps({"action": "style", "color": "gold", "icon": ""}),
            content_type="application/json",
        )
        self.assertEqual(invalid.status_code, 400)

    def test_only_owner_can_manage_shares_and_trash(self):
        note = self.create_note()
        NoteShare.objects.create(note=note, user=self.editor, role=NoteShare.ROLE_EDITOR)
        editor_client = Client()
        editor_client.login(username="editor@example.com", password="secret-12345")
        denied = editor_client.post(
            f"/notes/api/{note.id}/actions/",
            data=json.dumps({"action": "trash"}),
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 403)
        denied = editor_client.post(
            f"/notes/api/{note.id}/shares/",
            data=json.dumps({"user_id": self.reader.id, "role": "reader"}),
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 403)

        trashed = self.client.post(
            f"/notes/api/{note.id}/actions/",
            data=json.dumps({"action": "trash"}),
            content_type="application/json",
        )
        self.assertEqual(trashed.status_code, 200)
        self.assertEqual(editor_client.get(f"/notes/api/{note.id}/").status_code, 404)
        restored = self.client.post(
            f"/notes/api/{note.id}/actions/",
            data=json.dumps({"action": "restore"}),
            content_type="application/json",
        )
        self.assertEqual(restored.status_code, 200)

    def bulk_action(self, note_ids, action, *, folder_id=None, client=None):
        payload = {"note_ids": note_ids, "action": action}
        if folder_id is not None or action == "move_folder":
            payload["folder_id"] = folder_id
        return (client or self.client).post(
            "/notes/api/bulk-action/", data=json.dumps(payload), content_type="application/json"
        )

    def test_bulk_action_pins_and_archives_multiple_notes(self):
        first = self.create_note("Erste Notiz")
        second = self.create_note("Zweite Notiz")

        response = self.bulk_action([first.id, second.id], "pin")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(sorted(response.json()["updated_ids"]), sorted([first.id, second.id]))
        self.assertEqual(response.json()["skipped_ids"], [])
        self.assertTrue(NoteUserState.objects.get(note=first, user=self.owner).is_pinned)
        self.assertTrue(NoteUserState.objects.get(note=second, user=self.owner).is_pinned)

        response = self.bulk_action([first.id, second.id], "archive")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(NoteUserState.objects.get(note=first, user=self.owner).is_archived)
        self.assertTrue(NoteUserState.objects.get(note=second, user=self.owner).is_archived)

    def test_bulk_action_skips_notes_the_user_is_not_allowed_to_trash(self):
        owned = self.create_note("Eigene Notiz")
        editor_client = Client()
        editor_client.login(username="editor@example.com", password="secret-12345")
        foreign_note = self.create_note("Fremde Notiz", client=editor_client)
        NoteShare.objects.create(note=foreign_note, user=self.owner, role=NoteShare.ROLE_EDITOR)

        response = self.bulk_action([owned.id, foreign_note.id], "trash")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["updated_ids"], [owned.id])
        self.assertEqual(response.json()["skipped_ids"], [foreign_note.id])
        owned.refresh_from_db()
        foreign_note.refresh_from_db()
        self.assertIsNotNone(owned.deleted_at)
        self.assertIsNone(foreign_note.deleted_at)

    def test_bulk_move_notes_to_folder(self):
        folder = self.create_folder("Archiv")
        first = self.create_note("Erste Notiz")
        second = self.create_note("Zweite Notiz")

        response = self.bulk_action([first.id, second.id], "move_folder", folder_id=folder.id)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(sorted(response.json()["updated_ids"]), sorted([first.id, second.id]))
        self.assertEqual(NoteUserState.objects.get(note=first, user=self.owner).folder, folder)
        self.assertEqual(NoteUserState.objects.get(note=second, user=self.owner).folder, folder)

        missing_folder = self.bulk_action([first.id], "move_folder", folder_id=999999)
        self.assertEqual(missing_folder.status_code, 404)

    def test_bulk_action_validates_note_ids_and_action(self):
        note = self.create_note()
        empty = self.bulk_action([], "pin")
        self.assertEqual(empty.status_code, 400)
        unknown_action = self.bulk_action([note.id], "levitate")
        self.assertEqual(unknown_action.status_code, 400)
        bad_id = self.client.post(
            "/notes/api/bulk-action/",
            data=json.dumps({"note_ids": ["not-a-number"], "action": "pin"}),
            content_type="application/json",
        )
        self.assertEqual(bad_id.status_code, 400)

    def test_version_history_and_restore_create_new_revision(self):
        note = self.create_note()
        saved = self.save_note(note, text="Neue Fassung")
        self.assertEqual(saved.status_code, 200)
        version = NoteVersion.objects.get(note=note)
        note.refresh_from_db()
        response = self.client.post(
            f"/notes/api/{note.id}/versions/{version.id}/restore/",
            data=json.dumps({"base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        note.refresh_from_db()
        self.assertEqual(note.plain_text, "")
        self.assertEqual(note.revision, 3)
        self.assertEqual(note.versions.count(), 2)

    def test_version_diff_reports_word_level_changes(self):
        note = self.create_note()
        saved = self.save_note(note, text="Erster Inhalt")
        self.assertEqual(saved.status_code, 200)
        version = NoteVersion.objects.get(note=note)
        response = self.client.get(f"/notes/api/{note.id}/versions/{version.id}/diff/")
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["added_words"], 2)
        self.assertEqual(payload["removed_words"], 0)
        non_empty_segments = [segment for segment in payload["segments"] if segment["text"].strip()]
        self.assertEqual(non_empty_segments, [{"type": "insert", "text": "Erster Inhalt"}])

    def test_version_diff_is_visible_to_read_only_share(self):
        note = self.create_note()
        self.save_note(note, text="Erster Inhalt")
        version = NoteVersion.objects.get(note=note)
        NoteShare.objects.create(note=note, user=self.reader, role=NoteShare.ROLE_READER)
        reader_client = Client()
        reader_client.login(username="reader@example.com", password="secret-12345")
        response = reader_client.get(f"/notes/api/{note.id}/versions/{version.id}/diff/")
        self.assertEqual(response.status_code, 200, response.content)

    def test_version_diff_missing_version_returns_404(self):
        note = self.create_note()
        response = self.client.get(f"/notes/api/{note.id}/versions/999999/diff/")
        self.assertEqual(response.status_code, 404)

    def test_version_history_is_limited_to_100_entries_and_90_days(self):
        note = self.create_note()
        for revision in range(105):
            NoteVersion.objects.create(
                note=note,
                created_by=self.owner,
                source_revision=revision + 1,
                title=f"Version {revision + 1}",
                document=note_document(str(revision + 1)),
            )
        oldest = note.versions.order_by("created_at").first()
        NoteVersion.objects.filter(pk=oldest.pk).update(created_at=timezone.now() - timedelta(days=91))
        prune_note_versions(note)
        self.assertEqual(note.versions.count(), 100)
        self.assertFalse(NoteVersion.objects.filter(pk=oldest.pk).exists())

    def test_tiptap_table_attributes_are_accepted(self):
        note = self.create_note()
        table_document = {
            "type": "doc",
            "content": [
                {
                    "type": "table",
                    "content": [
                        {
                            "type": "tableRow",
                            "content": [
                                {
                                    "type": "tableHeader",
                                    "attrs": {"colspan": 1, "rowspan": 1, "colwidth": None, "align": None},
                                    "content": [{"type": "paragraph", "attrs": {"textAlign": None}}],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": table_document, "base_revision": 1}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_tiptap_table_rejects_oversized_grid(self):
        note = self.create_note()
        table_document = {
            "type": "doc",
            "content": [
                {
                    "type": "table",
                    "content": [
                        {
                            "type": "tableRow",
                            "content": [
                                {
                                    "type": "tableCell",
                                    "attrs": {"colspan": 1, "rowspan": 1, "colwidth": None, "align": None},
                                    "content": [{"type": "paragraph", "attrs": {"textAlign": None}}],
                                }
                            ],
                        }
                        for _ in range(21)
                    ],
                }
            ],
        }
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": table_document, "base_revision": 1}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)

    def test_tiptap_table_rejects_invalid_cell_colspan(self):
        note = self.create_note()
        table_document = {
            "type": "doc",
            "content": [
                {
                    "type": "table",
                    "content": [
                        {
                            "type": "tableRow",
                            "content": [
                                {
                                    "type": "tableCell",
                                    "attrs": {"colspan": 21, "rowspan": 1, "colwidth": None, "align": None},
                                    "content": [{"type": "paragraph", "attrs": {"textAlign": None}}],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": table_document, "base_revision": 1}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)

    def test_tiptap_table_rejects_invalid_colwidth(self):
        note = self.create_note()
        table_document = {
            "type": "doc",
            "content": [
                {
                    "type": "table",
                    "content": [
                        {
                            "type": "tableRow",
                            "content": [
                                {
                                    "type": "tableCell",
                                    "attrs": {"colspan": 1, "rowspan": 1, "colwidth": [10], "align": None},
                                    "content": [{"type": "paragraph", "attrs": {"textAlign": None}}],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": table_document, "base_revision": 1}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)

    def test_private_image_upload_requires_note_access(self):
        note = self.create_note()
        with TemporaryDirectory() as private_root, override_settings(PRIVATE_MEDIA_ROOT=private_root):
            upload = SimpleUploadedFile("moon.png", PNG_1X1_BYTES, content_type="image/png")
            response = self.client.post(
                f"/notes/api/{note.id}/attachments/", {"kind": "image", "file": upload}
            )
            self.assertEqual(response.status_code, 201, response.content)
            attachment = NoteAttachment.objects.get(note=note)
            allowed = self.client.get(f"/notes/attachments/{attachment.file_id}/inline/")
            self.assertEqual(allowed.status_code, 200)
            allowed.close()
            stranger_client = Client()
            stranger_client.login(username="reader@example.com", password="secret-12345")
            self.assertEqual(
                stranger_client.get(f"/notes/attachments/{attachment.file_id}/inline/").status_code,
                404,
            )

    def test_invalid_editor_json_and_unsafe_links_are_rejected(self):
        note = self.create_note()
        unsafe = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "X",
                            "marks": [{"type": "link", "attrs": {"href": "javascript:alert(1)"}}],
                        }
                    ],
                }
            ],
        }
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": unsafe, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        note.refresh_from_db()
        self.assertEqual(note.revision, 1)

    def test_tiptap_link_attributes_are_accepted(self):
        note = self.create_note()
        linked_document = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "YouTube",
                            "marks": [
                                {
                                    "type": "link",
                                    "attrs": {
                                        "href": "youtube.com",
                                        "target": "_blank",
                                        "rel": "noopener noreferrer",
                                        "class": None,
                                        "title": None,
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": linked_document, "base_revision": 1}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        saved_document = response.json()["note"]["document"]
        self.assertEqual(
            saved_document["content"][0]["content"][0]["marks"][0]["attrs"]["href"], "https://youtube.com"
        )

    def test_pdf_export_preserves_access_control_and_returns_pdf(self):
        note = self.create_note("PDF Beispiel")
        note.document = {
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 1, "textAlign": "center"},
                    "content": [{"type": "text", "text": "Gestaltete Überschrift"}],
                },
                {
                    "type": "paragraph",
                    "attrs": {"textAlign": "justify"},
                    "content": [
                        {"type": "text", "text": "Fett", "marks": [{"type": "bold"}]},
                        {
                            "type": "text",
                            "text": " und farbig",
                            "marks": [
                                {
                                    "type": "textStyle",
                                    "attrs": {"color": "#a67c52", "fontSize": "18px", "lineHeight": "1.5"},
                                }
                            ],
                        },
                    ],
                },
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {"type": "paragraph", "content": [{"type": "text", "text": "Listenpunkt"}]}
                            ],
                        },
                    ],
                },
                {
                    "type": "table",
                    "content": [
                        {
                            "type": "tableRow",
                            "content": [
                                {
                                    "type": "tableHeader",
                                    "attrs": {"colspan": 1, "rowspan": 1},
                                    "content": [
                                        {"type": "paragraph", "content": [{"type": "text", "text": "Spalte"}]}
                                    ],
                                },
                                {
                                    "type": "tableHeader",
                                    "attrs": {"colspan": 1, "rowspan": 1},
                                    "content": [
                                        {"type": "paragraph", "content": [{"type": "text", "text": "Wert"}]}
                                    ],
                                },
                            ],
                        },
                        {
                            "type": "tableRow",
                            "content": [
                                {
                                    "type": "tableCell",
                                    "attrs": {"colspan": 1, "rowspan": 1},
                                    "content": [
                                        {"type": "paragraph", "content": [{"type": "text", "text": "A"}]}
                                    ],
                                },
                                {
                                    "type": "tableCell",
                                    "attrs": {"colspan": 1, "rowspan": 1},
                                    "content": [
                                        {"type": "paragraph", "content": [{"type": "text", "text": "1"}]}
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ],
        }
        note.plain_text = "Gestaltete Überschrift Fett und farbig Listenpunkt Spalte Wert A 1"
        note.save(update_fields=["document", "plain_text"])

        response = self.client.get(f"/notes/{note.id}/export/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(".pdf", response["Content-Disposition"])
        self.assertEqual(response["Cache-Control"], "private, no-store")
        pdf_bytes = b"".join(response.streaming_content)
        response.close()
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertIn(b"%%EOF", pdf_bytes[-1024:])
        self.assertGreater(len(pdf_bytes), 1500)

        NoteShare.objects.create(note=note, user=self.reader, role=NoteShare.ROLE_READER)
        reader_client = Client()
        reader_client.login(username="reader@example.com", password="secret-12345")
        reader_response = reader_client.get(f"/notes/{note.id}/export/pdf/")
        self.assertEqual(reader_response.status_code, 200)
        reader_response.close()

        stranger_client = Client()
        stranger_client.login(username="editor@example.com", password="secret-12345")
        self.assertEqual(stranger_client.get(f"/notes/{note.id}/export/pdf/").status_code, 404)
        self.assertRedirects(
            Client().get(f"/notes/{note.id}/export/pdf/"), f"/login/?next=/notes/{note.id}/export/pdf/"
        )

    def test_markdown_export_preserves_access_control_and_renders_structure(self):
        note = self.create_note("Markdown Beispiel")
        note.document = {
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 2},
                    "content": [{"type": "text", "text": "Überschrift"}],
                },
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Fett", "marks": [{"type": "bold"}]},
                        {"type": "text", "text": " und *sternchen*"},
                    ],
                },
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {"type": "paragraph", "content": [{"type": "text", "text": "Listenpunkt"}]}
                            ],
                        },
                    ],
                },
                {
                    "type": "codeBlock",
                    "attrs": {"language": "python"},
                    "content": [{"type": "text", "text": "print('hi')"}],
                },
                {
                    "type": "table",
                    "content": [
                        {
                            "type": "tableRow",
                            "content": [
                                {
                                    "type": "tableHeader",
                                    "content": [
                                        {"type": "paragraph", "content": [{"type": "text", "text": "Spalte"}]}
                                    ],
                                },
                                {
                                    "type": "tableHeader",
                                    "content": [
                                        {"type": "paragraph", "content": [{"type": "text", "text": "Wert"}]}
                                    ],
                                },
                            ],
                        },
                        {
                            "type": "tableRow",
                            "content": [
                                {
                                    "type": "tableCell",
                                    "content": [
                                        {"type": "paragraph", "content": [{"type": "text", "text": "A | B"}]}
                                    ],
                                },
                                {
                                    "type": "tableCell",
                                    "content": [
                                        {"type": "paragraph", "content": [{"type": "text", "text": "1"}]}
                                    ],
                                },
                            ],
                        },
                    ],
                },
            ],
        }
        note.plain_text = "Überschrift Fett und sternchen Listenpunkt print hi Spalte Wert A B 1"
        note.save(update_fields=["document", "plain_text"])

        response = self.client.get(f"/notes/{note.id}/export/markdown/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/markdown; charset=utf-8")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(".md", response["Content-Disposition"])
        self.assertEqual(response["Cache-Control"], "private, no-store")
        body = b"".join(response.streaming_content).decode("utf-8")
        response.close()

        self.assertIn("# Markdown Beispiel", body)
        self.assertIn("## Überschrift", body)
        self.assertIn("**Fett**", body)
        self.assertIn("\\*sternchen\\*", body)
        self.assertIn("- Listenpunkt", body)
        self.assertIn("```python", body)
        self.assertIn("print('hi')", body)
        self.assertIn("| Spalte | Wert |", body)
        self.assertIn("| --- | --- |", body)
        self.assertIn("A \\| B", body)

        NoteShare.objects.create(note=note, user=self.reader, role=NoteShare.ROLE_READER)
        reader_client = Client()
        reader_client.login(username="reader@example.com", password="secret-12345")
        reader_response = reader_client.get(f"/notes/{note.id}/export/markdown/")
        self.assertEqual(reader_response.status_code, 200)
        reader_response.close()

        stranger_client = Client()
        stranger_client.login(username="editor@example.com", password="secret-12345")
        self.assertEqual(stranger_client.get(f"/notes/{note.id}/export/markdown/").status_code, 404)
        self.assertRedirects(
            Client().get(f"/notes/{note.id}/export/markdown/"),
            f"/login/?next=/notes/{note.id}/export/markdown/",
        )

    def test_shortcut_conflicts_are_rejected_and_valid_overrides_persist(self):
        invalid = self.client.patch(
            "/notes/api/shortcuts/",
            data=json.dumps({"shortcuts": {"save": "Mod+S", "bold": "Mod+S"}}),
            content_type="application/json",
        )
        self.assertEqual(invalid.status_code, 400)
        valid = self.client.patch(
            "/notes/api/shortcuts/",
            data=json.dumps({"shortcuts": {"save": "Alt+S", "bold": ""}}),
            content_type="application/json",
        )
        self.assertEqual(valid.status_code, 200, valid.content)
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.note_shortcuts, {"save": "Alt+S", "bold": ""})

    def test_expired_trash_is_purged_with_private_files(self):
        note = self.create_note()
        note.deleted_at = timezone.now() - timedelta(days=31)
        note.save(update_fields=["deleted_at"])
        self.assertEqual(purge_expired_notes(), 1)
        self.assertFalse(Note.objects.filter(pk=note.id).exists())

    def test_disabled_notes_feature_blocks_page_and_api(self):
        note = self.create_note()
        self.save_note(note, text="Erster Inhalt")
        note.refresh_from_db()
        NoteShare.objects.create(note=note, user=self.reader, role=NoteShare.ROLE_READER)
        version = NoteVersion.objects.create(
            note=note,
            created_by=self.owner,
            source_revision=note.revision,
            title=note.title,
            document=note.document,
        )

        with TemporaryDirectory() as private_root, override_settings(PRIVATE_MEDIA_ROOT=private_root):
            upload = SimpleUploadedFile("moon.png", PNG_1X1_BYTES, content_type="image/png")
            attachment_response = self.client.post(
                f"/notes/api/{note.id}/attachments/", {"kind": "image", "file": upload}
            )
            self.assertEqual(attachment_response.status_code, 201, attachment_response.content)
            attachment = NoteAttachment.objects.get(note=note)

            SystemSettings.objects.update_or_create(pk=1, defaults={"notes_enabled": False})

            self.assertEqual(self.client.get("/notes/").status_code, 503)
            self.assertEqual(self.client.get(f"/notes/{note.id}/export/pdf/").status_code, 503)
            self.assertEqual(self.client.get(f"/notes/{note.id}/export/markdown/").status_code, 503)
            response = self.client.post("/notes/api/create/", data="{}", content_type="application/json")
            self.assertEqual(response.status_code, 503)
            self.assertFalse(response.json()["ok"])
            self.assertEqual(self.client.get(f"/notes/api/{note.id}/").status_code, 503)
            self.assertEqual(
                self.client.post(
                    f"/notes/api/{note.id}/actions/", data="{}", content_type="application/json"
                ).status_code,
                503,
            )
            self.assertEqual(
                self.client.post(
                    "/notes/api/bulk-action/",
                    data=json.dumps({"note_ids": [note.id], "action": "pin"}),
                    content_type="application/json",
                ).status_code,
                503,
            )
            self.assertEqual(
                self.client.post(
                    "/notes/api/templates/",
                    data=json.dumps({"note_id": note.id, "name": "Vorlage"}),
                    content_type="application/json",
                ).status_code,
                503,
            )
            self.assertEqual(self.client.get(f"/notes/api/{note.id}/shares/").status_code, 503)
            self.assertEqual(
                self.client.delete(f"/notes/api/{note.id}/shares/{self.reader.id}/").status_code, 503
            )
            self.assertEqual(self.client.get("/notes/api/share-candidates/?q=re").status_code, 503)
            self.assertEqual(
                self.client.post(
                    f"/notes/api/{note.id}/attachments/",
                    {
                        "kind": "image",
                        "file": SimpleUploadedFile("moon2.png", PNG_1X1_BYTES, content_type="image/png"),
                    },
                ).status_code,
                503,
            )
            self.assertEqual(
                self.client.get(f"/notes/attachments/{attachment.file_id}/inline/").status_code, 503
            )
            self.assertEqual(self.client.get(f"/notes/api/{note.id}/versions/").status_code, 503)
            self.assertEqual(
                self.client.post(
                    f"/notes/api/{note.id}/versions/{version.id}/restore/",
                    data=json.dumps({"base_revision": note.revision}),
                    content_type="application/json",
                ).status_code,
                503,
            )
            self.assertEqual(self.client.get("/notes/api/shortcuts/").status_code, 503)
            self.assertEqual(
                self.client.patch(
                    "/notes/api/shortcuts/",
                    data=json.dumps({"shortcuts": {}}),
                    content_type="application/json",
                ).status_code,
                503,
            )
            self.assertEqual(
                self.client.get(f"/notes/api/{note.id}/mention-candidates/?q=re").status_code, 503
            )
            self.assertEqual(self.client.get(f"/notes/api/{note.id}/comments/").status_code, 503)
            self.assertEqual(
                self.client.post(
                    f"/notes/api/{note.id}/comments/",
                    data=json.dumps({"thread_id": str(uuid.uuid4()), "body": "Hallo"}),
                    content_type="application/json",
                ).status_code,
                503,
            )
            self.assertEqual(
                self.client.post(
                    f"/notes/api/{note.id}/comments/{uuid.uuid4()}/",
                    data=json.dumps({"action": "reply", "body": "Hallo"}),
                    content_type="application/json",
                ).status_code,
                503,
            )

    def test_code_block_language_is_validated(self):
        note = self.create_note()
        document = {
            "type": "doc",
            "content": [
                {
                    "type": "codeBlock",
                    "attrs": {"language": "python"},
                    "content": [{"type": "text", "text": "print('hi')"}],
                }
            ],
        }
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

        note.refresh_from_db()
        document["content"][0]["attrs"]["language"] = "cobol"
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_math_nodes_are_validated_and_rendered_in_exports(self):
        note = self.create_note()
        document = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Satz des Pythagoras: "},
                        {"type": "mathInline", "attrs": {"latex": "a^2 + b^2 = c^2"}},
                    ],
                },
                {"type": "mathBlock", "attrs": {"latex": "\\frac{1}{2} g t^2"}},
            ],
        }
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        note.refresh_from_db()

        markdown_response = self.client.get(f"/notes/{note.id}/export/markdown/")
        self.assertEqual(markdown_response.status_code, 200)
        markdown_text = b"".join(markdown_response.streaming_content).decode("utf-8")
        markdown_response.close()
        self.assertIn("$a^2 + b^2 = c^2$", markdown_text)
        self.assertIn("$$\\frac{1}{2} g t^2$$", markdown_text)

        pdf_response = self.client.get(f"/notes/{note.id}/export/pdf/")
        self.assertEqual(pdf_response.status_code, 200)

        document["content"][1]["attrs"]["latex"] = ""
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

        note.refresh_from_db()
        document["content"][1]["attrs"]["latex"] = "x" * 4001
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_superscript_and_subscript_marks_are_validated_and_rendered_in_exports(self):
        note = self.create_note()
        document = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "x"},
                        {"type": "text", "text": "2", "marks": [{"type": "superscript"}]},
                        {"type": "text", "text": " und H"},
                        {"type": "text", "text": "2", "marks": [{"type": "subscript"}]},
                        {"type": "text", "text": "O"},
                    ],
                },
            ],
        }
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        note.refresh_from_db()

        markdown_response = self.client.get(f"/notes/{note.id}/export/markdown/")
        self.assertEqual(markdown_response.status_code, 200)
        markdown_text = b"".join(markdown_response.streaming_content).decode("utf-8")
        markdown_response.close()
        self.assertIn("<sup>2</sup>", markdown_text)
        self.assertIn("<sub>2</sub>", markdown_text)

        pdf_response = self.client.get(f"/notes/{note.id}/export/pdf/")
        self.assertEqual(pdf_response.status_code, 200)

        document["content"][0]["content"][1]["marks"] = [{"type": "superscript", "attrs": {"bogus": True}}]
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def mention_document(self, user, label, text="Hallo "):
        return {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": text},
                        {"type": "mention", "attrs": {"userId": user.id, "label": label}},
                    ],
                }
            ],
        }

    def test_mentioning_user_with_access_is_allowed_and_notifies(self):
        note = self.create_note()
        NoteShare.objects.create(note=note, user=self.reader, role=NoteShare.ROLE_READER)
        document = self.mention_document(self.reader, "Reader")

        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

        note.refresh_from_db()
        self.assertIn("@Reader", note.plain_text)
        notification = NoteActivityNotification.objects.get(note=note, recipient=self.reader)
        self.assertEqual(notification.kind, NoteActivityNotification.KIND_MENTION)
        self.assertEqual(notification.actor, self.owner)
        self.assertTrue(
            UserNotification.objects.filter(
                recipient=self.reader,
                source_key=f"note-activity:{notification.id}",
                kind=UserNotification.KIND_NOTE_MENTION,
            ).exists()
        )

        # Saving again without a new mention must not create a duplicate notification.
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(NoteActivityNotification.objects.filter(note=note, recipient=self.reader).count(), 1)
        self.assertEqual(UserNotification.objects.filter(recipient=self.reader).count(), 1)

    def test_mentioning_user_without_access_is_rejected(self):
        note = self.create_note()
        outsider = User.objects.create_user(
            username="outsider@example.com", email="outsider@example.com", password="secret-12345"
        )
        document = self.mention_document(outsider, "Outsider")

        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(NoteActivityNotification.objects.filter(note=note).exists())

    def test_mention_candidates_only_include_users_with_access(self):
        note = self.create_note()
        NoteShare.objects.create(note=note, user=self.reader, role=NoteShare.ROLE_READER)

        response = self.client.get(f"/notes/api/{note.id}/mention-candidates/")
        self.assertEqual(response.status_code, 200)
        names = {user["name"] for user in response.json()["users"]}
        self.assertEqual(names, {"Reader"})

    def test_comment_thread_full_lifecycle(self):
        note = self.create_note()
        NoteShare.objects.create(note=note, user=self.reader, role=NoteShare.ROLE_READER)
        thread_id = str(uuid.uuid4())

        response = self.client.post(
            f"/notes/api/{note.id}/comments/",
            data=json.dumps(
                {"thread_id": thread_id, "anchor_text": "Erster Inhalt", "body": "Was meinst du hiermit?"}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        threads = response.json()["threads"]
        self.assertEqual(len(threads), 1)
        self.assertEqual(threads[0]["comments"][0]["body"], "Was meinst du hiermit?")

        notification = NoteActivityNotification.objects.get(note=note, recipient=self.reader)
        self.assertEqual(notification.kind, NoteActivityNotification.KIND_COMMENT)

        document = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "Erster Inhalt",
                            "marks": [{"type": "commentThread", "attrs": {"threadId": thread_id}}],
                        }
                    ],
                }
            ],
        }
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

        self.client.logout()
        self.client.login(username="reader@example.com", password="secret-12345")
        response = self.client.post(
            f"/notes/api/{note.id}/comments/{thread_id}/",
            data=json.dumps({"action": "reply", "body": "Nur ein Hinweis."}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.json()["threads"][0]["comments"]), 2)
        self.assertTrue(
            NoteActivityNotification.objects.filter(
                note=note, recipient=self.owner, kind=NoteActivityNotification.KIND_COMMENT
            ).exists()
        )

        response = self.client.post(
            f"/notes/api/{note.id}/comments/{thread_id}/",
            data=json.dumps({"action": "resolve"}),
            content_type="application/json",
        )
        self.assertTrue(response.json()["threads"][0]["is_resolved"])

        response = self.client.delete(f"/notes/api/{note.id}/comments/{thread_id}/")
        self.assertEqual(response.status_code, 403)

        self.client.logout()
        self.client.login(username="owner@example.com", password="secret-12345")
        response = self.client.delete(f"/notes/api/{note.id}/comments/{thread_id}/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(NoteCommentThread.objects.filter(note=note).exists())

    def test_saving_document_with_unknown_comment_thread_is_rejected(self):
        note = self.create_note()
        document = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "Erster Inhalt",
                            "marks": [{"type": "commentThread", "attrs": {"threadId": str(uuid.uuid4())}}],
                        }
                    ],
                }
            ],
        }
        response = self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_duplicating_note_strips_comment_marks(self):
        note = self.create_note()
        thread_id = str(uuid.uuid4())
        self.client.post(
            f"/notes/api/{note.id}/comments/",
            data=json.dumps({"thread_id": thread_id, "anchor_text": "Erster Inhalt", "body": "Kommentar"}),
            content_type="application/json",
        )
        document = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "Erster Inhalt",
                            "marks": [{"type": "commentThread", "attrs": {"threadId": thread_id}}],
                        }
                    ],
                }
            ],
        }
        self.client.patch(
            f"/notes/api/{note.id}/",
            data=json.dumps({"title": note.title, "document": document, "base_revision": note.revision}),
            content_type="application/json",
        )

        response = self.client.post(
            f"/notes/api/{note.id}/actions/",
            data=json.dumps({"action": "duplicate"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        duplicate = Note.objects.get(pk=response.json()["note"]["id"])
        self.assertNotIn("commentThread", json.dumps(duplicate.document))

    def note_link_document(self, target_note, label, text="Siehe "):
        return {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": text},
                        {"type": "noteLink", "attrs": {"noteId": target_note.id, "label": label}},
                    ],
                }
            ],
        }

    def test_linking_to_accessible_note_creates_notelink_and_plain_text(self):
        source = self.create_note(title="Notiz A")
        target = self.create_note(title="Notiz B")
        document = self.note_link_document(target, "Notiz B")

        response = self.client.patch(
            f"/notes/api/{source.id}/",
            data=json.dumps({"title": source.title, "document": document, "base_revision": source.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

        source.refresh_from_db()
        self.assertIn("[[Notiz B]]", source.plain_text)
        self.assertTrue(NoteLink.objects.filter(source_note=source, target_note=target).exists())

        # Saving again without the link removes the stale NoteLink row.
        response = self.client.patch(
            f"/notes/api/{source.id}/",
            data=json.dumps(
                {
                    "title": source.title,
                    "document": note_document("Kein Link mehr"),
                    "base_revision": source.revision,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(NoteLink.objects.filter(source_note=source, target_note=target).exists())

    def test_linking_to_inaccessible_note_is_rejected(self):
        source = self.create_note(title="Notiz A")
        editor_client = Client()
        editor_client.login(username="editor@example.com", password="secret-12345")
        outsider_note = Note.objects.create(owner=self.editor, title="Fremde Notiz")
        document = self.note_link_document(outsider_note, "Fremde Notiz")

        response = self.client.patch(
            f"/notes/api/{source.id}/",
            data=json.dumps({"title": source.title, "document": document, "base_revision": source.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(NoteLink.objects.filter(source_note=source).exists())

    def test_backlinks_are_filtered_by_viewer_access(self):
        note_a = self.create_note(title="Privat A")
        note_b = self.create_note(title="Geteilt B")
        NoteShare.objects.create(note=note_b, user=self.reader, role=NoteShare.ROLE_READER)
        document = self.note_link_document(note_b, "Geteilt B")
        response = self.client.patch(
            f"/notes/api/{note_a.id}/",
            data=json.dumps({"title": note_a.title, "document": document, "base_revision": note_a.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

        reader_client = Client()
        reader_client.login(username="reader@example.com", password="secret-12345")
        response = reader_client.get(f"/notes/api/{note_b.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["note"]["backlinks"], [])

        NoteShare.objects.create(note=note_a, user=self.reader, role=NoteShare.ROLE_READER)
        response = reader_client.get(f"/notes/api/{note_b.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["note"]["backlinks"], [{"id": note_a.id, "title": "Privat A"}])

    def test_link_candidates_only_include_accessible_notes(self):
        source = self.create_note(title="Notiz A")
        own_other = self.create_note(title="Notiz B")
        outsider_note = Note.objects.create(owner=self.editor, title="Fremde Notiz")

        response = self.client.get(f"/notes/api/{source.id}/link-candidates/")
        self.assertEqual(response.status_code, 200)
        candidates = response.json()["notes"]
        self.assertEqual({item["title"] for item in candidates}, {"Notiz B"})
        ids = {item["id"] for item in candidates}
        self.assertIn(own_other.id, ids)
        self.assertNotIn(source.id, ids)
        self.assertNotIn(outsider_note.id, ids)

    def test_duplicating_note_carries_forward_note_links(self):
        source = self.create_note(title="Notiz A")
        target = self.create_note(title="Notiz B")
        document = self.note_link_document(target, "Notiz B")
        self.client.patch(
            f"/notes/api/{source.id}/",
            data=json.dumps({"title": source.title, "document": document, "base_revision": source.revision}),
            content_type="application/json",
        )

        response = self.client.post(
            f"/notes/api/{source.id}/actions/",
            data=json.dumps({"action": "duplicate"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        duplicate = Note.objects.get(pk=response.json()["note"]["id"])
        self.assertTrue(NoteLink.objects.filter(source_note=duplicate, target_note=target).exists())

    def test_restoring_version_resyncs_note_links(self):
        source = self.create_note(title="Notiz A")
        target = self.create_note(title="Notiz B")
        document = self.note_link_document(target, "Notiz B")
        self.client.patch(
            f"/notes/api/{source.id}/",
            data=json.dumps({"title": source.title, "document": document, "base_revision": source.revision}),
            content_type="application/json",
        )
        source.refresh_from_db()
        self.assertTrue(NoteLink.objects.filter(source_note=source, target_note=target).exists())

        version = NoteVersion.objects.get(note=source)
        response = self.client.post(
            f"/notes/api/{source.id}/versions/{version.id}/restore/",
            data=json.dumps({"base_revision": source.revision}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(NoteLink.objects.filter(source_note=source, target_note=target).exists())


class NoteLockSqlTests(SimpleTestCase):
    """Locked note lookups must compile to SQL PostgreSQL accepts (SQLite ignores FOR UPDATE)."""

    def test_locked_lookup_has_no_distinct_and_locks_only_the_note_row(self):
        from app.services.notes import _accessible_note_queryset

        queryset = _accessible_note_queryset(User(pk=1, username="mira@example.com"), 7, for_update=True)
        sql, _params = queryset.query.get_compiler(connection=postgres_connection()).as_sql()

        # PostgreSQL rejects FOR UPDATE with DISTINCT and on the nullable side of an outer join.
        self.assertNotIn("DISTINCT", sql)
        self.assertIn('FOR UPDATE OF "app_note"', sql)


class NotePresenceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="presence-owner@example.com",
            email="presence-owner@example.com",
            password="secret-12345",
            first_name="Owner",
        )
        self.reader = User.objects.create_user(
            username="presence-reader@example.com",
            email="presence-reader@example.com",
            password="secret-12345",
            first_name="Reader",
        )
        self.outsider = User.objects.create_user(
            username="presence-outsider@example.com",
            email="presence-outsider@example.com",
            password="secret-12345",
            first_name="Outsider",
        )
        Profile.objects.create(user=self.owner, display_name="Owner")
        Profile.objects.create(user=self.reader, display_name="Reader")
        Profile.objects.create(user=self.outsider, display_name="Outsider")
        self.note = Note.objects.create(owner=self.owner, title="Geteilte Notiz")
        NoteShare.objects.create(note=self.note, user=self.reader, role=NoteShare.ROLE_READER)
        self.client.login(username="presence-owner@example.com", password="secret-12345")
        self.reader_client = Client()
        self.reader_client.login(username="presence-reader@example.com", password="secret-12345")

    def presence_url(self, *, leave=False):
        url = f"/notes/api/{self.note.id}/presence/"
        return f"{url}?leave=1" if leave else url

    def test_ping_returns_other_active_viewers_only(self):
        response = self.client.post(self.presence_url())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["viewers"], [])
        self.assertTrue(NoteViewerPresence.objects.filter(note=self.note, user=self.owner).exists())

        # Owner is already present, so the reader's own ping must show the owner
        # but never itself.
        response = self.reader_client.post(self.presence_url())
        self.assertEqual(response.status_code, 200, response.content)
        viewers = response.json()["viewers"]
        self.assertEqual(len(viewers), 1)
        self.assertEqual(viewers[0]["user_id"], self.owner.id)
        self.assertEqual(viewers[0]["name"], "Owner")

        response = self.client.post(self.presence_url())
        viewers = response.json()["viewers"]
        self.assertEqual(len(viewers), 1)
        self.assertEqual(viewers[0]["user_id"], self.reader.id)
        self.assertEqual(viewers[0]["name"], "Reader")

    def test_ping_reports_current_note_revision(self):
        response = self.client.post(self.presence_url())
        self.assertEqual(response.json()["revision"], self.note.revision)

        save_response = self.client.patch(
            f"/notes/api/{self.note.id}/",
            data=json.dumps(
                {
                    "title": self.note.title,
                    "document": note_document("Neuer Inhalt"),
                    "base_revision": self.note.revision,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(save_response.status_code, 200, save_response.content)
        new_revision = save_response.json()["note"]["revision"]
        self.assertGreater(new_revision, self.note.revision)

        response = self.reader_client.post(self.presence_url())
        self.assertEqual(response.json()["revision"], new_revision)

    def test_leave_removes_presence_immediately(self):
        self.client.post(self.presence_url())
        self.reader_client.post(self.presence_url())
        self.assertEqual(NoteViewerPresence.objects.filter(note=self.note).count(), 2)

        response = self.client.post(self.presence_url(leave=True))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(NoteViewerPresence.objects.filter(note=self.note, user=self.owner).exists())

        viewers = self.reader_client.post(self.presence_url()).json()["viewers"]
        self.assertEqual(viewers, [])

    def test_expired_presence_is_excluded_and_cleaned_up(self):
        NoteViewerPresence.objects.create(
            note=self.note, user=self.reader, present_until=timezone.now() - timedelta(seconds=5)
        )

        response = self.client.post(self.presence_url())
        self.assertEqual(response.json()["viewers"], [])
        self.assertFalse(NoteViewerPresence.objects.filter(note=self.note, user=self.reader).exists())

    def test_ping_requires_note_access(self):
        outsider_client = Client()
        outsider_client.login(username="presence-outsider@example.com", password="secret-12345")

        response = outsider_client.post(self.presence_url())

        self.assertEqual(response.status_code, 404)
        self.assertFalse(NoteViewerPresence.objects.filter(note=self.note, user=self.outsider).exists())

    def test_revoked_access_excludes_stale_row_from_viewer_list(self):
        self.reader_client.post(self.presence_url())
        NoteShare.objects.filter(note=self.note, user=self.reader).delete()

        response = self.client.post(self.presence_url())

        self.assertEqual(response.json()["viewers"], [])

    def test_editor_cannot_trash_note(self):
        editor = User.objects.create_user(
            username="presence-editor@example.com",
            email="presence-editor@example.com",
            password="secret-12345",
            first_name="Editor",
        )
        Profile.objects.create(user=editor, display_name="Editor")
        NoteShare.objects.create(note=self.note, user=editor, role=NoteShare.ROLE_EDITOR)
        editor_client = Client()
        editor_client.login(username="presence-editor@example.com", password="secret-12345")

        response = editor_client.post(
            f"/notes/api/{self.note.id}/actions/",
            data=json.dumps({"action": "trash"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.note.refresh_from_db()
        self.assertIsNone(self.note.deleted_at)

    def test_ping_returns_404_for_reader_after_note_is_trashed(self):
        trash_response = self.client.post(
            f"/notes/api/{self.note.id}/actions/",
            data=json.dumps({"action": "trash"}),
            content_type="application/json",
        )
        self.assertEqual(trash_response.status_code, 200, trash_response.content)

        response = self.reader_client.post(self.presence_url())

        self.assertEqual(response.status_code, 404)

    def test_ping_still_works_for_owner_after_trashing_own_note(self):
        self.client.post(
            f"/notes/api/{self.note.id}/actions/",
            data=json.dumps({"action": "trash"}),
            content_type="application/json",
        )

        response = self.client.post(self.presence_url())

        self.assertEqual(response.status_code, 200, response.content)

    def test_ping_returns_404_after_note_is_permanently_deleted(self):
        purge_response = self.client.post(
            f"/notes/api/{self.note.id}/actions/",
            data=json.dumps({"action": "purge"}),
            content_type="application/json",
        )
        self.assertEqual(purge_response.status_code, 200, purge_response.content)

        response = self.reader_client.post(self.presence_url())

        self.assertEqual(response.status_code, 404)

    def test_ping_returns_404_for_reader_after_share_is_removed(self):
        NoteShare.objects.filter(note=self.note, user=self.reader).delete()

        response = self.reader_client.post(self.presence_url())

        self.assertEqual(response.status_code, 404)

    def test_presence_disabled_when_notes_feature_off(self):
        SystemSettings.objects.create(notes_enabled=False)

        response = self.client.post(self.presence_url())

        self.assertEqual(response.status_code, 503)
