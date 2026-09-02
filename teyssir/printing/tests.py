from decimal import Decimal

from django.test import TestCase, override_settings

from teyssir.billing.models import FiscalStampConfig
from teyssir.catalog.models import Product, TaxRate
from teyssir.inventory.models import StockMovement
from teyssir.inventory.services import apply_movement
from teyssir.printing.devices import last_dummy_output, send
from teyssir.printing.receipt import render_sale_receipt, render_text
from teyssir.sales.models import Sale, SaleLine
from teyssir.sales.services import finalize_sale


@override_settings(APPLY_VAT_AND_TIMBRE=True)
class ReceiptTests(TestCase):
    def setUp(self):
        FiscalStampConfig.objects.create(doc_type="FACTURE", amount=Decimal("1.000"))
        tva7 = TaxRate.objects.create(name="TVA 7%", rate_percent=Decimal("7.00"))
        self.product = Product.objects.create(
            sku="PEN", name_fr="Stylo Bic", tax_rate=tva7, sale_price=Decimal("0.850"),
        )
        apply_movement(product_id=self.product.id, qty=100, reason=StockMovement.RECEIPT)
        sale = Sale.objects.create(terminal="C1", status=Sale.DRAFT)
        SaleLine.objects.create(sale=sale, product=self.product, qty=3,
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

    def test_receipt_tva_matches_booked_tax_at_19_percent(self):
        """Regression: raw (base*rate/100) without HALF_UP printed 0.48 instead of 0.49."""
        tva19 = TaxRate.objects.create(name="TVA 19%", rate_percent=Decimal("19.00"))
        prod = Product.objects.create(
            sku="USB", name_fr="Clé USB", tax_rate=tva19, sale_price=Decimal("0.850"),
        )
        apply_movement(product_id=prod.id, qty=10, reason=StockMovement.RECEIPT)
        sale = Sale.objects.create(terminal="C1", status=Sale.DRAFT)
        SaleLine.objects.create(sale=sale, product=prod, qty=3,
                                unit_price=Decimal("0.850"), tax_rate=Decimal("19.00"))
        finalize_sale(sale, payment_method="CASH")
        sale.refresh_from_db()
        self.assertEqual(sale.tax_total, Decimal("0.485"))
        txt = render_text(sale)
        self.assertIn("0.49 DT", txt)   # display HALF_UP of 0.485
        # Sum of printed TVA lines must equal booked tax_total at storage scale
        from teyssir.printing.receipt import _receipt_model
        m = _receipt_model(sale)
        printed_tax = sum((t for _, (_b, t) in m["by_rate"]), Decimal("0"))
        self.assertEqual(printed_tax, sale.tax_total)

    def test_duplicate_receipt_marks_duplicata_without_kick(self):
        data = render_sale_receipt(self.sale, duplicate=True, kick=False)
        self.assertIn(b"DUPLICATA", data)
        self.assertNotIn(b"\x1bp", data)  # no drawer kick on reprint
        txt = render_text(self.sale, duplicate=True)
        self.assertIn("DUPLICATA", txt)
