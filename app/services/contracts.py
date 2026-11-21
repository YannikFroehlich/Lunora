"""Contracts and subscriptions: what they cost and until when they can be cancelled.

A contract with a minimum term (`term_months`) runs from `start_date` for that term and then extends
itself by `renewal_months` at a time; it has to be cancelled `notice_days` before the end of the
running term. One without a minimum term can be cancelled any time with the notice period. A
cancelled contract ends at the first term end it could still be cancelled for on `cancelled_on`.
`materialize_due_contract_reminders` (notifications.py) warns `remind_days` before each deadline.
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.models import Contract
from app.services.expenses import euro

MAX_CONTRACTS = 100
# Monthly equivalent of one payment per interval.
MONTHLY_FACTOR = {
    Contract.INTERVAL_WEEKLY: Decimal(52) / Decimal(12),
    Contract.INTERVAL_MONTHLY: Decimal(1),
    Contract.INTERVAL_QUARTERLY: Decimal(1) / Decimal(3),
    Contract.INTERVAL_HALF_YEARLY: Decimal(1) / Decimal(6),
    Contract.INTERVAL_YEARLY: Decimal(1) / Decimal(12),
}
# Bounds the renewal loop for contracts started very long ago with monthly renewals.
MAX_RENEWALS = 2400


def add_months(day, months):
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


@dataclass(frozen=True)
class Deadline:
    """The next date the contract can be cancelled by (`cancel_by`) and the term end that ends it."""

    cancel_by: date
    ends_on: date


def next_deadline(contract, today):
    """The first deadline on or after `today`; None for contracts without a minimum term."""
    if not contract.term_months:
        return None
    notice = timedelta(days=contract.notice_days)
    renewal = max(1, contract.renewal_months)
    ends_on = add_months(contract.start_date, contract.term_months)
    for _ in range(MAX_RENEWALS):
        if ends_on - notice >= today:
            return Deadline(cancel_by=ends_on - notice, ends_on=ends_on)
        ends_on = add_months(ends_on, renewal)
    return None


def end_date(contract):
    """When a cancelled contract ends; None while it isn't cancelled."""
    if not contract.cancelled_on:
        return None
    if not contract.term_months:
        return contract.cancelled_on + timedelta(days=contract.notice_days)
    deadline = next_deadline(contract, contract.cancelled_on)
    return deadline.ends_on if deadline else contract.cancelled_on


def is_running(contract, today):
    ends = end_date(contract)
    return ends is None or ends >= today


def monthly_cost(contract):
    return (contract.amount * MONTHLY_FACTOR[contract.interval]).quantize(Decimal("0.01"))


def contract_status(contract, today):
    """(German status line, tone) for the contract card; tone is "", "warning" or "muted"."""
    ends = end_date(contract)
    if ends is not None:
        if ends < today:
            return f"Beendet am {ends:%d.%m.%Y}", "muted"
        return f"Gekündigt – läuft bis {ends:%d.%m.%Y}", "muted"
    deadline = next_deadline(contract, today)
    if deadline is None:
        return f"Jederzeit kündbar mit {contract.notice_days} Tagen Frist", ""
    days = (deadline.cancel_by - today).days
    left = "heute" if days == 0 else ("morgen" if days == 1 else f"noch {days} Tage")
    renewed = add_months(deadline.ends_on, max(1, contract.renewal_months))
    tone = "warning" if days <= contract.remind_days else ""
    return (
        f"Kündbar bis {deadline.cancel_by:%d.%m.%Y} ({left}) – sonst verlängert bis {renewed:%d.%m.%Y}",
        tone,
    )


def contracts_overview(user, today):
    """Cards sorted by urgency (next deadline first, then any-time cancellable, ended last) and the
    running contracts' monthly and yearly totals, overall and per category."""
    labels = dict(Contract.CATEGORY_CHOICES)
    cards = []
    by_category = {}
    total = Decimal(0)
    for contract in Contract.objects.filter(owner=user):
        running = is_running(contract, today)
        deadline = next_deadline(contract, today) if running and not contract.cancelled_on else None
        status, tone = contract_status(contract, today)
        monthly = monthly_cost(contract)
        if running:
            total += monthly
            by_category[contract.category] = by_category.get(contract.category, Decimal(0)) + monthly
        cards.append(
            {
                "contract": contract,
                "status": status,
                "tone": tone,
                "amount_label": f"{euro(contract.amount)} {contract.get_interval_display()}",
                "monthly_label": euro(monthly) if contract.interval != Contract.INTERVAL_MONTHLY else "",
                "category_label": labels.get(contract.category, "Sonstiges"),
                "running": running,
                "sort": (
                    0 if deadline else (1 if running else 2),
                    deadline.cancel_by if deadline else date.max,
                    contract.name.lower(),
                ),
            }
        )
    cards.sort(key=lambda card: card["sort"])
    categories = [
        {"label": labels[key], "monthly": euro(value), "share": round(value / total * 100) if total else 0}
        for key, value in sorted(by_category.items(), key=lambda item: -item[1])
    ]
    upcoming = next((card for card in cards if card["sort"][0] == 0), None)
    return {
        "cards": cards,
        "monthly_total": euro(total),
        "yearly_total": euro(total * 12),
        "categories": categories,
        "upcoming": upcoming,
    }
