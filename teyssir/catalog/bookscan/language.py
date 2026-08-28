"""Normalize multilingual cover signals into ``language_detected``.

Phase 15.2 / 15-OCR-1: scalar tag for BookCreate autofill while keeping
``languages[]`` (ISO list) compatible.

Formats:
  * single: ``ar`` | ``fr`` | ``en``
  * mixed: ``mixed:ar+fr`` (codes sorted, ``+``-joined)
  * empty string when unknown
"""
from __future__ import annotations

import re
from typing import Iterable, Sequence

_CANON = {
    "ar": "ar",
    "ara": "ar",
    "arabic": "ar",
    "fr": "fr",
    "fra": "fr",
    "fre": "fr",
    "french": "fr",
    "en": "en",
    "eng": "en",
    "english": "en",
}

_ORDER = ("ar", "fr", "en")


def normalize_lang_code(code) -> str | None:
    """Map OCR / probe / ISO tags to ``ar``|``fr``|``en``, or None."""
    if code is None:
        return None
    s = str(code).strip().lower()
    if not s:
        return None
    if s.startswith("mixed:"):
        return None
    # tess packs / probe tags like ar+fr, ara+eng
    if "+" in s or "," in s:
        return None
    s = re.sub(r"[^a-z]", "", s)
    return _CANON.get(s)


def parse_script_probe(tag: str | None) -> list[str]:
    """Expand script-probe tags (``ar``, ``ar+fr``, ``latin``, …) to ISO codes."""
    if not tag:
        return []
    t = str(tag).strip().lower()
    if t in ("latin", "unknown", ""):
        return []
    if t.startswith("mixed:"):
        t = t[6:]
    parts = re.split(r"[+,/|\s]+", t)
    out: list[str] = []
    for p in parts:
        n = normalize_lang_code(p)
        if n and n not in out:
            out.append(n)
    return out


def format_language_detected(codes: Sequence[str]) -> str:
    """Build ``ar`` / ``fr`` / ``en`` / ``mixed:ar+fr`` from unique ISO codes."""
    seen: list[str] = []
    for c in codes:
        n = normalize_lang_code(c)
        if n and n not in seen:
            seen.append(n)
    ordered = [c for c in _ORDER if c in seen]
    for c in seen:
        if c not in ordered:
            ordered.append(c)
    if not ordered:
        return ""
    if len(ordered) == 1:
        return ordered[0]
    return "mixed:" + "+".join(ordered)


def languages_list_from_detected(detected: str) -> list[str]:
    """``languages[]`` compatible with a ``language_detected`` scalar."""
    if not detected:
        return []
    if detected.startswith("mixed:"):
        return [c for c in detected[6:].split("+") if normalize_lang_code(c)]
    n = normalize_lang_code(detected)
    return [n] if n else []


def collect_lang_codes(
    *,
    languages: Iterable[str] | None = None,
    script_probes: Iterable[str] | None = None,
    ocr_langs: Iterable[str] | None = None,
    texts: Iterable[str] | None = None,
) -> list[str]:
    """Union of language signals from merge inputs (front+back)."""
    codes: list[str] = []

    def _add(seq):
        for item in seq or []:
            n = normalize_lang_code(item)
            if n and n not in codes:
                codes.append(n)
            else:
                for p in parse_script_probe(str(item)):
                    if p not in codes:
                        codes.append(p)

    _add(languages)
    for probe in script_probes or []:
        for p in parse_script_probe(probe):
            if p not in codes:
                codes.append(p)
    # Tess ``-l`` strings: ara+fra+eng → ar, fr, en
    for pack in ocr_langs or []:
        for part in re.split(r"[+,]", str(pack)):
            n = normalize_lang_code(part)
            if n and n not in codes:
                codes.append(n)
    if texts:
        try:
            from .ocr import detect_script_langs
        except Exception:
            detect_script_langs = None  # type: ignore
        if detect_script_langs:
            for text in texts:
                if not text:
                    continue
                for lang in detect_script_langs(str(text)[:400]):
                    n = normalize_lang_code(lang)
                    if n and n not in codes:
                        codes.append(n)
    return codes


def detect_language_detected(
    *,
    languages: Iterable[str] | None = None,
    script_probes: Iterable[str] | None = None,
    ocr_langs: Iterable[str] | None = None,
    texts: Iterable[str] | None = None,
) -> str:
    """Detect/normalize ``language_detected`` from probe + OCR lang signals."""
    return format_language_detected(
        collect_lang_codes(
            languages=languages,
            script_probes=script_probes,
            ocr_langs=ocr_langs,
            texts=texts,
        )
    )


def apply_language_detected(draft, *, front=None, back=None):
    """Set ``draft.language_detected`` + ``raw`` mirror; keep ``languages[]`` filled.

    Prefer the draft's already-merged ``languages`` list (merge may have dropped
    false Arabic from verso noise on a Latin front). Optional front/back supply
    extra script_probe / title signals only when draft languages are empty.
    """
    if draft is None:
        return draft

    langs = list(getattr(draft, "languages", None) or [])
    probes: list[str] = []
    ocr_langs: list[str] = []
    texts: list[str] = []

    raw = dict(getattr(draft, "raw", None) or {})
    if raw.get("script_probe"):
        probes.append(str(raw["script_probe"]))
    if raw.get("ocr_langs"):
        ocr_langs.extend(
            raw["ocr_langs"] if isinstance(raw["ocr_langs"], list) else [raw["ocr_langs"]]
        )
    if raw.get("detected_langs"):
        langs.extend(
            raw["detected_langs"] if isinstance(raw["detected_langs"], list)
            else [raw["detected_langs"]]
        )

    title = (getattr(draft, "title", None) or "").strip()
    if title:
        texts.append(title)

    # Nested back ocr_langs from merge
    back_raw = (raw.get("back") or {}) if isinstance(raw.get("back"), dict) else {}
    if back_raw.get("ocr_langs"):
        ol = back_raw["ocr_langs"]
        ocr_langs.extend(ol if isinstance(ol, list) else [ol])

    # Only pull side languages when draft has none (avoid re-adding filtered ar)
    if not langs:
        for side in (front, back):
            if side is None:
                continue
            langs.extend(getattr(side, "languages", None) or [])
            sraw = getattr(side, "raw", None) or {}
            if sraw.get("script_probe"):
                probes.append(str(sraw["script_probe"]))
            ol = sraw.get("ocr_langs")
            if ol:
                ocr_langs.extend(ol if isinstance(ol, list) else [ol])
            st = (getattr(side, "title", None) or "").strip()
            if st:
                texts.append(st)
    else:
        # Still take probe tags from sides for mixed confirmation
        for side in (front, back):
            if side is None:
                continue
            sraw = getattr(side, "raw", None) or {}
            if sraw.get("script_probe"):
                probes.append(str(sraw["script_probe"]))

    detected = detect_language_detected(
        languages=langs,
        script_probes=probes,
        ocr_langs=ocr_langs,
        texts=texts if not langs else [],
    )

    existing = (getattr(draft, "language_detected", None) or "").strip()
    if existing and not detected:
        if existing.startswith("mixed:") or normalize_lang_code(existing):
            detected = existing
        else:
            detected = format_language_detected(
                parse_script_probe(existing) or languages_list_from_detected(existing)
            )
    elif existing and detected:
        detected = format_language_detected(
            languages_list_from_detected(existing) + languages_list_from_detected(detected)
        )

    # If draft languages are authoritative and non-empty, prefer them over probe noise
    if langs:
        from_langs = format_language_detected(langs)
        if from_langs:
            detected = from_langs

    draft.language_detected = detected
    draft.raw = {**(getattr(draft, "raw", None) or {}), "language_detected": detected}

    if detected:
        from_det = languages_list_from_detected(detected)
        cur = []
        for x in (draft.languages or []):
            n = normalize_lang_code(x)
            if n and n not in cur:
                cur.append(n)
        for c in from_det:
            if c not in cur:
                cur.append(c)
        if cur:
            draft.languages = cur
    return draft


def template_description(title: str, *, language_detected: str = "") -> str:
    """Short 1-sentence shelf blurb when Tess-only / no ISBN and title is usable."""
    t = (title or "").strip()
    if not t:
        return ""
    if len(t) > 120:
        t = t[:117].rstrip() + "…"
    codes = languages_list_from_detected(language_detected or "")
    if codes == ["ar"]:
        return f"كتاب: {t}."
    if codes == ["en"]:
        return f"Book: {t}."
    if codes == ["fr"]:
        return f"Livre : {t}."
    if "ar" in codes and ("fr" in codes or "en" in codes):
        return f"Livre / كتاب: {t}."
    if "fr" in codes:
        return f"Livre : {t}."
    return f"Livre / Book: {t}."
