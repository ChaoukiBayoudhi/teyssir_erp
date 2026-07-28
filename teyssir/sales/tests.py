import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from teyssir.billing.models import FiscalStampConfig
from teyssir.catalog.models import Product
from teyssir.inventory.models import StockMovement
from teyssir.inventory.services import apply_movement
from teyssir.sales.cash import open_session, x_report, z_report
from teyssir.sales.models import Sale, SaleLine
from teyssir.sales.services import finalize_sale, process_return

User = get_user_model()


class FinalizeSaleTests(TestCase):
    def setUp(self):
        FiscalStampConfig.objects.create(doc_type="FACTURE", amount=Decimal("1.000"))
        self.product = Product.objects.create(
            sku="BIC-CRISTAL", name_fr="Stylo Bic Cristal",
            cost_avg=Decimal("0.400"), sale_price=Decimal("0.850"),
            qty_on_hand=Decimal("100.000"),
        )

    def _draft_with_one_line(self, qty="3", price="0.850", rate="7.00"):
        sale = Sale.objects.create(terminal="C1", status=Sale.DRAFT)
        SaleLine.objects.create(
            sale=sale, product=self.product,
            qty=Decimal(qty), unit_price=Decimal(price), tax_rate=Decimal(rate),
        )
        return sale

    def test_offline_finalize_decrements_stock_and_issues_invoice(self):
        when = timezone.make_aware(datetime.datetime(2026, 6, 15, 12, 0))
        sale = self._draft_with_one_line()

        invoice = finalize_sale(sale, when=when)
        sale.refresh_from_db()
        self.product.refresh_from_db()

        # stock ledger: one SALE movement of -3, cached on-hand 100 -> 97
        self.assertEqual(StockMovement.objects.filter(reason="SALE").count(), 1)
        self.assertEqual(self.product.qty_on_hand, Decimal("97.000"))

        # totals: base 3*0.850 = 2.550 ; TVA 7% = 0.179 (half-up) ; timbre 1.000
        self.assertEqual(sale.subtotal, Decimal("2.550"))
        self.assertEqual(sale.tax_total, Decimal("0.179"))
        self.assertEqual(sale.timbre_amount_snapshot, Decimal("1.000"))
        self.assertEqual(sale.total, Decimal("3.729"))
        self.assertEqual(sale.status, Sale.FINALIZED)

        # invoice: per-terminal+month number + snapshotted stamp
        self.assertEqual(invoice.fiscal_number, "C1-202606-0001")
        self.assertEqual(invoice.timbre_amount_snapshot, Decimal("1.000"))

    def test_cannot_finalize_twice(self):
        sale = self._draft_with_one_line()
        finalize_sale(sale)
        with self.assertRaises(ValueError):
            finalize_sale(sale)

    def test_line_and_header_discount_before_vat(self):
        sale = self._draft_with_one_line()
        line = sale.lines.get()
        line.discount = Decimal("0.255")  # 10% of 2.550
        line.save(update_fields=["discount"])
        sale.discount = Decimal("0.255")  # further 10% of remaining, allocated
        sale.save(update_fields=["discount"])
        finalize_sale(sale, payment_method="CASH")
        sale.refresh_from_db()
        # 2.550 - 0.255 = 2.295; header 0.255 → HT 2.040; TVA 7% = 0.143; timbre 1.000
        self.assertEqual(sale.subtotal, Decimal("2.040"))
        self.assertEqual(sale.tax_total, Decimal("0.143"))
        self.assertEqual(sale.total, Decimal("3.183"))

    def test_discount_cannot_exceed_line(self):
        from teyssir.sales.services import DiscountError
        sale = self._draft_with_one_line()
        line = sale.lines.get()
        line.discount = Decimal("9.000")
        line.save(update_fields=["discount"])
        with self.assertRaises(DiscountError):
            finalize_sale(sale)


class ReturnTests(TestCase):
    def setUp(self):
        FiscalStampConfig.objects.create(doc_type="FACTURE", amount=Decimal("1.000"))
        FiscalStampConfig.objects.create(doc_type="AVOIR", amount=Decimal("0.000"))
        self.product = Product.objects.create(
            sku="PEN", name_fr="Stylo", sale_price=Decimal("0.850"), qty_on_hand=Decimal("100.000"),
        )
        self.when = timezone.make_aware(datetime.datetime(2026, 6, 15, 12, 0))

    def test_return_issues_avoir_restores_stock_keeps_original(self):
        sale = Sale.objects.create(terminal="C1", status=Sale.DRAFT)
        SaleLine.objects.create(sale=sale, product=self.product, qty=Decimal("3"),
                                unit_price=Decimal("0.850"), tax_rate=Decimal("7.00"))
        invoice = finalize_sale(sale, when=self.when)
        self.product.refresh_from_db()
        self.assertEqual(self.product.qty_on_hand, Decimal("97.000"))

        ret = process_return(
            original_sale=sale, when=self.when,
            items=[{"product_id": self.product.id, "qty": "1",
                    "unit_price": "0.850", "tax_rate": "7.00"}],
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.qty_on_hand, Decimal("98.000"))     # 1 returned to stock
        self.assertEqual(ret.number, "C1-AV-202606-0001")                 # AVOIR series, distinct
        self.assertEqual(ret.total, Decimal("0.910"))                     # 0.850 + 0.060 TVA + 0 timbre
        # original invoice is untouched (immutability) and never collides with the avoir
        invoice.refresh_from_db()
        self.assertEqual(invoice.fiscal_number, "C1-202606-0001")         # FACTURE series, separate
        self.assertEqual(StockMovement.objects.filter(reason="RETURN").count(), 1)
        self.assertEqual(self.product.movements.filter(reason="RETURN").count(), 1)

    def test_return_rejects_over_qty_and_unknown_product(self):
        sale = Sale.objects.create(terminal="C1", status=Sale.DRAFT)
        SaleLine.objects.create(sale=sale, product=self.product, qty=Decimal("2"),
                                unit_price=Decimal("0.850"), tax_rate=Decimal("7.00"))
        finalize_sale(sale, when=self.when)
        other = Product.objects.create(sku="OTHER", name_fr="Autre", sale_price=Decimal("1.000"))
        with self.assertRaises(ValueError):
            process_return(original_sale=sale, items=[{
                "product_id": other.id, "qty": "1", "unit_price": "1.000", "tax_rate": "0",
            }])
        with self.assertRaises(ValueError):
            process_return(original_sale=sale, items=[{
                "product_id": self.product.id, "qty": "3", "unit_price": "0.850", "tax_rate": "7",
            }])


class CashSessionTests(TestCase):
    def setUp(self):
        FiscalStampConfig.objects.create(doc_type="FACTURE", amount=Decimal("1.000"))
        self.product = Product.objects.create(
            sku="PEN", name_fr="Stylo", sale_price=Decimal("0.850"), qty_on_hand=Decimal("100.000"),
        )
        self.user = User.objects.create_user("cashier", password="pw-strong-123")

    def test_x_and_z_reports_reconcile(self):
        session = open_session(user=self.user, terminal="C1", opening_float="50.000")
        for q in ("3", "2"):
            sale = Sale.objects.create(terminal="C1", status=Sale.DRAFT, cash_session=session)
            SaleLine.objects.create(sale=sale, product=self.product, qty=Decimal(q),
                                    unit_price=Decimal("0.850"), tax_rate=Decimal("7.00"))
            finalize_sale(sale, payment_method="CASH")

        x = x_report(session)                       # mid-shift read, no close
        self.assertEqual(x["type"], "X")
        self.assertEqual(x["sales_count"], 2)
        self.assertEqual(x["cash_sales"], "6.548")  # 3.729 + 2.819
        self.assertEqual(x["expected_cash"], "56.548")  # 50 float + 6.548

        z = z_report(session, counted_cash="56.548")
        self.assertEqual(z["variance"], "0.000")    # counted matches expected
        session.refresh_from_db()
        self.assertIsNotNone(session.closed_at)
        self.assertEqual(session.variance, Decimal("0.000"))

    def test_cash_session_syncs_with_user_fk(self):
        import json

        from teyssir.sales.models import CashSession
        from teyssir.sync.models import SyncOutbox
        from teyssir.sync.services import apply_push

        session = open_session(user=self.user, terminal="C1", opening_float="50.000")
        entry = SyncOutbox.objects.get(entity="sales.CashSession")
        self.assertEqual(json.loads(entry.payload)[0]["model"], "sales.cashsession")

        sid = session.id
        CashSession.objects.filter(pk=sid).delete()
        apply_push([{"id": str(entry.id), "seq": entry.seq, "payload": entry.payload}])
        self.assertEqual(CashSession.objects.filter(pk=sid).count(), 1)
        # user FK resolves because users are UUID-stable + hub-replicated
        self.assertEqual(CashSession.objects.get(pk=sid).user_id, self.user.id)
