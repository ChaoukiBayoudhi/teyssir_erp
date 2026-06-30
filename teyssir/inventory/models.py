from django.db import models

from teyssir.catalog.models import Product
from teyssir.core.models import MONEY, QTY, SyncableModel


class StockMovement(SyncableModel):
    """Append-only stock ledger (spec §7.3/§14.1). On-hand is a *fold* over these rows.

    Sync merges movements by union (commutative); the cached `Product.qty_on_hand` can
    go negative across offline tills and is reconciled at the hub (spec §4.4).
    """

    RECEIPT = "RECEIPT"
    SALE = "SALE"
    RETURN = "RETURN"
    ADJUST = "ADJUST"
    TRANSFER = "TRANSFER"
    STOCKTAKE = "STOCKTAKE"
    REASONS = [(x, x) for x in (RECEIPT, SALE, RETURN, ADJUST, TRANSFER, STOCKTAKE)]

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="movements")
    qty = models.DecimalField(**QTY)  # signed: + in, - out
    reason = models.CharField(max_length=12, choices=REASONS)
    unit_cost = models.DecimalField(default=0, **MONEY)
    ref_type = models.CharField(max_length=24, blank=True, default="")
    ref_id = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["product", "created_at"])]

    def __str__(self):
        return f"{self.reason} {self.qty} of {self.product_id}"
