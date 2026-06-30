from decimal import Decimal

from django.db import transaction

from teyssir.billing.models import Invoice
from teyssir.billing.services import allocate_document_number, issue_invoice, resolve_fiscal_stamp
from teyssir.core import money
from teyssir.core.money import to_money
from teyssir.inventory.models import StockMovement
from teyssir.inventory.services import apply_movement
from teyssir.sync.services import enqueue_return, enqueue_sale

from .models import Payment, Return, ReturnLine, Sale


@transaction.atomic
def finalize_sale(sale: Sale, *, doc_type=Invoice.FACTURE, when=None, payment_method=None):
    """Finalize a DRAFT sale on the LOCAL node — works fully offline (spec §7.4/§13).

    Steps, all in one transaction: compute per-line totals + TVA → write a stock
    movement per line (append-only ledger) → snapshot the timbre + allocate the
    per-terminal+month gapless number → issue an immutable invoice → print/queue.
    """
    if sale.status != Sale.DRAFT:
        raise ValueError(f"sale {sale.id} is not DRAFT (status={sale.status})")

    subtotal = Decimal("0.000")
    tax_total = Decimal("0.000")

    for line in sale.lines.select_related("product"):
        base = to_money(line.qty * line.unit_price - line.discount)
        tax = money.line_tax(base, line.tax_rate)
        line.line_total = base
        line.save(update_fields=["line_total"])
        subtotal += base
        tax_total += tax
        apply_movement(
            product_id=line.product_id,
            qty=-line.qty,
            reason=StockMovement.SALE,
            unit_cost=line.product.cost_avg,
            ref_type="SALE",
            ref_id=str(sale.id),
            origin_terminal=sale.terminal,
        )

    invoice = issue_invoice(sale, doc_type=doc_type, when=when)

    sale.subtotal = to_money(subtotal)
    sale.tax_total = to_money(tax_total)
    sale.timbre_amount_snapshot = invoice.timbre_amount_snapshot
    sale.total = to_money(subtotal + tax_total + invoice.timbre_amount_snapshot)
    sale.status = Sale.FINALIZED
    sale.save(update_fields=[
        "subtotal", "tax_total", "timbre_amount_snapshot", "total", "status", "updated_at",
    ])
    if payment_method:
        Payment.objects.create(sale=sale, method=payment_method, amount=sale.total)
    enqueue_sale(sale)  # record the full aggregate (incl. payment) for the next hub sync (§4.4)
    return invoice


@transaction.atomic
def process_return(*, original_sale, items, reason="", refund_method=Payment.CASH,
                   terminal=None, when=None, created_by=None):
    """Issue a credit note (AVOIR) and restore stock (spec §13.2).

    `items` = [{product_id, qty, unit_price, tax_rate}]. The original sale/invoice is
    never modified; the AVOIR gets its own per-terminal+month series (doc_type AVOIR).
    """
    terminal = terminal or (original_sale.terminal if original_sale else "C1")
    ret = Return.objects.create(
        original_sale=original_sale, terminal=terminal, reason=reason,
        refund_method=refund_method, created_by=created_by, origin_terminal=terminal,
    )
    subtotal = Decimal("0.000")
    tax_total = Decimal("0.000")
    for it in items:
        qty = Decimal(str(it["qty"]))
        unit_price = to_money(it["unit_price"])
        rate = Decimal(str(it.get("tax_rate", 0)))
        base = to_money(qty * unit_price)
        ReturnLine.objects.create(
            ret=ret, product_id=it["product_id"], qty=qty, unit_price=unit_price,
            tax_rate=rate, line_total=base, origin_terminal=terminal,
        )
        subtotal += base
        tax_total += money.line_tax(base, rate)
        apply_movement(                                   # stock goes back up
            product_id=it["product_id"], qty=qty, reason=StockMovement.RETURN,
            ref_type="RETURN", ref_id=str(ret.id), origin_terminal=terminal,
        )

    number, seq, when = allocate_document_number(terminal, "AVOIR", when)
    ret.number, ret.year, ret.month, ret.seq = number, when.year, when.month, seq
    ret.subtotal = to_money(subtotal)
    ret.tax_total = to_money(tax_total)
    ret.timbre_amount_snapshot = resolve_fiscal_stamp("AVOIR")
    ret.total = to_money(subtotal + tax_total + ret.timbre_amount_snapshot)
    ret.save()
    enqueue_return(ret)
    return ret
