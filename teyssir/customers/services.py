"""Customer credit-account ledger (spec §M9): charge on account, pay on account, balance, statement."""
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from teyssir.core.money import to_money
from teyssir.sync.services import enqueue_account_entry

from .models import AccountEntry


@transaction.atomic
def charge_account(customer, amount, ref_type="", ref_id="", note=""):
    entry = AccountEntry.objects.create(
        customer=customer, entry_type=AccountEntry.CHARGE, amount=to_money(amount),
        ref_type=ref_type, ref_id=str(ref_id), note=note,
        origin_terminal=getattr(customer, "origin_terminal", ""),
    )
    enqueue_account_entry(entry)
    return entry


@transaction.atomic
def post_payment(customer, amount, note=""):
    entry = AccountEntry.objects.create(
        customer=customer, entry_type=AccountEntry.PAYMENT, amount=to_money(amount),
        ref_type="PAYMENT", note=note, origin_terminal=getattr(customer, "origin_terminal", ""),
    )
    enqueue_account_entry(entry)
    return entry


def balance(customer):
    charges = Decimal("0.000")
    payments = Decimal("0.000")
    for row in customer.entries.values("entry_type").annotate(s=Sum("amount")):
        if row["entry_type"] == AccountEntry.CHARGE:
            charges = row["s"] or Decimal("0")
        else:
            payments = row["s"] or Decimal("0")
    return to_money(Decimal(charges) - Decimal(payments))


def statement(customer):
    out = []
    running = Decimal("0.000")
    for e in customer.entries.order_by("created_at"):
        delta = e.amount if e.entry_type == AccountEntry.CHARGE else -e.amount
        running = to_money(running + delta)
        out.append({
            "at": e.created_at.isoformat(),
            "type": e.entry_type,
            "amount": str(e.amount),
            "balance": str(running),
            "note": e.note,
        })
    return {"balance": str(running), "entries": out}
