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
