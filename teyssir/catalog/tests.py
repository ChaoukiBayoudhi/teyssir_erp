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


class IsbnExtractionTests(unittest.TestCase):
    def test_isbn13_and_isbn10_conversion(self):
        from teyssir.catalog.bookscan.isbn import extract_isbn, isbn13_check_ok, to_isbn13
        self.assertTrue(isbn13_check_ok("9782070612758"))
        self.assertEqual(to_isbn13("978-2-07-061275-8"), "9782070612758")
        # ISBN-10 for Petit Prince (2-07-061275-9) → ISBN-13
        self.assertEqual(to_isbn13("2070612759"), "9782070612758")
        self.assertEqual(extract_isbn("ISBN: 978-2-07-061275-8 Gallimard"), "9782070612758")
        self.assertEqual(extract_isbn("no isbn here"), "")

    def test_rejects_bad_check_digit(self):
        from teyssir.catalog.bookscan.isbn import isbn13_check_ok, to_isbn13
        self.assertEqual(to_isbn13("9782070612750"), "")
        self.assertFalse(isbn13_check_ok("9782070612750"))
        # Wrong check digit on a 978 blob
        self.assertFalse(isbn13_check_ok("9787723827430"))
        self.assertEqual(to_isbn13("9787723827430"), "")

    def test_screenshot_isbn_checksum_valid_but_suspect(self):
        """9787723827435 has a valid check digit (OCR luck) — accept structurally,
        but scan_book must not treat digit-OCR as high-confidence barcode."""
        from teyssir.catalog.bookscan.isbn import isbn13_check_ok, to_isbn13
        self.assertTrue(isbn13_check_ok("9787723827435"))
        self.assertEqual(to_isbn13("9787723827435"), "9787723827435")


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

    def test_no_isbn_title_search_fallback(self):
        """Phase 6: without ISBN, title search can still enrich the draft."""
        from unittest.mock import patch

        ocr_draft = BookDraft(
            title="فقه السنة", authors=["سيد سابق"],
            source="tesseract", confidence=0.4,
            raw={"isbn_not_detected": True},
        )
        called = {}

        def fake_title(t, a=""):
            called["t"] = t
            return BookDraft(title="فقه السنة", authors=["سيد سابق"],
                             source="openlibrary", confidence=0.85)

        with patch("teyssir.catalog.bookscan.services.get_ocr_provider") as g:
            class P:
                def extract(self, path, role="auto"):
                    return "فقه السنة\nسيد سابق", ocr_draft
            g.return_value = P()
            out, _ = scan_book(["/tmp/fake.png"], isbn="", enrich=lambda i: None,
                               enrich_title=fake_title)
        self.assertEqual(called["t"], "فقه السنة")
        self.assertTrue(out.raw.get("title_search"))
        self.assertEqual(out.source, "openlibrary")

    def test_merge_front_back_covers(self):
        from teyssir.catalog.bookscan.services import _merge_cover_drafts
        front = BookDraft(title="Le Petit Prince", authors=["Saint-Exupéry"],
                          languages=["fr"], source="tesseract", confidence=0.4)
        back = BookDraft(isbn13="9782070612758", price="15.000", source="tesseract", confidence=0.6,
                         raw={"isbn_detected": True, "price_detected": True})
        out = _merge_cover_drafts(front, back)
        self.assertEqual(out.title, "Le Petit Prince")
        self.assertEqual(out.isbn13, "9782070612758")
        self.assertEqual(out.price, "15.000")
        self.assertEqual(out.authors, ["Saint-Exupéry"])
        self.assertTrue(out.raw["covers"]["back"])

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


class PriceAndLangTests(unittest.TestCase):
    def test_extract_price_dt_patterns(self):
        from teyssir.catalog.bookscan.price import extract_price_dt
        self.assertEqual(extract_price_dt("Prix: 15 DT"), "15.000")
        self.assertEqual(extract_price_dt("12.500"), "12.500")
        self.assertEqual(extract_price_dt("الثمن 8,750 د.ت"), "8.750")
        self.assertEqual(extract_price_dt("9.900 €"), "9.900")
        self.assertEqual(extract_price_dt("€12.500"), "12.500")
        self.assertEqual(extract_price_dt("DT 14.900"), "14.900")
        self.assertEqual(extract_price_dt("no price here"), "")
        # Spurious OCR noise (screenshot 3) must not become a shelf price
        self.assertEqual(extract_price_dt("ol YI a Teeny 8.043 FRE pte"), "")

    def test_detect_script_langs(self):
        from teyssir.catalog.bookscan.ocr import detect_script_langs
        self.assertIn("ar", detect_script_langs("كتاب الفقه للمبتدئين"))
        self.assertIn("fr", detect_script_langs("Été français — édition scolaire"))
        self.assertIn("en", detect_script_langs("The Little Prince"))

    def test_merge_bilingual_title(self):
        from teyssir.catalog.bookscan.ocr import merge_bilingual_title, _draft_from_text
        merged = merge_bilingual_title("Le premier", "الثلاثي الاول")
        self.assertEqual(merged, "Le premier (الثلاثي الاول)")
        self.assertEqual(
            merge_bilingual_title("Le premier", "الثلاثي الاول", style="slash"),
            "Le premier / الثلاثي الاول",
        )
        # Do not drop Latin when Arabic present
        self.assertIn("Le premier", merge_bilingual_title("الثلاثي الاول", "Le premier"))
        self.assertIn("الثلاثي", merge_bilingual_title("الثلاثي الاول", "Le premier"))
        # Single script passthrough
        self.assertEqual(merge_bilingual_title("Le Petit Prince"), "Le Petit Prince")

        draft = _draft_from_text(
            "الثلاثي الاول\nLe premier\n",
            role="front",
            mean_conf=55,
        )
        self.assertIn("Le premier", draft.title)
        self.assertIn("الثلاثي", draft.title)
        self.assertIn("ar", draft.languages)
        self.assertIn("fr", draft.languages)
        self.assertTrue(draft.raw.get("bilingual_title"))

    def test_digit_ocr_isbn_not_high_confidence(self):
        """Checksum-valid digit-OCR ISBN + OL miss must not stay at 85%."""
        from unittest.mock import patch

        from teyssir.catalog.bookscan.draft import BookDraft

        # Suspect ISBN from screenshot 1 — valid check digit, wrong book
        suspect = "9787723827435"
        ocr_draft = BookDraft(
            title="الثلاثي الاول",
            isbn13=suspect,
            source="tesseract",
            confidence=0.85,
            languages=["ar"],
            raw={
                "isbn_detected": True,
                "isbn_from_digit_ocr": True,
                "isbn_not_detected": False,
            },
        )

        with patch("teyssir.catalog.bookscan.services.get_ocr_provider") as g, \
             patch("teyssir.catalog.bookscan.services._barcode_isbn_from_paths",
                   return_value=(suspect, "digit_ocr")), \
             patch("teyssir.catalog.bookscan.services._should_try_vision", return_value=False):
            class P:
                name = "tesseract"

                def extract(self, path, role="auto"):
                    return "الثلاثي الاول", ocr_draft
            g.return_value = P()
            out, _ = scan_book(
                ["/tmp/fake.png"], isbn="", enrich=lambda i: None,
            )
        # Cleared or demoted — never high confidence
        self.assertLess(out.confidence or 0, 0.5)
        self.assertNotEqual(out.confidence or 0, 0.85)
        self.assertTrue(
            not out.isbn13 or out.raw.get("isbn_unconfirmed") or out.raw.get("suggested_isbn")
        )
        if not out.isbn13:
            self.assertEqual(out.raw.get("suggested_isbn"), suspect)

    def test_barcode_isbn_keeps_high_confidence_on_metadata_miss(self):
        from unittest.mock import patch

        from teyssir.catalog.bookscan.draft import BookDraft

        good = "9782070612758"
        ocr_draft = BookDraft(
            title="Le Petit Prince",
            isbn13=good,
            source="tesseract",
            confidence=0.4,
            languages=["fr"],
            raw={"isbn_detected": True, "isbn_from_barcode": True},
        )
        with patch("teyssir.catalog.bookscan.services.get_ocr_provider") as g, \
             patch("teyssir.catalog.bookscan.services._barcode_isbn_from_paths",
                   return_value=(good, "barcode")), \
             patch("teyssir.catalog.bookscan.services._should_try_vision", return_value=False):
            class P:
                name = "tesseract"

                def extract(self, path, role="auto"):
                    return "Le Petit Prince", ocr_draft
            g.return_value = P()
            out, _ = scan_book(
                ["/tmp/fake.png"], isbn="", enrich=lambda i: None,
            )
        self.assertEqual(out.isbn13, good)
        self.assertGreaterEqual(out.confidence or 0, 0.85)

    def test_garbage_latin_rejected_and_not_tagged_en(self):
        from teyssir.catalog.bookscan.ocr import (
            _draft_from_text,
            detect_script_langs,
            is_garbage_latin_ocr,
            is_usable_ocr_title,
        )
        for junk in ("wis! Boot ay", "9 or et O.", 'ol YI a "Teeny"', "arr", "FRE pte"):
            self.assertTrue(
                is_garbage_latin_ocr(junk, mean_conf=35),
                msg=f"expected garbage: {junk!r}",
            )
            self.assertNotIn("en", detect_script_langs(junk, mean_conf=35))
            self.assertFalse(is_usable_ocr_title(junk, mean_conf=35))

        self.assertFalse(is_garbage_latin_ocr("The Little Prince", mean_conf=70))
        self.assertTrue(is_usable_ocr_title("The Little Prince", mean_conf=70))
        self.assertTrue(is_usable_ocr_title("كتاب الفقه", mean_conf=40))

        draft = _draft_from_text("wis! Boot ay\narr", role="front", mean_conf=35)
        self.assertEqual(draft.title, "")
        self.assertTrue(draft.raw.get("ocr_garbage_latin"))
        self.assertIn("ar", draft.languages)
        self.assertNotIn("en", draft.languages)

    def test_garbage_arabic_title_rejected(self):
        from teyssir.catalog.bookscan.ocr import (
            _draft_from_text,
            is_garbage_arabic_ocr,
            is_plausible_author,
            is_usable_ocr_title,
        )
        for junk in ("عد ل |||", "الا )(", "عد ل"):
            self.assertTrue(
                is_garbage_arabic_ocr(junk, mean_conf=35),
                msg=f"expected garbage Arabic: {junk!r}",
            )
            self.assertFalse(is_usable_ocr_title(junk, mean_conf=35))
        self.assertFalse(is_plausible_author("الا )(", mean_conf=35))
        self.assertFalse(is_plausible_author("مسيحي", mean_conf=35))
        self.assertTrue(is_usable_ocr_title("فقه السنة لسيد سابق", mean_conf=40))

        draft = _draft_from_text("عد ل |||\nالا )(", role="front", mean_conf=35)
        self.assertEqual(draft.title, "")
        self.assertTrue(draft.raw.get("ocr_garbage_arabic") or draft.raw.get("ocr_title_unusable"))
        self.assertEqual(draft.authors, [])

    def test_arabic_char_ratio(self):
        from teyssir.catalog.bookscan.ocr import arabic_char_ratio
        self.assertGreater(arabic_char_ratio("فقه السنة سيد سابق"), 0.8)
        self.assertEqual(arabic_char_ratio("wis! Boot ay"), 0.0)


class BarcodeIsbnTests(unittest.TestCase):
    def test_decode_isbn_from_ean13_image(self):
        """pyzbar recovers ISBN from a rendered EAN-13 (and from a small angled crop)."""
        try:
            import barcode
            from barcode.writer import ImageWriter
            from pyzbar.pyzbar import decode as zbar_decode
        except Exception:
            self.skipTest("pyzbar / python-barcode not available")

        from teyssir.catalog.bookscan.barcode import decode_isbn_barcode

        buf = BytesIO()
        # python-barcode expects 12 digits; check digit appended
        barcode.get_barcode_class("ean13")("978207061275", writer=ImageWriter()).write(buf)
        buf.seek(0)
        bc = Image.open(buf).convert("RGB")
        self.assertTrue(any(h.data == b"9782070612758" for h in zbar_decode(bc)))

        # Full clean barcode image
        path = os.path.join(tempfile.mkdtemp(), "ean.png")
        bc.save(path)
        self.assertEqual(decode_isbn_barcode(path), "9782070612758")

        # Small angled barcode on a large page (phone verso simulation)
        page = Image.new("RGB", (900, 1200), "white")
        small = bc.resize((200, 90)).rotate(14, expand=True, fillcolor="white")
        page.paste(small, (340, 980))
        path2 = os.path.join(tempfile.mkdtemp(), "verso.png")
        page.save(path2)
        self.assertEqual(decode_isbn_barcode(path2), "9782070612758")

    def test_decode_isbn_corner_and_rotated(self):
        """Corner placement + 90° rotation still recover EAN-13."""
        try:
            import barcode
            from barcode.writer import ImageWriter
        except Exception:
            self.skipTest("pyzbar / python-barcode not available")

        from teyssir.catalog.bookscan.barcode import decode_isbn_barcode

        buf = BytesIO()
        barcode.get_barcode_class("ean13")("978207061275", writer=ImageWriter()).write(buf)
        buf.seek(0)
        bc = Image.open(buf).convert("RGB").resize((180, 80))

        page = Image.new("RGB", (1000, 1400), "white")
        page.paste(bc, (780, 1280))  # bottom-right corner
        path = os.path.join(tempfile.mkdtemp(), "corner.png")
        page.save(path)
        self.assertEqual(decode_isbn_barcode(path), "9782070612758")

        rotated = page.rotate(90, expand=True, fillcolor="white")
        path_r = os.path.join(tempfile.mkdtemp(), "rot90.png")
        rotated.save(path_r)
        self.assertEqual(decode_isbn_barcode(path_r), "9782070612758")

    def test_extract_isbn_from_digit_blob(self):
        from teyssir.catalog.bookscan.isbn import extract_isbn, to_isbn13
        self.assertEqual(extract_isbn("ISBN 978-2-07-061275-8"), "9782070612758")
        self.assertEqual(to_isbn13("9782070612758"), "9782070612758")
        self.assertEqual(extract_isbn("noise 12345 more"), "")

    def test_barcode_engine_available_flag(self):
        from teyssir.catalog.bookscan.barcode import barcode_engine_available
        # Soft assert: True when pyzbar imports; False is OK on hosts without it
        self.assertIsInstance(barcode_engine_available(), bool)


class TitleSearchGatingTests(unittest.TestCase):
    def test_title_similarity_jaccard(self):
        from teyssir.catalog.bookscan.metadata import title_similarity
        self.assertGreater(title_similarity("Beauty and the Beast", "Beauty and the Beast"), 0.9)
        self.assertLess(title_similarity("Beauty and the Beast", "The Little Prince"), 0.3)

    def test_pick_best_rejects_low_overlap(self):
        from teyssir.catalog.bookscan.metadata import _pick_best_ol_doc
        docs = [{"title": "Completely Different Book", "author_name": ["Someone"]}]
        self.assertIsNone(_pick_best_ol_doc(docs, "Beauty and the Beast"))

    def test_scan_skips_vision_when_title_present(self):
        from unittest.mock import patch
        ocr_draft = BookDraft(
            title="Beauty and the Beast", source="tesseract", confidence=0.35,
            raw={"isbn_not_detected": True, "ocr_text_only": True},
        )
        vision_called = {"n": 0}

        def fake_title(t, a=""):
            return None

        with patch("teyssir.catalog.bookscan.services.get_ocr_provider") as g, \
             patch("teyssir.catalog.bookscan.services._barcode_isbn_from_paths", return_value=("", "")), \
             patch("teyssir.catalog.bookscan.services._maybe_vision_upgrade") as vision:
            class P:
                name = "tesseract"
                def extract(self, path, role="auto"):
                    return "Beauty and the Beast", ocr_draft
            g.return_value = P()
            vision.side_effect = lambda *a, **k: vision_called.__setitem__("n", 1) or ocr_draft
            out, _ = scan_book(["/tmp/fake.png"], isbn="", enrich=lambda i: None,
                               enrich_title=fake_title)
        self.assertEqual(vision_called["n"], 0)
        self.assertEqual(out.title, "Beauty and the Beast")
        self.assertLessEqual(out.confidence or 0, 0.45)

    def test_scan_tries_vision_on_garbage_latin_title(self):
        """Arabic covers misread as Latin must not block Vision fallback."""
        from unittest.mock import patch
        ocr_draft = BookDraft(
            title="", source="tesseract", confidence=0.1,
            languages=["ar"],
            raw={
                "isbn_not_detected": True,
                "ocr_garbage_latin": True,
                "ocr_arabic_likely": True,
                "rejected_title": "wis! Boot ay",
                "ocr_mean_confidence": 35,
                "ocr_low_confidence": True,
            },
        )
        vision_called = {"n": 0}

        with patch("teyssir.catalog.bookscan.services.get_ocr_provider") as g, \
             patch("teyssir.catalog.bookscan.services._barcode_isbn_from_paths", return_value=("", "")), \
             patch("teyssir.catalog.bookscan.services._maybe_vision_upgrade") as vision:
            class P:
                name = "tesseract"
                def extract(self, path, role="auto"):
                    return "wis! Boot ay", ocr_draft
            g.return_value = P()

            def _vision(paths, draft, back):
                vision_called["n"] += 1
                return BookDraft(
                    title="كتاب الفقه", authors=["مؤلف"], languages=["ar"],
                    source="vision", confidence=0.8,
                    raw={"vision_fallback": True},
                )
            vision.side_effect = _vision
            out, _ = scan_book(
                ["/tmp/fake.png"], isbn="", enrich=lambda i: None,
                enrich_title=lambda t, a="": None,
            )
        self.assertEqual(vision_called["n"], 1)
        self.assertEqual(out.title, "كتاب الفقه")
        self.assertIn("ar", out.languages)

    def test_should_try_vision_rejects_garbage_even_if_title_long(self):
        from teyssir.catalog.bookscan.services import _should_try_vision
        class P:
            name = "tesseract"
        draft = BookDraft(
            title="wis! Boot ay", source="tesseract", confidence=0.35,
            raw={"ocr_mean_confidence": 35, "ocr_garbage_latin": True},
        )
        self.assertTrue(_should_try_vision(draft, P()))
        good = BookDraft(
            title="Beauty and the Beast", source="tesseract", confidence=0.35,
            raw={"ocr_text_only": True},
        )
        self.assertFalse(_should_try_vision(good, P()))

    def test_should_try_vision_on_garbage_arabic_and_weak_ar(self):
        from teyssir.catalog.bookscan.services import _should_try_vision
        class P:
            name = "tesseract"
        garbage = BookDraft(
            title="", source="tesseract", confidence=0.1, languages=["ar"],
            raw={"ocr_garbage_arabic": True, "ocr_title_unusable": True, "ocr_mean_confidence": 35},
        )
        self.assertTrue(_should_try_vision(garbage, P()))
        weak_ar = BookDraft(
            title="الكتالاميد السنة الأولى مت الحمليم", source="tesseract",
            confidence=0.35, languages=["ar"],
            raw={"ocr_mean_confidence": 35, "ocr_text_only": True},
        )
        self.assertTrue(_should_try_vision(weak_ar, P()))

    def test_scan_isbn_metadata_raises_confidence(self):
        """Barcode/ISBN hint + OpenLibrary must present high confidence, not 35%."""
        from unittest.mock import patch
        ocr_draft = BookDraft(
            title="wrong tess", source="tesseract", confidence=0.35,
            raw={"isbn_not_detected": True},
        )
        meta = BookDraft(
            title="Le Petit Prince", authors=["Saint-Exupéry"],
            isbn13="9782070612758", source="openlibrary", confidence=0.9,
        )
        with patch("teyssir.catalog.bookscan.services.get_ocr_provider") as g, \
             patch("teyssir.catalog.bookscan.services._barcode_isbn_from_paths",
                   return_value=("9782070612758", "barcode")), \
             patch("teyssir.catalog.bookscan.services._should_try_vision", return_value=False):
            class P:
                name = "tesseract"
                def extract(self, path, role="auto"):
                    return "x", ocr_draft
            g.return_value = P()
            out, _ = scan_book(
                ["/tmp/fake.png"], isbn="", enrich=lambda i: meta,
                enrich_title=lambda t, a="": None,
            )
        self.assertEqual(out.isbn13, "9782070612758")
        self.assertEqual(out.title, "Le Petit Prince")
        self.assertGreaterEqual(out.confidence or 0, 0.85)
        self.assertEqual(out.source, "openlibrary")

    def test_weak_title_search_keeps_ocr_authors(self):
        from unittest.mock import patch
        ocr_draft = BookDraft(
            title="Beauty and the Beast", authors=["Golden Tales"],
            source="tesseract", confidence=0.35,
            raw={"isbn_not_detected": True},
        )

        def fake_title(t, a=""):
            return BookDraft(
                title="Beauty and the Beast", authors=["Hannah Howell"],
                source="openlibrary", confidence=0.35,
                raw={"title_search": True, "title_search_weak": True},
            )

        with patch("teyssir.catalog.bookscan.services.get_ocr_provider") as g, \
             patch("teyssir.catalog.bookscan.services._barcode_isbn_from_paths", return_value=("", "")):
            class P:
                name = "tesseract"
                def extract(self, path, role="auto"):
                    return "Beauty and the Beast", ocr_draft
            g.return_value = P()
            out, _ = scan_book(["/tmp/fake.png"], isbn="", enrich=lambda i: None,
                               enrich_title=fake_title)
        self.assertEqual(out.authors, ["Golden Tales"])
        self.assertTrue(out.raw.get("title_search_weak") or out.raw.get("title_search"))
        self.assertLessEqual(out.confidence or 0, 0.45)


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

    def test_scan_marks_front_and_back_kinds(self):
        r = self.client.post(
            "/api/v1/catalog/books/scan",
            {"images": [_png("front.png"), _png("back.png")]},
            format="multipart",
        )
        self.assertEqual(r.status_code, 200)
        kinds = list(ProductImage.objects.order_by("order").values_list("kind", flat=True))
        self.assertEqual(kinds, [ProductImage.COVER, ProductImage.BACK])

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

    def test_scan_requires_auth(self):
        anon = APIClient()
        r = anon.post("/api/v1/catalog/books/scan", {"images": _png()}, format="multipart")
        self.assertIn(r.status_code, (401, 403))

    def test_poll_scan_job_requires_auth(self):
        # Create a job as authenticated user, then poll as anonymous
        from teyssir.catalog.models import ScanJob
        job = ScanJob.objects.create(status=ScanJob.PENDING)
        anon = APIClient()
        r = anon.get(f"/api/v1/catalog/books/scan/{job.id}")
        self.assertIn(r.status_code, (401, 403))


@unittest.skipUnless(_tesseract_ready(), "tesseract engine + fra language data not installed")
@override_settings(OCR_PROVIDER="tesseract", OCR_VISION_FALLBACK=False)
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
        self.assertEqual(detected.isbn13, "9782070612758")   # ISBN drives enrichment
        self.assertTrue(detected.raw.get("isbn_detected"))
        # Title OCR is best-effort after the digit-priority ISBN pass.
        blob = f"{text} {detected.title}"
        self.assertTrue("Petit" in blob or "Prince" in blob)


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


class ProductUpdateDeleteApiTests(TestCase):
    """PATCH/DELETE on /catalog/products/<id>/detail for furniture + books."""

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_superuser("owner", password="pw-strong-123"))
        self.furn = self.client.post("/api/v1/catalog/register", {
            "name_fr": "Sac à dos", "reference": "SAC-UPD", "sale_price": "40.000",
            "color": "Noir", "brand": "Generic", "product_type": "furniture",
        }, format="json").json()
        self.book = self.client.post("/api/v1/catalog/register", {
            "name_fr": "Le Petit Prince", "is_book": True, "product_type": "book",
            "isbn": "9782070612758", "sale_price": "12.000", "barcode": "9782070612758",
        }, format="json").json()

    def test_patch_furniture_updates_fields(self):
        r = self.client.patch(f"/api/v1/catalog/products/{self.furn['id']}/detail", {
            "name_fr": "Sac XL", "sale_price": "49.500", "color": "Bleu", "reference": "SAC-UPD",
        }, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body["name_fr"], "Sac XL")
        self.assertEqual(body["sale_price"], "49.500")
        self.assertEqual(body["color"], "Bleu")
        p = Product.objects.get(pk=self.furn["id"])
        self.assertEqual(p.name_fr, "Sac XL")
        self.assertEqual(str(p.sale_price), "49.500")

    def test_patch_book_updates_isbn_and_title(self):
        r = self.client.patch(f"/api/v1/catalog/products/{self.book['id']}/detail", {
            "name_fr": "Petit Prince (éd. Gallimard)", "isbn": "9782070612758",
        }, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["name_fr"], "Petit Prince (éd. Gallimard)")
        self.assertEqual(r.json()["isbn"], "9782070612758")

    def test_patch_duplicate_reference_conflict(self):
        other = self.client.post("/api/v1/catalog/register", {
            "name_fr": "Autre", "reference": "OTHER-1",
        }, format="json").json()
        r = self.client.patch(f"/api/v1/catalog/products/{other['id']}/detail", {
            "reference": "SAC-UPD",
        }, format="json")
        self.assertEqual(r.status_code, 409)

    def test_delete_soft_hides_from_catalog_search(self):
        r = self.client.delete(f"/api/v1/catalog/products/{self.furn['id']}/detail")
        self.assertEqual(r.status_code, 204)
        p = Product.objects.get(pk=self.furn["id"])
        self.assertFalse(p.active)
        search = self.client.get("/api/v1/catalog/search", {"q": "SAC-UPD"})
        self.assertEqual(search.status_code, 200)
        ids = [row["id"] for row in search.json()["results"]]
        self.assertNotIn(self.furn["id"], ids)
        # Second delete → 404 (already inactive)
        r2 = self.client.delete(f"/api/v1/catalog/products/{self.furn['id']}/detail")
        self.assertEqual(r2.status_code, 404)

    def test_delete_book_same_path(self):
        r = self.client.delete(f"/api/v1/catalog/products/{self.book['id']}/detail")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(Product.objects.get(pk=self.book["id"]).active)


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

    def test_tesseract_unavailable_stamps_ocr_error(self):
        """Soft-fail: UI can warn instead of looking like a successful empty scan."""
        import builtins
        from teyssir.catalog.bookscan.ocr import TesseractOcrProvider

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name in ("pytesseract", "PIL") or name.startswith("PIL."):
                raise ImportError("forced missing")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = fake_import
        try:
            _, draft = TesseractOcrProvider().extract(self._png_path())
        finally:
            builtins.__import__ = real_import
        self.assertEqual(draft.source, "manual")
        self.assertEqual(draft.confidence, 0.0)
        self.assertFalse(draft.raw.get("ocr_available", True))
        self.assertIn("ocr_error", draft.raw)

    def test_configure_tesseract_finds_binary(self):
        """Absolute tesseract path is resolved even when cmd was the bare name."""
        import pytesseract
        from pathlib import Path
        from django.test import override_settings
        from teyssir.catalog.bookscan.ocr import configure_tesseract

        pytesseract.pytesseract.tesseract_cmd = "tesseract"
        with override_settings(TESSERACT_CMD="tesseract"):
            cmd = configure_tesseract(pytesseract)
        self.assertTrue(cmd)
        self.assertTrue(Path(cmd).is_file())
        self.assertEqual(pytesseract.pytesseract.tesseract_cmd, cmd)

    def test_configure_tesseract_candidate_when_not_on_path(self):
        """LaunchAgent-style empty PATH: fall back to known install locations."""
        import pytesseract
        from django.test import override_settings
        from teyssir.catalog.bookscan.ocr import configure_tesseract

        pytesseract.pytesseract.tesseract_cmd = "tesseract"
        with override_settings(TESSERACT_CMD="tesseract"):
            with unittest.mock.patch("teyssir.catalog.bookscan.ocr.shutil.which", return_value=None):
                with unittest.mock.patch(
                    "teyssir.catalog.bookscan.ocr.Path.is_file",
                    lambda self: str(self) == "/opt/homebrew/bin/tesseract",
                ):
                    cmd = configure_tesseract(pytesseract)
        self.assertEqual(cmd, "/opt/homebrew/bin/tesseract")

    def test_configure_tesseract_uses_settings_cmd(self):
        import pytesseract
        from django.test import override_settings
        from teyssir.catalog.bookscan.ocr import configure_tesseract

        with override_settings(TESSERACT_CMD="/opt/homebrew/bin/tesseract"):
            with unittest.mock.patch(
                "teyssir.catalog.bookscan.ocr.Path.is_file",
                lambda self: str(self) == "/opt/homebrew/bin/tesseract",
            ):
                cmd = configure_tesseract(pytesseract)
        self.assertEqual(cmd, "/opt/homebrew/bin/tesseract")

    def test_tesseract_langs_only_use_installed_packs(self):
        from teyssir.catalog.bookscan.ocr import _tesseract_langs_for

        self.assertEqual(
            _tesseract_langs_for(role="front", available={"eng", "osd"}),
            "eng",
        )
        langs = _tesseract_langs_for(role="front", available={"eng", "fra"})
        self.assertEqual(langs, "fra+eng")


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
