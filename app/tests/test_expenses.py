from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from app.models import (
    Expense,
    ExpenseGroup,
    ExpenseGroupMember,
    RecurringExpense,
    SystemSettings,
    UserNotification,
)
from app.services.expenses import (
    add_expense,
    balances,
    euro,
    post_due_recurring_expenses,
    settle_up_plan,
    split_amount,
)

D = Decimal


class ExpenseMathTests(SimpleTestCase):
    def test_split_puts_leftover_cents_on_the_first_people_and_adds_up(self):
        shares = split_amount(D("10.00"), [3, 1, 2])

        self.assertEqual(shares, [(3, D("3.34")), (1, D("3.33")), (2, D("3.33"))])
        self.assertEqual(sum(amount for _user, amount in shares), D("10.00"))

    def test_settle_up_plan_brings_everyone_to_zero(self):
        balance_map = {1: D("50.00"), 2: D("-20.00"), 3: D("-30.00"), 4: D("0.00")}

        plan = settle_up_plan(balance_map)

        self.assertEqual(plan, [(3, 1, D("30.00")), (2, 1, D("20.00"))])

    def test_euro_label(self):
        self.assertEqual(euro(D("1234.5")), "1.234,50 €")
        self.assertEqual(euro(D("-3")), "-3,00 €")


class ExpenseGroupTests(TestCase):
    def setUp(self):
        self.lea = User.objects.create_user(username="lea", email="lea@example.com", password="pw-12345!")
        self.tom = User.objects.create_user(username="tom", email="tom@example.com", password="pw-12345!")
        self.sam = User.objects.create_user(username="sam", email="sam@example.com", password="pw-12345!")
        self.client.force_login(self.lea)
        self.client.post("/tools/expenses/", {"form_name": "expense_group_create", "name": "WG"})
        self.group = ExpenseGroup.objects.get(name="WG")
        self.url = f"/tools/expenses/{self.group.pk}/"
        self.client.post(self.url, {"form_name": "expense_member_add", "recipient": "tom@example.com"})

    def _add(self, **data):
        return self.client.post(
            self.url,
            {
                "form_name": "expense_add",
                "title": "Einkauf",
                "amount": "30,00",
                "spent_on": "2026-11-10",
                "paid_by": self.lea.pk,
                "participants": [self.lea.pk, self.tom.pk],
                **data,
            },
        )

    def test_creating_a_group_makes_the_creator_a_member_and_adding_notifies(self):
        self.assertEqual(sorted(self.group.members.values_list("user__username", flat=True)), ["lea", "tom"])
        notification = UserNotification.objects.get(recipient=self.tom)
        self.assertEqual(notification.kind, UserNotification.KIND_EXPENSE_GROUP)
        self.assertEqual(notification.url, self.url)

    def test_expense_splits_and_shows_who_owes_whom(self):
        response = self._add(amount="1.234,50")

        self.assertRedirects(response, self.url)
        self.assertEqual(balances(self.group), {self.lea.pk: D("617.25"), self.tom.pk: D("-617.25")})
        page = self.client.get(self.url)
        self.assertEqual(page.context["own_balance_label"], "617,25 €")
        self.assertEqual(page.context["payments"][0]["text"], "tom@example.com → Du")

    def test_invalid_amounts_and_outsiders_are_rejected(self):
        for data in (
            {"amount": "abc"},
            {"amount": "0"},
            {"amount": "1,234"},
            {"participants": [self.sam.pk]},
        ):
            with self.subTest(data=data):
                response = self._add(**data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
        self.assertFalse(Expense.objects.exists())

    def test_settlement_by_someone_involved_clears_the_balance(self):
        self._add()
        self.client.force_login(self.tom)

        self.client.post(
            self.url, {"form_name": "expense_settle", "debtor_id": self.tom.pk, "creditor_id": self.lea.pk}
        )

        self.assertEqual(balances(self.group), {})
        self.assertTrue(Expense.objects.get(is_settlement=True).title == "Ausgleich")

    def test_settlement_needs_a_pending_payment_and_a_party_to_it(self):
        ExpenseGroupMember.objects.create(group=self.group, user=self.sam)
        self._add()
        self.client.force_login(self.sam)

        self.client.post(
            self.url, {"form_name": "expense_settle", "debtor_id": self.tom.pk, "creditor_id": self.lea.pk}
        )
        self.client.force_login(self.tom)
        self.client.post(
            self.url, {"form_name": "expense_settle", "debtor_id": self.lea.pk, "creditor_id": self.tom.pk}
        )

        self.assertFalse(Expense.objects.filter(is_settlement=True).exists())

    def test_non_members_get_404(self):
        self.client.force_login(self.sam)

        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self._add(paid_by=self.sam.pk).status_code, 404)

    def test_leaving_needs_a_zero_balance_and_the_last_one_out_deletes_the_group(self):
        self._add()
        response = self.client.post(self.url, {"form_name": "expense_group_leave"})
        self.assertRedirects(response, self.url)
        self.assertTrue(self.group.members.filter(user=self.lea).exists())

        add_expense(
            self.group,
            self.tom,
            title="Pizza",
            amount=D("30.00"),
            paid_by=self.tom,
            participants=[self.lea, self.tom],
            spent_on=self.group.created_at.date(),
        )
        self.assertRedirects(
            self.client.post(self.url, {"form_name": "expense_group_leave"}), "/tools/expenses/"
        )
        self.client.force_login(self.tom)
        self.client.post(self.url, {"form_name": "expense_group_leave"})

        self.assertFalse(ExpenseGroup.objects.exists())

    def test_deleted_account_keeps_the_others_balances(self):
        self._add()
        self.lea.delete()

        self.assertEqual(balances(self.group), {None: D("15.00"), self.tom.pk: D("-15.00")})

    def test_disabled_tools_flag_returns_503(self):
        SystemSettings.objects.update_or_create(pk=1, defaults={"tools_enabled": False})

        self.assertEqual(self.client.get("/tools/expenses/").status_code, 503)
        self.assertEqual(self.client.get(self.url).status_code, 503)


class ExpenseSplitTests(TestCase):
    def setUp(self):
        self.lea = User.objects.create_user(username="lea", email="lea@example.com", password="pw-12345!")
        self.tom = User.objects.create_user(username="tom", email="tom@example.com", password="pw-12345!")
        self.client.force_login(self.lea)
        self.client.post("/tools/expenses/", {"form_name": "expense_group_create", "name": "WG"})
        self.group = ExpenseGroup.objects.get(name="WG")
        self.url = f"/tools/expenses/{self.group.pk}/"
        ExpenseGroupMember.objects.create(group=self.group, user=self.tom)

    def _add(self, **data):
        return self.client.post(
            self.url,
            {
                "form_name": "expense_add",
                "title": "Miete",
                "amount": "30,00",
                "spent_on": "2026-01-31",
                "paid_by": self.lea.pk,
                "participants": [self.lea.pk, self.tom.pk],
                **data,
            },
        )

    def test_weighted_split_gives_leftover_cents_to_the_largest_remainder(self):
        self.assertEqual(split_amount(D("10.00"), [1, 2], [2, 1]), [(1, D("6.67")), (2, D("3.33"))])
        self.assertEqual(
            split_amount(D("30.00"), [1, 2], [D("20.01"), D("9.99")]), [(1, D("20.01")), (2, D("9.99"))]
        )

    def test_shares_and_fixed_amounts_split_unevenly_and_show_the_amounts(self):
        self._add(split_mode="shares", **{f"split_{self.lea.pk}": "2", f"split_{self.tom.pk}": "1"})
        self.assertEqual(balances(self.group), {self.lea.pk: D("10.00"), self.tom.pk: D("-10.00")})

        self._add(split_mode="amounts", **{f"split_{self.lea.pk}": "5", f"split_{self.tom.pk}": "25,00"})
        self.assertEqual(balances(self.group), {self.lea.pk: D("35.00"), self.tom.pk: D("-35.00")})
        split = self.client.get(self.url).context["expenses"][0]["split"]
        self.assertEqual(split, "Du 5,00 €, tom@example.com 25,00 €")

    def test_fixed_amounts_must_add_up_and_every_person_needs_a_value(self):
        response = self._add(
            split_mode="amounts", **{f"split_{self.lea.pk}": "5", f"split_{self.tom.pk}": "20"}
        )
        self.assertContains(response, "Die Beträge ergeben 25,00 €, die Ausgabe ist 30,00 €.")
        response = self._add(split_mode="shares", **{f"split_{self.lea.pk}": "1"})
        self.assertContains(response, "einen Wert eintragen")
        response = self._add(
            split_mode="shares", **{f"split_{self.lea.pk}": "0", f"split_{self.tom.pk}": "1"}
        )
        self.assertContains(response, "Anteile sind Zahlen")
        self.assertFalse(Expense.objects.exists())

    def test_monthly_expense_comes_back_on_its_day_once_per_month(self):
        self._add(
            repeat_monthly="on",
            split_mode="shares",
            **{f"split_{self.lea.pk}": "2", f"split_{self.tom.pk}": "1"},
        )
        rule = RecurringExpense.objects.get()
        self.assertEqual(rule.next_on, date(2026, 2, 28))

        self.assertEqual(post_due_recurring_expenses(today=date(2026, 4, 15)), 2)
        self.assertEqual(post_due_recurring_expenses(today=date(2026, 4, 15)), 0)

        self.assertEqual(
            list(Expense.objects.order_by("spent_on").values_list("spent_on", flat=True)),
            [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)],
        )
        rule.refresh_from_db()
        self.assertEqual(rule.next_on, date(2026, 4, 30))
        self.assertEqual(balances(self.group), {self.lea.pk: D("30.00"), self.tom.pk: D("-30.00")})

    def test_monthly_expense_ends_when_stopped_or_when_the_payer_left(self):
        today = timezone.localdate().isoformat()
        self._add(repeat_monthly="on", spent_on=today)
        rule = RecurringExpense.objects.get()
        self.client.force_login(self.tom)
        self.client.post(self.url, {"form_name": "expense_recurring_stop", "recurring_id": rule.pk})
        self.assertFalse(RecurringExpense.objects.exists())

        self.client.force_login(self.lea)
        self._add(repeat_monthly="on", spent_on=today)
        self.group.members.filter(user=self.lea).delete()
        post_due_recurring_expenses(today=date(2099, 1, 1))
        self.assertFalse(RecurringExpense.objects.exists())
        self.assertEqual(Expense.objects.count(), 2)

    def test_csv_export_has_a_column_per_person_and_defuses_formulas(self):
        self._add(title="=HYPERLINK(1)")

        response = self.client.get(self.url, {"format": "csv"})

        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        lines = response.content.decode("utf-8").lstrip("﻿").splitlines()
        self.assertEqual(
            lines[0], "Datum;Wofür;Art;Betrag;Bezahlt von;Anteil lea@example.com;Anteil tom@example.com"
        )
        self.assertEqual(lines[1], "31.01.2026;'=HYPERLINK(1);Ausgabe;30,00;lea@example.com;15,00;15,00")
