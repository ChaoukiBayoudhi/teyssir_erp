import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from teyssir.billing.models import FiscalStampConfig
from teyssir.catalog.models import Product, TaxRate
from teyssir.purchasing.services import receive_goods
from teyssir.reports.services import sales_report
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
        receive_goods(product_id=self.product.id, qty=Decimal("100"), unit_cost=Decimal("0.400"))
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
        self.assertEqual(rep["best_sellers"][0]["qty"], "5.000")
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
