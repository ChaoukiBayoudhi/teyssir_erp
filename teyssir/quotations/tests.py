import json
from decimal import Decimal

from django.test import TestCase

from teyssir.billing.models import FiscalStampConfig
from teyssir.catalog.models import Product
from teyssir.quotations.models import Quotation, Reservation
from teyssir.quotations.services import (
    convert_quotation, create_quotation, create_reservation, release_reservation,
)
from teyssir.sync.models import SyncOutbox
from teyssir.sync.services import apply_push


class QuotationTests(TestCase):
    def setUp(self):
        FiscalStampConfig.objects.create(doc_type="FACTURE", amount=Decimal("1.000"))
        self.product = Product.objects.create(
            sku="PEN", name_fr="Stylo", sale_price=Decimal("0.850"), qty_on_hand=Decimal("100.000"),
        )

    def test_create_quote_then_convert(self):
        q = create_quotation(
            terminal="C1",
            items=[{"product_id": self.product.id, "qty": "3",
                    "unit_price": "0.850", "tax_rate": "7.00"}],
        )
        self.assertEqual(q.subtotal, Decimal("2.550"))
        self.assertEqual(q.total, Decimal("2.729"))            # ex-timbre (2.550 + 0.179)
        self.product.refresh_from_db()
        self.assertEqual(self.product.qty_on_hand, Decimal("100.000"))  # quote moves no stock

        invoice = convert_quotation(q, payment_method="CASH")
        q.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(q.status, Quotation.CONVERTED)
        self.assertEqual(self.product.qty_on_hand, Decimal("97.000"))   # now sold
        self.assertTrue(invoice.fiscal_number.startswith("C1-"))

        with self.assertRaises(ValueError):                    # cannot convert twice
            convert_quotation(q, payment_method="CASH")

    def test_reservation_lifecycle(self):
        r = create_reservation(product_id=self.product.id, qty="2", customer_id="ecole-1")
        self.assertEqual(r.status, Reservation.HELD)
        release_reservation(r)
        r.refresh_from_db()
        self.assertEqual(r.status, Reservation.RELEASED)


class QuotationSyncTests(TestCase):
    def setUp(self):
        FiscalStampConfig.objects.create(doc_type="FACTURE", amount=Decimal("1.000"))
        self.product = Product.objects.create(
            sku="PEN", name_fr="Stylo", sale_price=Decimal("0.850"), qty_on_hand=Decimal("100.000"),
        )

    def test_quotation_enqueued_and_round_trips_to_hub(self):
        q = create_quotation(
            terminal="C1",
            items=[{"product_id": self.product.id, "qty": "3",
                    "unit_price": "0.850", "tax_rate": "7.00"}],
        )
        entry = SyncOutbox.objects.get(entity="quotations.Quotation")
        models = [r["model"] for r in json.loads(entry.payload)]
        self.assertIn("quotations.quotation", models)
        self.assertIn("quotations.quotationline", models)

        # hub side: drop locally, then apply the pushed aggregate (idempotent by UUID)
        Quotation.objects.all().delete()
        apply_push([{"id": str(entry.id), "seq": entry.seq, "payload": entry.payload}])
        self.assertEqual(Quotation.objects.filter(pk=q.id).count(), 1)
        self.assertEqual(Quotation.objects.get(pk=q.id).lines.count(), 1)

    def test_reservation_enqueued(self):
        create_reservation(product_id=self.product.id, qty="2", terminal="C1")
        entry = SyncOutbox.objects.get(entity="quotations.Reservation")
        self.assertEqual(json.loads(entry.payload)[0]["model"], "quotations.reservation")
