import tempfile
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

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
