"""Contracts & subscriptions, and Lunora Wrapped."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, SimpleTestCase, TestCase
from django.utils import timezone

from app.models import Contract, Habit, HabitCheck, Note, Task, TaskTimeEntry, UserNotification
from app.services.contracts import add_months, contract_status, end_date, monthly_cost, next_deadline
from app.services.notifications import materialize_due_contract_reminders
from app.services.user_preferences import localtime_for_user
from app.services.wrapped import year_in_review


def contract(**fields):
    defaults = {
        "name": "Handy",
        "amount": Decimal("20"),
        "interval": Contract.INTERVAL_MONTHLY,
        "start_date": date(2025, 1, 15),
        "term_months": 24,
        "renewal_months": 12,
        "notice_days": 90,
        "remind_days": 14,
    }
    return Contract(**{**defaults, **fields})


class ContractMathTests(SimpleTestCase):
    def test_deadline_moves_to_the_next_term_once_it_has_passed(self):
        item = contract()

        self.assertEqual(next_deadline(item, date(2026, 9, 1)).cancel_by, date(2026, 10, 17))
        later = next_deadline(item, date(2026, 11, 21))
        self.assertEqual((later.cancel_by, later.ends_on), (date(2027, 10, 17), date(2028, 1, 15)))

    def test_without_a_minimum_term_there_is_no_deadline(self):
        item = contract(term_months=None, notice_days=30)

        self.assertIsNone(next_deadline(item, date(2026, 11, 21)))
        self.assertEqual(
            contract_status(item, date(2026, 11, 21)), ("Jederzeit kündbar mit 30 Tagen Frist", "")
        )

    def test_a_cancelled_contract_ends_at_the_term_it_could_still_be_cancelled_for(self):
        self.assertEqual(end_date(contract(cancelled_on=date(2026, 9, 1))), date(2027, 1, 15))
        self.assertEqual(end_date(contract(cancelled_on=date(2026, 11, 1))), date(2028, 1, 15))
        self.assertEqual(
            end_date(contract(term_months=None, notice_days=30, cancelled_on=date(2026, 11, 1))),
            date(2026, 12, 1),
        )
        self.assertIsNone(end_date(contract()))

    def test_status_warns_inside_the_reminder_window(self):
        status, tone = contract_status(contract(), date(2026, 10, 10))

        self.assertEqual(tone, "warning")
        self.assertIn("Kündbar bis 17.10.2026 (noch 7 Tage)", status)
        self.assertIn("verlängert bis 15.01.2028", status)

    def test_monthly_cost_and_month_arithmetic(self):
        self.assertEqual(
            monthly_cost(contract(amount=Decimal("120"), interval=Contract.INTERVAL_YEARLY)), Decimal("10.00")
        )
        self.assertEqual(
            monthly_cost(contract(amount=Decimal("3"), interval=Contract.INTERVAL_WEEKLY)), Decimal("13.00")
        )
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2026, 11, 30), 3), date(2027, 2, 28))


class ContractPageTests(TestCase):
    def setUp(self):
        self.lea = User.objects.create_user(
            username="lea@example.com", email="lea@example.com", password="pw"
        )
        self.tom = User.objects.create_user(
            username="tom@example.com", email="tom@example.com", password="pw"
        )
        self.client.force_login(self.lea)

    def save(self, **fields):
        data = {
            "form_name": "contract_save",
            "name": "Streaming",
            "provider": "",
            "category": "streaming",
            "amount": "12,99",
            "interval": "monthly",
            "start_date": "2026-01-01",
            "term_months": "",
            "renewal_months": "1",
            "notice_days": "30",
            "remind_days": "14",
            "notes": "",
            **fields,
        }
        return self.client.post("/tools/contracts/", data, follow=True)

    def test_add_edit_cancel_and_delete(self):
        response = self.save()
        item = Contract.objects.get(owner=self.lea)
        self.assertEqual(item.amount, Decimal("12.99"))
        self.assertContains(response, "Monatlich <strong>12,99 €</strong>", html=False)
        self.assertContains(response, "Streaming &amp; Medien")

        self.save(contract_id=item.pk, name="Musik", amount="120", interval="yearly")
        item.refresh_from_db()
        self.assertEqual((item.name, item.interval), ("Musik", "yearly"))
        self.assertContains(self.client.get("/tools/contracts/"), "= 10,00 € pro Monat")

        response = self.client.post(
            "/tools/contracts/", {"form_name": "contract_cancel", "contract_id": item.pk}, follow=True
        )
        item.refresh_from_db()
        self.assertIsNotNone(item.cancelled_on)
        self.assertContains(response, "Als gekündigt markiert")

        self.client.post("/tools/contracts/", {"form_name": "contract_delete", "contract_id": item.pk})
        self.assertFalse(Contract.objects.exists())

    def test_invalid_input_and_foreign_contracts(self):
        response = self.save(amount="viel")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Contract.objects.exists())

        foreign = contract(owner=self.tom)
        foreign.save()
        self.client.post("/tools/contracts/", {"form_name": "contract_delete", "contract_id": foreign.pk})
        self.save(contract_id=foreign.pk, name="Gekapert")
        foreign.refresh_from_db()
        self.assertEqual(foreign.name, "Handy")
        # A foreign id on save creates a new own contract instead of touching the other one.
        self.assertEqual(Contract.objects.filter(owner=self.lea).count(), 1)

    def test_reminder_comes_once_per_deadline(self):
        item = contract(owner=self.lea)
        item.save()
        Contract.objects.create(
            owner=self.lea, name="Weit weg", amount=1, start_date=date(2026, 1, 1), term_months=60
        )
        cancelled = contract(owner=self.lea, name="Gekündigt", cancelled_on=date(2026, 1, 1))
        cancelled.save()
        now = timezone.make_aware(datetime(2026, 10, 10, 12))

        self.assertEqual(materialize_due_contract_reminders(now=now), 1)
        self.assertEqual(materialize_due_contract_reminders(now=now), 0)

        row = UserNotification.objects.get(kind=UserNotification.KIND_CONTRACT_DEADLINE)
        self.assertEqual(row.source_key, f"contract:{item.pk}:2026-10-17")
        self.assertEqual(row.title, "Kündigungsfrist: Handy")
        self.assertIn("17.10.2026", row.body)


class WrappedTests(TestCase):
    def setUp(self):
        self.lea = User.objects.create_user(
            username="lea@example.com", email="lea@example.com", password="pw"
        )
        self.tom = User.objects.create_user(
            username="tom@example.com", email="tom@example.com", password="pw"
        )
        self.client.force_login(self.lea)
        self.year = localtime_for_user(timezone.now(), self.lea).year

    def test_counts_only_the_users_own_year(self):
        now = timezone.now()
        Task.objects.create(user=self.lea, title="A", is_done=True)
        Task.objects.create(user=self.lea, title="B", is_done=False)
        Task.objects.create(user=self.tom, title="C", is_done=True)
        task = Task.objects.create(user=self.lea, title="Bericht")
        TaskTimeEntry.objects.create(
            task=task, user=self.lea, started_at=now, ended_at=now + timedelta(hours=2)
        )
        Note.objects.create(owner=self.lea, title="N", plain_text="eins zwei drei")
        habit = Habit.objects.create(owner=self.lea, title="Laufen")
        today = localtime_for_user(now, self.lea).date()
        for offset in range(3):
            day = today - timedelta(days=offset)
            if day.year == self.year:
                HabitCheck.objects.create(habit=habit, date=day)

        slides = {slide["key"]: slide for slide in year_in_review(self.lea, self.year)}

        self.assertEqual(slides["tasks"]["value"], "1")
        self.assertEqual(slides["time"]["value"], "2,0 h")
        self.assertIn("Bericht", slides["time"]["lines"][0])
        self.assertEqual(slides["notes"]["lines"], ["3 Wörter"])
        self.assertIn("Laufen", slides["habits"]["lines"][0])
        self.assertEqual(year_in_review(self.lea, self.year - 5), [])

    def test_page_renders_slides_and_falls_back_to_the_current_year(self):
        Note.objects.create(owner=self.lea, title="N", plain_text="Hallo")

        response = self.client.get("/wrapped/?year=1999")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["year"], self.year)
        self.assertContains(response, f"Dein Jahr {self.year}")
        self.assertContains(response, "Notizen geschrieben")

        empty = Client()
        empty.force_login(self.tom)
        self.assertContains(empty.get("/wrapped/"), "noch nicht genug zu erzählen")
