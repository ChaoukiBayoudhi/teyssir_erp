"""Cash-session control: open float -> X read (mid-shift) -> Z close (variance). Spec §13.3.

The #1 anti-theft control: every cash sale is attributed to the open session; the Z report
reconciles counted cash against expected (opening float + cash sales - cash refunds).
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from teyssir.core.money import to_money
from teyssir.sync.services import enqueue_cash_session

from .models import CashSession, Payment, Return, Sale


def open_session(*, user, terminal, opening_float=0):
    session = CashSession.objects.create(
        user=user, terminal=terminal, opening_float=to_money(opening_float),
        origin_terminal=terminal,
    )
    enqueue_cash_session(session)
    return session


def current_session(terminal):
    return (
        CashSession.objects.filter(terminal=terminal, closed_at__isnull=True)
        .order_by("-opened_at").first()
    )


def _sum(qs, field="amount"):
    return qs.aggregate(s=Sum(field))["s"] or Decimal("0")


def session_totals(session, end=None):
    end = end or timezone.now()
    sales = Sale.objects.filter(cash_session=session, status=Sale.FINALIZED)
    payments = Payment.objects.filter(sale__in=sales)
    cash_sales = to_money(_sum(payments.filter(method=Payment.CASH)))
    card_sales = to_money(_sum(payments.filter(method=Payment.CARD)))
    account_sales = to_money(_sum(payments.filter(method=Payment.ACCOUNT)))
    cash_refunds = to_money(_sum(
        Return.objects.filter(
            terminal=session.terminal, refund_method=Payment.CASH,
            created_at__gte=session.opened_at, created_at__lte=end,
        ),
        "total",
    ))
    expected_cash = to_money(session.opening_float + cash_sales - cash_refunds)
    return {
        "terminal": session.terminal,
        "opened_at": session.opened_at.isoformat(),
        "opening_float": str(session.opening_float),
        "sales_count": sales.count(),
        "cash_sales": str(cash_sales),
        "card_sales": str(card_sales),
        "account_sales": str(account_sales),
        "cash_refunds": str(cash_refunds),
        "expected_cash": str(expected_cash),
    }


def x_report(session):
    """Mid-shift read — no mutation."""
    return {**session_totals(session), "type": "X"}


@transaction.atomic
def z_report(session, counted_cash):
    """End-of-day close: record counted cash + variance and lock the session."""
    end = timezone.now()
    totals = session_totals(session, end=end)
    session.counted_cash = to_money(counted_cash)
    session.variance = to_money(session.counted_cash - Decimal(totals["expected_cash"]))
    session.closed_at = end
    session.save(update_fields=["counted_cash", "variance", "closed_at"])
    enqueue_cash_session(session)   # sync the close (variance) to the hub
    return {**totals, "type": "Z",
            "counted_cash": str(session.counted_cash), "variance": str(session.variance)}
