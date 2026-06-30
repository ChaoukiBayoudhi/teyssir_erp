from decimal import Decimal

from django.test import TestCase

from teyssir.billing.models import FiscalStampConfig
from teyssir.catalog.models import Product, TaxRate
from teyssir.inventory.models import StockMovement
from teyssir.inventory.services import apply_movement
from teyssir.printing.devices import last_dummy_output, send
from teyssir.printing.receipt import render_sale_receipt, render_text
from teyssir.sales.models import Sale, SaleLine
from teyssir.sales.services import finalize_sale


class ReceiptTests(TestCase):
    def setUp(self):
        FiscalStampConfig.objects.create(doc_type="FACTURE", amount=Decimal("1.000"))
        tva7 = TaxRate.objects.create(name="TVA 7%", rate_percent=Decimal("7.00"))
        self.product = Product.objects.create(
            sku="PEN", name_fr="Stylo Bic", tax_rate=tva7, sale_price=Decimal("0.850"),
        )
        apply_movement(product_id=self.product.id, qty=Decimal("100"), reason=StockMovement.RECEIPT)
        sale = Sale.objects.create(terminal="C1", status=Sale.DRAFT)
        SaleLine.objects.create(sale=sale, product=self.product, qty=Decimal("3"),
                                unit_price=Decimal("0.850"), tax_rate=Decimal("7.00"))
        self.invoice = finalize_sale(sale, payment_method="CASH")
        self.sale = sale

    def test_text_receipt_has_key_fields(self):
        txt = render_text(self.sale)
        for needle in ["Teyssir Library", self.invoice.fiscal_number, "Stylo Bic",
                       "TVA 7%", "Timbre fiscal", "3.73 DT", "CASH"]:
            self.assertIn(needle, txt)

    def test_escpos_bytes_have_init_cut_kick_and_number(self):
        data = render_sale_receipt(self.sale)
        self.assertTrue(data.startswith(b"\x1b@"))                 # ESC @ init
        self.assertIn(b"\x1dV\x00", data)                          # GS V 0 -> cut
        self.assertIn(b"\x1bp", data)                              # ESC p -> drawer kick
        self.assertIn(self.invoice.fiscal_number.encode("cp1252"), data)

    def test_dummy_device_captures_bytes(self):
        data = render_sale_receipt(self.sale)
        self.assertEqual(send(data, target="dummy"), len(data))
        self.assertEqual(last_dummy_output(), data)
