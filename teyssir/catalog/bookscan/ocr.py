"""Pluggable OCR providers (extract text/fields from a cover image). Default is Tesseract
(free, offline, ara+fra+eng), degrading to a no-op if it isn't installed; a Vision-LLM provider
can be plugged for online high-accuracy extraction. Spec docs/BOOK-OCR-ARCHITECTURE.md."""
import re

from django.conf import settings

from .draft import BookDraft


class OcrProvider:
    name = "base"

    def extract(self, image_path):
        """Return (raw_text, BookDraft) from an image path."""
        raise NotImplementedError


class ManualOcrProvider(OcrProvider):
    """No-op fallback when no OCR engine is available (user enters everything)."""

    name = "manual"

    def extract(self, image_path):
        return "", BookDraft(source=self.name, confidence=0.0)


def _draft_from_text(text):
    """Best-effort fields from OCR text. The ISBN is the high-value signal — once found it drives
    accurate metadata enrichment; title/author are loose heuristics for books without an ISBN."""
    draft = BookDraft(source="tesseract", confidence=0.4)
    isbn = re.search(r"(97[89][-\s]?(?:\d[-\s]?){9}\d)", text)
    if isbn:
        draft.isbn13 = re.sub(r"[-\s]", "", isbn.group(1))
    lines = [
        ln.strip() for ln in text.splitlines()
        if len(ln.strip()) >= 3 and not ln.strip().upper().startswith("ISBN")
    ]
    if lines:
        draft.title = lines[0]            # first substantial line ≈ title
    if len(lines) > 1:
        draft.authors = [lines[1]]        # next line ≈ author
    return draft


class TesseractOcrProvider(OcrProvider):
    """Offline OCR via pytesseract (ara+fra+eng). Falls back to manual if unavailable."""

    name = "tesseract"
    LANGS = "ara+fra+eng"

    def extract(self, image_path):
        try:
            import pytesseract
            from PIL import Image
        except Exception:
            return ManualOcrProvider().extract(image_path)
        try:
            text = pytesseract.image_to_string(Image.open(image_path), lang=self.LANGS)
        except Exception:
            return ManualOcrProvider().extract(image_path)
        return text, _draft_from_text(text)


_PROVIDERS = {"manual": ManualOcrProvider, "tesseract": TesseractOcrProvider}


def get_ocr_provider():
    return _PROVIDERS.get(settings.OCR_PROVIDER, ManualOcrProvider)()
