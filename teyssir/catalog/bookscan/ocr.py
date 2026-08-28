"""Pluggable OCR providers (extract text/fields from a cover image). Default is Tesseract
(free, offline, ara+fra+eng), degrading to a no-op if it isn't installed; a Vision-LLM provider
(free, offline via Ollama) can be plugged for high-accuracy structured multilingual extraction.
Spec docs/BOOK-OCR-ARCHITECTURE.md.

Phase 6: front vs back cover roles, Arabic/French/English language adaptation, price OCR.

Phase 2C: script probe → one primary ``-l`` string (second pass only on bilingual
evidence); title_band downscaled ~1200px; barcode/price bands upscaled; early exit
when usable title + (ISBN | local barcode | price).
"""
import logging
import re
import shutil
import time
from pathlib import Path

from django.conf import settings

from .draft import BookDraft
from .isbn import extract_isbn, to_isbn13
from .price import extract_price_dt

logger = logging.getLogger("teyssir.ocr")

# LaunchAgents / Windows services often have a minimal PATH without Homebrew or
# "Program Files\\Tesseract-OCR". Resolve an absolute binary so OCR works under those.
_TESSERACT_CANDIDATES = (
    "/opt/homebrew/bin/tesseract",
    "/usr/local/bin/tesseract",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)

# Phase 2C: title OCR max edge (px); barcode/price bands may upscale separately.
_TITLE_MAX_EDGE = 1200
_PROBE_MAX_EDGE = 800
_BARCODE_MIN_EDGE = 900
# Compact CNP stickers need a stronger upscale than wide price bands.
_STICKER_MIN_EDGE = 1400


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

    def extract(self, image_path, role="auto", prepare=None, known_barcode=None):
        """Return (raw_text, BookDraft). ``role`` is front|back|auto."""
        raise NotImplementedError


class ManualOcrProvider(OcrProvider):
    """No-op fallback when no OCR engine is available (user enters everything)."""

    name = "manual"

    def extract(self, image_path, role="auto", prepare=None, known_barcode=None):
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


def _latin_title_shape_ok(s: str, words: list[str]) -> bool:
    """True when Latin text looks like a real title (not ``herbe`` / ``Whee`` / ``ead chien``)."""
    alpha_lens = [len(re.sub(r"[^A-Za-zÀ-ÿ]", "", w)) for w in words]
    longest = max(alpha_lens, default=0)
    has_article = bool(
        re.search(r"\b(The|A|An|Le|La|Les|L'|Un|Une|El|Al)\b", s, re.I)
    )
    has_diacritic = bool(
        re.search(r"[àâäéèêëïîôùûüçœæÀÂÄÉÈÊËÏÎÔÙÛÜÇ]", s)
    )
    title_case = sum(1 for w in words if re.match(r"[A-ZÀ-Ÿ][a-zà-ÿ]{2,}", w))
    # Long word / French diacritics / article phrase / multi-word Title Case
    if longest >= 8:
        return True
    if has_diacritic and longest >= 5:
        return True
    if has_article and longest >= 5:
        return True
    if title_case >= 2 and longest >= 4:
        return True
    if longest >= 6 and title_case >= 1 and len(words) >= 3:
        return True
    # Known school-book tokens (Mathématiques may arrive without diacritics)
    if re.search(
        r"\b(Beauty|Beast|Prince|Mathématiques|Mathematiques|Histoire|"
        r"Premier|Golden|Tales|Cinderella)\b",
        s,
        re.I,
    ):
        return True
    return False


def is_garbage_latin_ocr(text: str, *, mean_conf: float | None = None) -> bool:
    """True when Latin OCR looks like a misread non-Latin (e.g. Arabic) cover.

    Examples from production: ``wis! Boot ay``, ``9 or et O.``, ``ol YI a "Teeny"``,
    ``PEL oe nee``, ``ead chien``, ``herbe``, ``Whee``.
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
    if conf <= 55 and not _latin_title_shape_ok(s, words):
        # Single short token / mush without title shape (herbe, Whee, ead chien)
        if len(words) <= 3 and len(letters) <= 14:
            return True
        if avg_len < 4.5 and len(words) >= 2:
            return True
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
    bonus += 0.55 if re.match(r"^(Le|La|Les|L'|Un|Une|The)\b", s, re.I) else 0.0
    bonus += 0.7 if re.search(
        r"\b(Mathématiques|Mathematiques|Beauty|Beast)\b", s, re.I
    ) else 0.0
    # Subtitle-ish "en … année" / "2ème année…" must not beat the main title
    if re.match(r"^(en|in|pour|for)\b", s, re.I):
        bonus -= 0.45
    if re.match(r"^\d", s) or re.search(r"année de l['']?enseignement", s, re.I):
        bonus -= 0.7
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
    bonus = 0.9 if s.startswith("كتاب") or s.startswith("الأول") else 0.0
    # Subtitle / audience lines ("لتلاميذ…") must not beat the main title
    if re.match(r"^(لتلاميذ|لطلبة|للسنة|مع كتاب)", s):
        bonus -= 1.2
    return ar / 20.0 + bonus + min(len(s), 40) / 120.0


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
    Never tags ``ar`` from tiny Arabic noise on a Latin-majority English/French cover.
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
    # Require meaningful Arabic share — a few hallucinated glyphs must not tag ar
    if ar / total >= 0.15 and ar >= 6:
        out.append("ar")
    elif ar / total >= 0.35 and ar >= 4:
        out.append("ar")
    # Garbage Latin at low confidence must not claim English
    if is_garbage_latin_ocr(text, mean_conf=mean_conf) and "ar" not in out:
        return out  # empty or ar-only — never en from noise
    if latin / total >= 0.15 and not is_garbage_latin_ocr(text, mean_conf=mean_conf):
        if re.search(r"[àâäéèêëïîôùûüçœæÀÂÄÉÈÊËÏÎÔÙÛÜÇ]", text) or re.search(
            r"\b(Le|La|Les|L'|Un|Une|Des|Du|Mathématiques|Mathematiques)\b", text, re.I
        ):
            out.append("fr")
        else:
            out.append("en")
    if out:
        return out
    if ar >= 6:
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
    """Legacy multi-pass list (kept for tests). Prefer :func:`_budgeted_lang_passes`."""
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


def _join_installed(parts: list[str], installed: set[str]) -> str:
    chosen = [p for p in parts if p in installed]
    if not chosen and "eng" in installed:
        chosen = ["eng"]
    return "+".join(chosen) if chosen else ("eng" if "eng" in installed else "")


def _primary_lang_from_script(
    script_hint: str, role: str, installed: set[str]
) -> str:
    """Map probe script tag (ar|fr|en|ar+fr|…) to ONE Tesseract ``-l`` string."""
    hint = (script_hint or "").lower()
    if "ar" in hint and ("fr" in hint or "en" in hint):
        return _join_installed(["ara", "fra", "eng"], installed) or _join_installed(
            ["ara", "eng"], installed
        )
    if "ar" in hint:
        return _join_installed(["ara", "fra"], installed) or _join_installed(
            ["ara", "eng"], installed
        )
    if "fr" in hint:
        return _join_installed(["fra", "eng"], installed)
    if "en" in hint:
        return _join_installed(["eng", "fra"], installed)
    if role == "back":
        return _join_installed(["fra", "eng"], installed)
    # Unknown cover: Tunisian stock often Arabic+French — one mixed pack
    return _join_installed(["ara", "fra", "eng"], installed) or _join_installed(
        ["fra", "eng"], installed
    )


def _budgeted_lang_passes(
    role: str,
    installed: set[str],
    *,
    primary: str,
    bilingual_evidence: bool = False,
) -> list[str]:
    """Phase 2C: at most one primary ``-l``; optional second pass for bilingual covers."""
    passes: list[str] = []
    if primary:
        passes.append(primary)
    if bilingual_evidence:
        # Complementary pack: if primary is ara-heavy, try Latin; else try ara
        if primary.startswith("ara"):
            alt = _join_installed(["fra", "eng"], installed)
        else:
            alt = _join_installed(["ara", "eng"], installed) or _join_installed(
                ["ara"], installed
            )
        if alt and alt not in passes:
            passes.append(alt)
    if not passes:
        fallback = _tesseract_langs_for(role=role, available=installed)
        passes = [fallback] if fallback else ["eng"]
    return passes[:2]


def _downscale_max_edge(im, max_edge: int):
    from PIL import Image

    w, h = im.size
    m = max(w, h)
    if m <= max_edge or max_edge <= 0:
        return im
    scale = max_edge / m
    return im.resize(
        (max(1, int(w * scale)), max(1, int(h * scale))),
        Image.Resampling.LANCZOS,
    )


def _upscale_min_edge(im, min_edge: int):
    from PIL import Image

    w, h = im.size
    m = max(w, h)
    if m >= min_edge or min_edge <= 0:
        return im
    scale = min_edge / m
    return im.resize(
        (max(1, int(w * scale)), max(1, int(h * scale))),
        Image.Resampling.LANCZOS,
    )


def _script_probe(pytesseract, image, installed: set[str], role: str) -> tuple[str, bool, str]:
    """Cheap script probe → (primary_lang, bilingual_evidence, script_tag).

    Downscales to ~800px and runs a single short OCR (or OSD when available).
    Latin-majority / OSD-Latin covers must not be flipped to Arabic by ara hallucination.
    """
    probe_im = _downscale_max_edge(image, _PROBE_MAX_EDGE)
    script_tag = ""
    osd_latin = False
    # OSD: orientation+script detection (meta pack ``osd``)
    if "osd" in installed:
        try:
            osd = pytesseract.image_to_osd(probe_im) or ""
            # Example: "Script: Arabic" / "Script: Latin"
            m = re.search(r"Script:\s*(\w+)", osd, re.I)
            if m:
                name = m.group(1).lower()
                if "arab" in name:
                    script_tag = "ar"
                elif "latin" in name or "fraktur" in name:
                    script_tag = "latin"
                    osd_latin = True
        except Exception:
            pass

    if osd_latin:
        probe_langs = _join_installed(["eng", "fra"], installed) or "eng"
    else:
        probe_langs = _join_installed(["ara", "eng"], installed) or _join_installed(
            ["eng"], installed
        ) or "eng"
    try:
        snippet = _safe_image_to_string(
            pytesseract, probe_im, lang=probe_langs, config="--psm 6"
        ) or ""
    except Exception:
        snippet = ""

    ar_n = sum(1 for c in snippet if is_arabic_char(c))
    lat_n = sum(
        1 for c in snippet
        if c.isalpha() and not is_arabic_char(c)
    )
    detected = detect_script_langs(snippet[:200], mean_conf=40.0)

    # Strong Latin majority → never let ara-pack noise win (English Beauty covers)
    if lat_n >= 10 and lat_n > max(ar_n, 1) * 2:
        script_tag = "fr" if ("fr" in detected or role == "back") else "en"
        bilingual = ar_n >= 10 and ar_n / max(ar_n + lat_n, 1) >= 0.25
        if bilingual:
            script_tag = "ar+fr" if script_tag == "fr" or "fr" in detected else "ar+en"
        primary = _primary_lang_from_script(script_tag, role, installed)
        return primary, bilingual, script_tag

    if not script_tag:
        if "ar" in detected and ("fr" in detected or "en" in detected):
            script_tag = "ar+fr" if "fr" in detected else "ar+en"
        elif "ar" in detected:
            script_tag = "ar"
        elif "fr" in detected:
            script_tag = "fr"
        elif "en" in detected:
            script_tag = "en"
        elif arabic_char_ratio(snippet) >= 0.25 and ar_n >= 6:
            script_tag = "ar"

    bilingual = False
    if script_tag in ("ar+fr", "ar+en"):
        bilingual = True
    elif "ar" in script_tag and any(x in detected for x in ("fr", "en")):
        bilingual = True
    elif script_tag in ("fr", "en", "latin") and ar_n >= 10 and arabic_char_ratio(snippet) >= 0.25:
        bilingual = True
        script_tag = "ar+fr" if "fr" in detected or script_tag == "fr" else "ar+en"
    elif osd_latin:
        # OSD Latin + weak ara noise → stay Latin (EN cover ≠ Arabic weak path)
        bilingual = False
        script_tag = "fr" if role == "back" or "fr" in detected else "en"

    if script_tag == "latin":
        script_tag = "fr" if role == "back" else "en"

    primary = _primary_lang_from_script(script_tag, role, installed)
    return primary, bilingual, script_tag or "unknown"


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


def _fast_path_ready(
    draft: "BookDraft",
    mean_conf: float,
    *,
    bilingual_pending: bool = False,
) -> bool:
    """Early-exit gate: usable title + (ISBN | local barcode | price).

    Never exit early while a bilingual second lang pass is still required.
    """
    if bilingual_pending:
        return False
    if not is_usable_ocr_title(draft.title or "", mean_conf=mean_conf):
        return False
    if draft.isbn13:
        return True
    if (draft.barcode_raw or "").strip():
        return True
    if draft.price:
        return True
    return False


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


def _preprocess_variants(
    image_path,
    role="auto",
    *,
    isbn_found=False,
    prepare=None,
    has_product_barcode=False,
):
    """Yield (label, PIL.Image) — Phase 2C budgeted crops.

    Prefer ``title_band`` / ``price_band`` / ``barcode_band`` from preprocess ROIs.
    Title crops downscale to ~1200px; barcode/price bands upscale only.
    """
    from PIL import Image, ImageEnhance, ImageOps, ImageFilter

    img = Image.open(image_path).convert("RGB")
    w, h = img.size

    def _crop_box(box):
        if box is None:
            return None
        clamped = box.clamp(w, h)
        if clamped.width < 12 or clamped.height < 12:
            return None
        return img.crop(clamped.as_tuple())

    title_roi = price_roi = barcode_roi = white_roi = None
    if prepare is not None:
        title_roi = _crop_box(getattr(prepare, "title_band", None))
        price_roi = _crop_box(getattr(prepare, "price_band", None))
        barcode_roi = _crop_box(getattr(prepare, "barcode_band", None))
        white_roi = _crop_box(getattr(prepare, "white_label", None))
        if white_roi is not None and (
            barcode_roi is None or barcode_roi.height < white_roi.height
        ):
            # Prefer white sticker for barcode OCR when present
            barcode_roi = barcode_roi or white_roi

    mx, my = int(w * 0.12), int(h * 0.12)
    crop = title_roi or (
        img.crop((mx, my, w - mx, h - my)) if w > 40 and h > 40 else img
    )
    lower = price_roi or (
        img.crop((0, int(h * 0.55), w, h)) if role in ("back", "auto") and h > 80 else None
    )
    barcode_band = barcode_roi or (
        img.crop((0, int(h * 0.72), w, h)) if role in ("back", "auto") and h > 100 else None
    )

    def _title_prep(im, *, color=False):
        im = _downscale_max_edge(im, _TITLE_MAX_EDGE)
        if color:
            im = ImageOps.autocontrast(im)
            im = ImageEnhance.Contrast(im).enhance(1.6)
            im = ImageEnhance.Sharpness(im).enhance(1.4)
            return im
        im = ImageOps.grayscale(im)
        im = ImageOps.autocontrast(im)
        im = ImageEnhance.Contrast(im).enhance(2.0)
        im = im.filter(ImageFilter.MedianFilter(size=3))
        return ImageEnhance.Sharpness(im).enhance(1.5).convert("RGB")

    def _title_thr(im):
        """One extra Otsu/adaptive threshold on title_band only (15-OCR-1)."""
        im = _downscale_max_edge(im, _TITLE_MAX_EDGE)
        g = ImageOps.autocontrast(ImageOps.grayscale(im))
        try:
            import numpy as np

            arr = np.asarray(g, dtype=np.uint8)
            try:
                import cv2

                _, thr = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                return Image.fromarray(thr).convert("RGB")
            except Exception:
                hist = np.bincount(arr.ravel(), minlength=256).astype(np.float64)
                total = arr.size
                sum_total = float(np.dot(np.arange(256), hist))
                sum_b = 0.0
                w_b = 0.0
                best = 0
                max_var = -1.0
                for t in range(256):
                    w_b += hist[t]
                    if w_b == 0:
                        continue
                    w_f = total - w_b
                    if w_f == 0:
                        break
                    sum_b += t * hist[t]
                    m_b = sum_b / w_b
                    m_f = (sum_total - sum_b) / w_f
                    var = w_b * w_f * (m_b - m_f) ** 2
                    if var > max_var:
                        max_var = var
                        best = t
                return g.point(lambda x, b=best: 255 if x > b else 0).convert("RGB")
        except Exception:
            return g.point(lambda x: 255 if x > 140 else 0).convert("RGB")

    def _band_up(im, *, threshold=False, min_edge: int | None = None):
        im = _upscale_min_edge(im, min_edge or _BARCODE_MIN_EDGE)
        g = ImageOps.autocontrast(ImageOps.grayscale(im))
        g = ImageEnhance.Contrast(g).enhance(2.2)
        if threshold:
            g = g.point(lambda x: 255 if x > 140 else 0)
        return g.convert("RGB")

    # Title path (always downscaled) + one threshold variant (15-OCR-1)
    yield "title_band", _title_prep(crop)
    yield "title_thr", _title_thr(crop)
    if role == "front":
        yield "title_color", _title_prep(crop, color=True)

    def _yield_sticker_price():
        """Compact CNP/PVP white sticker first — avoids barcode digit bleed on price."""
        if role not in ("back", "auto") or white_roi is None:
            return
        # Full sticker + stronger upscale/threshold (reads ثمن … 4,900 د.ت)
        yield "white_label", _band_up(white_roi, min_edge=_STICKER_MIN_EDGE)
        yield "white_label_thr", _band_up(
            white_roi, threshold=True, min_edge=_STICKER_MIN_EDGE,
        )
        wh, ww = white_roi.height, white_roi.width
        if wh > 40:
            # Bottom half often holds the price line below the bars
            bot = white_roi.crop((0, int(wh * 0.45), ww, wh))
            yield "white_label_price", _band_up(bot, min_edge=_STICKER_MIN_EDGE)
            yield "white_label_price_thr", _band_up(
                bot, threshold=True, min_edge=_STICKER_MIN_EDGE,
            )

    identity_known = isbn_found or has_product_barcode
    if identity_known:
        if role in ("back", "auto"):
            yield from _yield_sticker_price()
            if lower is not None:
                yield "price_band", _band_up(lower)
        elif role == "front":
            yield "title_gray", _title_prep(crop)
        return

    yield "title_gray", _title_prep(crop)

    # Price / barcode bands — upscale only (no rotation fan-out)
    if role in ("back", "auto"):
        # Prefer compact sticker OCR before wide price_band (History 34.900 FP)
        yield from _yield_sticker_price()
        if lower is not None:
            yield "price_band", _band_up(lower)
            yield "price_thr", _band_up(lower, threshold=True)
        if barcode_band is not None:
            yield "barcode_band", _band_up(barcode_band, threshold=True)
            up = ImageOps.autocontrast(ImageOps.grayscale(barcode_band)).convert("RGB")
            yield "barcode_band_x3", _upscale_min_edge(up, max(_BARCODE_MIN_EDGE, 1100))


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
        r"^(isbn|issn|www\.|http|prix|price|سعر|الطبعة|édition|edition|"
        r"titles?\s+in\s+this\s+series|pvp|الثمن|centre\s+national|"
        r"المركز\s+الوطني|république|republique|ministère|ministere)\b",
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
        # Numbered series list items on verso ("11. Beauty and the Beast") — keep
        # only when long enough to be a real title line, else skip short noise
        if re.match(r"^\d{1,2}[.)]\s+\S", s) and len(s) < 12:
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
    ar_count = sum(1 for c in (text or "") if is_arabic_char(c))
    if ar_ratio >= 0.15 and ar_count >= 6:
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
                and not re.match(r"^(en|in|pour|for)\b", s, re.I)
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
        # Only mark Arabic-likely when the blob actually has Arabic script.
        # English covers with Latin garbage must NOT get a false Arabic warning.
        if ar_ratio >= 0.12:
            draft.languages = ["ar"] if ar_ratio < 0.15 else (draft.languages or ["ar"])
            draft.raw["ocr_arabic_likely"] = True
            draft.raw.pop("detected_langs", None)
            draft.raw["detected_langs"] = list(draft.languages)
        else:
            draft.languages = [x for x in (draft.languages or []) if x == "ar"]
            draft.raw.pop("ocr_arabic_likely", None)
            draft.raw["detected_langs"] = list(draft.languages)
            draft.raw.pop("arabic_script_detected", None)
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

    # Prefer languages inferred from the surviving title (not full verso blob)
    if draft.title and is_usable_ocr_title(draft.title, mean_conf=mean_conf):
        title_langs = detect_script_langs(draft.title, mean_conf=mean_conf)
        if title_langs:
            draft.languages = title_langs
            draft.raw["detected_langs"] = title_langs
            if "ar" not in title_langs:
                draft.raw.pop("arabic_script_detected", None)
                draft.raw.pop("ocr_arabic_likely", None)
            elif arabic_char_ratio(draft.title) >= 0.15:
                draft.raw["arabic_script_detected"] = True

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

    def extract(self, image_path, role="auto", prepare=None, known_barcode=None):
        t0 = time.perf_counter()
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
            from .barcode import decode_isbn_with_source, decode_product_barcode

            digit_blob = ""
            text_blob = ""
            used = []
            installed = _installed_tess_langs(pytesseract)
            missing_rec = [c for c in ("ara", "fra", "eng") if c not in installed]
            best = None  # (score, label, combined_text, draft, mean_conf, langs)
            best_latin_title = ""
            best_latin_score = 0.0
            best_arabic_title = ""
            best_arabic_score = 0.0

            # Barcode-first: ISBN when bookland; else retain CNP/GTIN (Phase 2B).
            barcode_isbn = ""
            isbn_source = ""  # barcode | digit_ocr | ""
            product_bc = None
            t_bc = time.perf_counter()
            if role in ("back", "auto"):
                # known_barcode: DecodedBarcode | False (already searched, miss) | None (search)
                if known_barcode is False:
                    product_bc = None
                elif known_barcode is not None:
                    product_bc = known_barcode
                else:
                    product_bc = decode_product_barcode(image_path, prepare=prepare)
                if product_bc and product_bc.kind == "isbn13":
                    barcode_isbn = product_bc.raw
                    isbn_source = "barcode"
                    digit_blob = barcode_isbn
                    used.append("barcode")
                elif product_bc:
                    used.append("barcode_non_isbn")
                elif known_barcode is None:
                    barcode_isbn, isbn_source = decode_isbn_with_source(
                        image_path, prepare=prepare,
                    )
                    if barcode_isbn:
                        digit_blob = barcode_isbn
                        used.append("barcode" if isbn_source == "barcode" else "digit_ocr")
            barcode_ms = int((time.perf_counter() - t_bc) * 1000)

            # Phase 2C: script probe → ONE primary lang (+ optional bilingual second)
            probe_im = Image.open(image_path).convert("RGB")
            if prepare is not None and getattr(prepare, "title_band", None):
                box = prepare.title_band.clamp(*probe_im.size)
                if box.width >= 12 and box.height >= 12:
                    probe_im = probe_im.crop(box.as_tuple())
            primary, bilingual_hint, script_tag = _script_probe(
                pytesseract, probe_im, installed, role,
            )
            lang_passes = _budgeted_lang_passes(
                role, installed, primary=primary, bilingual_evidence=bilingual_hint,
            )
            langs = lang_passes[0] if lang_passes else _tesseract_langs_for(
                role=role, available=installed,
            )
            used.append(f"script_probe:{script_tag}")

            def _stamp_timing(draft):
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                draft.raw["ocr_ms"] = elapsed_ms
                draft.raw["barcode_ms"] = barcode_ms
                draft.raw["ocr_fast_path"] = True
                draft.raw["script_probe"] = script_tag
                logger.info(
                    "tess_fast_path role=%s ms=%s barcode_ms=%s langs=%s script=%s",
                    role, elapsed_ms, barcode_ms, lang_passes, script_tag,
                )
                return draft

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
                return _stamp_timing(_apply_confidence_gate(draft, mean_conf))

            def _apply_isbn(draft):
                if product_bc and product_bc.kind != "isbn13":
                    draft.barcode_raw = product_bc.raw
                    draft.barcode_symbology = product_bc.symbology
                    draft.barcode_kind = product_bc.kind
                    draft.raw["barcode_detected"] = True
                    draft.raw["barcode_non_isbn"] = True
                    draft.raw["barcode_source"] = product_bc.source
                if not barcode_isbn:
                    return draft
                draft.isbn13 = barcode_isbn
                draft.raw["isbn_detected"] = True
                draft.raw.pop("isbn_not_detected", None)
                if isbn_source == "barcode":
                    draft.raw["isbn_from_barcode"] = True
                    draft.raw.pop("isbn_from_digit_ocr", None)
                    draft.confidence = max(draft.confidence or 0, 0.85)
                    if product_bc and product_bc.kind == "isbn13":
                        draft.barcode_raw = product_bc.raw
                        draft.barcode_symbology = product_bc.symbology
                        draft.barcode_kind = "isbn13"
                        draft.raw["barcode_detected"] = True
                else:
                    draft.raw["isbn_from_digit_ocr"] = True
                    draft.raw.pop("isbn_from_barcode", None)
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
                # Second lang pass only when bilingual evidence appears mid-scan
                nonlocal lang_passes, bilingual_hint
                if (
                    best_latin_title and best_arabic_title and not bilingual_hint
                    and len(lang_passes) < 2
                ):
                    bilingual_hint = True
                    lang_passes = _budgeted_lang_passes(
                        role, installed, primary=primary, bilingual_evidence=True,
                    )
                return draft

            has_product = bool(product_bc)
            def _bilingual_pending() -> bool:
                return bool(
                    bilingual_hint
                    and not (best_latin_title and best_arabic_title)
                )

            for label, im in _preprocess_variants(
                image_path,
                role=role,
                isbn_found=bool(barcode_isbn and isbn_source == "barcode"),
                prepare=prepare,
                has_product_barcode=has_product,
            ):
                used.append(label)
                pass_digits = ""
                pass_text = ""
                if role in ("back", "auto"):
                    # Digit OCR only when no real product barcode yet (never invent barcode_*)
                    if not product_bc:
                        pass_digits = _ocr_digits(pytesseract, im)
                        digit_blob += "\n" + pass_digits
                    isbn = barcode_isbn or extract_isbn(digit_blob)
                    if isbn and not barcode_isbn:
                        barcode_isbn = isbn
                        isbn_source = "digit_ocr"
                    # Price only from sticker / price / barcode bands — not title mush.
                    # Prefer text OCR (PVP/ثمن/د.ت) over digit-only — digit OCR glues
                    # barcode fragments onto prices (History 34.900 vs 4.900).
                    price = None
                    if label.startswith(("price", "barcode")) or "white_label" in label:
                        # Sparse sticker lines read better with PSM 11 (sparse text)
                        psm = 11 if "white_label" in label else 6
                        pass_text = _ocr_text(pytesseract, im, langs, psm=psm)
                        price = (
                            extract_price_dt(pass_text)
                            or extract_price_dt(f"{pass_text}\n{digit_blob}")
                        )
                    if isbn or price or barcode_isbn or product_bc:
                        if not pass_text:
                            pass_text = _ocr_text(pytesseract, im, langs)
                        combined = f"{digit_blob}\n{pass_text}"
                        mean_conf = _mean_ocr_confidence(pytesseract, im, langs)
                        draft = _draft_from_text(
                            combined, isbn_hint=isbn or barcode_isbn, role=role,
                            mean_conf=mean_conf,
                        )
                        # Prefer band-local sticker price over combined digit-soup FPs
                        if price:
                            draft.price = price
                            draft.raw["price_detected"] = True
                        draft = _apply_isbn(draft)
                        draft = _annotate(draft, label, mean_conf, langs)
                        score = (
                            (3 if isbn_source == "barcode" else 0)
                            + (2 if product_bc else 0)
                            + (1 if isbn_source == "digit_ocr" else 0)
                            + (2 if isbn else 0)
                            + (1 if price else 0)
                            + mean_conf / 100.0
                            + (draft.confidence or 0)
                        )
                        if best is None or score > best[0]:
                            best = (score, label, combined, draft, mean_conf, langs)
                        if _fast_path_ready(
                            draft, mean_conf, bilingual_pending=_bilingual_pending(),
                        ):
                            return combined, draft
                        if isbn_source == "barcode" and (draft.price or mean_conf >= 40):
                            if not _bilingual_pending():
                                return combined, draft

                if role in ("front", "auto"):
                    psm = 11 if role == "front" else 6
                    if label.startswith("title"):
                        psm = 6
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
                        if draft.raw.get("bilingual_title"):
                            score += 0.6
                        if best is None or score > best[0]:
                            best = (score, label, combined_try, draft, mean_conf, langs_try)
                            langs = langs_try
                            pass_text = chunk
                        if _fast_path_ready(
                            draft, mean_conf, bilingual_pending=_bilingual_pending(),
                        ):
                            return combined_try, draft
                        # Strong Arabic title with primary pack: stop secondary packs
                        if (
                            draft.title
                            and arabic_char_ratio(draft.title) >= 0.3
                            and mean_conf >= 40
                            and "ara" in langs_try
                            and (best_latin_title or not bilingual_hint)
                        ):
                            break
                    if pass_text:
                        text_blob += "\n" + pass_text
                    if best and _fast_path_ready(
                        best[3], best[4], bilingual_pending=_bilingual_pending(),
                    ):
                        break
                    # Enough variants once we have a usable title (keep filters honest)
                    if best and is_usable_ocr_title(
                        best[3].title or "", mean_conf=best[4],
                    ) and len([u for u in used if u.startswith("title")]) >= 1:
                        if not (
                            best_arabic_title and not best_latin_title
                            and bilingual_hint and len(lang_passes) > 1
                            and len(used) < 4
                        ):
                            break

            if best is not None:
                _, label, combined, draft, mean_conf, langs_used = best
                draft = _apply_isbn(draft)
                draft = _finalize_bilingual(draft, mean_conf)
                draft = _annotate(draft, label, mean_conf, langs_used)
                return combined, draft

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



# --- Vision-LLM provider (free, offline via Ollama) — Phase 15.4 dual-image in vision.py ---

# Back-compat aliases for tests / callers that import from ocr
from .vision import (  # noqa: E402
    VISION_PROMPT as _VISION_PROMPT,
    draft_from_vision_json as _draft_from_vision_json,
    image_to_b64 as _vision_image_b64,
)


class VisionLlmOcrProvider(OcrProvider):
    """Structured multilingual extraction via local Ollama vision (Phase 15.4).

    Single-image ``extract`` for OCR_PROVIDER=vision; scan_book uses
    ``vision.analyze_covers`` for front+back in one call.
    """

    name = "vision"

    def __init__(self, transport=None, timeout=None):
        self._transport = transport
        self._timeout = timeout

    def _wrap_transport(self):
        """Adapt legacy single-b64 transport(str) → list[str] API."""
        if self._transport is None:
            return None

        def _adapted(images_b64):
            # Legacy tests pass transport(lambda b64: ...) expecting one string
            if len(images_b64) == 1:
                try:
                    return self._transport(images_b64[0])
                except TypeError:
                    return self._transport(images_b64)
            return self._transport(images_b64)

        return _adapted

    def extract(self, image_path, role="auto", prepare=None, known_barcode=None):
        from .vision import analyze_covers

        try:
            timeout = self._timeout if self._timeout is not None else settings.VISION_TIMEOUT
            text, draft = analyze_covers(
                image_path,
                None,
                timeout=timeout,
                transport=self._wrap_transport(),
            )
        except Exception:
            return ManualOcrProvider().extract(image_path, role=role, prepare=prepare)
        draft.raw = {
            **(draft.raw or {}),
            "cover_role": role,
            "vision_downscaled": True,
        }
        return text, draft

    def extract_dual(self, front_path, back_path=None, role="front"):
        """One Ollama call with front (+ optional back). Used by scan_book layer 2."""
        from .vision import analyze_covers

        try:
            timeout = self._timeout if self._timeout is not None else settings.VISION_TIMEOUT
            text, draft = analyze_covers(
                front_path,
                back_path,
                timeout=timeout,
                transport=self._wrap_transport(),
            )
        except Exception:
            return ManualOcrProvider().extract(front_path, role=role)
        draft.raw = {
            **(draft.raw or {}),
            "cover_role": "front+back" if back_path else role,
            "vision_downscaled": True,
        }
        return text, draft


_PROVIDERS = {
    "manual": ManualOcrProvider,
    "tesseract": TesseractOcrProvider,
    "vision": VisionLlmOcrProvider,
}


def get_ocr_provider():
    return _PROVIDERS.get(settings.OCR_PROVIDER, ManualOcrProvider)()
