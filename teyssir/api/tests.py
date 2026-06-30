from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from teyssir.billing.models import FiscalStampConfig
from teyssir.catalog.models import Barcode, Product, TaxRate
from teyssir.inventory.models import StockMovement
from teyssir.inventory.services import apply_movement

User = get_user_model()


class ApiTests(TestCase):
    def setUp(self):
        FiscalStampConfig.objects.create(doc_type="FACTURE", amount=Decimal("1.000"))
        self.tva7 = TaxRate.objects.create(name="TVA 7%", rate_percent=Decimal("7.00"))
        self.product = Product.objects.create(
            sku="PEN", name_fr="Stylo", tax_rate=self.tva7, sale_price=Decimal("0.850"),
        )
        Barcode.objects.create(product=self.product, value="6191234567890", symbology="EAN13")
        apply_movement(product_id=self.product.id, qty=Decimal("10"), reason=StockMovement.RECEIPT)
        self.user = User.objects.create_user("cashier", password="pw-strong-123")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_token_login(self):
        r = APIClient().post("/api/v1/auth/token",
                             {"username": "cashier", "password": "pw-strong-123"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("token", r.json())

    def test_endpoints_require_auth(self):
        r = APIClient().get("/api/v1/catalog/products/")
        self.assertIn(r.status_code, (401, 403))

    def test_product_search(self):
        r = self.client.get("/api/v1/catalog/products/?search=Stylo")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()), 1)
        self.assertEqual(r.json()[0]["tax_rate_percent"], "7.00")

    def test_barcode_lookup(self):
        r = self.client.get("/api/v1/catalog/products/?barcode=6191234567890")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()[0]["sku"], "PEN")

    def test_checkout_finalizes_and_decrements_stock(self):
        body = {"terminal": "C1", "payment_method": "CASH",
                "lines": [{"product": str(self.product.id), "qty": "3"}]}
        r = self.client.post("/api/v1/pos/checkout", body, format="json")
        self.assertEqual(r.status_code, 201)
        data = r.json()
        self.assertTrue(data["invoice_number"].startswith("C1-"))
        self.assertEqual(data["total"], "3.729")        # 2.550 + TVA7%(0.179) + timbre 1.000
        self.assertEqual(data["total_display"], "3.73")  # 2-dp display
        self.product.refresh_from_db()
        self.assertEqual(self.product.qty_on_hand, Decimal("7.000"))
