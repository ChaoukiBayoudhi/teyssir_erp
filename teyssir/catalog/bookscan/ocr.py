"""Pluggable OCR providers (extract text/fields from a cover image). Default is Tesseract
(free, offline, ara+fra+eng), degrading to a no-op if it isn't installed; a Vision-LLM provider
(free, offline via Ollama) can be plugged for high-accuracy structured multilingual extraction.
Spec docs/BOOK-OCR-ARCHITECTURE.md.

Phase 6: front vs back cover roles, Arabic/French/English language adaptation, price OCR.
"""
import base64
import json
import re
import urllib.request

from django.conf import settings

from .draft import BookDraft
from .isbn import extract_isbn, to_isbn13
from .price import extract_price_dt


class OcrProvider:
    name = "base"

    def extract(self, image_path, role="auto"):
        """Return (raw_text, BookDraft). ``role`` is front|back|auto."""
        raise NotImplementedError


class ManualOcrProvider(OcrProvider):
    """No-op fallback when no OCR engine is available (user enters everything)."""

    name = "manual"

    def extract(self, image_path, role="auto"):
        return "", BookDraft(
            source=self.name, confidence=0.0,
            raw={"ocr_available": False, "isbn_not_detected": True, "cover_role": role},
        )


def detect_script_langs(text: str) -> list[str]:
    """Heuristic language tags from Unicode ranges (ar / fr|en Latin)."""
    if not text:
        return []
    ar = sum(1 for c in text if "\u0600" <= c <= "\u06FF")
    latin = sum(1 for c in text if ("A" <= c <= "Z") or ("a" <= c <= "z")
                or ("À" <= c <= "ÿ"))
    total = max(ar + latin, 1)
    out = []
    if ar / total >= 0.15:
        out.append("ar")
    if latin / total >= 0.15:
        # French diacritics hint
        if re.search(r"[àâäéèêëïîôùûüçœæÀÂÄÉÈÊËÏÎÔÙÛÜÇ]", text):
            out.append("fr")
        else:
            out.append("en")
    return out or (["ar"] if ar else ["fr"])


def _tesseract_langs_for(text_hint: str = "", role: str = "auto") -> str:
    """Pick Tesseract traineddata set. Prefer Arabic when script suggests it."""
    langs = detect_script_langs(text_hint)
    if "ar" in langs and ("fr" in langs or "en" in langs):
        return "ara+fra+eng"
    if "ar" in langs:
        return "ara+fra"  # Arabic books often mix FR publisher lines
    if role == "back":
        return "fra+eng"  # ISBN/price are Latin digits
    return "fra+eng+ara"


def _preprocess_variants(image_path, role="auto"):
    """Yield (label, PIL.Image) variants tailored to cover role."""
    from PIL import Image, ImageEnhance, ImageOps

    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    mx, my = int(w * 0.12), int(h * 0.12)
    crop = img.crop((mx, my, w - mx, h - my)) if w > 40 and h > 40 else img
    # Back covers: barcode/price often in the lower third
    lower = img.crop((0, int(h * 0.55), w, h)) if role == "back" and h > 80 else None

    def _enhance(im, *, color=False):
        if color:
            # Color path preserves Arabic calligraphy better on some covers
            im = ImageOps.autocontrast(im)
            im = ImageEnhance.Contrast(im).enhance(1.4)
        else:
            im = ImageOps.grayscale(im)
            im = ImageOps.autocontrast(im)
            im = ImageEnhance.Contrast(im).enhance(1.8)
        if max(im.size) < 900:
            scale = 900 / max(im.size)
            im = im.resize((int(im.width * scale), int(im.height * scale)), Image.Resampling.LANCZOS)
        return im

    if role == "back" and lower is not None:
        yield "lower", _enhance(lower)
    yield "crop", _enhance(crop)
    yield "full", _enhance(img)
    if role == "front":
        yield "crop_color", _enhance(crop, color=True)


def _ocr_digits(pytesseract, image) -> str:
    cfg = "--psm 6 -c tessedit_char_whitelist=0123456789Xx-.,"
    return pytesseract.image_to_string(image, config=cfg) or ""


def _ocr_text(pytesseract, image, langs: str, *, psm: int = 6) -> str:
    return pytesseract.image_to_string(image, lang=langs, config=f"--psm {psm}") or ""


def _clean_lines(text: str) -> list[str]:
    skip = re.compile(
        r"^(isbn|issn|www\.|http|prix|price|سعر|الطبعة|édition|edition)\b",
        re.IGNORECASE,
    )
    lines = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if len(s) < 3 or skip.match(s):
            continue
        # Drop pure digit/noise lines for title heuristics
        if re.fullmatch(r"[\d\s\-.,]+", s):
            continue
        lines.append(s)
    return lines


def _draft_from_text(text, *, isbn_hint="", role="auto"):
    """Best-effort fields from OCR text. Role biases ISBN/price vs title/author."""
    draft = BookDraft(source="tesseract", confidence=0.4, raw={"ocr_available": True, "cover_role": role})
    langs = detect_script_langs(text or "")
    if langs:
        draft.languages = langs
        draft.raw["detected_langs"] = langs

    isbn = to_isbn13(isbn_hint) or extract_isbn(text or "")
    if isbn:
        draft.isbn13 = isbn
        draft.raw["isbn_detected"] = True
    else:
        draft.raw["isbn_not_detected"] = True

    price = extract_price_dt(text or "")
    if price:
        draft.price = price
        draft.raw["price_detected"] = True

    if not (text or "").strip():
        draft.confidence = 0.0
        draft.raw["ocr_empty"] = True
        return draft

    lines = _clean_lines(text)
    if role != "back":
        if lines:
            # Prefer longer "title-like" Latin/Arabic line; skip LTR/RTL marks noise
            cleaned = []
            for s in lines[:8]:
                s2 = s.replace("\u200e", "").replace("\u200f", "").strip()
                if len(s2) >= 3:
                    cleaned.append(s2)
            title_cands = sorted(cleaned[:5], key=lambda s: (-len(s), cleaned.index(s))) if cleaned else []
            if title_cands:
                draft.title = title_cands[0]
                draft.raw["title_candidates"] = cleaned[:5]
            if len(cleaned) > 1:
                for ln in cleaned[1:4]:
                    if ln != draft.title and 3 <= len(ln) <= 60:
                        draft.authors = [ln]
                        break

    if draft.isbn13 or draft.price:
        draft.confidence = 0.6 if draft.isbn13 else 0.45
    elif draft.title:
        draft.confidence = 0.35
        draft.raw["ocr_text_only"] = True
    else:
        draft.confidence = 0.1
        draft.raw["ocr_weak"] = True
    return draft


class TesseractOcrProvider(OcrProvider):
    """Offline OCR via pytesseract with front/back and multilingual adaptation."""

    name = "tesseract"

    def extract(self, image_path, role="auto"):
        try:
            import pytesseract
            from PIL import Image  # noqa: F401
        except Exception as exc:
            draft = BookDraft(source="manual", confidence=0.0,
                              raw={"ocr_error": f"tesseract unavailable: {exc}",
                                   "ocr_available": False, "isbn_not_detected": True,
                                   "cover_role": role})
            return "", draft
        try:
            digit_blob = ""
            text_blob = ""
            used = []
            langs = _tesseract_langs_for(role=role)
            for label, im in _preprocess_variants(image_path, role=role):
                used.append(label)
                if role in ("back", "auto"):
                    digit_blob += "\n" + _ocr_digits(pytesseract, im)
                    isbn = extract_isbn(digit_blob)
                    price = extract_price_dt(digit_blob)
                    if isbn or (role == "back" and price):
                        text_blob = _ocr_text(pytesseract, im, langs)
                        combined = f"{digit_blob}\n{text_blob}"
                        draft = _draft_from_text(combined, isbn_hint=isbn, role=role)
                        if not draft.price and price:
                            draft.price = price
                        draft.raw["ocr_pass"] = label
                        draft.raw["ocr_variants"] = used
                        draft.raw["ocr_langs"] = langs
                        return combined, draft
                if role in ("front", "auto"):
                    # Large-text pass for titles (psm 11 sparse / 6 block)
                    psm = 11 if role == "front" else 6
                    chunk = _ocr_text(pytesseract, im, langs, psm=psm)
                    text_blob += "\n" + chunk
                    # Refine lang model once we see script
                    langs = _tesseract_langs_for(text_blob, role=role)

            if role == "front" and not text_blob.strip():
                text_blob = _ocr_text(
                    pytesseract, list(_preprocess_variants(image_path, role="front"))[-1][1], langs
                )
            if role != "front":
                text_blob += "\n" + _ocr_text(
                    pytesseract, list(_preprocess_variants(image_path, role=role))[-1][1], langs
                )
            combined = f"{digit_blob}\n{text_blob}"
            draft = _draft_from_text(combined, role=role)
            draft.raw["ocr_pass"] = "full_fallback"
            draft.raw["ocr_variants"] = used
            draft.raw["ocr_langs"] = langs
            return combined, draft
        except Exception as exc:
            draft = BookDraft(source="manual", confidence=0.0,
                              raw={"ocr_error": f"tesseract failed: {exc}",
                                   "ocr_available": False, "isbn_not_detected": True,
                                   "cover_role": role})
            return "", draft


# --- Vision-LLM provider (free, offline via Ollama) -------------------------------------------

_VISION_PROMPT = (
    "You are reading the cover of a book. The cover may be in Arabic, French or English "
    "(sometimes mixed). Extract the bibliographic data and reply with a SINGLE JSON object only, "
    "no prose. Use these keys (empty string or empty list if unknown): "
    "title, subtitle, authors (list), translators (list), publisher, series, edition, "
    "languages (ISO codes list, e.g. [\"ar\",\"fr\"]), pub_year (int), pages (int), "
    "isbn13, subject, description, price (string in TND if printed). "
    "Preserve the original script for Arabic text."
)

_VISION_KEYS = {
    "title", "subtitle", "publisher", "series", "edition",
    "subject", "description", "isbn13", "price",
}


def _draft_from_vision_json(raw):
    """Parse the model's JSON reply into a BookDraft (tolerant of stray prose around the JSON)."""
    draft = BookDraft(source="vision", confidence=0.85, raw={"ocr_available": True})
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        data = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        draft.raw = {"vision_reply": raw, "isbn_not_detected": True}
        return draft
    draft.raw = {**data, "ocr_available": True}
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
            value = int(data.get(num))
            if value > 0:
                setattr(draft, num, value)
        except (TypeError, ValueError):
            pass
    if draft.price:
        draft.price = extract_price_dt(draft.price) or draft.price
    if draft.isbn13:
        draft.isbn13 = to_isbn13(draft.isbn13) or re.sub(r"[-\s]", "", draft.isbn13)
        if draft.isbn13:
            draft.raw["isbn_detected"] = True
        else:
            draft.raw["isbn_not_detected"] = True
    else:
        draft.raw["isbn_not_detected"] = True
    return draft


class VisionLlmOcrProvider(OcrProvider):
    """Structured multilingual extraction via a local Ollama vision model (free, offline, no key)."""

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

    def extract(self, image_path, role="auto"):
        try:
            with open(image_path, "rb") as fh:
                image_b64 = base64.b64encode(fh.read()).decode()
            raw = (self._transport or self._ollama)(image_b64)
        except Exception:
            return ManualOcrProvider().extract(image_path, role=role)
        text, draft = raw, _draft_from_vision_json(raw)
        draft.raw = {**(draft.raw or {}), "cover_role": role}
        return text, draft


_PROVIDERS = {
    "manual": ManualOcrProvider,
    "tesseract": TesseractOcrProvider,
    "vision": VisionLlmOcrProvider,
}


def get_ocr_provider():
    return _PROVIDERS.get(settings.OCR_PROVIDER, ManualOcrProvider)()
