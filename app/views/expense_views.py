from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render

from app.forms import ExpenseForm
from app.services.expenses import (
    add_expense,
    add_member,
    balances,
    create_group,
    euro,
    group_for,
    group_members,
    leave_group,
    record_settlement,
    settle_up_plan,
    user_groups,
)
from app.services.notes import display_name
from app.services.system_settings import disabled_feature_response, feature_enabled
from app.services.user_preferences import format_user_date, localtime_for_user
from app.views.navigation import posted_id

GROUP_NAME_MAX = 120


@login_required
def expense_groups(request):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools")
    name_error = ""
    if request.method == "POST" and request.POST.get("form_name") == "expense_group_create":
        name = request.POST.get("name", "").strip()[:GROUP_NAME_MAX]
        if not name:
            name_error = "Bitte gib der Gruppe einen Namen."
        else:
            group, name_error = create_group(request.user, name)
            if group:
                return redirect("expense_group_detail", group_id=group.pk)

    groups = []
    for group in user_groups(request.user).prefetch_related("members"):
        own = balances(group).get(request.user.pk)
        groups.append(
            {
                "id": group.pk,
                "name": group.name,
                "member_count": len(group.members.all()),
                "balance": own,
                "balance_label": euro(own) if own else "ausgeglichen",
            }
        )
    return render(
        request,
        "app/expense_groups.html",
        {"active_page": "tools", "groups": groups, "name_error": name_error},
    )


def _person_label(user_id, names, viewer):
    if user_id is None:
        return "Gelöschtes Konto"
    if user_id == viewer.pk:
        return "Du"
    return names.get(user_id, "Ehemaliges Mitglied")


@login_required
def expense_group_detail(request, group_id):
    if not feature_enabled("tools"):
        return disabled_feature_response(request, "tools")
    group = group_for(request.user, group_id)
    if group is None:
        raise Http404
    members = group_members(group)
    form = ExpenseForm(members=members, user=request.user)

    form_name = request.POST.get("form_name") if request.method == "POST" else None
    if form_name == "expense_add":
        form = ExpenseForm(request.POST, members=members, user=request.user)
        if form.is_valid():
            error = add_expense(group, request.user, **form.cleaned_data)
            if error:
                django_messages.error(request, error)
            return redirect("expense_group_detail", group_id=group.pk)
    elif form_name == "expense_delete":
        group.expenses.filter(pk=posted_id(request, "expense_id")).delete()
        return redirect("expense_group_detail", group_id=group.pk)
    elif form_name == "expense_settle":
        member_by_id = {member.pk: member for member in members}
        debtor = member_by_id.get(posted_id(request, "debtor_id"))
        creditor = member_by_id.get(posted_id(request, "creditor_id"))
        payment = None
        if debtor and creditor:
            pair = (debtor.pk, creditor.pk)
            payment = next(
                (amount for *ids, amount in settle_up_plan(balances(group)) if tuple(ids) == pair), None
            )
        # Only the two people involved confirm a payment between them, and only the suggested one.
        if payment is None or request.user not in (debtor, creditor):
            django_messages.error(request, "Diese Zahlung steht nicht mehr aus.")
        else:
            record_settlement(
                group,
                request.user,
                debtor=debtor,
                creditor=creditor,
                amount=payment,
                spent_on=localtime_for_user(profile_or_user=request.user).date(),
            )
            django_messages.success(request, f"Ausgleich über {euro(payment)} eingetragen.")
        return redirect("expense_group_detail", group_id=group.pk)
    elif form_name == "expense_member_add":
        error = add_member(group, request.user, request.POST.get("recipient", ""))
        if error:
            django_messages.error(request, error)
        else:
            django_messages.success(request, "Mitglied hinzugefügt.")
        return redirect("expense_group_detail", group_id=group.pk)
    elif form_name == "expense_group_leave":
        error = leave_group(group, request.user)
        if error:
            django_messages.error(request, error)
            return redirect("expense_group_detail", group_id=group.pk)
        django_messages.success(request, f"Du hast „{group.name}“ verlassen.")
        return redirect("expense_groups")

    names = {member.pk: display_name(member) for member in members}
    balance_map = balances(group)
    expenses = group.expenses.prefetch_related("shares")
    return render(
        request,
        "app/expense_group_detail.html",
        {
            "active_page": "tools",
            "group": group,
            "form": form,
            "members": [
                {
                    "name": _person_label(member.pk, names, request.user),
                    "balance": balance_map.get(member.pk),
                    "balance_label": euro(balance_map[member.pk])
                    if balance_map.get(member.pk)
                    else "ausgeglichen",
                }
                for member in members
            ],
            "own_balance": balance_map.get(request.user.pk),
            "own_balance_label": euro(balance_map[request.user.pk])
            if balance_map.get(request.user.pk)
            else "ausgeglichen",
            "payments": [
                {
                    "debtor_id": debtor_id,
                    "creditor_id": creditor_id,
                    "text": f"{_person_label(debtor_id, names, request.user)} → "
                    f"{_person_label(creditor_id, names, request.user)}",
                    "amount_label": euro(amount),
                    "can_confirm": request.user.pk in (debtor_id, creditor_id)
                    and debtor_id in names
                    and creditor_id in names,
                }
                for debtor_id, creditor_id, amount in settle_up_plan(balance_map)
            ],
            "expenses": [
                {
                    "id": expense.pk,
                    "title": expense.title,
                    "is_settlement": expense.is_settlement,
                    "amount_label": euro(expense.amount),
                    "date_label": format_user_date(expense.spent_on, request.user),
                    "paid_by": _person_label(expense.paid_by_id, names, request.user),
                    "split": ", ".join(
                        _person_label(share.user_id, names, request.user) for share in expense.shares.all()
                    ),
                }
                for expense in expenses
            ],
        },
    )
