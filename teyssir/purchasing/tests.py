from decimal import Decimal

from django.test import TestCase

from teyssir.catalog.models import Product
from teyssir.purchasing.models import PurchaseOrder, Supplier
from teyssir.purchasing.services import (
    create_po, receive_direct, receive_goods, receive_po, record_purchase_invoice,
)


class WeightedAverageCostTests(TestCase):
    def test_weighted_average_cost_rolls_correctly(self):
        p = Product.objects.create(sku="X", name_fr="X", cost_avg=Decimal("0"), qty_on_hand=Decimal("0"))

        r1 = receive_goods(product_id=p.id, qty=Decimal("10"), unit_cost=Decimal("0.400"))
        self.assertEqual(r1["cost_avg"], Decimal("0.400"))
        self.assertEqual(r1["qty_on_hand"], Decimal("10.000"))

        # 10 @ 0.400 + 10 @ 0.600 -> avg 0.500, qty 20
        r2 = receive_goods(product_id=p.id, qty=Decimal("10"), unit_cost=Decimal("0.600"))
        self.assertEqual(r2["cost_avg"], Decimal("0.500"))
        self.assertEqual(r2["qty_on_hand"], Decimal("20.000"))


class PurchaseWorkflowTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(name="Sobflux")
        self.product = Product.objects.create(
            sku="CAH", name_fr="Cahier", cost_avg=Decimal("0"), qty_on_hand=Decimal("0"),
        )

    def test_po_receive_then_invoice(self):
        po = create_po(
            supplier=self.supplier,
            items=[{"product_id": self.product.id, "qty": "10", "unit_cost": "0.400"}],
        )
        self.assertEqual(po.status, PurchaseOrder.ORDERED)
        self.assertEqual(po.lines.count(), 1)

        gr = receive_po(po=po)                       # receive the whole PO
        self.product.refresh_from_db()
        po.refresh_from_db()
        self.assertEqual(self.product.qty_on_hand, Decimal("10.000"))
        self.assertEqual(self.product.cost_avg, Decimal("0.400"))
        self.assertEqual(po.status, PurchaseOrder.RECEIVED)
        self.assertEqual(po.lines.first().qty_received, Decimal("10.000"))
        self.assertEqual(gr.lines.count(), 1)

        inv = record_purchase_invoice(
            supplier=self.supplier, supplier_number="F-2026-77",
            subtotal="4.000", tva_total="0.280", po=po,
        )
        self.assertEqual(inv.total, Decimal("4.280"))
        self.assertEqual(inv.status, "UNPAID")

    def test_direct_receive_rolls_cost(self):
        self.product.cost_avg = Decimal("0.400")
        self.product.qty_on_hand = Decimal("10.000")
        self.product.save()
        # 10 @ 0.400 already; receive 10 @ 0.600 -> avg 0.500, qty 20
        result = receive_direct(
            supplier=self.supplier, terminal="C1",
            items=[{"product_id": self.product.id, "qty": "10", "unit_cost": "0.600"}],
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.cost_avg, Decimal("0.500"))
        self.assertEqual(self.product.qty_on_hand, Decimal("20.000"))
        self.assertEqual(result["lines"][0]["cost_avg"], "0.500")

