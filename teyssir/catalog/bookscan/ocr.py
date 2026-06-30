"""Pluggable OCR providers (extract text/fields from a cover image). Default is Tesseract
(free, offline, ara+fra+eng), degrading to a no-op if it isn't installed; a Vision-LLM provider
(free, offline via Ollama) can be plugged for high-accuracy structured multilingual extraction.
Spec docs/BOOK-OCR-ARCHITECTURE.md."""
import base64
import json
import re
import urllib.request

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


# --- Vision-LLM provider (free, offline via Ollama) -------------------------------------------

_VISION_PROMPT = (
    "You are reading the cover of a book. The cover may be in Arabic, French or English "
    "(sometimes mixed). Extract the bibliographic data and reply with a SINGLE JSON object only, "
    "no prose. Use these keys (empty string or empty list if unknown): "
    "title, subtitle, authors (list), translators (list), publisher, series, edition, "
    "languages (ISO codes list, e.g. [\"ar\",\"fr\"]), pub_year (int), pages (int), "
    "isbn13, subject, description. Preserve the original script for Arabic text."
)

_VISION_KEYS = {
    "title", "subtitle", "publisher", "series", "edition",
    "subject", "description", "isbn13",
}


def _draft_from_vision_json(raw):
    """Parse the model's JSON reply into a BookDraft (tolerant of stray prose around the JSON)."""
    draft = BookDraft(source="vision", confidence=0.85)
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        data = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        draft.raw = {"vision_reply": raw}
        return draft
    draft.raw = data
    for key in _VISION_KEYS:
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            setattr(draft, key, val.strip())
    if isinstance(data.get("authors"), list):
        draft.authors = [str(a).strip() for a in data["authors"] if str(a).strip()]
    if isinstance(data.get("translators"), list):
        draft.translators = [str(a).strip() for a in data["translators"] if str(a).strip()]
    if isinstance(data.get("languages"), list):
        draft.languages = [str(a).strip() for a in data["languages"] if str(a).strip()]
    for num in ("pub_year", "pages"):
        try:
            if data.get(num) not in (None, "", []):
                setattr(draft, num, int(data[num]))
        except (TypeError, ValueError):
            pass
    if draft.isbn13:
        draft.isbn13 = re.sub(r"[-\s]", "", draft.isbn13)
    return draft


class VisionLlmOcrProvider(OcrProvider):
    """Structured multilingual extraction via a local Ollama vision model (free, offline, no key).
    `transport(image_b64) -> raw_reply` is injectable for tests; the default calls Ollama over
    stdlib urllib. Falls back to manual if the model/server is unreachable."""

    name = "vision"

    def __init__(self, transport=None):
        self._transport = transport

    def _ollama(self, image_b64):
        body = json.dumps({
            "model": settings.VISION_MODEL,
            "prompt": _VISION_PROMPT,
            "images": [image_b64],
            "stream": False,
            "format": "json",
        }).encode()
        req = urllib.request.Request(
            f"{settings.OLLAMA_URL}/api/generate", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=settings.VISION_TIMEOUT) as resp:
            return json.load(resp).get("response", "")

    def extract(self, image_path):
        try:
            with open(image_path, "rb") as fh:
                image_b64 = base64.b64encode(fh.read()).decode()
            raw = (self._transport or self._ollama)(image_b64)
        except Exception:
            return ManualOcrProvider().extract(image_path)
        return raw, _draft_from_vision_json(raw)


_PROVIDERS = {
    "manual": ManualOcrProvider,
    "tesseract": TesseractOcrProvider,
    "vision": VisionLlmOcrProvider,
}


def get_ocr_provider():
    return _PROVIDERS.get(settings.OCR_PROVIDER, ManualOcrProvider)()
