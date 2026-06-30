"""Quotation + reservation services (spec §13.2)."""
from decimal import Decimal

from django.db import transaction

from teyssir.core import money
from teyssir.core.money import to_money
from teyssir.sync.services import enqueue_quotation, enqueue_reservation

from .models import Quotation, QuotationLine, Reservation


@transaction.atomic
def create_quotation(*, customer_id="", items, terminal="", valid_until=None, created_by=None):
    """Build a quote and compute totals (ex-timbre). No stock movement."""
    q = Quotation.objects.create(
        customer_id=customer_id, terminal=terminal, valid_until=valid_until,
        created_by=created_by, origin_terminal=terminal,
    )
    subtotal = Decimal("0.000")
    tax_total = Decimal("0.000")
    for it in items:
        qty = Decimal(str(it["qty"]))
        unit_price = to_money(it["unit_price"])
        rate = Decimal(str(it.get("tax_rate", 0)))
        base = to_money(qty * unit_price)
        QuotationLine.objects.create(
            quotation=q, product_id=it["product_id"], qty=qty, unit_price=unit_price,
            tax_rate=rate, line_total=base, origin_terminal=terminal,
        )
        subtotal += base
        tax_total += money.line_tax(base, rate)
    q.subtotal = to_money(subtotal)
    q.tax_total = to_money(tax_total)
    q.total = to_money(subtotal + tax_total)
    q.save(update_fields=["subtotal", "tax_total", "total"])
    enqueue_quotation(q)
    return q


@transaction.atomic
def convert_quotation(quotation, *, payment_method=None, terminal=None, created_by=None):
    """Turn an OPEN quote into a finalized sale (stock moves, fiscal number issued)."""
    from teyssir.sales.models import Sale, SaleLine
    from teyssir.sales.services import finalize_sale

    if quotation.status != Quotation.OPEN:
        raise ValueError(f"quotation {quotation.id} is not OPEN")
    terminal = terminal or quotation.terminal or "C1"
    sale = Sale.objects.create(
        terminal=terminal, status=Sale.DRAFT, customer_id=quotation.customer_id,
        created_by=created_by, origin_terminal=terminal,
    )
    for ql in quotation.lines.select_related("product"):
        SaleLine.objects.create(
            sale=sale, product=ql.product, qty=ql.qty, unit_price=ql.unit_price,
            tax_rate=ql.tax_rate, origin_terminal=terminal,
        )
    invoice = finalize_sale(sale, payment_method=payment_method)
    quotation.status = Quotation.CONVERTED
    quotation.save(update_fields=["status"])
    enqueue_quotation(quotation)   # sync the CONVERTED status (the sale itself is enqueued too)
    return invoice


@transaction.atomic
def create_reservation(*, product_id, qty, customer_id="", terminal="", expires_at=None,
                       created_by=None):
    r = Reservation.objects.create(
        product_id=product_id, qty=Decimal(str(qty)), customer_id=customer_id,
        terminal=terminal, expires_at=expires_at, created_by=created_by, origin_terminal=terminal,
    )
    enqueue_reservation(r)
    return r


def release_reservation(reservation):
    reservation.status = Reservation.RELEASED
    reservation.save(update_fields=["status"])
    enqueue_reservation(reservation)
    return reservation


def fulfill_reservation(reservation):
    reservation.status = Reservation.FULFILLED
    reservation.save(update_fields=["status"])
    enqueue_reservation(reservation)
    return reservation
