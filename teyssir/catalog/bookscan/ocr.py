"""Pluggable OCR providers (extract text/fields from a cover image). Default is Tesseract
(free, offline, ara+fra+eng), degrading to a no-op if it isn't installed; a Vision-LLM provider
(free, offline via Ollama) can be plugged for high-accuracy structured multilingual extraction.
Spec docs/BOOK-OCR-ARCHITECTURE.md.

Phase 6: front vs back cover roles, Arabic/French/English language adaptation, price OCR.
"""
import base64
import json
import re
import shutil
import urllib.request
from pathlib import Path

from django.conf import settings

from .draft import BookDraft
from .isbn import extract_isbn, to_isbn13
from .price import extract_price_dt

# LaunchAgents / Windows services often have a minimal PATH without Homebrew or
# "Program Files\\Tesseract-OCR". Resolve an absolute binary so OCR works under those.
_TESSERACT_CANDIDATES = (
    "/opt/homebrew/bin/tesseract",
    "/usr/local/bin/tesseract",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


def configure_tesseract(pytesseract) -> str | None:
    """Point pytesseract at a real binary; return its path or None if not found.

    Prefer settings ``TESSERACT_CMD`` / env ``TEYSSIR_TESSERACT_CMD`` so LaunchAgent
    and Windows services do not depend on a rich PATH.
    """
    configured = (getattr(settings, "TESSERACT_CMD", None) or "").strip()
    if configured and configured != "tesseract" and Path(configured).is_file():
        pytesseract.pytesseract.tesseract_cmd = configured
        return configured
    current = getattr(pytesseract.pytesseract, "tesseract_cmd", None) or "tesseract"
    if current != "tesseract" and Path(current).is_file():
        return current
    found = shutil.which("tesseract")
    if found:
        pytesseract.pytesseract.tesseract_cmd = found
        return found
    if configured and configured != "tesseract":
        # Settings pointed at a missing path — still try platform candidates.
        pass
    for cand in _TESSERACT_CANDIDATES:
        if Path(cand).is_file():
            pytesseract.pytesseract.tesseract_cmd = cand
            return cand
    return None


def _installed_tess_langs(pytesseract) -> set[str]:
    try:
        return set(pytesseract.get_languages(config="") or [])
    except Exception:
        return set()


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


def _tesseract_langs_for(text_hint: str = "", role: str = "auto", available: set[str] | None = None) -> str:
    """Pick Tesseract traineddata set from *installed* packs only (never request missing langs)."""
    langs = detect_script_langs(text_hint)
    if "ar" in langs and ("fr" in langs or "en" in langs):
        preferred = ["ara", "fra", "eng"]
    elif "ar" in langs:
        preferred = ["ara", "fra", "eng"]
    elif role == "back":
        preferred = ["fra", "eng"]
    else:
        preferred = ["fra", "eng", "ara"]

    installed = available if available is not None else set()
    if not installed:
        # Before pytesseract is configured we still return a preference string;
        # extract() re-calls with the real installed set.
        return "+".join(preferred)

    chosen = [code for code in preferred if code in installed]
    if not chosen and "eng" in installed:
        chosen = ["eng"]
    if not chosen:
        # Whatever is installed (skip meta packs)
        chosen = sorted(c for c in installed if c not in ("osd", "snum"))[:3]
    return "+".join(chosen) if chosen else "eng"


def _preprocess_variants(image_path, role="auto", *, isbn_found=False):
    """Yield (label, PIL.Image) variants: original / grayscale / threshold (+ role crops).

    When ``isbn_found`` is True (barcode already decoded), skip expensive extra passes —
    keep a short path for title / price text only.
    """
    from PIL import Image, ImageEnhance, ImageOps, ImageFilter

    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    mx, my = int(w * 0.12), int(h * 0.12)
    crop = img.crop((mx, my, w - mx, h - my)) if w > 40 and h > 40 else img
    # Back covers: barcode/price often in the lower third
    lower = img.crop((0, int(h * 0.55), w, h)) if role in ("back", "auto") and h > 80 else None
    barcode_band = img.crop((0, int(h * 0.72), w, h)) if role in ("back", "auto") and h > 100 else None

    def _upscale(im):
        if max(im.size) < 900:
            scale = 900 / max(im.size)
            im = im.resize((int(im.width * scale), int(im.height * scale)), Image.Resampling.LANCZOS)
        return im

    def _enhance(im, *, color=False):
        if color:
            # Color path preserves Arabic calligraphy better on some covers
            im = ImageOps.autocontrast(im)
            im = ImageEnhance.Contrast(im).enhance(1.4)
        else:
            im = ImageOps.grayscale(im)
            im = ImageOps.autocontrast(im)
            im = ImageEnhance.Contrast(im).enhance(1.8)
        return _upscale(im)

    def _threshold(im):
        g = ImageOps.grayscale(im)
        g = ImageOps.autocontrast(g)
        g = ImageEnhance.Contrast(g).enhance(2.0)
        g = g.point(lambda x: 255 if x > 140 else 0)
        return _upscale(g.filter(ImageFilter.MedianFilter(size=3)))

    # Multi-pass baseline: original → grayscale → binary threshold
    yield "original", _upscale(ImageOps.autocontrast(crop.copy()))
    yield "grayscale", _enhance(crop)
    if isbn_found:
        # ISBN already known — one price/title pass is enough
        if role == "back" and lower is not None:
            yield "lower", _enhance(lower)
        elif role == "front":
            yield "crop_color", _enhance(crop, color=True)
        return

    yield "threshold", _threshold(crop)

    if role in ("back", "auto") and lower is not None:
        yield "lower", _enhance(lower)
        # Slight rotations of the barcode band (angled phone photos)
        for angle in (-12, 12, -20, 20):
            rotated = lower.rotate(angle, expand=True, fillcolor="white")
            yield f"lower_rot{angle}", _enhance(rotated)
    if barcode_band is not None:
        yield "barcode_band", _threshold(barcode_band)
        yield "barcode_band_up", _enhance(barcode_band)
    yield "full", _enhance(img)
    if role == "front":
        yield "crop_color", _enhance(crop, color=True)


def _ocr_digits(pytesseract, image) -> str:
    cfg = "--psm 6 -c tessedit_char_whitelist=0123456789Xx-.,"
    return _safe_image_to_string(pytesseract, image, config=cfg)


def _ocr_text(pytesseract, image, langs: str, *, psm: int = 6) -> str:
    return _safe_image_to_string(pytesseract, image, lang=langs, config=f"--psm {psm}")


def _safe_image_to_string(pytesseract, image, **kwargs) -> str:
    """Call pytesseract; never crash on non-UTF8 stderr (Leptonica binary noise)."""
    try:
        return pytesseract.image_to_string(image, **kwargs) or ""
    except UnicodeDecodeError:
        # Retry once with eng only — often a missing-lang / temp-path failure
        kwargs = dict(kwargs)
        kwargs["lang"] = "eng"
        try:
            return pytesseract.image_to_string(image, **kwargs) or ""
        except Exception:
            return ""
    except Exception:
        # TesseractError etc. — let caller decide; digits/text helpers prefer empty over crash
        return ""


def _mean_ocr_confidence(pytesseract, image, langs: str, *, psm: int = 6) -> float:
    """Average word confidence (0–100) from image_to_data; 0 if unavailable."""
    try:
        data = pytesseract.image_to_data(
            image, lang=langs, config=f"--psm {psm}", output_type=pytesseract.Output.DICT,
        )
        confs = []
        for c, txt in zip(data.get("conf") or [], data.get("text") or []):
            try:
                ci = float(c)
            except (TypeError, ValueError):
                continue
            if ci >= 0 and (txt or "").strip():
                confs.append(ci)
        return sum(confs) / len(confs) if confs else 0.0
    except Exception:
        return 0.0


def _apply_confidence_gate(draft: BookDraft, mean_conf: float) -> BookDraft:
    """Flag low Tesseract confidence → manual review + keep suggested text."""
    threshold = float(getattr(settings, "OCR_CONFIDENCE_THRESHOLD", 45) or 45)
    draft.raw = {**(draft.raw or {}), "ocr_mean_confidence": round(mean_conf, 1)}
    if mean_conf > 0 and mean_conf < threshold:
        draft.raw["ocr_low_confidence"] = True
        draft.raw["suggested_title"] = draft.title or ""
        draft.raw["suggested_text"] = draft.title or ""
        if draft.confidence and draft.confidence > 0.25:
            draft.confidence = min(draft.confidence, 0.3)
        draft.source = draft.source or "tesseract"
    return draft


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

        cmd = configure_tesseract(pytesseract)
        if not cmd:
            draft = BookDraft(
                source="manual", confidence=0.0,
                raw={
                    "ocr_error": (
                        "tesseract binary not found (PATH). "
                        "Install Tesseract and ensure it is on PATH "
                        "(macOS: brew install tesseract tesseract-lang; "
                        "Windows: UB Mannheim installer with fra+eng+ara)."
                    ),
                    "ocr_available": False,
                    "isbn_not_detected": True,
                    "cover_role": role,
                },
            )
            return "", draft

        try:
            from .barcode import decode_isbn_barcode

            digit_blob = ""
            text_blob = ""
            used = []
            installed = _installed_tess_langs(pytesseract)
            langs = _tesseract_langs_for(role=role, available=installed)
            best = None  # (score, label, combined_text, draft, mean_conf)

            # Barcode-first (EAN-13 = ISBN): beats Tesseract on small/angled verso photos.
            barcode_isbn = ""
            if role in ("back", "auto"):
                barcode_isbn = decode_isbn_barcode(image_path) or ""
                if barcode_isbn:
                    digit_blob = barcode_isbn
                    used.append("barcode")

            for label, im in _preprocess_variants(
                image_path, role=role, isbn_found=bool(barcode_isbn),
            ):
                used.append(label)
                pass_digits = ""
                pass_text = ""
                if role in ("back", "auto"):
                    pass_digits = _ocr_digits(pytesseract, im)
                    digit_blob += "\n" + pass_digits
                    isbn = barcode_isbn or extract_isbn(digit_blob)
                    price = extract_price_dt(digit_blob)
                    # Also hunt price in full text of this pass
                    if not price:
                        pass_text = _ocr_text(pytesseract, im, langs)
                        price = extract_price_dt(pass_text) or extract_price_dt(
                            f"{digit_blob}\n{pass_text}"
                        )
                    if isbn or (role == "back" and price) or barcode_isbn:
                        if not pass_text:
                            pass_text = _ocr_text(pytesseract, im, langs)
                        combined = f"{digit_blob}\n{pass_text}"
                        draft = _draft_from_text(
                            combined, isbn_hint=isbn or barcode_isbn, role=role,
                        )
                        if not draft.price and price:
                            draft.price = price
                            draft.raw["price_detected"] = True
                        if barcode_isbn:
                            draft.isbn13 = barcode_isbn
                            draft.raw["isbn_detected"] = True
                            draft.raw["isbn_from_barcode"] = True
                            draft.confidence = max(draft.confidence or 0, 0.85)
                        mean_conf = _mean_ocr_confidence(pytesseract, im, langs)
                        draft = _apply_confidence_gate(draft, mean_conf)
                        draft.raw["ocr_pass"] = label
                        draft.raw["ocr_variants"] = used
                        draft.raw["ocr_langs"] = langs
                        draft.raw["tesseract_cmd"] = cmd
                        # Strong ISBN hit: return early (barcode decode is definitive)
                        if barcode_isbn and (draft.price or label in ("lower", "grayscale", "original")):
                            return combined, draft
                        if isbn and (mean_conf >= 40 or draft.confidence >= 0.55):
                            return combined, draft
                        score = (
                            (3 if barcode_isbn else 0)
                            + (2 if isbn else 0)
                            + (1 if price else 0)
                            + mean_conf / 100.0
                            + (draft.confidence or 0)
                        )
                        if best is None or score > best[0]:
                            best = (score, label, combined, draft, mean_conf)
                        # Barcode ISBN + any pass: stop after a couple of price attempts
                        if barcode_isbn and len(used) >= 4:
                            break
                if role in ("front", "auto"):
                    # Large-text pass for titles (psm 11 sparse / 6 block)
                    psm = 11 if role == "front" else 6
                    chunk = _ocr_text(pytesseract, im, langs, psm=psm)
                    pass_text = chunk
                    text_blob += "\n" + chunk
                    mean_conf = _mean_ocr_confidence(pytesseract, im, langs, psm=psm)
                    combined = f"{digit_blob}\n{text_blob}"
                    draft = _draft_from_text(
                        combined, isbn_hint=barcode_isbn, role=role,
                    )
                    if barcode_isbn:
                        draft.isbn13 = barcode_isbn
                        draft.raw["isbn_detected"] = True
                        draft.raw["isbn_from_barcode"] = True
                    draft = _apply_confidence_gate(draft, mean_conf)
                    # Prefer longer title + higher confidence
                    title_len = len((draft.title or "").strip())
                    score = mean_conf / 100.0 + (draft.confidence or 0) + min(title_len, 40) / 40.0
                    if draft.isbn13:
                        score += 1.5
                    if best is None or score > best[0]:
                        best = (score, label, combined, draft, mean_conf)
                    # Refine lang model once we see script
                    langs = _tesseract_langs_for(text_blob, role=role, available=installed)
                    if barcode_isbn and draft.title and len(used) >= 3:
                        break

            if best is not None:
                _, label, combined, draft, mean_conf = best
                draft.raw["ocr_pass"] = label
                draft.raw["ocr_variants"] = used
                draft.raw["ocr_langs"] = langs
                draft.raw["tesseract_cmd"] = cmd
                if barcode_isbn:
                    draft.isbn13 = barcode_isbn
                    draft.raw["isbn_detected"] = True
                    draft.raw["isbn_from_barcode"] = True
                    draft.raw.pop("isbn_not_detected", None)
                    draft.confidence = max(draft.confidence or 0, 0.85)
                draft = _apply_confidence_gate(draft, mean_conf)
                return combined, draft

            if role == "front" and not text_blob.strip():
                text_blob = _ocr_text(
                    pytesseract,
                    list(_preprocess_variants(image_path, role="front"))[-1][1],
                    langs,
                )
            if role != "front":
                text_blob += "\n" + _ocr_text(
                    pytesseract,
                    list(_preprocess_variants(image_path, role=role))[-1][1],
                    langs,
                )
            combined = f"{digit_blob}\n{text_blob}"
            draft = _draft_from_text(combined, isbn_hint=barcode_isbn, role=role)
            if barcode_isbn:
                draft.isbn13 = barcode_isbn
                draft.raw["isbn_detected"] = True
                draft.raw["isbn_from_barcode"] = True
                draft.raw.pop("isbn_not_detected", None)
                draft.confidence = max(draft.confidence or 0, 0.85)
            draft.raw["ocr_pass"] = "full_fallback"
            draft.raw["ocr_variants"] = used
            draft.raw["ocr_langs"] = langs
            draft.raw["tesseract_cmd"] = cmd
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
