"""Customer credit-account ledger (spec §M9): charge on account, pay on account, balance, statement."""
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from teyssir.core.money import to_money
from teyssir.sync.services import enqueue_account_entry

from .models import AccountEntry


class AccountAmountError(ValueError):
    """Invalid charge/payment amount (non-positive or exceeds balance)."""


def _positive_amount(amount) -> Decimal:
    value = to_money(amount)
    if value <= 0:
        raise AccountAmountError("amount must be positive")
    return value


@transaction.atomic
def charge_account(customer, amount, ref_type="", ref_id="", note=""):
    entry = AccountEntry.objects.create(
        customer=customer, entry_type=AccountEntry.CHARGE, amount=_positive_amount(amount),
        ref_type=ref_type, ref_id=str(ref_id), note=note,
        origin_terminal=getattr(customer, "origin_terminal", ""),
    )
    enqueue_account_entry(entry)
    return entry


@transaction.atomic
def post_payment(customer, amount, note="", *, allow_overpay=False):
    """Record a customer payment-on-account.

    Amounts must be strictly positive (millime scale). By default a payment cannot exceed
    the outstanding balance — that was the root cause of negative "Solde" after a Règlement
    (e.g. paying 2222 DT on a 0 balance → Solde -2222). Pass ``allow_overpay=True`` only for
    intentional prepayments/credits.
    """
    value = _positive_amount(amount)
    owed = balance(customer)
    if not allow_overpay and value > owed:
        raise AccountAmountError(
            f"payment {value} exceeds outstanding balance {owed}"
        )
    entry = AccountEntry.objects.create(
        customer=customer, entry_type=AccountEntry.PAYMENT, amount=value,
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
        # Amounts are always stored as positive magnitudes; sign is applied by type.
        delta = e.amount if e.entry_type == AccountEntry.CHARGE else -abs(e.amount)
        running = to_money(running + delta)
        out.append({
            "at": e.created_at.isoformat(),
            "type": e.entry_type,
            "amount": str(abs(to_money(e.amount))),  # always positive magnitude for UI
            "balance": str(running),
            "note": e.note,
        })
    return {"balance": str(running), "entries": out}
