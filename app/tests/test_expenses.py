from decimal import Decimal

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from app.models import Expense, ExpenseGroup, ExpenseGroupMember, SystemSettings, UserNotification
from app.services.expenses import add_expense, balances, euro, settle_up_plan, split_amount

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
