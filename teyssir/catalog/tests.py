import json
import os
import tempfile
import unittest
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image, ImageDraw, ImageFont
from rest_framework.test import APIClient


def _tesseract_ready():
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return "fra" in pytesseract.get_languages()
    except Exception:
        return False

from teyssir.catalog.bookscan.draft import BookDraft
from teyssir.catalog.bookscan.services import create_book_from_draft, scan_book
from teyssir.catalog.models import Barcode, Book, Product, ProductImage

User = get_user_model()


def _png(name="cover.png"):
    buf = BytesIO()
    Image.new("RGB", (12, 16), "white").save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


class BookScanServiceTests(TestCase):
    def test_scan_is_isbn_first(self):
        draft, _ = scan_book(
            [], isbn="9782070612758",
            enrich=lambda i: BookDraft(title="Le Petit Prince",
                                       authors=["Antoine de Saint-Exupéry"],
                                       source="fake", confidence=0.9),
        )
        self.assertEqual(draft.title, "Le Petit Prince")
        self.assertEqual(draft.isbn13, "9782070612758")     # backfilled
        self.assertEqual(draft.source, "fake")

    def test_scan_falls_back_to_empty_when_no_isbn_no_ocr(self):
        draft, _ = scan_book([], isbn="", enrich=lambda i: None)
        self.assertEqual(draft.title, "")

    def test_create_book_from_draft_builds_normalized_records(self):
        product = create_book_from_draft(data={
            "title": "Le Petit Prince", "isbn13": "9782070612758", "publisher": "Gallimard",
            "pub_year": 1943, "pages": 96, "languages": ["fr"],
            "authors": ["Antoine de Saint-Exupéry"], "translators": [],
        }, sale_price="12.500")
        self.assertEqual(product.sku, "9782070612758")        # sku defaults to ISBN
        self.assertTrue(product.is_book)
        book = Book.objects.get(product=product)
        self.assertEqual(book.publisher, "Gallimard")
        self.assertEqual(book.contributors.count(), 1)
        self.assertEqual(book.contributors.first().contributor.name, "Antoine de Saint-Exupéry")
        self.assertTrue(Barcode.objects.filter(value="9782070612758", symbology="ISBN").exists())


@override_settings(OCR_PROVIDER="manual", METADATA_PROVIDERS=[], MEDIA_ROOT=tempfile.mkdtemp())
class BookScanApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_superuser("owner", password="pw-strong-123"))

    def test_scan_stores_image_and_returns_draft(self):
        r = self.client.post("/api/v1/catalog/books/scan",
                             {"images": _png(), "isbn": "9782070612758"}, format="multipart")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body["image_ids"]), 1)
        self.assertEqual(body["isbn13"], "9782070612758")     # echoed even with no metadata provider
        self.assertEqual(ProductImage.objects.count(), 1)

    def test_full_scan_then_create_with_image(self):
        scan = self.client.post("/api/v1/catalog/books/scan", {"images": _png()},
                                format="multipart").json()
        image_id = scan["image_ids"][0]
        r = self.client.post("/api/v1/catalog/books", {
            "title": "Le Petit Prince", "isbn13": "9782070612758", "sale_price": "12.500",
            "authors": ["Antoine de Saint-Exupéry"], "image_ids": [image_id],
        }, format="json")
        self.assertEqual(r.status_code, 201)
        product = Product.objects.get(sku="9782070612758")
        self.assertEqual(product.book.contributors.count(), 1)
        linked = ProductImage.objects.get(id=image_id)
        self.assertEqual(str(linked.product_id), str(product.id))
        self.assertTrue(linked.is_primary)


@unittest.skipUnless(_tesseract_ready(), "tesseract engine + fra language data not installed")
@override_settings(OCR_PROVIDER="tesseract")
class TesseractOcrTests(TestCase):
    def test_extracts_title_and_isbn_from_a_rendered_cover(self):
        from teyssir.catalog.bookscan.ocr import get_ocr_provider

        img = Image.new("RGB", (700, 320), "white")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 40)
        except Exception:
            font = ImageFont.load_default()
        draw.text((40, 40), "Le Petit Prince", fill="black", font=font)
        draw.text((40, 160), "ISBN 978-2-07-061275-8", fill="black", font=font)
        path = os.path.join(tempfile.mkdtemp(), "cover.png")
        img.save(path)

        text, detected = get_ocr_provider().extract(path)
        self.assertIn("Petit", text)
        self.assertEqual(detected.isbn13, "9782070612758")   # ISBN drives enrichment
        self.assertEqual(detected.title, "Le Petit Prince")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class CatalogBrowseApiTests(TestCase):
    """The catalogue browser: multi-criteria search, filters, sort, pagination, and detail."""

    def setUp(self):
        from decimal import Decimal

        from teyssir.catalog.models import Category

        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_superuser("owner", password="pw-strong-123"))
        self.cat = Category.objects.create(name_fr="Romans")
        self.book = create_book_from_draft(data={
            "title": "Le Petit Prince", "isbn13": "9782070612758", "publisher": "Gallimard",
            "authors": ["Antoine de Saint-Exupéry"]}, sale_price="12.500")
        self.book.category = self.cat
        self.book.qty_on_hand = 5; self.book.reorder_point = 3
        self.book.save()
        self.pen = Product.objects.create(sku="PEN-9", name_fr="Stylo Bic",
                                          sale_price=Decimal("0.850"), qty_on_hand=0)
        self.cah = Product.objects.create(sku="CAH-9", name_fr="Cahier", sale_price=Decimal("1.200"),
                                          qty_on_hand=2, reorder_point=5)

    def _skus(self, url, params=None):
        return {x["sku"] for x in self.client.get(url, params or {}).json()["results"]}

    def test_search_matches_title_author_isbn_barcode_publisher(self):
        for term in ("Petit", "Saint-Exupéry", "9782070612758", "Gallimard"):
            self.assertIn(self.book.sku, self._skus("/api/v1/catalog/search", {"q": term}),
                          f"search term did not match the book: {term}")

    def test_filters_type_stock_category(self):
        self.assertEqual(self._skus("/api/v1/catalog/search", {"type": "book"}), {self.book.sku})
        self.assertEqual(self._skus("/api/v1/catalog/search", {"stock": "out"}), {"PEN-9"})
        self.assertEqual(self._skus("/api/v1/catalog/search", {"stock": "low"}), {"CAH-9"})
        self.assertEqual(self._skus("/api/v1/catalog/search", {"category": str(self.cat.id)}),
                         {self.book.sku})

    def test_ordering_and_pagination(self):
        from decimal import Decimal

        r = self.client.get("/api/v1/catalog/search",
                            {"ordering": "price", "page_size": 2, "page": 1}).json()
        self.assertEqual(set(r), {"count", "page", "page_size", "num_pages", "results"})
        self.assertEqual((r["count"], r["page_size"], r["num_pages"]), (3, 2, 2))
        prices = [Decimal(x["sale_price"]) for x in r["results"]]
        self.assertEqual(prices, sorted(prices))                       # ascending by price
        page2 = self.client.get("/api/v1/catalog/search", {"page_size": 2, "page": 2}).json()
        self.assertEqual(len(page2["results"]), 1)                     # 3 items, 2 per page

    def test_detail_returns_full_profile(self):
        d = self.client.get(f"/api/v1/catalog/products/{self.book.id}/detail").json()
        self.assertEqual(d["book"]["publisher"], "Gallimard")
        self.assertEqual(d["book"]["contributors"][0]["name"], "Antoine de Saint-Exupéry")
        self.assertTrue(any(b["value"] == "9782070612758" for b in d["barcodes"]))
        self.assertEqual(d["qty_on_hand"], "5")


class ProductRegisterApiTests(TestCase):
    """Register ANY article (supply or book) from a scanned barcode, then find it by that barcode."""

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_superuser("owner", password="pw-strong-123"))

    def test_lookup_unknown_then_register_then_found_with_stock(self):
        r = self.client.get("/api/v1/catalog/lookup", {"barcode": "6191234567890"})
        self.assertFalse(r.json()["found"])                       # unknown before

        r = self.client.post("/api/v1/catalog/register", {
            "name_fr": "Stylo Bille Bleu", "barcode": "6191234567890",
            "sale_price": "0.850", "initial_qty": "50", "cost": "0.400"}, format="json")
        self.assertEqual(r.status_code, 201)
        pid = r.json()["id"]

        r = self.client.get("/api/v1/catalog/lookup", {"barcode": "6191234567890"})
        self.assertTrue(r.json()["found"])                        # scannable afterwards
        self.assertEqual(r.json()["product"]["id"], pid)
        self.assertEqual(r.json()["product"]["qty_on_hand"], "50")   # opening stock applied
        self.assertFalse(r.json()["product"]["is_book"])          # a supply, not a book
        self.assertTrue(Barcode.objects.filter(value="6191234567890").exists())

    def test_duplicate_barcode_is_rejected(self):
        self.client.post("/api/v1/catalog/register",
                         {"name_fr": "A", "barcode": "6199999999999"}, format="json")
        r = self.client.post("/api/v1/catalog/register",
                             {"name_fr": "B", "barcode": "6199999999999"}, format="json")
        self.assertEqual(r.status_code, 409)

    def test_register_without_barcode_generates_sku(self):
        r = self.client.post("/api/v1/catalog/register", {
            "name_fr": "Article sans code", "reference": "ART-MANUEL-1",
        }, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["sku"], "ART-MANUEL-1")
        self.assertEqual(r.json()["reference"], "ART-MANUEL-1")

    def test_furniture_requires_reference(self):
        r = self.client.post("/api/v1/catalog/register", {"name_fr": "Sac"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_furniture_creation_with_reference(self):
        r = self.client.post("/api/v1/catalog/register", {
            "name_fr": "Sac à dos", "reference": "1001", "color": "Blue", "brand": "HP",
            "sale_price": "45.000", "initial_qty": "8", "product_type": "furniture",
        }, format="json")
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual(body["reference"], "1001")
        self.assertEqual(body["product_type"], "furniture")
        p = Product.objects.get(id=body["id"])
        self.assertFalse(p.is_book)
        self.assertEqual(p.color, "Blue")
        self.assertEqual(p.brand, "HP")
        self.assertEqual(p.qty_on_hand, 8)
        self.assertEqual(str(p.sale_price), "45.000")

    def test_duplicate_reference_is_rejected(self):
        self.client.post("/api/v1/catalog/register",
                         {"name_fr": "A", "reference": "SAC-001"}, format="json")
        r = self.client.post("/api/v1/catalog/register",
                             {"name_fr": "B", "reference": "sac-001"}, format="json")
        self.assertEqual(r.status_code, 409)

    def test_pos_search_by_reference_and_barcode(self):
        created = self.client.post("/api/v1/catalog/register", {
            "name_fr": "Trousse", "reference": "TZ-9", "barcode": "6190000000001",
            "sale_price": "3.500",
        }, format="json").json()
        by_ref = self.client.get("/api/v1/catalog/products/", {"barcode": "TZ-9"})
        self.assertEqual(by_ref.status_code, 200)
        self.assertEqual(len(by_ref.json()), 1)
        self.assertEqual(by_ref.json()[0]["id"], created["id"])
        search = self.client.get("/api/v1/catalog/products/", {"search": "TZ-9"})
        self.assertEqual(search.json()[0]["name_fr"], "Trousse")
        by_bc = self.client.get("/api/v1/catalog/products/", {"barcode": "6190000000001"})
        self.assertEqual(by_bc.json()[0]["id"], created["id"])
        lookup = self.client.get("/api/v1/catalog/lookup", {"barcode": "TZ-9"})
        self.assertTrue(lookup.json()["found"])

    def test_alphanumeric_and_numeric_references(self):
        a = self.client.post("/api/v1/catalog/register",
                             {"name_fr": "Sac", "reference": "1001"}, format="json")
        b = self.client.post("/api/v1/catalog/register",
                             {"name_fr": "Sac HP", "reference": "SAC-001"}, format="json")
        self.assertEqual(a.status_code, 201)
        self.assertEqual(b.status_code, 201)


@override_settings(OCR_PROVIDER="vision")
class VisionLlmOcrTests(TestCase):
    """The Vision-LLM provider with an injected transport — no live model needed."""

    def _png_path(self):
        path = os.path.join(tempfile.mkdtemp(), "c.png")
        Image.new("RGB", (8, 8), "white").save(path)
        return path

    def test_parses_structured_multilingual_json(self):
        from teyssir.catalog.bookscan.ocr import VisionLlmOcrProvider

        reply = json.dumps({
            "title": "الأمير الصغير", "subtitle": "Le Petit Prince",
            "authors": ["Antoine de Saint-Exupéry"], "translators": ["محمد التهامي"],
            "publisher": "دار الجنوب", "languages": ["ar", "fr"],
            "pub_year": 2018, "pages": 110, "isbn13": "978-2-07-061275-8",
            "subject": "Roman", "description": "حكاية الأمير الصغير",
        }, ensure_ascii=False)
        prov = VisionLlmOcrProvider(transport=lambda b64: "data: " + reply)

        _, draft = prov.extract(self._png_path())
        self.assertEqual(draft.title, "الأمير الصغير")
        self.assertEqual(draft.subtitle, "Le Petit Prince")
        self.assertEqual(draft.authors, ["Antoine de Saint-Exupéry"])
        self.assertEqual(draft.translators, ["محمد التهامي"])
        self.assertEqual(draft.languages, ["ar", "fr"])
        self.assertEqual(draft.pub_year, 2018)
        self.assertEqual(draft.pages, 110)
        self.assertEqual(draft.isbn13, "9782070612758")    # dashes stripped
        self.assertEqual(draft.source, "vision")

    def test_degrades_to_manual_when_model_unreachable(self):
        from teyssir.catalog.bookscan.ocr import VisionLlmOcrProvider

        def boom(_b64):
            raise OSError("connection refused")

        _, draft = VisionLlmOcrProvider(transport=boom).extract(self._png_path())
        self.assertEqual(draft.source, "manual")           # graceful fallback, never crashes


@override_settings(OCR_PROVIDER="manual", METADATA_PROVIDERS=[], MEDIA_ROOT=tempfile.mkdtemp())
class ScanJobTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_superuser("owner", password="pw-strong-123"))

    def test_run_scan_job_worker_completes(self):
        from teyssir.catalog.bookscan.jobs import run_scan_job
        from teyssir.catalog.models import ScanJob

        job = ScanJob.objects.create(isbn="9782070612758", image_ids=[])
        run_scan_job(job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, ScanJob.DONE)
        self.assertEqual(job.result["isbn13"], "9782070612758")

    def test_inline_scan_returns_done_with_draft(self):
        # default executor is inline -> the job is already DONE in the POST response (backward compat)
        r = self.client.post("/api/v1/catalog/books/scan",
                             {"images": _png(), "isbn": "9782070612758"}, format="multipart")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "done")
        self.assertEqual(body["isbn13"], "9782070612758")
        self.assertIn("job_id", body)

    def test_poll_scan_job_endpoint(self):
        job_id = self.client.post("/api/v1/catalog/books/scan",
                                  {"images": _png(), "isbn": "9782070612758"},
                                  format="multipart").json()["job_id"]
        r = self.client.get(f"/api/v1/catalog/books/scan/{job_id}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "done")
        self.assertEqual(r.json()["isbn13"], "9782070612758")

    def test_local_image_paths_streams_remote_storage(self):
        """S3/MinIO fields have no .path -> the helper streams to a temp file OCR can read, then
        cleans it up (Phase 6 object-storage support)."""
        from teyssir.catalog.bookscan.jobs import local_image_paths

        class _RemoteField:                                  # mimics an S3-backed ImageField value
            name = "product_images/2026/06/cover.png"
            def __init__(self, data): self._data = data
            @property
            def path(self): raise NotImplementedError
            def open(self, mode="rb"): return BytesIO(self._data)
            def read(self): return self._data
            def close(self): pass

        with local_image_paths([_RemoteField(b"COVERBYTES")]) as paths:
            self.assertEqual(len(paths), 1)
            with open(paths[0], "rb") as fh:
                self.assertEqual(fh.read(), b"COVERBYTES")
            tmp = paths[0]
        self.assertFalse(os.path.exists(tmp))                # temp cleaned up afterwards
