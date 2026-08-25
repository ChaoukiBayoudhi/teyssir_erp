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


_ARABIC_RANGES = (
    ("\u0600", "\u06FF"),  # Arabic
    ("\u0750", "\u077F"),  # Arabic Supplement
    ("\u08A0", "\u08FF"),  # Arabic Extended-A
    ("\uFB50", "\uFDFF"),  # Arabic Presentation Forms-A
    ("\uFE70", "\uFEFF"),  # Arabic Presentation Forms-B
)


def is_arabic_char(c: str) -> bool:
    return any(lo <= c <= hi for lo, hi in _ARABIC_RANGES)


def arabic_char_ratio(text: str) -> float:
    """Share of letter-like chars that are Arabic script (0–1)."""
    letters = [c for c in (text or "") if c.isalpha() or is_arabic_char(c)]
    if not letters:
        return 0.0
    return sum(1 for c in letters if is_arabic_char(c)) / len(letters)


def is_garbage_latin_ocr(text: str, *, mean_conf: float | None = None) -> bool:
    """True when Latin OCR looks like a misread non-Latin (e.g. Arabic) cover.

    Examples from production: ``wis! Boot ay``, ``9 or et O.``, ``ol YI a "Teeny"``.
    """
    s = (text or "").strip()
    if not s:
        return False
    if arabic_char_ratio(s) >= 0.12:
        return False
    # Strip common OCR junk for analysis
    letters = "".join(c for c in s if c.isalpha())
    if len(letters) < 3:
        return True
    words = [w for w in re.split(r"\s+", s) if w]
    alpha = sum(1 for c in s if c.isalpha())
    if alpha == 0:
        return True
    # Short Latin with punctuation / digit noise at low confidence → garbage
    conf = mean_conf if mean_conf is not None else 35.0
    short_words = sum(
        1 for w in words if len(re.sub(r"[^A-Za-zÀ-ÿ]", "", w)) <= 2
    )
    punct_hits = bool(re.search(r"[!\"“”«»]|^\d", s))
    avg_len = sum(len(w) for w in words) / max(len(words), 1)
    if conf <= 40 and len(s) <= 48:
        if punct_hits and len(words) >= 2:
            return True
        if short_words >= max(2, (len(words) + 1) // 2):
            return True
        if avg_len < 4.0 and len(words) >= 2:
            return True
        # Tiny "author" / title blobs like "arr", "Add", "FRE pte"
        if len(letters) <= 8 and len(words) <= 3 and not re.search(
            r"[àâäéèêëïîôùûüçœæÀÂÄÉÈÊËÏÎÔÙÛÜÇ]", s
        ):
            # Real short titles are rare; require dictionary-ish vowels+consonants mix of length ≥ 4
            # without digit-leading tokens — still flag very short all-ASCII blobs at ≤35%.
            if conf <= 35 and len(letters) <= 6:
                return True
    return False


def is_garbage_arabic_ocr(text: str, *, mean_conf: float | None = None) -> bool:
    """True when Arabic OCR is unusable (bars, parens, tiny fragments).

    Production examples: ``عد ل |||``, ``الا )(``, short noisy calligraphy fragments.
    """
    s = (text or "").strip().replace("\u200e", "").replace("\u200f", "")
    if not s:
        return False
    if arabic_char_ratio(s) < 0.12:
        return False
    conf = mean_conf if mean_conf is not None else 35.0
    ar_letters = sum(1 for c in s if is_arabic_char(c))
    if ar_letters < 4:
        return True
    # Pipe / slash / paren storms from calligraphy misreads
    if s.count("|") >= 2 or re.search(r"[|\\/]{2,}", s):
        return True
    if re.search(r"[)(]{2,}", s) or (s.count("(") + s.count(")") >= 2 and ar_letters <= 8):
        return True
    punct = sum(
        1 for c in s
        if not (c.isspace() or c.isdigit() or is_arabic_char(c) or c.isalpha())
    )
    if punct >= 3 and ar_letters <= 10:
        return True
    words = [w for w in re.split(r"\s+", s) if w]
    ar_words = [w for w in words if any(is_arabic_char(c) for c in w)]
    # Tiny fragments like "عد ل" at low confidence
    if len(ar_words) <= 2 and sum(len(w) for w in ar_words) <= 5 and conf <= 50:
        return True
    # Mostly 1–2 letter Arabic tokens + noise
    short_ar = sum(1 for w in ar_words if len([c for c in w if is_arabic_char(c)]) <= 2)
    if ar_words and short_ar >= max(2, (len(ar_words) + 1) // 2) and conf <= 45:
        return True
    return False


def is_usable_ocr_title(title: str, *, mean_conf: float | None = None) -> bool:
    """Whether a title is good enough to skip Vision fallback / title-search gating."""
    t = (title or "").strip()
    if len(t) < 4:
        return False
    # Bilingual merged titles are usable if either side is solid
    if "(" in t and ")" in t:
        parts = re.split(r"\s*[/(]\s*", t, maxsplit=1)
        if len(parts) >= 2 and any(
            is_usable_ocr_title(p.rstrip(")"), mean_conf=mean_conf) for p in parts
        ):
            return True
    if is_garbage_arabic_ocr(t, mean_conf=mean_conf):
        return False
    if arabic_char_ratio(t) >= 0.15:
        ar_letters = sum(1 for c in t if is_arabic_char(c))
        # Real Arabic titles need substance — not 4 noisy glyphs
        return ar_letters >= 6 and len(t) >= 6
    if is_garbage_latin_ocr(t, mean_conf=mean_conf):
        return False
    # Decent Latin/French title
    letters = sum(1 for c in t if c.isalpha())
    return letters >= 4


def _latin_title_quality(text: str, *, mean_conf: float | None = None) -> float:
    """Score a Latin/French title fragment (higher = better); 0 if unusable."""
    s = (text or "").strip().replace("\u200e", "").replace("\u200f", "")
    if len(s) < 3 or arabic_char_ratio(s) >= 0.2:
        return 0.0
    if is_garbage_latin_ocr(s, mean_conf=mean_conf):
        return 0.0
    letters = sum(1 for c in s if c.isalpha())
    if letters < 3:
        return 0.0
    # Prefer French diacritics / longer phrases (e.g. "Le premier")
    bonus = 0.3 if re.search(r"[àâäéèêëïîôùûüçœæÀÂÄÉÈÊËÏÎÔÙÛÜÇ]", s) else 0.0
    bonus += 0.2 if re.match(r"^(Le|La|Les|L'|Un|Une|The)\b", s, re.I) else 0.0
    return letters / 20.0 + bonus + min(len(s), 40) / 80.0


def _arabic_title_quality(text: str, *, mean_conf: float | None = None) -> float:
    """Score an Arabic title fragment; 0 if unusable."""
    s = (text or "").strip().replace("\u200e", "").replace("\u200f", "")
    if arabic_char_ratio(s) < 0.2:
        return 0.0
    if is_garbage_arabic_ocr(s, mean_conf=mean_conf):
        return 0.0
    ar = sum(1 for c in s if is_arabic_char(c))
    if ar < 4:
        return 0.0
    return ar / 20.0 + min(len(s), 40) / 80.0


def merge_bilingual_title(
    *parts: str,
    mean_conf: float | None = None,
    style: str = "parens",
) -> str:
    """Merge Latin/French + Arabic cover titles into one string.

    Preferred form: ``Le premier (الثلاثي الاول)``. Falls back to `` / `` separator
    when ``style='slash'``. Single-script input is returned unchanged (cleaned).
    Never drops a usable Latin title when Arabic is also present.
    """
    best_latin = ""
    best_latin_score = 0.0
    best_arabic = ""
    best_arabic_score = 0.0
    for raw in parts:
        s = (raw or "").strip()
        if not s:
            continue
        # Already merged? peel sides
        if re.search(r"[/（(]", s) and (arabic_char_ratio(s) >= 0.15):
            for chunk in re.split(r"\s*[/(（]\s*", s):
                chunk = chunk.rstrip(")）").strip()
                if not chunk:
                    continue
                ls = _latin_title_quality(chunk, mean_conf=mean_conf)
                if ls > best_latin_score:
                    best_latin_score, best_latin = ls, chunk
                as_ = _arabic_title_quality(chunk, mean_conf=mean_conf)
                if as_ > best_arabic_score:
                    best_arabic_score, best_arabic = as_, chunk
            continue
        ls = _latin_title_quality(s, mean_conf=mean_conf)
        if ls > best_latin_score:
            best_latin_score, best_latin = ls, s
        as_ = _arabic_title_quality(s, mean_conf=mean_conf)
        if as_ > best_arabic_score:
            best_arabic_score, best_arabic = as_, s

    if best_latin and best_arabic:
        if style == "slash":
            return f"{best_latin} / {best_arabic}"
        return f"{best_latin} ({best_arabic})"
    return best_latin or best_arabic or ""


def is_plausible_author(name: str, *, mean_conf: float | None = None) -> bool:
    """Reject OCR author noise (``مسيحي`` alone is OK if long enough; ``الا )(`` is not)."""
    s = (name or "").strip().replace("\u200e", "").replace("\u200f", "")
    if len(s) < 3:
        return False
    if is_garbage_latin_ocr(s, mean_conf=mean_conf):
        return False
    if is_garbage_arabic_ocr(s, mean_conf=mean_conf):
        return False
    if arabic_char_ratio(s) >= 0.15:
        ar = sum(1 for c in s if is_arabic_char(c))
        # Single short token at low conf is often cover ornament misread
        words = [w for w in re.split(r"\s+", s) if w]
        if len(words) == 1 and ar <= 5 and (mean_conf or 40) <= 45:
            return False
        return ar >= 3
    letters = sum(1 for c in s if c.isalpha())
    return letters >= 3


def detect_script_langs(text: str, *, mean_conf: float | None = None) -> list[str]:
    """Heuristic language tags from Unicode ranges (ar / fr|en Latin).

    Never tags ``en``/``fr`` from garbage Latin OCR (Arabic covers misread as Latin).
    """
    if not text:
        return []
    ar = sum(1 for c in text if is_arabic_char(c))
    latin = sum(
        1 for c in text
        if ("A" <= c <= "Z") or ("a" <= c <= "z") or ("À" <= c <= "ÿ")
    )
    total = max(ar + latin, 1)
    out: list[str] = []
    if ar / total >= 0.15:
        out.append("ar")
    # Garbage Latin at low confidence must not claim English
    if is_garbage_latin_ocr(text, mean_conf=mean_conf) and "ar" not in out:
        return out  # empty or ar-only — never en from noise
    if latin / total >= 0.15 and not is_garbage_latin_ocr(text, mean_conf=mean_conf):
        if re.search(r"[àâäéèêëïîôùûüçœæÀÂÄÉÈÊËÏÎÔÙÛÜÇ]", text):
            out.append("fr")
        else:
            out.append("en")
    if out:
        return out
    if ar:
        return ["ar"]
    # Unknown script / noise — do not default to fr/en
    return []


def _tesseract_langs_for(text_hint: str = "", role: str = "auto", available: set[str] | None = None) -> str:
    """Pick Tesseract traineddata set from *installed* packs only (never request missing langs)."""
    langs = detect_script_langs(text_hint)
    if "ar" in langs and ("fr" in langs or "en" in langs):
        preferred = ["ara", "fra", "eng"]
    elif "ar" in langs:
        preferred = ["ara", "eng", "fra"]
    elif role == "back":
        preferred = ["fra", "eng", "ara"]
    else:
        # Front covers may be Arabic calligraphy — prefer ara early when installed
        preferred = ["ara", "fra", "eng"]

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


def _lang_pass_candidates(role: str, available: set[str]) -> list[str]:
    """Ordered Tesseract ``-l`` strings to try (dedicated ara + mixed + Latin)."""
    installed = available or set()
    cands: list[str] = []

    def _add(parts: list[str]):
        chosen = [p for p in parts if p in installed]
        if chosen:
            s = "+".join(chosen)
            if s not in cands:
                cands.append(s)

    if role in ("front", "auto"):
        _add(["ara"])
        _add(["ara", "eng"])
        _add(["ara", "fra", "eng"])
        _add(["fra", "eng"])
        _add(["eng"])
        _add(["fra"])
    else:
        _add(["fra", "eng"])
        _add(["eng", "fra"])
        _add(["ara", "eng"])
        _add(["eng"])
    if not cands:
        fallback = _tesseract_langs_for(role=role, available=installed)
        cands = [fallback] if fallback else ["eng"]
    return cands


def _score_ocr_candidate(
    text: str, draft: "BookDraft", mean_conf: float, *, langs: str = "",
) -> float:
    """Rank multi-pass OCR: reward Arabic script + confidence; penalize garbage Latin."""
    ar = arabic_char_ratio(text or "")
    title = (draft.title or "").strip()
    garbage = is_garbage_latin_ocr(title or (text or "")[:80], mean_conf=mean_conf)
    score = mean_conf / 100.0 + (draft.confidence or 0) + ar * 2.5
    if "ara" in (langs or "") and ar >= 0.15:
        score += 0.8
    if garbage:
        score -= 2.0
    elif title:
        score += min(len(title), 40) / 40.0
    if draft.isbn13:
        score += 1.5
    if draft.price and not garbage:
        score += 0.3
    return score


def _deskew_light(im):
    """Tiny deskew via small rotation search on projection variance (cheap)."""
    try:
        import statistics

        g = im.convert("L")
        best = im
        best_score = -1.0
        for angle in (0, -2, 2, -4, 4):
            rot = g.rotate(angle, expand=True, fillcolor=255) if angle else g
            # Prefer angles that concentrate ink in fewer horizontal bands
            w, h = rot.size
            step = max(1, h // 40)
            rows = []
            for y in range(0, h, step):
                band = list(rot.crop((0, y, w, min(h, y + step))).getdata())
                rows.append(sum(1 for p in band if p < 128) / max(len(band), 1))
            if len(rows) < 3:
                continue
            score = statistics.pstdev(rows)
            if score > best_score:
                best_score = score
                best = im.rotate(angle, expand=True, fillcolor="white") if angle else im
        return best
    except Exception:
        return im


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
        if max(im.size) < 1100:
            scale = 1100 / max(im.size)
            im = im.resize((int(im.width * scale), int(im.height * scale)), Image.Resampling.LANCZOS)
        return im

    def _enhance(im, *, color=False):
        if color:
            # Color path preserves Arabic calligraphy better on some covers
            im = ImageOps.autocontrast(im)
            im = ImageEnhance.Contrast(im).enhance(1.6)
            im = ImageEnhance.Sharpness(im).enhance(1.4)
        else:
            im = ImageOps.grayscale(im)
            im = ImageOps.autocontrast(im)
            im = ImageEnhance.Contrast(im).enhance(2.0)
            im = im.filter(ImageFilter.MedianFilter(size=3))
            im = ImageEnhance.Sharpness(im).enhance(1.5)
        return _upscale(im)

    def _threshold(im):
        g = ImageOps.grayscale(im)
        g = ImageOps.autocontrast(g)
        g = ImageEnhance.Contrast(g).enhance(2.2)
        g = g.point(lambda x: 255 if x > 140 else 0)
        return _upscale(g.filter(ImageFilter.MedianFilter(size=3)))

    # Multi-pass baseline: original → grayscale → binary threshold
    yield "original", _upscale(ImageOps.autocontrast(crop.copy()))
    yield "grayscale", _enhance(crop)
    if role == "front":
        yield "deskew", _enhance(_deskew_light(crop))
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
        for angle in (-12, 12, -20, 20, 90, 180, 270):
            rotated = lower.rotate(angle, expand=True, fillcolor="white")
            yield f"lower_rot{angle}", _enhance(rotated)
    if barcode_band is not None:
        yield "barcode_band", _threshold(barcode_band)
        yield "barcode_band_up", _enhance(barcode_band)
        g3 = ImageOps.autocontrast(ImageOps.grayscale(barcode_band))
        g3 = ImageEnhance.Contrast(g3).enhance(2.5)
        g3 = g3.resize(
            (max(1, barcode_band.width * 3), max(1, barcode_band.height * 3)),
            Image.Resampling.LANCZOS,
        )
        yield "barcode_band_x3", g3.convert("RGB")
    # Price stickers: mid-lower third + right/left shelves (Tunisian DT tags)
    if role in ("back", "auto") and h > 120 and w > 80:
        mid_lower = img.crop((0, int(h * 0.35), w, int(h * 0.75)))
        yield "price_mid", _enhance(mid_lower)
        yield "price_mid_thr", _threshold(mid_lower)
        # Sticker often glued on lower-right / lower-left
        yield "price_br", _enhance(
            img.crop((int(w * 0.45), int(h * 0.4), w, int(h * 0.85)))
        )
        yield "price_bl", _enhance(
            img.crop((0, int(h * 0.4), int(w * 0.55), int(h * 0.85)))
        )
    yield "full", _enhance(img)
    if role == "front":
        yield "crop_color", _enhance(crop, color=True)
        # High-contrast calligraphy pass
        callig = ImageOps.autocontrast(ImageOps.equalize(crop.convert("L"))).convert("RGB")
        yield "calligraphy", _enhance(callig, color=True)


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
    """Flag low Tesseract confidence → manual review + keep suggested text.

    High confidence (≥0.85) only when ISBN came from a real barcode decode.
    Digit-OCR ISBN must never inherit barcode trust.
    """
    threshold = float(getattr(settings, "OCR_CONFIDENCE_THRESHOLD", 45) or 45)
    draft.raw = {**(draft.raw or {}), "ocr_mean_confidence": round(mean_conf, 1)}
    if draft.raw.get("isbn_from_barcode") and draft.isbn13:
        # Real pyzbar barcode — definitive
        draft.confidence = max(draft.confidence or 0, 0.85)
        return draft
    if draft.raw.get("isbn_from_digit_ocr") and draft.isbn13:
        # Checksum-valid digit OCR is still unverified — keep low until OL confirms
        draft.confidence = min(draft.confidence or 0.25, 0.35)
        draft.raw["ocr_low_confidence"] = True
        return draft
    if mean_conf > 0 and mean_conf < threshold:
        draft.raw["ocr_low_confidence"] = True
        draft.raw["suggested_title"] = draft.title or ""
        draft.raw["suggested_text"] = draft.title or ""
        if draft.confidence and draft.confidence > 0.25:
            draft.confidence = min(draft.confidence, 0.3)
        draft.source = draft.source or "tesseract"
    # Incomplete / weak title must not advertise high tess confidence
    title = (draft.title or "").strip()
    if title and not is_usable_ocr_title(title, mean_conf=mean_conf):
        draft.confidence = min(draft.confidence or 0.2, 0.3)
        draft.raw["ocr_weak"] = True
    elif title and arabic_char_ratio(title) >= 0.2 and not re.search(
        r"[A-Za-zÀ-ÿ]{3,}", title
    ):
        # Arabic-only title when Latin may exist on cover — don't claim high conf
        if (draft.confidence or 0) > 0.55 and not draft.raw.get("isbn_from_barcode"):
            draft.confidence = min(draft.confidence, 0.5)
            draft.raw["title_may_be_incomplete"] = True
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


def _draft_from_text(text, *, isbn_hint="", role="auto", mean_conf: float | None = None):
    """Best-effort fields from OCR text. Role biases ISBN/price vs title/author."""
    draft = BookDraft(source="tesseract", confidence=0.4, raw={"ocr_available": True, "cover_role": role})
    ar_ratio = arabic_char_ratio(text or "")
    langs = detect_script_langs(text or "", mean_conf=mean_conf)
    if langs:
        draft.languages = langs
        draft.raw["detected_langs"] = langs
    if ar_ratio >= 0.15:
        draft.raw["arabic_script_detected"] = True
        if "ar" not in (draft.languages or []):
            draft.languages = ["ar", *(draft.languages or [])]

    isbn = to_isbn13(isbn_hint) or extract_isbn(text or "")
    if isbn:
        draft.isbn13 = isbn
        draft.raw["isbn_detected"] = True
    else:
        draft.raw["isbn_not_detected"] = True

    # Price: only from back / auto, or when text has a currency cue — avoid front-cover noise
    price = ""
    if role in ("back", "auto"):
        price = extract_price_dt(text or "")
    elif re.search(r"(?:prix|price|سعر|الثمن|dt|tnd|د\.?\s*ت|€)", text or "", re.I):
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
            # Prefer Arabic lines, then longer title-like lines; skip LTR/RTL marks
            cleaned = []
            for s in lines[:8]:
                s2 = s.replace("\u200e", "").replace("\u200f", "").strip()
                if len(s2) >= 3:
                    cleaned.append(s2)
            arabic_lines = [
                s for s in cleaned
                if _arabic_title_quality(s, mean_conf=mean_conf) > 0
            ]
            latin_lines = [
                s for s in cleaned
                if _latin_title_quality(s, mean_conf=mean_conf) > 0
            ]
            # Merge FR + AR when both scripts appear on the cover
            merged = merge_bilingual_title(
                *latin_lines[:3], *arabic_lines[:3], mean_conf=mean_conf,
            )
            if merged:
                draft.title = merged
                draft.raw["title_candidates"] = cleaned[:5]
                if latin_lines and arabic_lines:
                    draft.raw["bilingual_title"] = True
            else:
                pool = arabic_lines or latin_lines or cleaned
                title_cands = sorted(
                    pool[:5],
                    key=lambda s: (
                        -max(
                            _arabic_title_quality(s, mean_conf=mean_conf),
                            _latin_title_quality(s, mean_conf=mean_conf),
                        ),
                        -len(s),
                        pool.index(s),
                    ),
                ) if pool else []
                if title_cands:
                    draft.title = title_cands[0]
                    draft.raw["title_candidates"] = cleaned[:5]
            # Languages: both scripts → ar,fr (or ar,en)
            if latin_lines and arabic_lines:
                has_fr = any(
                    re.search(r"[àâäéèêëïîôùûüçœæÀÂÄÉÈÊËÏÎÔÙÛÜÇ]", ln)
                    or re.match(r"^(Le|La|Les|L'|Un|Une)\b", ln, re.I)
                    for ln in latin_lines
                )
                langs_bi = ["ar", "fr" if has_fr else "en"]
                draft.languages = langs_bi
                draft.raw["detected_langs"] = langs_bi
            if len(cleaned) > 1:
                for ln in cleaned:
                    if ln == draft.title or (draft.title and ln in draft.title):
                        continue
                    if not is_plausible_author(ln, mean_conf=mean_conf):
                        continue
                    if 3 <= len(ln) <= 60:
                        draft.authors = [ln]
                        break

    # Reject garbage Latin titles (Arabic calligraphy misread as Latin)
    if draft.title and is_garbage_latin_ocr(draft.title, mean_conf=mean_conf):
        draft.raw["ocr_garbage_latin"] = True
        draft.raw["suggested_title"] = draft.title
        draft.raw["rejected_title"] = draft.title
        draft.title = ""
        if draft.authors and all(
            is_garbage_latin_ocr(a, mean_conf=mean_conf) for a in draft.authors
        ):
            draft.raw["rejected_authors"] = list(draft.authors)
            draft.authors = []
        # Likely Arabic cover when Latin OCR is garbage at low conf — never leave languages=en
        draft.languages = ["ar"] if ar_ratio < 0.15 else (draft.languages or ["ar"])
        draft.raw["ocr_arabic_likely"] = True
        draft.raw.pop("detected_langs", None)
        draft.raw["detected_langs"] = list(draft.languages)
        # Spurious prices from garbage passes
        if draft.price and role == "front":
            draft.raw["rejected_price"] = draft.price
            draft.price = ""
            draft.raw.pop("price_detected", None)

    # Reject garbage Arabic titles (``عد ل |||``) — force Vision / manual
    if draft.title and is_garbage_arabic_ocr(draft.title, mean_conf=mean_conf):
        draft.raw["ocr_garbage_arabic"] = True
        draft.raw["ocr_title_unusable"] = True
        draft.raw["suggested_title"] = draft.title
        draft.raw["rejected_title"] = draft.title
        draft.title = ""
        if draft.authors:
            draft.raw["rejected_authors"] = list(draft.authors)
            draft.authors = []
        if "ar" not in (draft.languages or []):
            draft.languages = ["ar", *(draft.languages or [])]
        draft.raw["ocr_arabic_likely"] = True

    # Drop implausible authors even when title survived
    if draft.authors:
        kept = [a for a in draft.authors if is_plausible_author(a, mean_conf=mean_conf)]
        if len(kept) != len(draft.authors):
            draft.raw["rejected_authors"] = [
                a for a in draft.authors if a not in kept
            ]
        draft.authors = kept

    if draft.isbn13 or draft.price:
        if draft.isbn13 and not (draft.raw or {}).get("isbn_from_barcode"):
            # Text-extracted / digit OCR ISBN — modest confidence until confirmed
            draft.confidence = 0.35 if draft.isbn13 else 0.45
        else:
            draft.confidence = 0.6 if draft.isbn13 else 0.45
    elif draft.title and is_usable_ocr_title(draft.title, mean_conf=mean_conf):
        # Reflect Tesseract mean when available — never hardcode a fake "35%" floor
        if mean_conf and mean_conf > 0:
            draft.confidence = max(0.15, min(0.55, mean_conf / 100.0))
        else:
            draft.confidence = 0.25
        draft.raw["ocr_text_only"] = True
    else:
        draft.confidence = 0.1
        draft.raw["ocr_weak"] = True
        if not draft.title:
            draft.raw["ocr_title_unusable"] = True
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
            from .barcode import decode_isbn_with_source

            digit_blob = ""
            text_blob = ""
            used = []
            installed = _installed_tess_langs(pytesseract)
            lang_passes = _lang_pass_candidates(role, installed)
            langs = lang_passes[0] if lang_passes else _tesseract_langs_for(
                role=role, available=installed,
            )
            missing_rec = [c for c in ("ara", "fra", "eng") if c not in installed]
            best = None  # (score, label, combined_text, draft, mean_conf, langs)
            # Track best FR / AR title fragments across lang passes for bilingual merge
            best_latin_title = ""
            best_latin_score = 0.0
            best_arabic_title = ""
            best_arabic_score = 0.0

            # Barcode-first (EAN-13 = ISBN): beats Tesseract on small/angled verso photos.
            # Never treat digit-OCR as barcode — checksum-valid OCR can still be wrong.
            barcode_isbn = ""
            isbn_source = ""  # barcode | digit_ocr | ""
            if role in ("back", "auto"):
                barcode_isbn, isbn_source = decode_isbn_with_source(image_path)
                if barcode_isbn:
                    digit_blob = barcode_isbn
                    used.append("barcode" if isbn_source == "barcode" else "digit_ocr")

            def _annotate(draft, label, mean_conf, langs_used):
                draft.raw["ocr_pass"] = label
                draft.raw["ocr_variants"] = list(used)
                draft.raw["ocr_langs"] = langs_used
                draft.raw["ocr_lang_passes"] = list(lang_passes)
                draft.raw["tesseract_cmd"] = cmd
                if missing_rec:
                    draft.raw["tess_missing_langs"] = missing_rec
                    if "ara" in missing_rec:
                        draft.raw["tess_missing_ara"] = True
                return _apply_confidence_gate(draft, mean_conf)

            def _apply_isbn(draft):
                if not barcode_isbn:
                    return draft
                draft.isbn13 = barcode_isbn
                draft.raw["isbn_detected"] = True
                draft.raw.pop("isbn_not_detected", None)
                if isbn_source == "barcode":
                    draft.raw["isbn_from_barcode"] = True
                    draft.raw.pop("isbn_from_digit_ocr", None)
                    draft.confidence = max(draft.confidence or 0, 0.85)
                else:
                    draft.raw["isbn_from_digit_ocr"] = True
                    draft.raw.pop("isbn_from_barcode", None)
                    # Unverified digit OCR — low until OpenLibrary confirms
                    draft.confidence = min(draft.confidence or 0.25, 0.35)
                return draft

            def _note_title_parts(chunk: str, mean_conf: float):
                nonlocal best_latin_title, best_latin_score
                nonlocal best_arabic_title, best_arabic_score
                for ln in _clean_lines(chunk or ""):
                    ls = _latin_title_quality(ln, mean_conf=mean_conf)
                    if ls > best_latin_score:
                        best_latin_score, best_latin_title = ls, ln
                    as_ = _arabic_title_quality(ln, mean_conf=mean_conf)
                    if as_ > best_arabic_score:
                        best_arabic_score, best_arabic_title = as_, ln

            def _finalize_bilingual(draft, mean_conf):
                merged = merge_bilingual_title(
                    best_latin_title, best_arabic_title,
                    draft.title or "", mean_conf=mean_conf,
                )
                if merged and merged != (draft.title or ""):
                    draft.raw["title_before_bilingual_merge"] = draft.title
                    draft.title = merged
                    draft.raw["bilingual_title"] = True
                if best_latin_title and best_arabic_title:
                    has_fr = bool(
                        re.search(
                            r"[àâäéèêëïîôùûüçœæÀÂÄÉÈÊËÏÎÔÙÛÜÇ]",
                            best_latin_title,
                        )
                        or re.match(
                            r"^(Le|La|Les|L'|Un|Une)\b", best_latin_title, re.I
                        )
                    )
                    langs_bi = ["ar", "fr" if has_fr else "en"]
                    draft.languages = langs_bi
                    draft.raw["detected_langs"] = langs_bi
                return draft

            for label, im in _preprocess_variants(
                image_path, role=role, isbn_found=bool(barcode_isbn and isbn_source == "barcode"),
            ):
                used.append(label)
                pass_digits = ""
                pass_text = ""
                if role in ("back", "auto"):
                    pass_digits = _ocr_digits(pytesseract, im)
                    digit_blob += "\n" + pass_digits
                    # Prefer real barcode; only accept checksum-valid digit blobs as ISBN
                    isbn = barcode_isbn or extract_isbn(digit_blob)
                    if isbn and not barcode_isbn:
                        # Tess digit pass found a checksum-valid candidate
                        barcode_isbn = isbn
                        isbn_source = "digit_ocr"
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
                        mean_conf = _mean_ocr_confidence(pytesseract, im, langs)
                        draft = _draft_from_text(
                            combined, isbn_hint=isbn or barcode_isbn, role=role,
                            mean_conf=mean_conf,
                        )
                        if not draft.price and price:
                            draft.price = price
                            draft.raw["price_detected"] = True
                        draft = _apply_isbn(draft)
                        draft = _annotate(draft, label, mean_conf, langs)
                        # Strong ISBN hit: return early only for real barcode
                        if isbn_source == "barcode" and (
                            draft.price or label in ("lower", "grayscale", "original", "price_mid")
                        ):
                            return combined, draft
                        if isbn_source == "barcode" and (mean_conf >= 40 or draft.confidence >= 0.55):
                            return combined, draft
                        # Digit-OCR ISBN: never early-return at high confidence
                        score = (
                            (3 if isbn_source == "barcode" else 0)
                            + (1 if isbn_source == "digit_ocr" else 0)
                            + (2 if isbn else 0)
                            + (1 if price else 0)
                            + mean_conf / 100.0
                            + (draft.confidence or 0)
                        )
                        if best is None or score > best[0]:
                            best = (score, label, combined, draft, mean_conf, langs)
                        if isbn_source == "barcode" and len(used) >= 4:
                            break
                        if isbn_source == "digit_ocr" and draft.price and len(used) >= 6:
                            break
                if role in ("front", "auto"):
                    # Large-text pass for titles (psm 11 sparse / 6 block)
                    psm = 11 if role == "front" else 6
                    # Try dedicated ara / mixed / Latin packs; keep the best by script+conf
                    for langs_try in lang_passes:
                        chunk = _ocr_text(pytesseract, im, langs_try, psm=psm)
                        mean_conf = _mean_ocr_confidence(
                            pytesseract, im, langs_try, psm=psm,
                        )
                        _note_title_parts(chunk, mean_conf)
                        combined_try = f"{digit_blob}\n{text_blob}\n{chunk}"
                        draft = _draft_from_text(
                            combined_try, isbn_hint=barcode_isbn, role=role,
                            mean_conf=mean_conf,
                        )
                        draft = _apply_isbn(draft)
                        draft = _finalize_bilingual(draft, mean_conf)
                        draft = _annotate(draft, f"{label}/{langs_try}", mean_conf, langs_try)
                        score = _score_ocr_candidate(
                            combined_try, draft, mean_conf, langs=langs_try,
                        )
                        # Reward bilingual merges
                        if draft.raw.get("bilingual_title"):
                            score += 0.6
                        if best is None or score > best[0]:
                            best = (score, label, combined_try, draft, mean_conf, langs_try)
                            langs = langs_try
                            pass_text = chunk
                        # Strong Arabic title: stop trying weaker Latin packs only if
                        # we already captured a Latin fragment (or no fra pack available)
                        if (
                            draft.title
                            and arabic_char_ratio(draft.title) >= 0.3
                            and mean_conf >= 40
                            and "ara" in langs_try
                            and (best_latin_title or "fra" not in installed)
                        ):
                            break
                    if pass_text:
                        text_blob += "\n" + pass_text
                    # Refine preferred order once we see Arabic script
                    if arabic_char_ratio(text_blob) >= 0.15:
                        lang_passes = _lang_pass_candidates("front", installed)
                    if isbn_source == "barcode" and best and best[3].title and len(used) >= 3:
                        break
                    # Enough image variants once we have a usable bilingual or solid title
                    if best and is_usable_ocr_title(
                        best[3].title or "", mean_conf=best[4],
                    ) and len(used) >= 3:
                        # Keep going if Arabic-only and Latin might still appear
                        if not (
                            best_arabic_title and not best_latin_title
                            and "fra" in installed and len(used) < 5
                        ):
                            break

            if best is not None:
                _, label, combined, draft, mean_conf, langs_used = best
                draft = _apply_isbn(draft)
                draft = _finalize_bilingual(draft, mean_conf)
                draft = _annotate(draft, label, mean_conf, langs_used)
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
            draft = _draft_from_text(
                combined, isbn_hint=barcode_isbn, role=role, mean_conf=0.0,
            )
            draft = _apply_isbn(draft)
            draft = _finalize_bilingual(draft, 0.0)
            draft = _annotate(draft, "full_fallback", 0.0, langs)
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
        valid = to_isbn13(draft.isbn13)
        if valid:
            draft.isbn13 = valid
            draft.raw["isbn_detected"] = True
        else:
            # Reject checksum-invalid vision guesses
            draft.raw["rejected_isbn"] = draft.isbn13
            draft.isbn13 = ""
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
