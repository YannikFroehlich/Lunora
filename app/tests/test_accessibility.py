"""Markup-level accessibility guards for the main pages.

Not a replacement for a real audit (axe-core in a browser was used for that), but these are the
regressions that are easy to reintroduce in templates: a second <main>, a page without <h1>, ids
that collide between forms on the same page (which sends <label for> to the wrong field), and form
fields without any accessible name.
"""

from datetime import date
from decimal import Decimal
from html.parser import HTMLParser

from django.contrib.auth.models import User
from django.test import TestCase

from app.models import (
    CalendarEvent,
    CalendarEventAttendee,
    CalendarEventComment,
    Conversation,
    ConversationMember,
    ExpenseGroupMember,
    Habit,
    MealPlanEntry,
    Note,
    Profile,
    Recipe,
    ScheduledChatMessage,
    SystemSettings,
    Task,
    UmlDiagram,
    VocabularyCard,
    VocabularyList,
)
from app.services.chat import create_poll
from app.services.expenses import add_expense, create_group
from app.services.uml_content import empty_uml_document

UNNAMED_INPUT_TYPES = {"hidden", "submit", "button", "reset", "image"}


class _MarkupCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = []
        self.label_targets = set()
        self.fields = []
        self.main_count = 0
        self.h1_count = 0
        self.soft_regions_without_id = []
        self._label_depth = 0
        self._template_depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "template":
            self._template_depth += 1
        if self._template_depth:
            return
        if attributes.get("id"):
            self.ids.append(attributes["id"])
        # base.js swaps soft regions by id, so one without an id would silently never update.
        if "data-soft-region" in attributes and not attributes.get("id"):
            self.soft_regions_without_id.append(attributes.get("class") or tag)
        if tag == "main" or attributes.get("role") == "main":
            self.main_count += 1
        if tag == "h1":
            self.h1_count += 1
        if tag == "label":
            self._label_depth += 1
            if attributes.get("for"):
                self.label_targets.add(attributes["for"])
        if tag in {"input", "select", "textarea"}:
            if tag == "input" and (attributes.get("type") or "text") in UNNAMED_INPUT_TYPES:
                return
            self.fields.append(
                {
                    "tag": tag,
                    "name": attributes.get("name") or attributes.get("data-format") or "",
                    "id": attributes.get("id"),
                    "named": bool(
                        attributes.get("aria-label")
                        or attributes.get("aria-labelledby")
                        or attributes.get("title")
                        or self._label_depth
                    ),
                }
            )

    def handle_endtag(self, tag):
        if tag == "template" and self._template_depth:
            self._template_depth -= 1
        elif tag == "label" and self._label_depth and not self._template_depth:
            self._label_depth -= 1


class PageAccessibilityMarkupTests(TestCase):
    def setUp(self):
        self.mira = User.objects.create_user(
            username="mira@example.com",
            email="mira@example.com",
            password="secret-12345",
            is_staff=True,
            is_superuser=True,
        )
        self.lukas = User.objects.create_user(
            username="lukas@example.com", email="lukas@example.com", password="secret-12345"
        )
        Profile.objects.create(user=self.mira, display_name="Mira")
        Profile.objects.create(user=self.lukas, display_name="Lukas")
        settings, _created = SystemSettings.objects.get_or_create(pk=1)
        for field in SystemSettings._meta.fields:
            if field.name.endswith("_enabled"):
                setattr(settings, field.name, True)
        settings.save()

        self.note = Note.objects.create(owner=self.mira, title="Packliste")
        self.conversation = Conversation.objects.create(created_by=self.mira)
        ConversationMember.objects.create(conversation=self.conversation, user=self.mira)
        ConversationMember.objects.create(conversation=self.conversation, user=self.lukas)
        self.conversation.messages.create(sender=self.lukas, body="Hallo")
        # A group chat with a poll and a scheduled message renders the poll dialog, the "+" menu
        # and the scheduled list on top of the direct chat's controls.
        self.group = Conversation.objects.create(created_by=self.mira, title="Team", is_group=True)
        ConversationMember.objects.create(conversation=self.group, user=self.mira)
        ConversationMember.objects.create(conversation=self.group, user=self.lukas)
        create_poll(self.group, self.lukas, "Pizza oder Pasta?", ["Pizza", "Pasta"])
        ScheduledChatMessage.objects.create(
            conversation=self.group, sender=self.mira, body="Später", send_at="2099-03-10T10:00:00+01:00"
        )
        Task.objects.create(user=self.mira, title="Bericht")
        self.event = CalendarEvent.objects.create(
            user=self.mira,
            title="Zahnarzt",
            start_at="2099-03-10T10:00:00+01:00",
            end_at="2099-03-10T11:00:00+01:00",
        )
        # An invitee and a comment, so the detail page renders its RSVP-free thread and form.
        CalendarEventAttendee.objects.create(event=self.event, user=self.lukas, invited_by=self.mira)
        CalendarEventComment.objects.create(event=self.event, author=self.lukas, body="Ich komme mit.")
        Habit.objects.create(owner=self.mira, title="Lesen")
        self.recipe = Recipe.objects.create(
            owner=self.mira,
            title="Pfannkuchen",
            ingredients="Teig:\n200 g Mehl\n2 Eier",
            instructions="Verrühren.",
        )
        self.diagram = UmlDiagram.objects.create(
            owner=self.mira, title="Klassen", document=empty_uml_document()
        )
        self.vocabulary = VocabularyList.objects.create(owner=self.mira, title="Englisch")
        VocabularyCard.objects.create(vocabulary_list=self.vocabulary, term="Haus", translation="house")
        MealPlanEntry.objects.create(owner=self.mira, recipe=self.recipe, date=date(2099, 3, 10))
        self.expense_group, _error = create_group(self.mira, "WG")
        ExpenseGroupMember.objects.create(group=self.expense_group, user=self.lukas)
        add_expense(
            self.expense_group,
            self.mira,
            title="Einkauf",
            amount=Decimal("20.00"),
            paid_by=self.mira,
            participants=[self.mira, self.lukas],
            spent_on=date(2099, 3, 10),
        )
        self.client.login(username="mira@example.com", password="secret-12345")

    def pages(self):
        return [
            "/home/",
            "/calendar/?view=month&year=2099&month=3",
            "/calendar/?view=week&date=2099-03-10",
            "/calendar/?view=year&year=2099",
            "/calendar/?view=agenda&date=2099-03-09",
            f"/calendar/events/{self.event.pk}/",
            "/tasks/",
            "/tasks/time/",
            "/trash/",
            "/notes/",
            f"/notes/{self.note.pk}/",
            "/messages/",
            f"/messages/{self.conversation.pk}/",
            f"/messages/{self.group.pk}/",
            "/weather/",
            "/settings/",
            "/notifications/",
            "/search/?q=Pack",
            "/tools/",
            "/tools/habits/",
            "/tools/shopping/",
            "/tools/recipes/",
            f"/tools/recipes/{self.recipe.pk}/",
            "/tools/recipes/?week=2099-W11",
            "/tools/expenses/",
            f"/tools/expenses/{self.expense_group.pk}/",
            "/tools/focus/",
            "/tools/uml/",
            f"/tools/uml/{self.diagram.pk}/",
            "/tools/vocabulary/",
            f"/tools/vocabulary/{self.vocabulary.pk}/",
            f"/tools/vocabulary/{self.vocabulary.pk}/practice/",
            "/vacation-planner/",
            "/administration/",
        ]

    def collect(self, url, client=None):
        response = (client or self.client).get(url)
        self.assertEqual(response.status_code, 200, url)
        collector = _MarkupCollector()
        collector.feed(response.content.decode())
        return collector

    def assert_page_markup(self, url, collector):
        with self.subTest(url=url):
            self.assertEqual(collector.main_count, 1, f"{url}: exactly one <main> landmark")
            self.assertGreaterEqual(collector.h1_count, 1, f"{url}: a level-one heading")
            duplicates = sorted({value for value in collector.ids if collector.ids.count(value) > 1})
            self.assertEqual(duplicates, [], f"{url}: duplicate ids")
            self.assertEqual(collector.soft_regions_without_id, [], f"{url}: soft regions without an id")
            unnamed = [
                f"<{field['tag']} name={field['name']!r} id={field['id']!r}>"
                for field in collector.fields
                if not field["named"] and field["id"] not in collector.label_targets
            ]
            self.assertEqual(unnamed, [], f"{url}: form fields without an accessible name")

    def test_signed_in_pages(self):
        for url in self.pages():
            self.assert_page_markup(url, self.collect(url))

    def test_public_pages(self):
        self.client.logout()
        self.note.public_token = "public-token-123"
        self.note.save(update_fields=["public_token"])
        for url in ["/login/", "/register/", "/password-reset/", "/s/notes/public-token-123/", "/offline/"]:
            self.assert_page_markup(url, self.collect(url))
