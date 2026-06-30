import datetime
import json
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from teyssir.billing.models import FiscalStampConfig
from teyssir.catalog.models import Product, TaxRate
from teyssir.inventory.models import StockMovement
from teyssir.inventory.services import apply_movement
from teyssir.sales.models import Sale, SaleLine
from teyssir.sales.services import finalize_sale
from teyssir.sync.models import SyncOutbox
from teyssir.sync.services import apply_master_changes, apply_push, collect_master_changes

WHEN = timezone.make_aware(datetime.datetime(2026, 6, 15, 12, 0))


def as_entry(e):
    return {"id": str(e.id), "seq": e.seq, "payload": e.payload}


@override_settings(SYNC_KEY="test-key")
class SyncTests(TestCase):
    def setUp(self):
        FiscalStampConfig.objects.create(doc_type="FACTURE", amount=Decimal("1.000"))
        self.tva7 = TaxRate.objects.create(name="TVA 7%", rate_percent=Decimal("7.00"))
        self.product = Product.objects.create(
            sku="PEN-1", name_fr="Stylo", tax_rate=self.tva7,
            cost_avg=Decimal("0.400"), sale_price=Decimal("0.850"),
        )

    def _stock(self, qty):
        apply_movement(product_id=self.product.id, qty=Decimal(qty), reason=StockMovement.RECEIPT)

    def _sale(self, terminal, qty="1"):
        sale = Sale.objects.create(terminal=terminal, status=Sale.DRAFT)
        SaleLine.objects.create(
            sale=sale, product=self.product, qty=Decimal(qty),
            unit_price=Decimal("0.850"), tax_rate=Decimal("7.00"),
        )
        return finalize_sale(sale, when=WHEN)

    # --- outbox population ---------------------------------------------------
    def test_finalize_enqueues_sale_aggregate(self):
        self._stock("10")
        self._sale("C1")
        entry = SyncOutbox.objects.get()
        self.assertEqual(entry.entity, "sales.Sale")
        models = [r["model"] for r in json.loads(entry.payload)]
        self.assertIn("sales.sale", models)
        self.assertIn("inventory.stockmovement", models)
        self.assertIn("billing.invoice", models)

    # --- hub push endpoint ---------------------------------------------------
    def test_push_endpoint_is_idempotent(self):
        self._stock("10")
        self._sale("C1")
        entries = [as_entry(e) for e in SyncOutbox.objects.all()]
        client = APIClient()
        for _ in range(2):  # deliver the same batch twice
            r = client.post("/api/v1/sync/push", {"entries": entries},
                            format="json", HTTP_X_SYNC_KEY="test-key")
            self.assertEqual(r.status_code, 200)
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(StockMovement.objects.filter(reason="SALE").count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.qty_on_hand, Decimal("9.000"))  # 10 received - 1 sold

    def test_push_requires_sync_key(self):
        r = APIClient().post("/api/v1/sync/push", {"entries": []}, format="json")
        self.assertEqual(r.status_code, 403)

    # --- hub pull endpoint ---------------------------------------------------
    def test_pull_returns_master_data(self):
        r = APIClient().get("/api/v1/sync/pull", HTTP_X_SYNC_KEY="test-key")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("cursor", body)
        models = {row["model"] for row in json.loads(body["records"])}
        self.assertIn("catalog.product", models)
        self.assertIn("catalog.taxrate", models)

    # --- headline: cross-till oversell is flagged, never blocked -------------
    def test_cross_till_oversell_reconciliation(self):
        self._stock("1")                       # exactly one in stock
        inv_a = self._sale("C1")               # till C1 sells it (offline)
        inv_b = self._sale("C2")               # till C2 ALSO sells it (offline) -> oversold
        # both sales succeeded locally with their own gapless series, no collision
        self.assertEqual(inv_a.fiscal_number, "C1-202606-0001")
        self.assertEqual(inv_b.fiscal_number, "C2-202606-0001")

        entries = [as_entry(e) for e in SyncOutbox.objects.order_by("seq")]
        result = apply_push(entries)           # hub merges both tills' movements
        self.product.refresh_from_db()
        self.assertEqual(self.product.qty_on_hand, Decimal("-1.000"))   # fold: 1 - 1 - 1
        self.assertEqual(len(result["reconciliation_warnings"]), 1)
        self.assertEqual(result["reconciliation_warnings"][0]["on_hand"], "-1.000")

    def test_config_snapshot_propagates_fiscal_stamp(self):
        # hub raises the timbre to 1.500
        FiscalStampConfig.objects.filter(doc_type="FACTURE").update(amount=Decimal("1.500"))
        collected = collect_master_changes()
        stamps = {c["doc_type"]: c["amount"] for c in collected["config"]["fiscal_stamps"]}
        self.assertEqual(stamps["FACTURE"], "1.500")

        # a till still holds the old 1.000 -> applying the pull upserts it to 1.500
        FiscalStampConfig.objects.all().delete()
        FiscalStampConfig.objects.create(doc_type="FACTURE", amount=Decimal("1.000"))
        apply_master_changes(collected["records"], config=collected["config"])
        self.assertEqual(
            FiscalStampConfig.objects.get(doc_type="FACTURE").amount, Decimal("1.500")
        )

    def test_identity_replicates_user_and_group_with_password(self):
        from django.contrib.auth.models import Group

        from teyssir.accounts.models import User

        group = Group.objects.create(name="Cashier")
        user = User.objects.create_user("sami", password="pw-strong-123")
        user.groups.add(group)
        uid = user.id

        collected = collect_master_changes()
        models = [r["model"] for r in json.loads(collected["records"])]
        self.assertIn("accounts.user", models)
        self.assertIn("auth.group", models)

        # a till that lacks this user applies the pull -> user usable offline
        User.objects.filter(pk=uid).delete()
        apply_master_changes(collected["records"], config=collected["config"])
        replicated = User.objects.get(pk=uid)
        self.assertEqual(replicated.username, "sami")
        self.assertTrue(replicated.check_password("pw-strong-123"))   # hash replicated -> offline login
        self.assertTrue(replicated.groups.filter(name="Cashier").exists())  # RBAC offline

    def test_book_metadata_replicates_hub_to_till(self):
        from teyssir.catalog.bookscan.services import create_book_from_draft
        from teyssir.catalog.models import Book

        product = create_book_from_draft(data={
            "title": "Le Petit Prince", "isbn13": "9782070612758", "authors": ["Saint-Exupéry"],
        }, sale_price="12.500")
        book = Book.objects.get(product=product)

        collected = collect_master_changes()
        models = [r["model"] for r in json.loads(collected["records"])]
        self.assertIn("catalog.book", models)
        self.assertIn("catalog.bookcontributor", models)

        # a till that lacks the book applies the pull -> book + contributor recreated
        Book.objects.filter(pk=book.pk).delete()
        apply_master_changes(collected["records"], config=collected["config"])
        replicated = Book.objects.get(pk=book.pk)
        self.assertEqual(replicated.contributors.first().contributor.name, "Saint-Exupéry")

    def test_fetch_missing_media_downloads_cover_files(self):
        import tempfile
        from io import BytesIO

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from PIL import Image

        from teyssir.catalog.models import ProductImage
        from teyssir.sync.client import fetch_missing_media

        buf = BytesIO()
        Image.new("RGB", (8, 8), "white").save(buf, "PNG")
        with override_settings(MEDIA_ROOT=tempfile.mkdtemp()):
            img = ProductImage.objects.create(
                product=self.product,
                image=SimpleUploadedFile("c.png", buf.getvalue(), content_type="image/png"))
            name = img.image.name
            img.image.storage.delete(name)                    # till has the row but not the file
            self.assertFalse(img.image.storage.exists(name))

            n = fetch_missing_media("http://hub", "k", fetch=lambda nm: b"PNGDATA")
            self.assertEqual(n, 1)
            self.assertTrue(img.image.storage.exists(name))   # file pulled from the hub
