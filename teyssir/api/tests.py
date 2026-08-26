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

    def test_product_search_partial_case_accents_and_codes(self):
        """POS search: exact/partial/case/Arabic/accents/reference/barcode (Phase 14)."""
        Product.objects.create(
            sku="BK-PP", name_fr="Le Petit Prince", name_ar="الأمير الصغير",
            tax_rate=self.tva7, sale_price=Decimal("12.000"),
            product_type=Product.BOOK, is_book=True, isbn="9782070612758",
        )
        sac = Product.objects.create(
            sku="1001", reference="1001", name_fr="Sac à dos",
            tax_rate=self.tva7, sale_price=Decimal("45.000"),
        )
        Barcode.objects.create(product=sac, value="6199988776655", symbology="EAN13")

        cases = [
            ("Stylo", "Stylo"),           # A exact FR name
            ("sty", "Stylo"),             # B partial
            ("STYLO", "Stylo"),           # C case-insensitive
            ("الأمير", "Le Petit Prince"),  # D Arabic
            ("Sac à", "Sac à dos"),       # E French accents
            ("  Sac  ", "Sac à dos"),     # F whitespace trim
            ("1001", "Sac à dos"),        # G furniture reference
            ("6199988776655", "Sac à dos"),  # H barcode
            ("9782070612758", "Le Petit Prince"),  # ISBN
        ]
        for q, expect in cases:
            with self.subTest(q=q):
                r = self.client.get("/api/v1/catalog/products/", {"search": q})
                self.assertEqual(r.status_code, 200, r.content)
                names = [p["name_fr"] for p in r.json()]
                self.assertIn(expect, names, f"search={q!r} -> {names}")

        # q= alias matches search=
        r = self.client.get("/api/v1/catalog/products/", {"q": "sty"})
        self.assertEqual(r.json()[0]["name_fr"], "Stylo")

        # Rank: exact name before contains-only when both match
        Product.objects.create(
            sku="STY-X", name_fr="Stylo Bic", tax_rate=self.tva7, sale_price=Decimal("1.000"),
        )
        ranked = self.client.get("/api/v1/catalog/products/", {"search": "Stylo"}).json()
        self.assertEqual(ranked[0]["name_fr"], "Stylo")

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
