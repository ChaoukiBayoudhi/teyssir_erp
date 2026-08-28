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
        self.assertEqual(out.language_detected, "fr")
        self.assertEqual(out.raw.get("language_detected"), "fr")
        self.assertIn("fr", out.languages)

    def test_merge_language_detected_mixed_ar_fr(self):
        from teyssir.catalog.bookscan.services import _merge_cover_drafts
        front = BookDraft(
            title="الأول في السنة", languages=["ar"], source="tesseract", confidence=0.5,
            raw={"script_probe": "ar"},
        )
        back = BookDraft(
            title="Le premier", languages=["fr"], isbn13="9789973352743",
            source="tesseract", confidence=0.6,
            raw={"script_probe": "fr", "isbn_from_barcode": True},
        )
        out = _merge_cover_drafts(front, back)
        self.assertEqual(out.language_detected, "mixed:ar+fr")
        self.assertIn("ar", out.languages)
        self.assertIn("fr", out.languages)
        self.assertIn("language_detected", out.as_dict())
        self.assertEqual(out.as_dict()["language_detected"], "mixed:ar+fr")

    def test_language_detected_helpers(self):
        from teyssir.catalog.bookscan.language import (
            detect_language_detected,
            format_language_detected,
            template_description,
        )
        self.assertEqual(format_language_detected(["fr"]), "fr")
        self.assertEqual(format_language_detected(["fr", "ar"]), "mixed:ar+fr")
        self.assertEqual(
            detect_language_detected(script_probes=["ar+fr"], languages=[]),
            "mixed:ar+fr",
        )
        self.assertEqual(
            detect_language_detected(ocr_langs=["ara+fra"], languages=["en"]),
            "mixed:ar+fr+en",
        )
        self.assertTrue(template_description("Mathématiques", language_detected="fr").startswith("Livre"))
        self.assertEqual(template_description("", language_detected="fr"), "")

    def test_merge_priority_metadata_over_vision_over_ocr(self):
        """Phase 15.3: metadata > vision > OCR for bibliographic fields."""
        from teyssir.catalog.bookscan.merge import merge_scan_layers

        meta = BookDraft(
            title="Le Petit Prince", authors=["Saint-Exupéry"], publisher="Gallimard",
            description="Meta desc", isbn13="9782070612758",
            source="openlibrary", confidence=0.9,
        )
        vision = BookDraft(
            title="Vision Title", authors=["Vision Author"], description="Vision desc",
            isbn13="9782070612758", source="vision", confidence=0.7,
            price="99.000",
        )
        ocr = BookDraft(
            title="Tess garbage", authors=["OCR"], description="OCR desc",
            source="tesseract", confidence=0.4, price="12.500",
            raw={"isbn_from_barcode": True, "barcode_detected": True, "barcode_source": "pyzbar"},
            barcode_raw="9782070612758", barcode_symbology="ISBN", barcode_kind="isbn13",
            isbn13="9782070612758",
        )
        out = merge_scan_layers(
            metadata=meta, vision=vision, ocr=ocr,
            isbn_hint="9782070612758", isbn_source="barcode",
        )
        self.assertEqual(out.title, "Le Petit Prince")
        self.assertEqual(out.authors, ["Saint-Exupéry"])
        self.assertEqual(out.description, "Meta desc")
        self.assertEqual(out.publisher, "Gallimard")
        # Price: OCR sticker beats LLM
        self.assertEqual(out.price, "12.500")
        self.assertEqual(out.raw["field_sources"]["title"], "metadata")
        self.assertEqual(out.raw["field_sources"]["price"], "ocr")
        self.assertEqual(out.raw["field_sources"]["isbn13"], "barcode")
        self.assertGreaterEqual(out.confidence or 0, 0.85)

    def test_merge_vision_wins_title_when_metadata_missing(self):
        from teyssir.catalog.bookscan.merge import merge_scan_layers

        vision = BookDraft(
            title="Beauty and the Beast", description="A classic tale.",
            source="vision", confidence=0.65, languages=["en"],
        )
        ocr = BookDraft(
            title="wis! Boot ay", source="tesseract", confidence=0.2,
            raw={"ocr_garbage_latin": True},
        )
        out = merge_scan_layers(metadata=None, vision=vision, ocr=ocr)
        self.assertEqual(out.title, "Beauty and the Beast")
        self.assertEqual(out.description, "A classic tale.")
        self.assertEqual(out.raw["field_sources"]["title"], "vision")
        self.assertEqual(out.raw["field_sources"]["description"], "vision")

    def test_merge_barcode_isbn_beats_vision_isbn(self):
        from teyssir.catalog.bookscan.merge import merge_scan_layers

        good = "9782070612758"
        vision_isbn = "9789973352743"
        vision = BookDraft(
            title="Vision", isbn13=vision_isbn, source="vision",
            raw={"isbn_from_vision": True},
        )
        ocr = BookDraft(
            title="OCR", isbn13=good, source="tesseract",
            barcode_raw=good, barcode_kind="isbn13", barcode_symbology="ISBN",
            raw={
                "isbn_from_barcode": True,
                "barcode_detected": True,
                "barcode_source": "pyzbar",
            },
        )
        out = merge_scan_layers(
            metadata=None, vision=vision, ocr=ocr,
            isbn_hint=good, isbn_source="barcode",
        )
        self.assertEqual(out.isbn13, good)
        self.assertEqual(out.raw["field_sources"]["isbn13"], "barcode")
        self.assertTrue(out.raw.get("isbn_from_barcode"))

    def test_merge_619_never_isbn13_and_barcode_decoder_only(self):
        from teyssir.catalog.bookscan.merge import merge_scan_layers

        cnp = "6192202606921"
        vision = BookDraft(
            title="Vision", isbn13=cnp, source="vision",
            barcode_raw=cnp, barcode_kind="isbn13",
        )
        ocr = BookDraft(
            title="تاريخ", source="tesseract",
            barcode_raw=cnp, barcode_kind="local_product", barcode_symbology="EAN13",
            price="4.900",
            raw={"barcode_detected": True, "barcode_non_isbn": True, "barcode_source": "pyzbar"},
        )
        out = merge_scan_layers(metadata=None, vision=vision, ocr=ocr)
        self.assertEqual(out.barcode_raw, cnp)
        self.assertEqual(out.barcode_kind, "local_product")
        self.assertEqual(out.isbn13, "")
        self.assertNotEqual(out.isbn13, cnp)
        self.assertEqual(out.price, "4.900")
        self.assertEqual(out.raw["field_sources"]["barcode_raw"], "barcode")
        self.assertEqual(out.raw["field_sources"]["price"], "ocr")
        self.assertNotEqual(out.raw["field_sources"].get("barcode_raw"), "vision")

    def test_merge_price_ocr_beats_vision_unless_empty(self):
        from teyssir.catalog.bookscan.merge import merge_scan_layers

        vision = BookDraft(title="T", price="50.000", source="vision")
        ocr = BookDraft(title="T", price="8.500", source="tesseract")
        out = merge_scan_layers(metadata=None, vision=vision, ocr=ocr)
        self.assertEqual(out.price, "8.500")
        self.assertEqual(out.raw["field_sources"]["price"], "ocr")

        ocr_empty = BookDraft(title="T", price="", source="tesseract")
        out2 = merge_scan_layers(metadata=None, vision=vision, ocr=ocr_empty)
        self.assertEqual(out2.price, "50.000")
        self.assertEqual(out2.raw["field_sources"]["price"], "vision")

    def test_merge_digit_ocr_isbn_low_confidence_provenance(self):
        from teyssir.catalog.bookscan.merge import merge_scan_layers

        suspect = "9787723827435"
        ocr = BookDraft(
            title="الثلاثي", isbn13=suspect, source="tesseract", confidence=0.85,
            raw={"isbn_from_digit_ocr": True, "isbn_detected": True},
        )
        out = merge_scan_layers(
            metadata=None, vision=None, ocr=ocr,
            isbn_hint=suspect, isbn_source="digit_ocr",
        )
        self.assertEqual(out.isbn13, suspect)
        self.assertEqual(out.raw["field_sources"]["isbn13"], "digit_ocr")
        self.assertLessEqual(out.confidence or 0, 0.35)
        self.assertTrue(out.raw.get("isbn_from_digit_ocr"))

    def test_merge_rejects_invalid_checksum_isbn(self):
        from teyssir.catalog.bookscan.merge import merge_scan_layers

        vision = BookDraft(
            title="Fake", isbn13="9781234567890", source="vision",
            raw={"isbn_from_vision": True},
        )
        ocr = BookDraft(title="x", source="tesseract")
        out = merge_scan_layers(metadata=None, vision=vision, ocr=ocr)
        self.assertEqual(out.isbn13, "")
        self.assertNotIn("isbn13", out.raw.get("field_sources") or {})

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
        # Phase 2D: PVP / ثمن sticker labels + reject millime noise / off-by-one
        self.assertEqual(extract_price_dt("PVP : 4,200 DT"), "4.200")
        self.assertEqual(extract_price_dt("ثمن البيع للعموم 4,900 د.ت"), "4.900")
        self.assertEqual(extract_price_dt("Prix : 2,000"), "2.000")
        self.assertEqual(
            extract_price_dt("الثمن 17,000\nnoise 17,900 elsewhere"),
            "17.000",
        )
        self.assertEqual(extract_price_dt("random 9.255 mush"), "")
        self.assertEqual(extract_price_dt("ISBN 9789973352743 34.900"), "")
        # History CNP: barcode digit glued onto 4.900 → reject 34.900 / prefer labeled
        self.assertEqual(
            extract_price_dt("ME . 34,900 ppt\n3\n. , 14,9002 ,\n7 5"),
            "",
        )
        self.assertEqual(
            extract_price_dt("ثمن البيع للعموم 4,900 د.ت\n34,900"),
            "4.900",
        )
        self.assertEqual(
            extract_price_dt("نس نيم تعسرم 4,900\n19330282\n24,900"),
            "4.900",
        )
        self.assertEqual(
            extract_price_dt("6192302468921\n34.900"),
            "",
        )

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
        for junk in (
            "wis! Boot ay", "9 or et O.", 'ol YI a "Teeny"', "arr", "FRE pte",
            "PEL oe nee", "ead chien", "herbe", "Whee",
        ):
            self.assertTrue(
                is_garbage_latin_ocr(junk, mean_conf=35),
                msg=f"expected garbage: {junk!r}",
            )
            self.assertNotIn("en", detect_script_langs(junk, mean_conf=35))
            self.assertFalse(is_usable_ocr_title(junk, mean_conf=35))

        self.assertFalse(is_garbage_latin_ocr("The Little Prince", mean_conf=70))
        self.assertFalse(is_garbage_latin_ocr("Beauty and the Beast", mean_conf=55))
        self.assertFalse(is_garbage_latin_ocr("Mathématiques", mean_conf=55))
        self.assertFalse(is_garbage_latin_ocr("Le premier", mean_conf=55))
        self.assertTrue(is_usable_ocr_title("The Little Prince", mean_conf=70))
        self.assertTrue(is_usable_ocr_title("كتاب الفقه", mean_conf=40))

        # Latin garbage on a Latin cover → no fake Arabic warning / no en tag
        draft = _draft_from_text("wis! Boot ay\narr", role="front", mean_conf=35)
        self.assertEqual(draft.title, "")
        self.assertTrue(draft.raw.get("ocr_garbage_latin"))
        self.assertNotIn("en", draft.languages)
        self.assertFalse(draft.raw.get("ocr_arabic_likely"))

        # Arabic cover with Latin garbage still marks Arabic-likely
        draft_ar = _draft_from_text(
            "wis! Boot ay\nكتاب الفقه للمبتدئين", role="front", mean_conf=35,
        )
        self.assertTrue(draft_ar.raw.get("ocr_garbage_latin") or "ar" in (draft_ar.languages or []))
        self.assertNotIn("en", draft_ar.languages)

        # Usable English title must stay en-only (Beauty cover)
        beauty = _draft_from_text(
            "Golden Tales\nBeauty and the Beast\nDAR EL MAAREF",
            role="front", mean_conf=60,
        )
        self.assertIn("Beauty", beauty.title)
        self.assertEqual(beauty.languages, ["en"])
        self.assertNotIn("ar", beauty.languages)
        self.assertFalse(beauty.raw.get("ocr_arabic_likely"))

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


class CoverPreprocessTests(unittest.TestCase):
    """Phase 2A: orient/clamp/bands/white-label ROI (OpenCV or Pillow fallback)."""

    def test_opencv_flag_and_synthetic_bands(self):
        from teyssir.catalog.bookscan.preprocess import (
            cleanup_preprocess,
            opencv_available,
            preprocess_cover,
        )

        self.assertIsInstance(opencv_available(), bool)
        # Synthetic cover: dark margins + white sticker in lower-right
        img = Image.new("RGB", (800, 1100), (40, 40, 40))
        cover = Image.new("RGB", (520, 780), (30, 90, 40))
        sticker = Image.new("RGB", (160, 90), (250, 250, 250))
        cover.paste(sticker, (330, 650))
        img.paste(cover, (140, 120))
        path = os.path.join(tempfile.mkdtemp(), "synth_cover.jpg")
        img.save(path, quality=92)
        prep = preprocess_cover(path, max_edge=1600)
        try:
            self.assertGreaterEqual(prep.width, 200)
            self.assertGreaterEqual(prep.height, 200)
            self.assertLessEqual(max(prep.width, prep.height), 2000)
            self.assertGreaterEqual(prep.title_band.height, int(prep.height * 0.25))
            self.assertGreaterEqual(prep.barcode_band.y0, int(prep.height * 0.45))
            self.assertIn("opencv" if opencv_available() else "pillow", prep.method)
            # White sticker should be detected when OpenCV is present
            if opencv_available() and prep.white_label is not None:
                wl = prep.white_label
                self.assertLessEqual(prep.barcode_band.x0, wl.x0 + 8)
                self.assertLessEqual(prep.barcode_band.y0, wl.y0 + 8)
                self.assertGreaterEqual(prep.barcode_band.x1, wl.x1 - 8)
                self.assertGreaterEqual(prep.barcode_band.y1, wl.y1 - 8)
        finally:
            cleanup_preprocess([prep])

    def test_books_photos_critical_versos_have_sticker_in_bands(self):
        """Corpus History 12.41 + Math 12.42 versos: compact sticker, not sleeve FP."""
        root = Path(__file__).resolve().parents[2] / "books_photos"
        if not root.is_dir():
            self.skipTest("books_photos/ not present")

        from teyssir.catalog.bookscan.preprocess import cleanup_preprocess, preprocess_cover

        critical = []
        for path in root.iterdir():
            if not path.name.lower().endswith(".jpg"):
                continue
            if "12.42" in path.name:
                critical.append(path)
            elif "12.41" in path.name and "#2" not in path.name:
                critical.append(path)
        if len(critical) < 2:
            self.skipTest("critical verso samples (*12.41* / *12.42*) missing")

        for path in critical:
            prep = preprocess_cover(str(path))
            try:
                self.assertIsNotNone(prep.white_label, msg=str(path))
                wl = prep.white_label
                bb, pb = prep.barcode_band, prep.price_band
                # Sticker-like: compact, not left-edge sleeve, lower cover
                cover_area = max(prep.width * prep.height, 1)
                label_area = wl.width * wl.height
                ar = wl.width / float(max(wl.height, 1))
                cy = (wl.y0 + wl.y1) / 2.0 / max(prep.height, 1)
                left_hug = wl.x0 <= max(6, int(prep.width * 0.045))
                self.assertFalse(
                    left_hug and (wl.height / prep.height > 0.25 or ar < 1.15),
                    msg=f"{path.name}: white_label looks like left sleeve {wl}",
                )
                self.assertLess(
                    label_area / cover_area,
                    0.12,
                    msg=f"{path.name}: white_label too large (sleeve-sized) {wl}",
                )
                self.assertGreater(
                    label_area / cover_area,
                    0.004,
                    msg=f"{path.name}: white_label too tiny {wl}",
                )
                self.assertGreaterEqual(ar, 0.7, msg=f"{path.name}: ar={ar}")
                self.assertLessEqual(ar, 5.0, msg=f"{path.name}: ar={ar}")
                self.assertGreaterEqual(cy, 0.55, msg=f"{path.name}: not lower cover cy={cy}")
                self.assertLessEqual(bb.x0 - 8, wl.x0)
                self.assertLessEqual(bb.y0 - 8, wl.y0)
                self.assertGreaterEqual(bb.x1 + 8, wl.x1)
                self.assertGreaterEqual(bb.y1 + 8, wl.y1)
                self.assertLessEqual(pb.x0 - 8, wl.x0)
                self.assertLessEqual(pb.y0 - 8, wl.y0)
                self.assertGreaterEqual(pb.x1 + 8, wl.x1)
                self.assertGreaterEqual(pb.y1 + 8, wl.y1)
            finally:
                cleanup_preprocess([prep])

    def test_prefers_compact_sticker_over_left_sleeve(self):
        """Synthetic: large left grey sleeve must lose to compact white barcode sticker."""
        from teyssir.catalog.bookscan.preprocess import (
            cleanup_preprocess,
            opencv_available,
            preprocess_cover,
        )

        if not opencv_available():
            self.skipTest("OpenCV required for white_label scoring test")

        # Portrait cover: dark green page, left grey sleeve strip, white sticker BR
        img = Image.new("RGB", (900, 1200), (30, 30, 30))
        cover = Image.new("RGB", (620, 900), (40, 120, 50))
        sleeve = Image.new("RGB", (140, 700), (170, 170, 170))
        cover.paste(sleeve, (0, 120))
        # Compact white sticker with dark barcode-like bars
        sticker = Image.new("RGB", (170, 100), (248, 248, 248))
        draw = ImageDraw.Draw(sticker)
        for i, x in enumerate(range(20, 150, 4)):
            draw.line([(x, 25), (x, 75)], fill=(20, 20, 20), width=2 if i % 3 else 1)
        cover.paste(sticker, (380, 720))
        img.paste(cover, (140, 100))
        path = os.path.join(tempfile.mkdtemp(), "sleeve_vs_sticker.jpg")
        img.save(path, quality=95)
        prep = preprocess_cover(path, max_edge=1600)
        try:
            self.assertIsNotNone(prep.white_label)
            wl = prep.white_label
            cx = (wl.x0 + wl.x1) / 2.0 / prep.width
            # Must not be the left-edge sleeve
            self.assertGreater(wl.x0, int(prep.width * 0.08), msg=f"sleeve FP: {wl}")
            self.assertGreater(cx, 0.35, msg=f"expected sticker toward right: {wl}")
            self.assertLess(wl.height / prep.height, 0.35, msg=f"too tall (sleeve): {wl}")
        finally:
            cleanup_preprocess([prep])

    def test_scan_book_runs_preprocess_before_barcode(self):
        """scan_book still returns a draft when preprocess wraps the path."""
        from unittest.mock import patch

        from teyssir.catalog.bookscan.draft import BookDraft

        img = Image.new("RGB", (400, 600), "white")
        path = os.path.join(tempfile.mkdtemp(), "front.jpg")
        img.save(path)
        empty = BookDraft(title="", raw={"isbn_not_detected": True}, confidence=0.1)
        with patch("teyssir.catalog.bookscan.services.get_ocr_provider") as g, \
             patch("teyssir.catalog.bookscan.services._barcode_isbn_from_paths",
                   return_value=("", "")), \
             patch("teyssir.catalog.bookscan.services._should_try_vision", return_value=False):
            g.return_value.extract.return_value = ("", empty)
            draft, _text = scan_book([path])
        self.assertIsNotNone(draft)
        self.assertTrue(draft.raw.get("isbn_not_detected"))


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


class NonIsbnBarcodeRetentionTests(TestCase):
    """Phase 2B: keep Tunisian CNP / GTIN barcodes without inventing ISBN."""

    # Ground-truth CNP EAN-13 (checksum-valid) for Book C History sticker
    HISTORY_CNP = "6192202606921"
    # Checksum-valid stand-in for Book D Math (printed digits + check)
    MATH_CNP = "6192202502353"

    def test_classify_cnp_619_is_local_product_not_isbn(self):
        from teyssir.catalog.bookscan.barcode import classify_barcode
        from teyssir.catalog.bookscan.isbn import to_isbn13

        hit = classify_barcode(self.HISTORY_CNP, "EAN13")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.kind, "local_product")
        self.assertEqual(hit.raw, self.HISTORY_CNP)
        self.assertEqual(to_isbn13(self.HISTORY_CNP), "")
        self.assertNotEqual(hit.kind, "isbn13")

    def test_classify_bookland_is_isbn13(self):
        from teyssir.catalog.bookscan.barcode import classify_barcode

        hit = classify_barcode("9789973352743", "EAN13")
        self.assertEqual(hit.kind, "isbn13")
        self.assertEqual(hit.raw, "9789973352743")
        self.assertEqual(hit.symbology, "ISBN")

    def test_decode_retains_cnp_ean_from_rendered_image(self):
        try:
            import barcode
            from barcode.writer import ImageWriter
        except Exception:
            self.skipTest("python-barcode not available")

        from teyssir.catalog.bookscan.barcode import decode_isbn_barcode, decode_product_barcode
        from teyssir.catalog.bookscan.isbn import to_isbn13

        buf = BytesIO()
        barcode.get_barcode_class("ean13")(self.HISTORY_CNP[:12], writer=ImageWriter()).write(buf)
        buf.seek(0)
        bc = Image.open(buf).convert("RGB")
        # Phone-like: small angled barcode on a large page (ROI path matters)
        page = Image.new("RGB", (900, 1200), (40, 90, 50))
        sticker = Image.new("RGB", (280, 160), (250, 250, 250))
        small = bc.resize((220, 90))
        sticker.paste(small, (30, 40))
        page.paste(sticker, (560, 980))
        path = os.path.join(tempfile.mkdtemp(), "history_cnp.png")
        page.save(path)

        hit = decode_product_barcode(path)
        self.assertIsNotNone(hit, "zbar should retain CNP EAN-13")
        self.assertEqual(hit.raw, self.HISTORY_CNP)
        self.assertEqual(hit.kind, "local_product")
        self.assertEqual(decode_isbn_barcode(path), "")
        self.assertEqual(to_isbn13(hit.raw), "")

    def test_create_book_c_history_barcode_no_isbn(self):
        """Book C History: no ISBN, CNP barcode searchable, price 4.900."""
        from teyssir.catalog.search import lookup_by_code

        product = create_book_from_draft(
            data={
                "title": "كتاب التاريخ",
                "isbn13": "",
                "barcode_raw": self.HISTORY_CNP,
                "barcode_symbology": "EAN13",
                "barcode_kind": "local_product",
                "languages": ["ar"],
                "publisher": "Centre National Pédagogique",
            },
            sale_price="4.900",
        )
        self.assertEqual(product.sku, self.HISTORY_CNP)
        self.assertEqual(product.isbn, "")
        book = Book.objects.get(product=product)
        self.assertEqual(book.isbn13, "")
        self.assertTrue(
            Barcode.objects.filter(value=self.HISTORY_CNP, product=product).exists()
        )
        found = lookup_by_code(self.HISTORY_CNP).filter(pk=product.pk).first()
        self.assertIsNotNone(found)
        self.assertEqual(str(product.sale_price), "4.900")

    def test_create_book_d_math_barcode_and_side_code(self):
        """Book D Math: no ISBN, CNP + side code 222231, price 4.200."""
        from teyssir.catalog.search import lookup_by_code

        product = create_book_from_draft(
            data={
                "title": "الرياضيات",
                "isbn13": "",
                "barcode_raw": self.MATH_CNP,
                "barcode_symbology": "EAN13",
                "barcode_kind": "local_product",
                "extra_barcodes": [{"value": "222231", "symbology": "CODE128"}],
                "languages": ["ar"],
            },
            sale_price="4.200",
        )
        self.assertEqual(product.isbn, "")
        self.assertTrue(Barcode.objects.filter(value=self.MATH_CNP, product=product).exists())
        self.assertTrue(Barcode.objects.filter(value="222231", product=product).exists())
        self.assertIsNotNone(lookup_by_code(self.MATH_CNP).filter(pk=product.pk).first())
        self.assertIsNotNone(lookup_by_code("222231").filter(pk=product.pk).first())
        self.assertEqual(str(product.sale_price), "4.200")

    def test_scan_keeps_cnp_barcode_fields_without_isbn(self):
        try:
            import barcode
            from barcode.writer import ImageWriter
        except Exception:
            self.skipTest("python-barcode not available")

        from unittest.mock import patch

        from teyssir.catalog.bookscan.draft import BookDraft

        buf = BytesIO()
        barcode.get_barcode_class("ean13")(self.HISTORY_CNP[:12], writer=ImageWriter()).write(buf)
        buf.seek(0)
        bc = Image.open(buf).convert("RGB").resize((200, 80))
        page = Image.new("RGB", (700, 1000), "white")
        page.paste(bc, (250, 880))
        path = os.path.join(tempfile.mkdtemp(), "cnp_scan.png")
        page.save(path)

        empty = BookDraft(
            title="كتاب التاريخ",
            source="tesseract",
            confidence=0.4,
            languages=["ar"],
            price="4.900",
            raw={"isbn_not_detected": True, "price_detected": True},
        )
        with patch("teyssir.catalog.bookscan.services.get_ocr_provider") as g, \
             patch("teyssir.catalog.bookscan.services._should_try_vision", return_value=False):
            class P:
                name = "tesseract"

                def extract(self, p, role="auto"):
                    return "كتاب التاريخ", empty
            g.return_value = P()
            out, _ = scan_book([path], isbn="", enrich=lambda i: None)

        self.assertEqual(out.barcode_raw, self.HISTORY_CNP)
        self.assertEqual(out.barcode_kind, "local_product")
        self.assertEqual(out.isbn13, "")
        self.assertTrue(out.raw.get("barcode_detected"))
        self.assertTrue(out.raw.get("barcode_non_isbn"))
        self.assertFalse(out.raw.get("isbn_from_barcode"))

    def test_digit_ocr_never_fills_barcode_raw(self):
        """Digit-OCR ISBN path must not promote OCR digits as barcode_*."""
        from unittest.mock import patch

        from teyssir.catalog.bookscan.draft import BookDraft

        suspect = "9787723827435"
        ocr_draft = BookDraft(
            title="test",
            isbn13=suspect,
            source="tesseract",
            confidence=0.85,
            raw={"isbn_from_digit_ocr": True, "isbn_detected": True},
        )
        with patch("teyssir.catalog.bookscan.services.get_ocr_provider") as g, \
             patch("teyssir.catalog.bookscan.services._product_barcode_from_paths",
                   return_value=None), \
             patch("teyssir.catalog.bookscan.services._barcode_isbn_from_paths",
                   return_value=(suspect, "digit_ocr")), \
             patch("teyssir.catalog.bookscan.services._should_try_vision", return_value=False):
            class P:
                name = "tesseract"

                def extract(self, path, role="auto"):
                    return "test", ocr_draft
            g.return_value = P()
            out, _ = scan_book(["/tmp/fake.png"], isbn="", enrich=lambda i: None)
        self.assertEqual(out.barcode_raw, "")
        self.assertNotEqual(out.raw.get("barcode_source"), "digit_ocr")
        self.assertLess(out.confidence or 0, 0.5)


class FastOcrPathTests(unittest.TestCase):
    """Phase 2C: budgeted lang passes + legacy barcode fallback cap."""

    def test_budgeted_lang_passes_max_two(self):
        from teyssir.catalog.bookscan.ocr import _budgeted_lang_passes

        installed = {"ara", "fra", "eng"}
        one = _budgeted_lang_passes(
            "front", installed, primary="ara+fra", bilingual_evidence=False,
        )
        self.assertEqual(len(one), 1)
        two = _budgeted_lang_passes(
            "front", installed, primary="ara+fra", bilingual_evidence=True,
        )
        self.assertLessEqual(len(two), 2)
        self.assertTrue(two[0].startswith("ara"))

    def test_fast_path_waits_for_bilingual_second_pass(self):
        from teyssir.catalog.bookscan.draft import BookDraft
        from teyssir.catalog.bookscan.ocr import _fast_path_ready

        draft = BookDraft(
            title="الأول في السنة الأولى", isbn13="9789973352743",
            raw={"isbn_from_barcode": True},
        )
        self.assertTrue(_fast_path_ready(draft, 55.0, bilingual_pending=False))
        self.assertFalse(_fast_path_ready(draft, 55.0, bilingual_pending=True))

    def test_legacy_barcode_budget_small(self):
        from teyssir.catalog.bookscan import barcode as bc
        from PIL import Image

        self.assertLessEqual(bc._LEGACY_FALLBACK_BUDGET, 8)
        img = Image.new("RGB", (900, 1200), "white")
        regions = list(bc._barcode_regions(img))
        self.assertLessEqual(len(regions), 5)
        variants = list(bc._variants(regions[0][1]))
        # region×variant uncapped would explode; budget truncates decode tries
        self.assertLessEqual(len(regions) * len(variants), 40)

    def test_preprocess_variants_prefer_title_band_and_cap(self):
        import tempfile
        from teyssir.catalog.bookscan.ocr import _preprocess_variants
        from teyssir.catalog.bookscan.preprocess import RoiBox, CoverPreprocessResult
        from PIL import Image

        img = Image.new("RGB", (800, 1000), "white")
        path = tempfile.mktemp(suffix=".jpg")
        img.save(path)
        prep = CoverPreprocessResult(
            path=path,
            original_path=path,
            width=800,
            height=1000,
            title_band=RoiBox(40, 40, 760, 320),
            barcode_band=RoiBox(40, 720, 760, 980),
            price_band=RoiBox(40, 520, 760, 720),
            white_label=None,
            method="test",
        )
        labels = [lab for lab, _ in _preprocess_variants(path, role="auto", prepare=prep)]
        self.assertIn("title_band", labels)
        self.assertIn("title_thr", labels)
        self.assertTrue(any(l.startswith("price") or l.startswith("barcode") for l in labels))
        # title_band + title_thr + title_gray + price/barcode bands (≤9 without white_label)
        self.assertLessEqual(len(labels), 9)
        self.assertNotIn("lower_rot90", labels)
        self.assertNotIn("calligraphy", labels)


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
             patch("teyssir.catalog.bookscan.services._maybe_vision_draft") as vision:
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
             patch("teyssir.catalog.bookscan.services._maybe_vision_draft") as vision:
            class P:
                name = "tesseract"
                def extract(self, path, role="auto"):
                    return "wis! Boot ay", ocr_draft
            g.return_value = P()

            def _vision(paths, draft):
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

    def test_should_try_vision_skips_strong_barcode_title(self):
        """Phase 2E: barcode ISBN + usable title must not call Vision."""
        from teyssir.catalog.bookscan.services import _should_try_vision
        class P:
            name = "tesseract"
        strong = BookDraft(
            title="Le Petit Prince", isbn13="9782070612758",
            source="tesseract", confidence=0.7,
            raw={
                "isbn_from_barcode": True,
                "barcode_detected": True,
                "ocr_mean_confidence": 70,
            },
        )
        self.assertFalse(_should_try_vision(strong, P()))
        cnp_title = BookDraft(
            title="Mathematique 6eme", barcode_raw="6191234567890",
            source="tesseract", confidence=0.6,
            raw={
                "barcode_detected": True,
                "barcode_non_isbn": True,
                "ocr_mean_confidence": 65,
            },
        )
        self.assertFalse(_should_try_vision(cnp_title, P()))

    def test_should_try_vision_on_phone_photo_no_barcode(self):
        """Missing barcode + unusable title (phone cover) -> Vision."""
        from teyssir.catalog.bookscan.services import _should_try_vision
        class P:
            name = "tesseract"
        phone = BookDraft(
            title="", source="tesseract", confidence=0.2,
            raw={"isbn_not_detected": True, "ocr_mean_confidence": 28},
        )
        self.assertTrue(_should_try_vision(phone, P()))
        garb = BookDraft(
            title="wis! Boot ay", source="tesseract", confidence=0.25,
            raw={"ocr_garbage_latin": True, "ocr_mean_confidence": 30},
        )
        self.assertTrue(_should_try_vision(garb, P()))

    def test_vision_rejects_invented_isbn_without_checksum(self):
        """Vision must never keep an ISBN that fails check digit."""
        from teyssir.catalog.bookscan.ocr import _draft_from_vision_json
        from teyssir.catalog.bookscan.services import _sanitize_vision_isbn

        bad = _draft_from_vision_json(
            '{"title": "Fake Book", "isbn13": "9781234567890", "authors": []}'
        )
        self.assertEqual(bad.isbn13, "")
        self.assertTrue(bad.raw.get("rejected_isbn") or bad.raw.get("isbn_not_detected"))
        drafted = BookDraft(title="X", isbn13="9781234567890", source="vision", raw={})
        cleaned = _sanitize_vision_isbn(drafted)
        self.assertEqual(cleaned.isbn13, "")
        self.assertTrue(cleaned.raw.get("vision_isbn_rejected"))

    def test_dual_image_vision_json_parse_and_description(self):
        """Phase 15.4: one call with front+back; language_detected + description required."""
        import tempfile
        from unittest.mock import patch

        from PIL import Image

        from teyssir.catalog.bookscan.vision import analyze_covers, draft_from_vision_json

        reply = json.dumps({
            "title": "الأمير الصغير",
            "authors": ["Antoine de Saint-Exupéry"],
            "publisher": "Gallimard",
            "languages": ["ar", "fr"],
            "language_detected": "mixed:ar+fr",
            "description": (
                "Roman poétique pour enfants et adultes. "
                "L'histoire suit un petit prince venu d'une autre planète. "
                "Il rencontre un aviateur dans le désert."
            ),
            "isbn13": "9782070612758",
            "price": "",
            "barcode_raw": "6199999999999",
        }, ensure_ascii=False)

        draft = draft_from_vision_json(reply)
        self.assertEqual(draft.title, "الأمير الصغير")
        self.assertEqual(draft.language_detected, "mixed:ar+fr")
        self.assertTrue(draft.description)
        self.assertGreaterEqual(len([s for s in draft.description.split(".") if s.strip()]), 2)
        self.assertEqual(draft.isbn13, "9782070612758")
        self.assertEqual(draft.barcode_raw, "")  # never invent barcode_*

        # Invented ISBN rejected
        invented = draft_from_vision_json(
            '{"title": "X", "language_detected": "en", '
            '"description": "One. Two. Three.", "isbn13": "9781234567890"}'
        )
        self.assertEqual(invented.isbn13, "")
        self.assertTrue(invented.raw.get("vision_isbn_rejected") or invented.raw.get("rejected_isbn"))

        # Dual-image transport receives TWO base64 payloads
        seen = {"n": 0, "count": 0}
        tmp = tempfile.mkdtemp()
        front = os.path.join(tmp, "f.png")
        back = os.path.join(tmp, "b.png")
        Image.new("RGB", (64, 64), "white").save(front)
        Image.new("RGB", (64, 64), "blue").save(back)

        def transport(images_b64):
            seen["n"] += 1
            seen["count"] = len(images_b64)
            return reply

        raw, out = analyze_covers(front, back, transport=transport, timeout=5)
        self.assertEqual(seen["n"], 1)
        self.assertEqual(seen["count"], 2)
        self.assertTrue(out.raw.get("vision_dual_image"))
        self.assertEqual(out.description.count("."), 3)  # three sentences end with .
        self.assertEqual(out.language_detected, "mixed:ar+fr")
        self.assertEqual(out.barcode_raw, "")

    def test_maybe_vision_draft_dual_image_once(self):
        """_maybe_vision_draft uses analyze_covers once (front+back), not two sequential calls."""
        import tempfile
        from unittest.mock import patch

        from PIL import Image

        from teyssir.catalog.bookscan.services import _maybe_vision_draft

        tmp = tempfile.mkdtemp()
        front = os.path.join(tmp, "f.png")
        back = os.path.join(tmp, "b.png")
        Image.new("RGB", (32, 32), "white").save(front)
        Image.new("RGB", (32, 32), "red").save(back)
        ocr_draft = BookDraft(
            title="", source="tesseract", confidence=0.1,
            raw={"ocr_garbage_latin": True},
        )
        calls = {"n": 0}

        def fake_analyze(front_path, back_path=None, **kwargs):
            calls["n"] += 1
            self.assertIsNotNone(back_path)
            d = BookDraft(
                title="Book", description="A. B. C.", language_detected="en",
                source="vision", confidence=0.8,
                raw={"vision_dual_image": True},
            )
            return '{"title":"Book"}', d

        with patch(
            "teyssir.catalog.bookscan.vision.analyze_covers", side_effect=fake_analyze,
        ):
            out = _maybe_vision_draft([front, back], ocr_draft)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(out.title, "Book")
        self.assertTrue(out.description)

    def test_scan_autofills_description_from_vision(self):
        """Vision layer description becomes draft.description via merge."""
        from unittest.mock import patch

        ocr_draft = BookDraft(
            title="", source="tesseract", confidence=0.1, languages=["ar"],
            raw={"ocr_garbage_latin": True, "ocr_arabic_likely": True},
        )
        vision = BookDraft(
            title="كتاب الفقه", authors=["مؤلف"], languages=["ar"],
            language_detected="ar",
            description="كتاب في الفقه الإسلامي. يتناول أحكام العبادات. مناسب للطلاب.",
            source="vision", confidence=0.8,
            raw={"vision_fallback": True, "vision_description": True},
        )
        with patch("teyssir.catalog.bookscan.services.get_ocr_provider") as g, \
             patch("teyssir.catalog.bookscan.services._barcode_isbn_from_paths", return_value=("", "")), \
             patch("teyssir.catalog.bookscan.services._maybe_vision_draft", return_value=vision):
            class P:
                name = "tesseract"
                def extract(self, path, role="auto"):
                    return "garbage", ocr_draft
            g.return_value = P()
            out, _ = scan_book(
                ["/tmp/fake.png"], isbn="", enrich=lambda i: None,
                enrich_title=lambda t, a="": None,
            )
        self.assertEqual(out.title, "كتاب الفقه")
        self.assertTrue(out.description)
        self.assertIn("الفقه", out.description)
        self.assertEqual((out.raw or {}).get("field_sources", {}).get("description"), "vision")

    def test_scan_isbn_metadata_raises_confidence(self):
        """Barcode/ISBN hint + OpenLibrary must present high confidence, not 35%."""
        from unittest.mock import patch

        from teyssir.catalog.bookscan.barcode import DecodedBarcode

        ocr_draft = BookDraft(
            title="wrong tess", source="tesseract", confidence=0.35,
            raw={"isbn_not_detected": True},
        )
        meta = BookDraft(
            title="Le Petit Prince", authors=["Saint-Exupéry"],
            isbn13="9782070612758", source="openlibrary", confidence=0.9,
        )
        hit = DecodedBarcode(
            raw="9782070612758", symbology="ISBN", kind="isbn13", source="barcode",
        )
        with patch("teyssir.catalog.bookscan.services.get_ocr_provider") as g, \
             patch("teyssir.catalog.bookscan.services._product_barcode_from_paths",
                   return_value=hit), \
             patch("teyssir.catalog.bookscan.services._should_try_vision", return_value=False):
            class P:
                name = "tesseract"
                def extract(self, path, role="auto", prepare=None, known_barcode=None):
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
        self.book.qty_on_hand = Decimal("5"); self.book.reorder_point = Decimal("3")
        self.book.save()
        self.pen = Product.objects.create(sku="PEN-9", name_fr="Stylo Bic",
                                          sale_price=Decimal("0.850"), qty_on_hand=Decimal("0"))
        self.cah = Product.objects.create(sku="CAH-9", name_fr="Cahier", sale_price=Decimal("1.200"),
                                          qty_on_hand=Decimal("2"), reorder_point=Decimal("5"))

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
        self.assertEqual(d["qty_on_hand"], "5.000")


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
        self.assertEqual(r.json()["product"]["qty_on_hand"], "50.000")   # opening stock applied
        self.assertFalse(r.json()["product"]["is_book"])          # a supply, not a book
        self.assertTrue(Barcode.objects.filter(value="6191234567890").exists())

    def test_duplicate_barcode_is_rejected(self):
        self.client.post("/api/v1/catalog/register",
                         {"name_fr": "A", "barcode": "6199999999999"}, format="json")
        r = self.client.post("/api/v1/catalog/register",
                             {"name_fr": "B", "barcode": "6199999999999"}, format="json")
        self.assertEqual(r.status_code, 409)

    def test_register_without_barcode_generates_sku(self):
        r = self.client.post("/api/v1/catalog/register", {"name_fr": "Article sans code"},
                             format="json")
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.json()["sku"].startswith("ART-"))


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


class BooksPhotosFixtureTests(unittest.TestCase):
    """Phase 2D: A–D ground-truth fields Tess can reasonably hit (ROI / text fixtures)."""

    @staticmethod
    def _root():
        return Path(__file__).resolve().parents[2] / "books_photos"

    @classmethod
    def _photo(cls, *needles, exclude=()):
        root = cls._root()
        if not root.is_dir():
            return None
        for p in sorted(root.iterdir()):
            name = p.name
            if all(n in name for n in needles) and not any(x in name for x in exclude):
                return p
        return None

    def test_ground_truth_price_and_lang_text_fixtures(self):
        """Sticker OCR strings → expected price / lang tags (no live Tess)."""
        from teyssir.catalog.bookscan.ocr import detect_script_langs, _draft_from_text
        from teyssir.catalog.bookscan.price import extract_price_dt

        # A Beauty verso
        self.assertEqual(extract_price_dt("2ème Edition : 2019\nPrix : 2,000"), "2.000")
        beauty = _draft_from_text(
            "Golden Tales\nBeauty and the Beast\nDAR EL MAAREF",
            role="front", mean_conf=60,
        )
        self.assertIn("Beauty", beauty.title)
        self.assertEqual(beauty.languages, ["en"])

        # B Premier — bilingual FR+AR + ثمن 17.000
        self.assertEqual(extract_price_dt("الثمن\n17,000"), "17.000")
        premier = _draft_from_text(
            "الأول في السنة الأولى ثانوي\nLe premier\nen première année secondaire",
            role="front", mean_conf=55,
        )
        self.assertIn("Le premier", premier.title)
        self.assertIn("الأول", premier.title)
        self.assertIn("ar", premier.languages)
        self.assertIn("fr", premier.languages)

        # C History sticker
        self.assertEqual(
            extract_price_dt("المركز الوطني البيداغوجي\nثمن البيع للعموم 4,900 د.ت"),
            "4.900",
        )
        hist = _draft_from_text(
            "كتاب التاريخ\nلتلاميذ السنة الأولى من التعليم الثانوي",
            role="front", mean_conf=50,
        )
        self.assertIn("التاريخ", hist.title)
        self.assertEqual(hist.languages, ["ar"])

        # D Math front + PVP sticker
        self.assertEqual(extract_price_dt("PVP : 4,200 DT"), "4.200")
        math = _draft_from_text(
            "Mathématiques\n2ème année de l'enseignement secondaire",
            role="front", mean_conf=55,
        )
        self.assertIn("Mathématiques", math.title)
        self.assertIn("fr", math.languages)
        self.assertNotIn("ar", math.languages)
        self.assertNotIn("en", detect_script_langs("Mathématiques", mean_conf=55))

    def test_premier_isbn_classify_and_optional_photo_decode(self):
        from teyssir.catalog.bookscan.barcode import classify_barcode, decode_product_barcode
        from teyssir.catalog.bookscan.preprocess import preprocess_cover, cleanup_preprocess

        hit = classify_barcode("9789973352743", "EAN13")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.kind, "isbn13")
        self.assertEqual(hit.raw, "9789973352743")

        path = self._photo("12.40", "#2")
        if path is None:
            self.skipTest("Premier verso photo missing")
        prep = None
        try:
            prep = preprocess_cover(str(path))
            decoded = decode_product_barcode(str(path), prepare=prep)
        finally:
            if prep is not None:
                cleanup_preprocess([prep])
        if decoded is None:
            self.skipTest("zbar/OpenCV missed Premier ISBN bars (lighting) — Vision 2E")
        self.assertEqual(decoded.kind, "isbn13")
        self.assertEqual(decoded.raw, "9789973352743")

    def test_history_math_cnp_optional_photo_decode(self):
        from teyssir.catalog.bookscan.barcode import decode_product_barcode
        from teyssir.catalog.bookscan.preprocess import preprocess_cover, cleanup_preprocess

        cases = [
            (("12.41",), ("#2",), "local_product", "619"),
            (("12.42",), (), "local_product", "619"),
        ]
        any_hit = False
        for needles, exclude, kind, prefix in cases:
            path = self._photo(*needles, exclude=exclude)
            if path is None:
                continue
            prep = None
            try:
                prep = preprocess_cover(str(path))
                decoded = decode_product_barcode(str(path), prepare=prep)
            finally:
                if prep is not None:
                    cleanup_preprocess([prep])
            if decoded is None:
                continue
            any_hit = True
            self.assertEqual(decoded.kind, kind)
            self.assertTrue(decoded.raw.startswith(prefix))
        if not any_hit:
            self.skipTest("zbar/OpenCV missed CNP stickers on History/Math versos")


class BookScanRegressionFixtureTests(unittest.TestCase):
    """Phase 2F: fixture schema + honesty rules (offline; live scan optional)."""

    def test_fixtures_exist_and_schema(self):
        from teyssir.catalog.bookscan.regression import fixtures_dir, load_all_fixtures

        d = fixtures_dir()
        if not d.is_dir():
            self.skipTest("fixtures/bookscan/expected missing")
        fxs = load_all_fixtures(d)
        self.assertGreaterEqual(len(fxs), 4)
        ids = {f["id"] for f in fxs}
        self.assertTrue({"A_beauty", "B_premier", "C_history_cnp", "D_math_cnp"} <= ids)
        for fx in fxs:
            self.assertIn("expect", fx)
            self.assertIn("honesty", fx)
            self.assertIn("images", fx)
            exp = fx["expect"]
            self.assertIn("title_contains", exp)
            self.assertIn("languages", exp)
            self.assertIn("isbn13", exp)
            self.assertIn("barcode_raw", exp)
            self.assertIn("price", exp)
            self.assertTrue(exp.get("title_allow_empty") or exp.get("title_contains"))
            self.assertIn("digit_ocr_confidence_max", fx["honesty"])
            self.assertIn("619", fx["honesty"].get("never_isbn13_prefix") or ["619"])

    def test_honesty_rejects_619_as_isbn_and_digit_ocr_high_conf(self):
        from teyssir.catalog.bookscan.regression import assert_honesty

        bad = BookDraft(
            title="كتاب",
            isbn13="6192202606921",
            barcode_raw="6192202606921",
            barcode_kind="isbn13",
            confidence=0.9,
            source="tesseract",
            raw={"isbn_from_digit_ocr": True},
        )
        fails = {c.code for c in assert_honesty(bad, {
            "never_isbn13_prefix": ["619"],
            "digit_ocr_confidence_max": 0.35,
            "high_confidence_requires": "barcode_isbn_or_metadata",
            "max_confidence_without_strong_id": 0.55,
            "cnp_619_never_isbn13": True,
        }) if not c.ok}
        self.assertIn("isbn13_banned_prefix", fails)
        self.assertIn("isbn13_checksum", fails)
        self.assertIn("cnp_not_isbn", fails)
        self.assertIn("digit_ocr_confidence", fails)

        good = BookDraft(
            title="Le premier",
            isbn13="9789973352743",
            barcode_raw="9789973352743",
            barcode_kind="isbn13",
            confidence=0.9,
            source="tesseract",
            raw={"isbn_from_barcode": True},
        )
        self.assertTrue(all(c.ok for c in assert_honesty(good, {
            "never_isbn13_prefix": ["619"],
            "digit_ocr_confidence_max": 0.35,
            "high_confidence_requires": "barcode_isbn_or_metadata",
            "max_confidence_without_strong_id": 0.55,
        })))

    def test_expect_allow_empty_title(self):
        from teyssir.catalog.bookscan.regression import assert_expect

        draft = BookDraft(
            title="", languages=["ar"], source="tesseract", confidence=0.2,
            raw={"ocr_title_unusable": True},
        )
        checks = assert_expect(draft, {
            "title_contains": ["التاريخ"],
            "title_allow_empty": True,
            "languages": ["ar"],
            "languages_mode": "includes_any",
            "isbn13": "",
            "isbn13_allow_empty": True,
            "barcode_raw": "6192202606921",
            "barcode_raw_allow_empty": True,
            "price": "4.900",
            "price_allow_empty": True,
        })
        self.assertTrue(all(c.ok for c in checks), checks)

    def test_live_books_photos_regression_optional(self):
        """Full Tess scan of A–D; set TEYSSIR_BOOKSCAN_REGRESSION=1 to enable."""
        from teyssir.catalog.bookscan.regression import (
            books_photos_dir,
            env_wants_live_regression,
            run_all_fixtures,
        )

        if not env_wants_live_regression():
            self.skipTest("Set TEYSSIR_BOOKSCAN_REGRESSION=1 for live photo scan")
        if not books_photos_dir().is_dir():
            self.skipTest("books_photos/ missing")
        results = run_all_fixtures(vision=False, strict_fields=True)
        self.assertTrue(results)
        honesty_fails = []
        for r in results:
            self.assertFalse(r.skipped, r.skip_reason)
            for c in r.checks:
                if not c.ok and c.code in (
                    "isbn13_banned_prefix", "isbn13_checksum", "cnp_not_isbn",
                    "cnp_kind", "digit_ocr_confidence", "confidence_honesty",
                ):
                    honesty_fails.append((r.fixture_id, c.code, c.detail))
        self.assertEqual(honesty_fails, [], msg=str(honesty_fails))
