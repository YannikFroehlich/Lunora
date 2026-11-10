"""Shared expenses: groups of people who note what they paid, see who owes whom, and settle up.

Every member has the same rights: add expenses and members, delete expenses, record a
settlement. Nobody can remove someone else; members leave themselves once their balance is zero,
and a group without members is deleted. Amounts are Decimal euros with two places throughout.
"""

import calendar
import csv
import io
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from app.models import (
    Expense,
    ExpenseGroup,
    ExpenseGroupMember,
    ExpenseShare,
    RecurringExpense,
    UserNotification,
)
from app.services.notes import display_name
from app.services.notifications import materialize_expense_group_notification
from app.services.vocabulary import resolve_share_recipient

MAX_GROUPS_PER_USER = 20
MAX_MEMBERS = 20
MAX_EXPENSES_PER_GROUP = 2000
MAX_RECURRING_PER_GROUP = 50
# Months one rule may catch up in a single pass (e.g. after the server was down for a while).
MAX_CATCH_UP = 24
CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def user_groups(user):
    return ExpenseGroup.objects.filter(members__user=user).order_by("name", "id")


def group_for(user, group_id):
    """The group with `group_id` if `user` is a member of it, else None."""
    return user_groups(user).filter(pk=group_id).first()


def group_members(group):
    return [member.user for member in group.members.select_related("user__profile")]


def create_group(user, name):
    """Creates a group with `user` as its first member; returns (group, error)."""
    if user_groups(user).count() >= MAX_GROUPS_PER_USER:
        return None, f"Du kannst in höchstens {MAX_GROUPS_PER_USER} Gruppen sein."
    with transaction.atomic():
        group = ExpenseGroup.objects.create(name=name, created_by=user)
        ExpenseGroupMember.objects.create(group=group, user=user)
    return group, None


def add_member(group, actor, identifier):
    """Adds the user named by e-mail or username and notifies them; returns an error or None."""
    if (identifier or "").strip().lower() in {actor.username.lower(), actor.email.lower()}:
        return "Du bist schon in der Gruppe."
    user, error = resolve_share_recipient(actor, identifier)
    if error:
        return error
    if group.members.count() >= MAX_MEMBERS:
        return f"Eine Gruppe kann höchstens {MAX_MEMBERS} Mitglieder haben."
    member, created = ExpenseGroupMember.objects.get_or_create(group=group, user=user)
    if not created:
        return "Diese Person ist schon in der Gruppe."
    materialize_expense_group_notification(member, actor)
    return None


def leave_group(group, user):
    """Takes `user` out of the group once their balance is zero; returns an error or None."""
    if balances(group).get(user.pk, ZERO) != ZERO:
        return "Gleich zuerst deinen Saldo aus, dann kannst du die Gruppe verlassen."
    with transaction.atomic():
        group.members.filter(user=user).delete()
        UserNotification.objects.filter(source_key=f"expense-group:{group.pk}:{user.pk}").delete()
        if not group.members.exists():
            group.delete()
    return None


def split_amount(amount, user_ids, weights=None):
    """`amount` split into cent shares for `user_ids` in proportion to `weights` (equal without).

    The leftover cents go to the largest remainders, ties to the first ones, so the shares always
    add up to the amount exactly; weights that are cent amounts adding up to it come back as is.
    """
    user_ids = list(user_ids)
    weights = [Decimal(1)] * len(user_ids) if weights is None else [Decimal(weight) for weight in weights]
    cents = int(amount / CENT)
    total = sum(weights)
    exact = [cents * weight / total for weight in weights]
    shares = [int(value) for value in exact]
    by_remainder = sorted(range(len(shares)), key=lambda index: (shares[index] - exact[index], index))
    for index in by_remainder[: cents - sum(shares)]:
        shares[index] += 1
    return [(user_id, share * CENT) for user_id, share in zip(user_ids, shares, strict=True)]


def _create_expense(group, *, title, category, amount, paid_by_id, spent_on, created_by_id, weights):
    """The expense plus its shares; `weights` is {user id: weight}."""
    expense = Expense.objects.create(
        group=group,
        title=title,
        category=category,
        amount=amount,
        paid_by_id=paid_by_id,
        spent_on=spent_on,
        created_by_id=created_by_id,
    )
    user_ids = sorted(weights)
    ExpenseShare.objects.bulk_create(
        ExpenseShare(expense=expense, user_id=user_id, amount=share)
        for user_id, share in split_amount(amount, user_ids, [weights[user_id] for user_id in user_ids])
    )
    return expense


def add_expense(
    group,
    actor,
    *,
    title,
    amount,
    paid_by,
    participants,
    spent_on,
    category="other",
    weights=None,
    repeat_monthly=False,
):
    """Records an expense; returns an error or None.

    `weights` ({user id: share or amount}) splits it in proportion, without it it's split equally.
    `paid_by` and `participants` must be members already (ExpenseForm offers only those). With
    `repeat_monthly` the same expense comes back on that day every month.
    """
    if group.expenses.count() >= MAX_EXPENSES_PER_GROUP:
        return f"Eine Gruppe kann höchstens {MAX_EXPENSES_PER_GROUP} Ausgaben enthalten."
    if repeat_monthly and group.recurring_expenses.count() >= MAX_RECURRING_PER_GROUP:
        return f"Eine Gruppe kann höchstens {MAX_RECURRING_PER_GROUP} monatliche Ausgaben haben."
    weights = {user.pk: (weights or {}).get(user.pk, Decimal(1)) for user in participants}
    with transaction.atomic():
        _create_expense(
            group,
            title=title,
            category=category,
            amount=amount,
            paid_by_id=paid_by.pk,
            spent_on=spent_on,
            created_by_id=actor.pk,
            weights=weights,
        )
        if repeat_monthly:
            RecurringExpense.objects.create(
                group=group,
                title=title,
                category=category,
                amount=amount,
                paid_by=paid_by,
                weights={str(user_id): str(weight) for user_id, weight in weights.items()},
                day_of_month=spent_on.day,
                next_on=_next_month(spent_on, spent_on.day),
                created_by=actor,
            )
    return None


def _next_month(day, day_of_month):
    """`day_of_month` of the month after `day`, or that month's last day when it is shorter."""
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return date(year, month, min(day_of_month, calendar.monthrange(year, month)[1]))


def _post_recurring(rule):
    """Adds the rule's expense for `rule.next_on`; returns False when nothing (more) can be added."""
    member_ids = set(rule.group.members.values_list("user_id", flat=True))
    weights = {
        int(user_id): Decimal(weight)
        for user_id, weight in rule.weights.items()
        if int(user_id) in member_ids
    }
    if rule.paid_by_id not in member_ids or not weights:
        # The payer left, or everyone who shared it: there is nobody left to bill.
        rule.delete()
        return False
    if rule.group.expenses.count() >= MAX_EXPENSES_PER_GROUP:
        return False
    due = rule.next_on
    following = _next_month(due, rule.day_of_month)
    with transaction.atomic():
        # Claim the month first, so the scheduler and a page view can't both add it.
        if not RecurringExpense.objects.filter(pk=rule.pk, next_on=due).update(next_on=following):
            return False
        _create_expense(
            rule.group,
            title=rule.title,
            category=rule.category,
            amount=rule.amount,
            paid_by_id=rule.paid_by_id,
            spent_on=due,
            created_by_id=rule.created_by_id,
            weights=weights,
        )
    rule.next_on = following
    return True


def post_due_recurring_expenses(*, group=None, today=None):
    """Adds every monthly expense that has come due, missed months included; returns the count."""
    today = today or timezone.localdate()
    rules = RecurringExpense.objects.filter(next_on__lte=today).select_related("group")
    if group is not None:
        rules = rules.filter(group=group)
    added = 0
    for rule in rules:
        for _ in range(MAX_CATCH_UP):
            if rule.next_on > today or not _post_recurring(rule):
                break
            added += 1
    return added


def record_settlement(group, actor, *, debtor, creditor, amount, spent_on):
    """`debtor` paid `creditor` back `amount`: an expense paid by the debtor, owed by the creditor."""
    with transaction.atomic():
        expense = Expense.objects.create(
            group=group,
            title="Ausgleich",
            amount=amount,
            paid_by=debtor,
            spent_on=spent_on,
            is_settlement=True,
            created_by=actor,
        )
        ExpenseShare.objects.create(expense=expense, user=creditor, amount=amount)


def balances(group):
    """{user id (None for deleted accounts): paid minus owed}; positive means others owe them."""
    totals = defaultdict(lambda: ZERO)
    for row in group.expenses.values("paid_by").annotate(total=Sum("amount")):
        totals[row["paid_by"]] += row["total"]
    for row in ExpenseShare.objects.filter(expense__group=group).values("user").annotate(total=Sum("amount")):
        totals[row["user"]] -= row["total"]
    return {user_id: total for user_id, total in totals.items() if total != ZERO}


def settle_up_plan(balance_map):
    """Payments [(debtor id, creditor id, amount)] that bring every balance to zero.

    ponytail: greedy (largest debtor pays largest creditor), which needs at most n-1 payments but
    not always the fewest; an exact minimum is NP-hard and not worth it for groups this small.
    """
    debtors = sorted(((-total, user_id) for user_id, total in balance_map.items() if total < 0), reverse=True)
    creditors = sorted(
        ((total, user_id) for user_id, total in balance_map.items() if total > 0), reverse=True
    )
    debtors = [list(item) for item in debtors]
    creditors = [list(item) for item in creditors]
    payments = []
    while debtors and creditors:
        debt, creditor_due = debtors[0], creditors[0]
        amount = min(debt[0], creditor_due[0])
        payments.append((debt[1], creditor_due[1], amount))
        debt[0] -= amount
        creditor_due[0] -= amount
        if debt[0] == ZERO:
            debtors.pop(0)
        if creditor_due[0] == ZERO:
            creditors.pop(0)
    return payments


def month_overview(group, month_start, user_id):
    """The month's spending (settlements left out): total, the viewer's part of it, and the
    categories largest first with their share of the total in percent."""
    following = _next_month(month_start, 1)
    expenses = group.expenses.filter(is_settlement=False, spent_on__gte=month_start, spent_on__lt=following)
    totals = defaultdict(lambda: ZERO)
    for row in expenses.values("category").annotate(total=Sum("amount")):
        totals[row["category"]] += row["total"]
    total = sum(totals.values(), ZERO)
    own = ExpenseShare.objects.filter(expense__in=expenses, user_id=user_id).aggregate(total=Sum("amount"))
    labels = dict(Expense.CATEGORY_CHOICES)
    return {
        "total": total,
        "own": own["total"] or ZERO,
        "categories": [
            {
                "key": key,
                "label": labels.get(key, key),
                "icon": Expense.CATEGORY_ICONS.get(key, "fa-tag"),
                "total": amount,
                "percent": round(amount * 100 / total) if total else 0,
            }
            for key, amount in sorted(totals.items(), key=lambda item: -item[1])
        ],
    }


def _csv_cell(text):
    """Text from other members can't become a spreadsheet formula."""
    return f"'{text}" if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _decimal_label(amount):
    return f"{amount:.2f}".replace(".", ",")


def expenses_csv(group):
    """All expenses of the group as semicolon-separated CSV (opens directly in German Excel):
    one row per expense, one column per person with their share."""
    expenses = list(group.expenses.prefetch_related("shares").order_by("spent_on", "created_at", "id"))
    member_ids = list(group.members.values_list("user_id", flat=True))
    share_ids = [share.user_id for expense in expenses for share in expense.shares.all()]
    people = list(dict.fromkeys(member_ids + share_ids))
    users = get_user_model().objects.filter(pk__in=[pk for pk in people if pk]).select_related("profile")
    names = {user.pk: display_name(user) for user in users}
    names[None] = "Gelöschtes Konto"

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(
        ["Datum", "Wofür", "Art", "Kategorie", "Betrag", "Bezahlt von"]
        + [_csv_cell(f"Anteil {names.get(pk, 'Ehemaliges Mitglied')}") for pk in people]
    )
    for expense in expenses:
        shares = defaultdict(lambda: ZERO)
        for share in expense.shares.all():
            shares[share.user_id] += share.amount
        writer.writerow(
            [
                expense.spent_on.strftime("%d.%m.%Y"),
                _csv_cell(expense.title),
                "Ausgleich" if expense.is_settlement else "Ausgabe",
                "" if expense.is_settlement else expense.get_category_display(),
                _decimal_label(expense.amount),
                _csv_cell(names.get(expense.paid_by_id, "Ehemaliges Mitglied")),
            ]
            + [_decimal_label(shares[pk]) if pk in shares else "" for pk in people]
        )
    return "\ufeff" + buffer.getvalue()


def euro(amount):
    """German money label: 1234.5 → "1.234,50 €"."""
    text = f"{abs(amount):,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{'-' if amount < 0 else ''}{text} €"
