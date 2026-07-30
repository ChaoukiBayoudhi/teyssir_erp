import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from teyssir.billing.models import FiscalStampConfig
from teyssir.catalog.models import Product, TaxRate
from teyssir.purchasing.services import receive_goods
from teyssir.reports.services import consolidated_sales_by_store, sales_report
from teyssir.sales.models import Sale, SaleLine
from teyssir.sales.services import finalize_sale

User = get_user_model()


class SalesReportTests(TestCase):
    def setUp(self):
        FiscalStampConfig.objects.create(doc_type="FACTURE", amount=Decimal("1.000"))
        tva7 = TaxRate.objects.create(name="TVA 7%", rate_percent=Decimal("7.00"))
        self.product = Product.objects.create(
            sku="PEN", name_fr="Stylo Bic", tax_rate=tva7, sale_price=Decimal("0.850"),
        )
        receive_goods(product_id=self.product.id, qty=100, unit_cost=Decimal("0.400"))
        for q in ("3", "2"):
            sale = Sale.objects.create(terminal="C1", status=Sale.DRAFT)
            SaleLine.objects.create(sale=sale, product=self.product, qty=Decimal(q),
                                    unit_price=Decimal("0.850"), tax_rate=Decimal("7.00"))
            finalize_sale(sale, payment_method="CASH")
        self.today = datetime.date.today()

    def test_report_math(self):
        rep = sales_report(self.today, self.today)
        self.assertEqual(rep["sales_count"], 2)
        self.assertEqual(rep["revenue_ex_tax"], "4.250")    # 2.550 + 1.700
        self.assertEqual(rep["cogs"], "2.000")              # (3+2) * 0.400
        self.assertEqual(rep["gross_profit"], "2.250")      # 4.250 - 2.000
        self.assertEqual(rep["best_sellers"][0]["qty"], "5")
        self.assertEqual(rep["payment_mix"][0]["method"], "CASH")

    def test_endpoint_requires_report_permission(self):
        plain = User.objects.create_user("clerk", password="pw-strong-123")
        c = APIClient()
        c.force_authenticate(plain)
        r = c.get(f"/api/v1/reports/sales?from={self.today}&to={self.today}")
        self.assertEqual(r.status_code, 403)               # lacks view_financial_reports

    def test_endpoint_allows_privileged_user(self):
        boss = User.objects.create_superuser("owner", password="pw-strong-123")
        c = APIClient()
        c.force_authenticate(boss)
        r = c.get(f"/api/v1/reports/sales?from={self.today}&to={self.today}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["gross_profit"], "2.250")


class ConsolidatedReportTests(TestCase):
    """Phase 6: cross-store roll-up by Invoice.store_code at a cloud hub."""

    def setUp(self):
        FiscalStampConfig.objects.create(doc_type="FACTURE", amount=Decimal("1.000"))
        tva7 = TaxRate.objects.create(name="TVA 7%", rate_percent=Decimal("7.00"))
        self.product = Product.objects.create(
            sku="PEN", name_fr="Stylo", tax_rate=tva7, sale_price=Decimal("0.850"))
        receive_goods(product_id=self.product.id, qty=100, unit_cost=Decimal("0.400"))
        self.today = datetime.date.today()

    def _sale(self, qty):
        sale = Sale.objects.create(terminal="C1", status=Sale.DRAFT)
        SaleLine.objects.create(sale=sale, product=self.product, qty=Decimal(qty),
                                unit_price=Decimal("0.850"), tax_rate=Decimal("7.00"))
        return finalize_sale(sale, payment_method="CASH")

    def test_rollup_disaggregates_by_store(self):
        with override_settings(STORE_CODE="S1"):
            self._sale("3")
            self._sale("2")
        with override_settings(STORE_CODE="S2"):
            self._sale("1")

        rep = consolidated_sales_by_store(self.today, self.today)
        by = {s["store_code"]: s for s in rep["stores"]}
        self.assertEqual(by["S1"]["sales_count"], 2)
        self.assertEqual(by["S1"]["revenue_ex_tax"], "4.250")   # (3+2) * 0.850
        self.assertEqual(by["S2"]["sales_count"], 1)
        self.assertEqual(by["S2"]["revenue_ex_tax"], "0.850")
        self.assertEqual(rep["grand_total"]["sales_count"], 3)
        self.assertEqual(rep["grand_total"]["revenue_ex_tax"], "5.100")

    def test_sales_report_store_filter(self):
        with override_settings(STORE_CODE="S1"):
            self._sale("3")
        with override_settings(STORE_CODE="S2"):
            self._sale("2")
        self.assertEqual(sales_report(self.today, self.today, store="S1")["sales_count"], 1)
        self.assertEqual(sales_report(self.today, self.today, store="S1")["revenue_ex_tax"], "2.550")
        self.assertEqual(sales_report(self.today, self.today)["sales_count"], 2)   # unfiltered = both
