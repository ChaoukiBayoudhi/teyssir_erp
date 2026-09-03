from decimal import Decimal

from django.conf import settings
from django.db import transaction

from teyssir.billing.models import Invoice
from teyssir.billing.services import allocate_document_number, issue_invoice, resolve_fiscal_stamp
from teyssir.core import money
from teyssir.core.money import mul_qty_price, to_money
from teyssir.inventory.models import StockMovement
from teyssir.inventory.services import apply_movement
from teyssir.sync.services import enqueue_return, enqueue_sale

from .models import Payment, Return, ReturnLine, Sale


def _apply_vat_and_timbre() -> bool:
    """Shop flag: when false, sale/return totals exclude TVA and timbre."""
    return bool(getattr(settings, "APPLY_VAT_AND_TIMBRE", False))


class DiscountError(ValueError):
    """Line or header discount is out of bounds."""


@transaction.atomic
def finalize_sale(sale: Sale, *, doc_type=Invoice.FACTURE, when=None, payment_method=None):
    """Finalize a DRAFT sale on the LOCAL node — works fully offline (spec §7.4/§13).

    Steps, all in one transaction: compute per-line totals + TVA → write a stock
    movement per line (append-only ledger) → snapshot the timbre + allocate the
    per-terminal+month gapless number → issue an immutable invoice → print/queue.

    Discount order (Tunisian retail / fiscal): line discount then header discount,
    both applied on the HT base **before** TVA.
    """
    if sale.status != Sale.DRAFT:
        raise ValueError(f"sale {sale.id} is not DRAFT (status={sale.status})")

    lines = list(sale.lines.select_related("product"))
    if not lines:
        raise ValueError(f"sale {sale.id} has no lines")

    # 1) Per-line HT after line discount (discount BEFORE VAT).
    line_bases = []  # (line, base_ht)
    gross_subtotal = Decimal("0.000")
    for line in lines:
        gross = mul_qty_price(line.qty, line.unit_price)
        disc = to_money(line.discount or 0)
        if disc < 0:
            raise DiscountError(f"line discount cannot be negative ({disc})")
        if disc > gross:
            raise DiscountError(f"line discount {disc} exceeds line HT {gross}")
        base = to_money(gross - disc)
        line_bases.append((line, base))
        gross_subtotal += base
    gross_subtotal = to_money(gross_subtotal)

    # 2) Allocate header (global) discount proportionally across lines, still pre-VAT.
    header_disc = to_money(sale.discount or 0)
    if header_disc < 0:
        raise DiscountError(f"header discount cannot be negative ({header_disc})")
    if header_disc > gross_subtotal:
        raise DiscountError(f"header discount {header_disc} exceeds subtotal {gross_subtotal}")

    subtotal = Decimal("0.000")
    tax_total = Decimal("0.000")
    allocated = Decimal("0.000")
    n = len(line_bases)
    for i, (line, base) in enumerate(line_bases):
        if header_disc and gross_subtotal:
            if i == n - 1:
                share = to_money(header_disc - allocated)
            else:
                share = to_money(header_disc * base / gross_subtotal)
                allocated += share
        else:
            share = Decimal("0.000")
        adj = to_money(base - share)
        tax = money.line_tax(adj, line.tax_rate) if _apply_vat_and_timbre() else Decimal("0.000")
        line.line_total = adj
        line.save(update_fields=["line_total"])
        subtotal += adj
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
    if sale.total < 0:
        raise DiscountError(f"sale total cannot be negative ({sale.total})")
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

    `items` = [{product_id, qty, unit_price, tax_rate, discount?}]. When `original_sale`
    is provided, each item is validated against the original sale lines (product, qty
    remaining after prior returns, and price/tax defaulted from the original).
    """
    terminal = terminal or (original_sale.terminal if original_sale else "C1")

    # Index original lines + already-returned qty when an original sale is bound.
    original_by_product = {}
    already_returned = {}
    if original_sale is not None:
        if original_sale.status != Sale.FINALIZED:
            raise ValueError("original sale must be FINALIZED")
        for ln in original_sale.lines.all():
            original_by_product[ln.product_id] = ln
        from .models import ReturnLine as RL
        for rl in RL.objects.filter(ret__original_sale=original_sale):
            already_returned[rl.product_id] = already_returned.get(rl.product_id, Decimal("0")) + rl.qty

    ret = Return.objects.create(
        original_sale=original_sale, terminal=terminal, reason=reason,
        refund_method=refund_method, created_by=created_by, origin_terminal=terminal,
    )
    subtotal = Decimal("0.000")
    tax_total = Decimal("0.000")
    for it in items:
        product_id = it["product_id"]
        qty = Decimal(str(it["qty"]))
        if qty <= 0:
            raise ValueError("return qty must be positive")

        orig = original_by_product.get(product_id) if original_sale is not None else None
        if original_sale is not None:
            if orig is None:
                raise ValueError(f"product {product_id} was not on the original sale")
            prior = already_returned.get(product_id, Decimal("0"))
            if prior + qty > orig.qty:
                raise ValueError(
                    f"return qty {qty} exceeds remaining {orig.qty - prior} for product {product_id}"
                )
            # Prefer original fiscal facts; ignore client price/tax overrides when bound.
            unit_price = to_money(orig.unit_price)
            rate = orig.tax_rate
            # Pro-rate the original line discount for the returned qty.
            line_disc = to_money(orig.discount * qty / orig.qty) if orig.qty else Decimal("0.000")
        else:
            unit_price = to_money(it["unit_price"])
            rate = Decimal(str(it.get("tax_rate", 0)))
            line_disc = to_money(it.get("discount", 0))

        gross = mul_qty_price(qty, unit_price)
        if line_disc > gross:
            raise DiscountError(f"return discount {line_disc} exceeds line HT {gross}")
        base = to_money(gross - line_disc)
        ReturnLine.objects.create(
            ret=ret, product_id=product_id, qty=qty, unit_price=unit_price,
            tax_rate=rate, line_total=base, origin_terminal=terminal,
        )
        subtotal += base
        tax_total += money.line_tax(base, rate) if _apply_vat_and_timbre() else Decimal("0.000")
        apply_movement(                                   # stock goes back up
            product_id=product_id, qty=qty, reason=StockMovement.RETURN,
            unit_cost=(orig.product.cost_avg if orig else Decimal("0")),
            ref_type="RETURN", ref_id=str(ret.id), origin_terminal=terminal,
        )
        if original_sale is not None:
            already_returned[product_id] = already_returned.get(product_id, Decimal("0")) + qty

    number, seq, when = allocate_document_number(terminal, "AVOIR", when)
    ret.number, ret.year, ret.month, ret.seq = number, when.year, when.month, seq
    ret.subtotal = to_money(subtotal)
    ret.tax_total = to_money(tax_total)
    ret.timbre_amount_snapshot = resolve_fiscal_stamp("AVOIR")
    ret.total = to_money(subtotal + tax_total + ret.timbre_amount_snapshot)
    ret.save()
    enqueue_return(ret)
    return ret
