"""Purchasing: weighted-average receiving primitive + PO -> goods-receipt -> supplier-invoice
workflow (spec §14.2, §"Purchase management")."""
from decimal import Decimal

from django.db import transaction

from teyssir.catalog.models import Product
from teyssir.core.money import to_money
from teyssir.inventory.models import StockMovement
from teyssir.inventory.services import apply_movement

from .models import (
    GoodsReceipt, GoodsReceiptLine, PurchaseInvoice, PurchaseOrder, PurchaseOrderLine,
)


@transaction.atomic
def receive_goods(*, product_id, qty, unit_cost, supplier=None, ref_id=""):
    """Append a RECEIPT movement and roll the weighted-average cost:

        new_avg = (old_qty*old_avg + recv_qty*recv_cost) / (old_qty + recv_qty)

    Returns the product's updated cost_avg and qty_on_hand.
    """
    qty = Decimal(qty)
    unit_cost = to_money(unit_cost)
    product = Product.objects.select_for_update().get(pk=product_id)
    old_qty = product.qty_on_hand or Decimal("0")
    old_avg = product.cost_avg or Decimal("0")
    total_qty = old_qty + qty
    new_avg = to_money((old_qty * old_avg + qty * unit_cost) / total_qty) if total_qty > 0 else unit_cost
    product.cost_avg = new_avg
    product.save(update_fields=["cost_avg"])

    apply_movement(
        product_id=product_id, qty=qty, reason=StockMovement.RECEIPT, unit_cost=unit_cost,
        ref_type="RECEIPT", ref_id=ref_id or (str(supplier.id) if supplier else ""),
    )
    product.refresh_from_db()
    return {"cost_avg": product.cost_avg, "qty_on_hand": product.qty_on_hand}


@transaction.atomic
def create_po(*, supplier, items, terminal="", created_by=None, status=PurchaseOrder.ORDERED):
    """Create a purchase order with lines (no stock effect until received)."""
    po = PurchaseOrder.objects.create(
        supplier=supplier, terminal=terminal, status=status, created_by=created_by,
        origin_terminal=terminal,
    )
    for it in items:
        PurchaseOrderLine.objects.create(
            po=po, product_id=it["product_id"], qty_ordered=Decimal(str(it["qty"])),
            unit_cost=to_money(it["unit_cost"]), origin_terminal=terminal,
        )
    return po


@transaction.atomic
def receive_po(*, po, items=None, terminal=""):
    """Receive goods against a PO (defaults to the outstanding quantity of every line).

    Each received line rolls weighted-average cost via receive_goods, updates the PO line's
    qty_received, and the PO is marked RECEIVED once every line is fully received.
    """
    terminal = terminal or po.terminal
    gr = GoodsReceipt.objects.create(
        po=po, supplier=po.supplier, terminal=terminal, origin_terminal=terminal,
    )
    if items is None:
        items = [
            {"product_id": ln.product_id, "qty": ln.qty_ordered - ln.qty_received,
             "unit_cost": ln.unit_cost}
            for ln in po.lines.all() if (ln.qty_ordered - ln.qty_received) > 0
        ]
    for it in items:
        qty = Decimal(str(it["qty"]))
        if qty <= 0:
            continue
        cost = to_money(it["unit_cost"])
        GoodsReceiptLine.objects.create(
            gr=gr, product_id=it["product_id"], qty=qty, unit_cost=cost, origin_terminal=terminal,
        )
        receive_goods(product_id=it["product_id"], qty=qty, unit_cost=cost,
                      supplier=po.supplier, ref_id=str(gr.id))
        line = po.lines.filter(product_id=it["product_id"]).first()
        if line:
            line.qty_received = (line.qty_received or Decimal("0")) + qty
            line.save(update_fields=["qty_received"])

    fully = all((ln.qty_received or 0) >= ln.qty_ordered for ln in po.lines.all())
    po.status = PurchaseOrder.RECEIVED if fully else PurchaseOrder.ORDERED
    po.save(update_fields=["status"])
    return gr


@transaction.atomic
def receive_direct(*, supplier, items, terminal=""):
    """Receive goods without a prior PO (ad-hoc delivery). Creates a GoodsReceipt and rolls
    weighted-average cost for each line (spec §14.2). `items` = [{product_id, qty, unit_cost}]."""
    gr = GoodsReceipt.objects.create(
        po=None, supplier=supplier, terminal=terminal, origin_terminal=terminal,
    )
    lines = []
    for it in items:
        qty = Decimal(str(it["qty"]))
        if qty <= 0:
            continue
        cost = to_money(it["unit_cost"])
        GoodsReceiptLine.objects.create(
            gr=gr, product_id=it["product_id"], qty=qty, unit_cost=cost, origin_terminal=terminal,
        )
        r = receive_goods(product_id=it["product_id"], qty=qty, unit_cost=cost,
                          supplier=supplier, ref_id=str(gr.id))
        lines.append({"product": str(it["product_id"]), "qty": str(qty),
                      "cost_avg": str(r["cost_avg"]), "qty_on_hand": str(r["qty_on_hand"])})
    return {"receipt": str(gr.id), "lines": lines}


@transaction.atomic
def record_purchase_invoice(*, supplier, supplier_number, subtotal, tva_total, po=None,
                            invoice_date=None):
    """Register the supplier's invoice (accounts payable; TVA deductible feeds §15)."""
    subtotal = to_money(subtotal)
    tva_total = to_money(tva_total)
    return PurchaseInvoice.objects.create(
        supplier=supplier, po=po, supplier_number=supplier_number, invoice_date=invoice_date,
        subtotal=subtotal, tva_total=tva_total, total=to_money(subtotal + tva_total),
    )
