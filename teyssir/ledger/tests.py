from decimal import Decimal

from django.db.models import Sum
from django.test import TestCase

from teyssir.billing.models import FiscalStampConfig
from teyssir.catalog.models import Product, TaxRate
from teyssir.ledger.models import JournalEntry
from teyssir.ledger.services import (
    post_sale_to_gl, post_sales_to_gl, seed_chart, trial_balance,
)
from teyssir.purchasing.services import receive_goods
from teyssir.sales.models import Sale, SaleLine
from teyssir.sales.services import finalize_sale


class GeneralLedgerTests(TestCase):
    def setUp(self):
        seed_chart()
        FiscalStampConfig.objects.create(doc_type="FACTURE", amount=Decimal("1.000"))
        tva7 = TaxRate.objects.create(name="TVA 7%", rate_percent=Decimal("7.00"))
        self.product = Product.objects.create(
            sku="PEN", name_fr="Stylo", tax_rate=tva7, sale_price=Decimal("0.850"),
        )
        receive_goods(product_id=self.product.id, qty=Decimal("100"), unit_cost=Decimal("0.400"))
        sale = Sale.objects.create(terminal="C1", status=Sale.DRAFT)
        SaleLine.objects.create(sale=sale, product=self.product, qty=Decimal("3"),
                                unit_price=Decimal("0.850"), tax_rate=Decimal("7.00"))
        finalize_sale(sale, payment_method="CASH")
        self.sale = sale

    def test_sale_posts_balanced_double_entry(self):
        entry = post_sale_to_gl(self.sale)
        agg = entry.lines.aggregate(d=Sum("debit"), c=Sum("credit"))
        self.assertEqual(agg["d"], agg["c"])                          # double-entry balances

        by = {line.account.code: (line.debit, line.credit) for line in entry.lines.all()}
        self.assertEqual(by["531"][0], Decimal("3.729"))             # Dr Caisse = total tendered
        self.assertEqual(by["700"][1], Decimal("2.550"))             # Cr Ventes (ex-VAT)
        self.assertEqual(by["4367"][1], Decimal("0.179"))            # Cr TVA collectée
        self.assertEqual(by["4471"][1], Decimal("1.000"))            # Cr Droit de timbre
        self.assertEqual(by["607"][0], Decimal("1.200"))            # Dr COGS = 3 * 0.400
        self.assertEqual(by["370"][1], Decimal("1.200"))            # Cr Stocks

    def test_trial_balance_balances_and_posting_is_idempotent(self):
        self.assertEqual(post_sales_to_gl(), 1)
        tb = trial_balance()
        self.assertTrue(tb["balanced"])
        self.assertEqual(tb["total_debit"], tb["total_credit"])

        self.assertEqual(post_sales_to_gl(), 0)                       # idempotent re-run
        self.assertEqual(JournalEntry.objects.filter(ref_type="SALE").count(), 1)

    def test_full_postings_and_balance_sheet(self):
        from teyssir.customers.models import Customer
        from teyssir.customers.services import post_payment
        from teyssir.ledger.services import financial_statements, post_all_to_gl
        from teyssir.purchasing.models import Supplier
        from teyssir.purchasing.services import receive_direct

        receive_direct(supplier=Supplier.objects.create(name="Sup"),
                       items=[{"product_id": self.product.id, "qty": "50", "unit_cost": "0.400"}])
        post_payment(Customer.objects.create(name="Cust"), Decimal("5.000"))

        counts = post_all_to_gl()
        self.assertEqual(counts, {
            "sales": 1, "returns": 0, "receipts": 1, "purchase_invoices": 0, "payments": 1,
        })

        fs = financial_statements()
        self.assertTrue(fs["balance_sheet"]["balanced"])              # A = L + Equity
        self.assertEqual(fs["income_statement"]["net_income"], "1.350")  # 2.550 revenue - 1.200 COGS

    def test_vat_declaration_collected_minus_deductible(self):
        import datetime

        from teyssir.ledger.services import post_all_to_gl, vat_declaration
        from teyssir.purchasing.models import Supplier
        from teyssir.purchasing.services import record_purchase_invoice

        record_purchase_invoice(supplier=Supplier.objects.create(name="Sup"),
                                supplier_number="F-2026-1", subtotal="100.000", tva_total="19.000")
        post_all_to_gl()  # sale collects 0.179 TVA ; purchase invoice books 19.000 deductible

        today = datetime.date.today()
        vd = vat_declaration(today, today)
        self.assertEqual(vd["tva_collected"], "0.179")
        self.assertEqual(vd["tva_deductible"], "19.000")
        self.assertEqual(vd["net_payable"], "-18.821")   # VAT credit carried forward

    def test_return_reverses_vat_in_gl(self):
        from teyssir.ledger.services import post_all_to_gl, post_return_to_gl, vat_declaration
        from teyssir.sales.services import process_return
        import datetime

        FiscalStampConfig.objects.get_or_create(doc_type="AVOIR", defaults={"amount": Decimal("0")})
        ret = process_return(
            original_sale=self.sale,
            items=[{"product_id": self.product.id, "qty": "3",
                    "unit_price": "0.850", "tax_rate": "7.00"}],
            refund_method="CASH",
        )
        entry = post_return_to_gl(ret)
        agg = entry.lines.aggregate(d=Sum("debit"), c=Sum("credit"))
        self.assertEqual(agg["d"], agg["c"])

        post_all_to_gl()
        today = datetime.date.today()
        vd = vat_declaration(today, today)
        # Sale collected 0.179; full return reverses it → net collected 0
        self.assertEqual(vd["tva_collected"], "0.000")

    def test_sale_without_payment_is_skipped_by_batch(self):
        from teyssir.ledger.services import post_sales_to_gl
        orphan = Sale.objects.create(terminal="C1", status=Sale.DRAFT)
        SaleLine.objects.create(sale=orphan, product=self.product, qty=Decimal("1"),
                                unit_price=Decimal("0.850"), tax_rate=Decimal("7.00"))
        finalize_sale(orphan)  # no payment_method
        # Batch must not raise; only the paid self.sale posts (already posted in other tests = 0 new)
        # Clear prior postings for this check:
        JournalEntry.objects.all().delete()
        self.assertEqual(post_sales_to_gl(), 1)  # only self.sale has CASH payment
