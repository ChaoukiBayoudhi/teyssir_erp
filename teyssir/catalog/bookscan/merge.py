"""Field-level merge for book scans (Phase 15.3).

Priority per bibliographic field:
  1. ISBN → metadata provider (OpenLibrary / Google)
  2. Vision LLM structured (fills empties; may win title/description when metadata missing)
  3. Tess / barcode / price OCR (fills remaining empties)

Hard overrides (never invented by LLM alone):
  * isbn13 — checksum bookland 978/979 only; barcode ISBN beats Vision
  * barcode_* — real decoder only; 619… never isbn13
  * price — sticker/OCR rules beat LLM guess unless OCR empty

Provenance is recorded in ``draft.raw["field_sources"]``.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .draft import BookDraft
from .isbn import to_isbn13

# Provenance labels (stable strings for UI / debugging)
SRC_METADATA = "metadata"
SRC_VISION = "vision"
SRC_OCR = "ocr"
SRC_BARCODE = "barcode"
SRC_DIGIT_OCR = "digit_ocr"
SRC_CLIENT = "client"

_SCALAR_BIB = (
    "title",
    "subtitle",
    "publisher",
    "series",
    "edition",
    "subject",
    "description",
    "isbn10",
    "dimensions",
    "cover_type",
    "currency",
    "language_detected",
)
_LIST_BIB = ("authors", "translators", "languages", "keywords")
_OPTIONAL_INT = ("pub_year", "pages")


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, dict, str)):
        return bool(value)
    return True


def _layer_raw(draft: BookDraft | None) -> dict:
    return dict((draft.raw or {}) if draft else {})


def _is_real_barcode(draft: BookDraft | None) -> bool:
    """True when barcode_* came from a real decoder (not digit-OCR)."""
    if not draft or not (draft.barcode_raw or "").strip():
        return False
    raw = draft.raw or {}
    if raw.get("barcode_source") == "digit_ocr":
        return False
    if raw.get("barcode_detected") or (draft.barcode_kind or "").strip():
        return True
    # Decoder hit often stamps barcode_source=pyzbar/opencv/…
    src = (raw.get("barcode_source") or "").strip()
    return bool(src and src != "digit_ocr")


def _pick_isbn13(
    *,
    metadata: BookDraft | None,
    vision: BookDraft | None,
    ocr: BookDraft | None,
    isbn_hint: str = "",
    isbn_source: str = "",
    client_isbn_hint: bool = False,
) -> tuple[str, str]:
    """Return ``(isbn13, field_source)`` with hard overrides.

    Preference among checksum-valid bookland ISBNs:
      barcode → client hint → metadata → OCR (non-digit) → digit_ocr → vision
    """
    ocr_raw = _layer_raw(ocr)
    meta_raw = _layer_raw(metadata)
    vis_raw = _layer_raw(vision)

    candidates: list[tuple[int, str, str]] = []  # (rank, isbn, source)

    def _add(rank: int, raw_isbn: str, source: str) -> None:
        valid = to_isbn13(raw_isbn or "")
        if not valid:
            return
        # Never promote Tunisian CNP 619… (to_isbn13 already rejects non-978/979)
        if valid.startswith("619"):
            return
        candidates.append((rank, valid, source))

    # Rank 0: real barcode ISBN
    if isbn_source == "barcode" and isbn_hint:
        _add(0, isbn_hint, SRC_BARCODE)
    if ocr and ocr_raw.get("isbn_from_barcode") and ocr.isbn13:
        _add(0, ocr.isbn13, SRC_BARCODE)
    if ocr and ocr.barcode_kind == "isbn13" and ocr.barcode_raw:
        _add(0, ocr.barcode_raw, SRC_BARCODE)

    # Rank 1: explicit client ISBN hint
    if client_isbn_hint and isbn_hint:
        _add(1, isbn_hint, SRC_CLIENT)

    # Rank 2: metadata provider
    if metadata and metadata.isbn13:
        _add(2, metadata.isbn13, SRC_METADATA)
    elif isbn_hint and metadata is not None:
        # Enrich key when provider returned a draft without isbn13 filled
        _add(2, isbn_hint, SRC_METADATA)

    # Rank 3: Tess OCR ISBN (not digit-OCR)
    if ocr and ocr.isbn13 and not ocr_raw.get("isbn_from_digit_ocr"):
        if not ocr_raw.get("isbn_from_barcode"):
            _add(3, ocr.isbn13, SRC_OCR)

    # Rank 4: digit-OCR ISBN
    if isbn_source == "digit_ocr" and isbn_hint:
        _add(4, isbn_hint, SRC_DIGIT_OCR)
    if ocr and ocr_raw.get("isbn_from_digit_ocr") and (ocr.isbn13 or isbn_hint):
        _add(4, ocr.isbn13 or isbn_hint, SRC_DIGIT_OCR)

    # Rank 5: Vision LLM (checksum already required by caller sanitizer)
    if vision and vision.isbn13:
        _add(5, vision.isbn13, SRC_VISION)
    if vis_raw.get("isbn_from_vision") and vision and vision.isbn13:
        _add(5, vision.isbn13, SRC_VISION)

    if not candidates:
        # Last resort: validated hint with unknown source
        valid = to_isbn13(isbn_hint or "")
        if valid:
            return valid, SRC_OCR if isbn_source == "digit_ocr" else (isbn_source or SRC_OCR)
        return "", ""

    candidates.sort(key=lambda t: t[0])
    return candidates[0][1], candidates[0][2]


def _pick_price(
    metadata: BookDraft | None,
    vision: BookDraft | None,
    ocr: BookDraft | None,
) -> tuple[str, str]:
    """OCR/sticker price beats LLM; metadata rarely has TND stickers."""
    if ocr and (ocr.price or "").strip():
        return ocr.price.strip(), SRC_OCR
    if metadata and (metadata.price or "").strip():
        return metadata.price.strip(), SRC_METADATA
    if vision and (vision.price or "").strip():
        return vision.price.strip(), SRC_VISION
    return "", ""


def _pick_barcode_fields(
    ocr: BookDraft | None,
    vision: BookDraft | None,
) -> tuple[str, str, str, str]:
    """Real decoder only — never Vision, never digit-OCR as barcode_*."""
    del vision  # Vision must not invent barcode_*
    if not _is_real_barcode(ocr):
        return "", "", "", ""
    raw = (ocr.barcode_raw or "").strip()
    if not raw:
        return "", "", "", ""
    kind = (ocr.barcode_kind or "").strip()
    sym = (ocr.barcode_symbology or "").strip()
    # 619… is local_product forever
    if raw.startswith("619"):
        kind = "local_product" if kind == "isbn13" or not kind else kind
        if kind == "isbn13":
            kind = "local_product"
    return raw, sym, kind, SRC_BARCODE


def _fill_from_layers(
    out: BookDraft,
    sources: dict[str, str],
    field: str,
    layers: list[tuple[str, BookDraft | None]],
) -> None:
    """Assign first non-empty value across layers (already ordered by priority)."""
    for src_label, draft in layers:
        if not draft:
            continue
        val = getattr(draft, field, None)
        if _truthy(val):
            if isinstance(val, list):
                setattr(out, field, list(val))
            else:
                setattr(out, field, val)
            sources[field] = src_label
            return


def merge_cover_drafts(front: BookDraft, back: BookDraft | None) -> BookDraft:
    """Combine front (title/author) + back (ISBN/price) into one reviewable draft."""
    out = BookDraft(source=front.source or (back.source if back else ""), confidence=0.0)
    for key in (
        "title", "subtitle", "publisher", "series", "edition", "subject",
        "description", "isbn13", "isbn10", "price",
        "barcode_raw", "barcode_symbology", "barcode_kind",
    ):
        setattr(out, key, getattr(front, key) or "")
    out.authors = list(front.authors or [])
    out.translators = list(front.translators or [])
    out.languages = list(front.languages or [])
    out.pub_year = front.pub_year
    out.pages = front.pages
    out.raw = {**(front.raw or {}), "covers": {"front": True}}

    if back:
        if back.isbn13:
            out.isbn13 = back.isbn13
        if back.price:
            out.price = back.price
        if back.barcode_raw:
            out.barcode_raw = back.barcode_raw
            out.barcode_symbology = back.barcode_symbology or out.barcode_symbology
            out.barcode_kind = back.barcode_kind or out.barcode_kind
        if back.isbn10 and not out.isbn10:
            out.isbn10 = back.isbn10
        for key in ("title", "subtitle", "publisher", "subject", "description"):
            if not getattr(out, key) and getattr(back, key):
                setattr(out, key, getattr(back, key))
        if not out.authors and back.authors:
            out.authors = list(back.authors)
        if back.languages:
            from .ocr import arabic_char_ratio, is_usable_ocr_title

            front_latin_only = (
                is_usable_ocr_title(front.title or "")
                and arabic_char_ratio(front.title or "") < 0.12
                and "ar" not in (front.languages or [])
            )
            if front_latin_only:
                merged_langs = list(front.languages or [])
                for lang in back.languages:
                    if lang == "ar":
                        continue
                    if lang not in merged_langs:
                        merged_langs.append(lang)
                out.languages = merged_langs
                out.raw.pop("arabic_script_detected", None)
                out.raw.pop("ocr_arabic_likely", None)
            else:
                merged_langs = list(out.languages or [])
                for lang in back.languages:
                    if lang not in merged_langs:
                        merged_langs.append(lang)
                out.languages = merged_langs
        out.raw["covers"] = {"front": True, "back": True}
        out.raw["back"] = {
            k: back.raw.get(k)
            for k in (
                "isbn_detected", "isbn_not_detected", "price_detected",
                "isbn_from_barcode", "isbn_from_digit_ocr", "ocr_langs",
                "barcode_detected", "barcode_non_isbn", "barcode_source",
            )
            if back.raw
        }
        if back.raw.get("isbn_from_barcode"):
            out.raw["isbn_from_barcode"] = True
        if back.raw.get("isbn_from_digit_ocr"):
            out.raw["isbn_from_digit_ocr"] = True
        if back.raw.get("barcode_detected"):
            out.raw["barcode_detected"] = True
        if back.raw.get("barcode_non_isbn"):
            out.raw["barcode_non_isbn"] = True

    from .ocr import is_usable_ocr_title, merge_bilingual_title

    if front.title and back and back.title:
        merged = merge_bilingual_title(front.title, back.title)
        if merged and ("(" in merged or "/" in merged):
            out.title = merged
            out.raw["bilingual_title"] = True

    if out.isbn13 and (
        out.raw.get("isbn_from_barcode")
        or (back and back.raw.get("isbn_from_barcode"))
    ):
        out.confidence = max(front.confidence or 0, (back.confidence if back else 0) or 0, 0.85)
        out.raw["isbn_from_barcode"] = True
    elif out.isbn13 and (
        out.raw.get("isbn_from_digit_ocr")
        or (back and back.raw.get("isbn_from_digit_ocr"))
    ):
        out.confidence = min(
            max(front.confidence or 0, (back.confidence if back else 0) or 0),
            0.35,
        )
        out.raw["isbn_from_digit_ocr"] = True
    elif out.isbn13:
        out.confidence = min(
            max(front.confidence or 0, (back.confidence if back else 0) or 0, 0.55),
            0.6,
        )
    elif out.title and is_usable_ocr_title(out.title):
        out.confidence = max(front.confidence or 0, (back.confidence if back else 0) or 0)
    else:
        out.confidence = max(front.confidence or 0, (back.confidence if back else 0) or 0)

    if out.isbn13:
        out.raw["isbn_detected"] = True
        out.raw.pop("isbn_not_detected", None)
    else:
        out.raw["isbn_not_detected"] = True

    from .language import apply_language_detected

    apply_language_detected(out)
    from .edition import refine_school_draft

    refine_school_draft(out)
    return out


def merge_scan_layers(
    *,
    metadata: BookDraft | None = None,
    vision: BookDraft | None = None,
    ocr: BookDraft | None = None,
    isbn_hint: str = "",
    isbn_source: str = "",
    client_isbn_hint: bool = False,
) -> BookDraft:
    """Merge metadata / vision / OCR layers into one draft with field provenance.

    Bibliographic fields follow metadata → vision → OCR.
    isbn13 / barcode_* / price use the hard overrides documented above.
    """
    sources: dict[str, str] = {}
    layers_bib: list[tuple[str, BookDraft | None]] = [
        (SRC_METADATA, metadata),
        (SRC_VISION, vision),
        (SRC_OCR, ocr),
    ]

    out = BookDraft()
    # Source label: prefer the strongest contributing layer
    if metadata and (metadata.source or metadata.title or metadata.isbn13):
        out.source = metadata.source or SRC_METADATA
    elif vision and (vision.source or vision.title):
        out.source = vision.source or SRC_VISION
    elif ocr:
        out.source = ocr.source or SRC_OCR

    for field in _SCALAR_BIB:
        _fill_from_layers(out, sources, field, layers_bib)
    for field in _LIST_BIB:
        _fill_from_layers(out, sources, field, layers_bib)
    for field in _OPTIONAL_INT:
        _fill_from_layers(out, sources, field, layers_bib)

    # --- Hard overrides ---
    isbn, isbn_src = _pick_isbn13(
        metadata=metadata,
        vision=vision,
        ocr=ocr,
        isbn_hint=isbn_hint,
        isbn_source=isbn_source,
        client_isbn_hint=client_isbn_hint,
    )
    # Guard: barcode_raw 619 must never become isbn13
    ocr_bc = (ocr.barcode_raw or "").strip() if ocr else ""
    if isbn and ocr_bc.startswith("619") and isbn == to_isbn13(ocr_bc):
        isbn, isbn_src = "", ""
    if isbn and isbn.startswith("619"):
        isbn, isbn_src = "", ""
    out.isbn13 = isbn
    if isbn_src:
        sources["isbn13"] = isbn_src

    price, price_src = _pick_price(metadata, vision, ocr)
    out.price = price
    if price_src:
        sources["price"] = price_src

    bc_raw, bc_sym, bc_kind, bc_src = _pick_barcode_fields(ocr, vision)
    out.barcode_raw = bc_raw
    out.barcode_symbology = bc_sym
    out.barcode_kind = bc_kind
    if bc_src:
        sources["barcode_raw"] = bc_src
        if bc_sym:
            sources["barcode_symbology"] = bc_src
        if bc_kind:
            sources["barcode_kind"] = bc_src

    # If barcode is ISBN bookland and isbn13 empty, align (decoder already validated)
    if (
        not out.isbn13
        and bc_kind == "isbn13"
        and bc_raw
        and to_isbn13(bc_raw)
    ):
        out.isbn13 = to_isbn13(bc_raw)
        sources["isbn13"] = SRC_BARCODE

    # CNP: never leave kind=isbn13 on 619
    if out.barcode_raw.startswith("619"):
        out.barcode_kind = "local_product"
        if out.isbn13 == out.barcode_raw or (out.isbn13 or "").startswith("619"):
            out.isbn13 = ""
            sources.pop("isbn13", None)

    # Merge raw blobs (OCR base → vision → metadata overlays) + provenance
    merged_raw: dict[str, Any] = {}
    for layer in (ocr, vision, metadata):
        if layer and layer.raw:
            merged_raw.update(layer.raw)
    # Re-stamp identity flags from chosen isbn source
    if out.isbn13:
        merged_raw["isbn_detected"] = True
        merged_raw.pop("isbn_not_detected", None)
        if sources.get("isbn13") == SRC_BARCODE or sources.get("isbn13") == SRC_CLIENT:
            merged_raw["isbn_from_barcode"] = True
            merged_raw.pop("isbn_from_digit_ocr", None)
        elif sources.get("isbn13") == SRC_DIGIT_OCR:
            merged_raw["isbn_from_digit_ocr"] = True
            merged_raw.pop("isbn_from_barcode", None)
        elif sources.get("isbn13") == SRC_VISION:
            merged_raw["isbn_from_vision"] = True
        if client_isbn_hint:
            merged_raw["isbn_client_hint"] = True
    else:
        merged_raw.setdefault("isbn_not_detected", True)

    if out.barcode_raw:
        merged_raw["barcode_detected"] = True
        if out.barcode_raw.startswith("619") or out.barcode_kind == "local_product":
            merged_raw["barcode_non_isbn"] = True

    if vision and (vision.title or vision.isbn13 or vision.description):
        merged_raw.setdefault("vision_fallback", True)

    if metadata is not None and (metadata.title or metadata.source):
        merged_raw["metadata_hit"] = True

    merged_raw["field_sources"] = dict(sources)
    out.raw = merged_raw

    # Confidence from strongest identity signal
    conf_candidates = [
        (metadata.confidence if metadata else 0) or 0,
        (vision.confidence if vision else 0) or 0,
        (ocr.confidence if ocr else 0) or 0,
    ]
    base = max(conf_candidates)
    isbn_src_final = sources.get("isbn13", "")
    if metadata is not None and (metadata.title or metadata.source):
        out.confidence = max(base, 0.85)
        if isbn_src_final in (SRC_BARCODE, SRC_CLIENT):
            out.confidence = max(out.confidence, 0.9)
    elif isbn_src_final == SRC_BARCODE or isbn_src_final == SRC_CLIENT:
        out.confidence = max(base, 0.85)
    elif isbn_src_final == SRC_DIGIT_OCR:
        out.confidence = min(base or 0.25, 0.35)
    elif isbn_src_final == SRC_VISION:
        out.confidence = min(max(base, 0.45), 0.7)
    elif out.isbn13:
        out.confidence = min(max(base, 0.55), 0.6)
    else:
        out.confidence = base

    from .language import apply_language_detected

    apply_language_detected(out)
    from .edition import refine_school_draft

    refine_school_draft(out)
    return out


def layer_snapshot(draft: BookDraft | None) -> dict:
    """Debug helper: serialize a layer without mutating it."""
    if not draft:
        return {}
    return asdict(draft)
