"""Shared expenses: groups of people who note what they paid, see who owes whom, and settle up.

Every member has the same rights: add expenses and members, delete expenses, record a
settlement. Nobody can remove someone else; members leave themselves once their balance is zero,
and a group without members is deleted. Amounts are Decimal euros with two places throughout.
"""

from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from app.models import Expense, ExpenseGroup, ExpenseGroupMember, ExpenseShare, UserNotification
from app.services.notifications import materialize_expense_group_notification
from app.services.vocabulary import resolve_share_recipient

MAX_GROUPS_PER_USER = 20
MAX_MEMBERS = 20
MAX_EXPENSES_PER_GROUP = 2000
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


def split_amount(amount, user_ids):
    """`amount` split into equal shares for `user_ids`; leftover cents go to the first ones, so
    the shares always add up to the amount exactly."""
    user_ids = list(user_ids)
    cents = int(amount / CENT)
    base, rest = divmod(cents, len(user_ids))
    return [(user_id, (base + (index < rest)) * CENT) for index, user_id in enumerate(user_ids)]


def add_expense(group, actor, *, title, amount, paid_by, participants, spent_on):
    """Records an expense split equally between `participants`; returns an error or None.

    `paid_by` and `participants` must be members already (ExpenseForm offers only those).
    """
    if group.expenses.count() >= MAX_EXPENSES_PER_GROUP:
        return f"Eine Gruppe kann höchstens {MAX_EXPENSES_PER_GROUP} Ausgaben enthalten."
    with transaction.atomic():
        expense = Expense.objects.create(
            group=group, title=title, amount=amount, paid_by=paid_by, spent_on=spent_on, created_by=actor
        )
        ExpenseShare.objects.bulk_create(
            ExpenseShare(expense=expense, user_id=user_id, amount=share)
            for user_id, share in split_amount(amount, sorted(user.pk for user in participants))
        )
    return None


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


def euro(amount):
    """German money label: 1234.5 → "1.234,50 €"."""
    text = f"{abs(amount):,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{'-' if amount < 0 else ''}{text} €"
