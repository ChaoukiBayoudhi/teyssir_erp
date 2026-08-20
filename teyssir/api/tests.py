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
        apply_movement(product_id=self.product.id, qty=10, reason=StockMovement.RECEIPT)
        self.user = User.objects.create_user("cashier", password="pw-strong-123")
        # Checkout is gated by create_sale (RBAC §10).
        from django.contrib.auth.models import Permission
        perm = Permission.objects.get(codename="create_sale")
        self.user.user_permissions.add(perm)
        # Refresh so has_perm sees the new grant (Django caches perms on the instance).
        self.user = User.objects.get(pk=self.user.pk)
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
        self.assertIn("sale_id", data)
        self.assertIn("printed", data)
        self.assertTrue(data["receipt_url"].endswith(f"/receipt"))
        # Receipt preview endpoint
        rr = self.client.get(data["receipt_url"])
        self.assertEqual(rr.status_code, 200)
        self.assertIn(data["invoice_number"], rr.content.decode("utf-8"))
        self.product.refresh_from_db()
        self.assertEqual(self.product.qty_on_hand, 7)

    def test_checkout_requires_customer_for_account(self):
        body = {"terminal": "C1", "payment_method": "ACCOUNT",
                "lines": [{"product": str(self.product.id), "qty": "1"}]}
        r = self.client.post("/api/v1/pos/checkout", body, format="json")
        self.assertEqual(r.status_code, 400)

    def test_checkout_applies_line_and_header_discount_before_vat(self):
        body = {
            "terminal": "C1", "payment_method": "CASH", "discount": "0.255",
            "lines": [{"product": str(self.product.id), "qty": "3", "discount": "0.255"}],
        }
        # gross 2.550 - line 0.255 = 2.295; header 0.255 → HT 2.040; TVA 7% = 0.143; +timbre 1
        r = self.client.post("/api/v1/pos/checkout", body, format="json")
        self.assertEqual(r.status_code, 201)
        data = r.json()
        self.assertEqual(data["subtotal"], "2.040")
        self.assertEqual(data["tax_total"], "0.143")
        self.assertEqual(data["total"], "3.183")

    def test_checkout_denied_without_create_sale(self):
        plain = User.objects.create_user("auditor", password="pw-strong-123")
        c = APIClient()
        c.force_authenticate(plain)
        r = c.post("/api/v1/pos/checkout", {
            "terminal": "C1", "payment_method": "CASH",
            "lines": [{"product": str(self.product.id), "qty": "1"}],
        }, format="json")
        self.assertEqual(r.status_code, 403)

    def test_diagnostics_requires_configure_system(self):
        anon = APIClient()
        self.assertIn(anon.get("/api/v1/diagnostics").status_code, (401, 403))
        # cashier without configure_system
        r = self.client.get("/api/v1/diagnostics")
        self.assertEqual(r.status_code, 403)
        owner = User.objects.create_superuser("owner", password="pw-strong-123")
        admin = APIClient()
        admin.force_authenticate(owner)
        ok = admin.get("/api/v1/diagnostics?ping=0")
        self.assertEqual(ok.status_code, 200)
        body = ok.json()
        for key in ("db", "tesseract", "ocr", "printer", "llm", "camera"):
            self.assertIn(key, body)

    def test_reprint_receipt_does_not_create_sale(self):
        from teyssir.sales.models import Sale
        body = {
            "terminal": "C1", "payment_method": "CASH",
            "lines": [{"product": str(self.product.id), "qty": "1"}],
        }
        r = self.client.post("/api/v1/pos/checkout", body, format="json")
        self.assertEqual(r.status_code, 201)
        sale_id = r.json()["sale_id"]
        before = Sale.objects.filter(status=Sale.FINALIZED).count()
        rr = self.client.get(f"/api/v1/pos/sales/{sale_id}/receipt?print=1&format=json")
        self.assertEqual(rr.status_code, 200)
        self.assertTrue(rr.json().get("printed"))
        self.assertIn("DUPLICATA", rr.json().get("text", ""))
        self.assertEqual(Sale.objects.filter(status=Sale.FINALIZED).count(), before)

    def test_me_lists_capabilities(self):
        r = self.client.get("/api/v1/me")
        self.assertEqual(r.status_code, 200)
        self.assertIn("capabilities", r.json())
        self.assertIn("create_sale", r.json()["capabilities"])
