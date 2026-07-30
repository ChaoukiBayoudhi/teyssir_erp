from django.db import transaction
from django.db.models import Sum

from teyssir.catalog.models import Product
from teyssir.core.qty import to_qty

from .models import StockMovement


@transaction.atomic
def apply_movement(*, product_id, qty, reason, unit_cost=0, ref_type="", ref_id="",
                   origin_terminal=""):
    """Append a movement to the ledger and update the cached on-hand fold (spec §7.3/§14.1).

    Locks the product row so the cached `qty_on_hand` update is consistent within this node.
    Cross-till oversell (negative on-hand) is allowed and reconciled at the hub (spec §4.4).
    Quantities are whole pieces (integers); fractional values are rejected.
    """
    qty = to_qty(qty, allow_negative=True)
    product = Product.objects.select_for_update().get(pk=product_id)
    movement = StockMovement.objects.create(
        product=product,
        qty=qty,
        reason=reason,
        unit_cost=unit_cost,
        ref_type=ref_type,
        ref_id=ref_id,
        origin_terminal=origin_terminal,
    )
    product.qty_on_hand = int(product.qty_on_hand or 0) + qty
    product.save(update_fields=["qty_on_hand"])
    return movement


def recompute_on_hand(product_id):
    """Re-derive on-hand from the ledger (hub reconciliation / drift check, spec §7.3)."""
    total = StockMovement.objects.filter(product_id=product_id).aggregate(s=Sum("qty"))["s"] or 0
    total = int(total)
    Product.objects.filter(pk=product_id).update(qty_on_hand=total)
    return total


@transaction.atomic
def post_stocktake(items, terminal=""):
    """Reconcile a physical inventory count (spec §14.4): post a STOCKTAKE adjustment for each
    variance (counted − system on-hand). `items` = [{product_id, counted_qty}]."""
    lines = []
    movements = []
    for it in items:
        product = Product.objects.select_for_update().get(pk=it["product_id"])
        counted = to_qty(it["counted_qty"], allow_negative=False, label="counted_qty")
        on_hand = int(product.qty_on_hand or 0)
        variance = counted - on_hand
        if variance != 0:
            movements.append(apply_movement(
                product_id=product.id, qty=variance, reason=StockMovement.STOCKTAKE,
                ref_type="STOCKTAKE", origin_terminal=terminal))
        lines.append({"product": str(product.id), "counted": str(counted),
                      "variance": str(variance)})
    if movements:
        from teyssir.sync.services import enqueue_movements  # lazy: avoid import cycle
        enqueue_movements(movements, terminal)
    return {"adjusted": len(movements), "lines": lines}
