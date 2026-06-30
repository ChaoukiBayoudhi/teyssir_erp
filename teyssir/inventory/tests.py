import json
from decimal import Decimal

from django.test import TestCase

from teyssir.catalog.models import Product
from teyssir.inventory.models import StockMovement
from teyssir.inventory.services import apply_movement, post_stocktake
from teyssir.sync.models import SyncOutbox
from teyssir.sync.services import apply_push


class StockTakeTests(TestCase):
    def test_post_stocktake_posts_variance_adjustment(self):
        p = Product.objects.create(sku="X", name_fr="Cahier", qty_on_hand=Decimal("10.000"))
        result = post_stocktake([{"product_id": p.id, "counted_qty": "8"}], terminal="C1")

        p.refresh_from_db()
        self.assertEqual(p.qty_on_hand, Decimal("8.000"))          # cached on-hand corrected
        self.assertEqual(result["adjusted"], 1)
        self.assertEqual(result["lines"][0]["variance"], "-2.000")
        mv = StockMovement.objects.get(reason="STOCKTAKE")
        self.assertEqual(mv.qty, Decimal("-2.000"))                # audit trail of the correction

    def test_no_movement_when_count_matches(self):
        p = Product.objects.create(sku="Y", name_fr="Stylo", qty_on_hand=Decimal("5.000"))
        result = post_stocktake([{"product_id": p.id, "counted_qty": "5"}])
        self.assertEqual(result["adjusted"], 0)
        self.assertEqual(StockMovement.objects.filter(reason="STOCKTAKE").count(), 0)

    def test_stocktake_enqueues_and_rolls_up_at_hub(self):
        p = Product.objects.create(sku="Z", name_fr="Cahier")
        apply_movement(product_id=p.id, qty=Decimal("10"), reason=StockMovement.RECEIPT)  # ledger +10
        post_stocktake([{"product_id": p.id, "counted_qty": "8"}], terminal="C1")          # ADJUST -2

        entry = SyncOutbox.objects.get(entity="inventory.StockMovement")
        self.assertEqual(json.loads(entry.payload)[0]["model"], "inventory.stockmovement")

        # hub re-folds on-hand from the ledger union when it applies the pushed adjustment
        apply_push([{"id": str(entry.id), "seq": entry.seq, "payload": entry.payload}])
        p.refresh_from_db()
        self.assertEqual(p.qty_on_hand, Decimal("8.000"))   # 10 received - 2 stock-take
