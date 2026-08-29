"""Book-scan orchestration: ISBN-first (barcode → enrich), multi-cover merge, title fallback."""
from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from concurrent.futures import as_completed

from django.db import transaction

from teyssir.core.money import require_non_negative_money

from .barcode import DecodedBarcode, decode_isbn_with_source, decode_product_barcode
from .draft import BookDraft
from .isbn import to_isbn13
from .merge import merge_cover_drafts, merge_scan_layers
from .metadata import enrich_by_isbn, enrich_by_title
from .ocr import get_ocr_provider

logger = logging.getLogger("teyssir.ocr")

# Backward-compatible alias for tests / callers
_merge_cover_drafts = merge_cover_drafts


def _apply_barcode_hit(draft: BookDraft, hit: DecodedBarcode | None) -> None:
    """Attach Phase 2B barcode fields; set isbn13 only for bookland hits."""
    if not hit:
        return
    draft.barcode_raw = hit.raw
    draft.barcode_symbology = hit.symbology
    draft.barcode_kind = hit.kind
    draft.raw = {
        **(draft.raw or {}),
        "barcode_detected": True,
        "barcode_source": hit.source,
    }
    if hit.kind == "isbn13":
        draft.isbn13 = hit.raw
        draft.raw["isbn_from_barcode"] = True
        draft.raw["isbn_detected"] = True
        draft.raw.pop("isbn_not_detected", None)
    else:
        # Never invent ISBN from CNP 619… / other non-bookland codes
        draft.raw["barcode_non_isbn"] = True
        if draft.isbn13 and not to_isbn13(draft.isbn13):
            draft.isbn13 = ""


def _product_barcode_from_paths(image_paths, prepared=None) -> DecodedBarcode | None:
    """Real decoder hit (ISBN or non-ISBN) across frames; prefer verso, prefer ISBN."""
    if not image_paths:
        return None
    order = list(reversed(range(len(image_paths))))
    fallback: DecodedBarcode | None = None
    for i in order:
        prep = None
        if prepared and 0 <= i < len(prepared):
            prep = prepared[i]
        hit = decode_product_barcode(image_paths[i], prepare=prep)
        if not hit:
            continue
        if hit.kind == "isbn13":
            return hit
        if fallback is None:
            fallback = hit
    return fallback


def _barcode_isbn_from_paths(image_paths, prepared=None) -> tuple[str, str]:
    """Try barcode (+ digit OCR) on all images (prefer last = verso).

    Returns ``(isbn13, source)`` with ``source`` in ``barcode`` | ``digit_ocr`` | ``''``.
    Prefers a real pyzbar ISBN hit over digit OCR across all frames.
    Non-ISBN product codes are handled by :func:`_product_barcode_from_paths`.
    When ``prepared`` (list of CoverPreprocessResult) is given, ROI bands are tried first.
    """
    if not image_paths:
        return "", ""
    order = list(reversed(range(len(image_paths))))
    digit_fallback = ("", "")
    for i in order:
        prep = None
        if prepared and 0 <= i < len(prepared):
            prep = prepared[i]
        isbn, src = decode_isbn_with_source(image_paths[i], prepare=prep)
        if isbn and src == "barcode":
            return isbn, "barcode"
        if isbn and src == "digit_ocr" and not digit_fallback[0]:
            digit_fallback = (isbn, "digit_ocr")
    return digit_fallback


def _extract_pair(
    provider, image_paths, prepared=None, known_barcode=None, *, barcode_searched=False,
):
    """OCR front (+ optional back) in parallel when two covers are present."""
    texts: list[str] = []
    front = BookDraft(raw={"isbn_not_detected": True})
    back = None

    if not image_paths:
        return texts, front, back

    role0 = "front" if len(image_paths) > 1 else "auto"
    prep0 = prepared[0] if prepared and len(prepared) > 0 else None
    prep1 = prepared[1] if prepared and len(prepared) > 1 else None
    # When services already ran barcode decode, pass False-ish sentinel via empty tuple
    kb = known_barcode if known_barcode is not None else (
        False if barcode_searched else None
    )

    def _call(path, role, prep):
        try:
            return provider.extract(
                path, role=role, prepare=prep, known_barcode=kb,
            )
        except TypeError:
            try:
                return provider.extract(path, role=role, prepare=prep)
            except TypeError:
                return provider.extract(path, role=role)

    if len(image_paths) == 1:
        t0, front = _call(image_paths[0], role0, prep0)
        texts.append(t0 or "")
        return texts, front, back

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_front = pool.submit(_call, image_paths[0], role0, prep0)
        fut_back = pool.submit(_call, image_paths[1], "back", prep1)
        results = {}
        for fut in as_completed([fut_front, fut_back]):
            results[fut] = fut.result()
        t0, front = results[fut_front]
        t1, back = results[fut_back]
        texts.extend([t0 or "", t1 or ""])
    return texts, front, back


def scan_book(image_paths, isbn="", enrich=enrich_by_isbn, enrich_title=enrich_by_title, on_stage=None):
    """Produce a (BookDraft, ocr_text) from image path(s) + an optional ISBN.

    Multi-cover (Phase 6):
      * Phase 2A preprocess (orient / crop / deskew / CLAHE / band ROIs) first
      * barcode decode on images first (ISBN-13 from EAN)
      * image[0] → front cover (title / author / language)
      * image[1] → back cover (ISBN / barcode / price)
      * merge → enrich by ISBN, else cautious title search
      * optional Vision-LLM only when OCR has no usable title/ISBN (short timeout)

    ``on_stage(stage, progress=None)`` — optional Phase 15.5 progress hook
    (preprocess → barcode → ocr → language → vision → metadata → merge → done).
    """
    from .preprocess import prepared_cover_paths

    def _emit(stage, progress=None):
        if on_stage:
            try:
                on_stage(stage, progress)
            except TypeError:
                on_stage(stage)

    # Client / caller hint: only accept checksum-valid bookland ISBNs
    client_raw = (isbn or "").strip()
    isbn = to_isbn13(client_raw) or ""
    client_isbn_hint = bool(isbn)
    if client_raw and not isbn:
        client_isbn_hint = False

    provider = get_ocr_provider()

    # Phase 2A: rectify covers before barcode + OCR (temps cleaned on exit).
    _emit("preprocess")
    with prepared_cover_paths(image_paths or []) as (prep_paths, prep_results):
        work_paths = prep_paths if prep_paths else list(image_paths or [])
        return _scan_book_prepared(
            work_paths,
            prepared=prep_results,
            isbn=isbn,
            client_isbn_hint=client_isbn_hint,
            provider=provider,
            enrich=enrich,
            enrich_title=enrich_title,
            on_stage=on_stage,
        )


def _scan_book_prepared(
    image_paths,
    *,
    prepared,
    isbn,
    client_isbn_hint,
    provider,
    enrich,
    enrich_title,
    on_stage=None,
):
    """Inner scan on already-preprocessed paths (+ optional ROI metadata)."""
    def _emit(stage, progress=None):
        if on_stage:
            try:
                on_stage(stage, progress)
            except TypeError:
                on_stage(stage)

    # Product barcode first (Phase 2B): retain ISBN and non-ISBN (CNP 619…).
    # Digit OCR never fills barcode_* ; isbn13 only when bookland check OK.
    # Phase 2C: skip digit-OCR ISBN hunt when a non-ISBN product barcode is already retained.
    t_scan = time.perf_counter()
    product_bc: DecodedBarcode | None = None
    barcode_isbn = ""
    isbn_source = ""
    _emit("barcode")
    if image_paths:
        product_bc = _product_barcode_from_paths(image_paths, prepared)
        if product_bc and product_bc.kind == "isbn13" and not isbn:
            barcode_isbn = product_bc.raw
            isbn_source = "barcode"
            isbn = barcode_isbn
        elif not isbn and not product_bc:
            # Already ran zbar/OpenCV product decode — digit-OCR ISBN only (no re-fan-out)
            from .barcode import ocr_isbn_digits_from_image

            order = list(reversed(range(len(image_paths))))
            for i in order:
                prep = None
                if prepared and 0 <= i < len(prepared):
                    prep = prepared[i]
                dig = ocr_isbn_digits_from_image(image_paths[i], prepare=prep)
                if dig:
                    barcode_isbn, isbn_source = dig, "digit_ocr"
                    isbn = dig
                    break

    _emit("ocr")
    texts, front, back = _extract_pair(
        provider,
        image_paths,
        prepared=prepared,
        known_barcode=product_bc,
        barcode_searched=bool(image_paths),
    )
    if not isbn and front.isbn13:
        isbn = to_isbn13(front.isbn13) or ""
        if isbn and front.raw.get("isbn_from_barcode"):
            isbn_source = isbn_source or "barcode"
        elif isbn and front.raw.get("isbn_from_digit_ocr"):
            isbn_source = isbn_source or "digit_ocr"
    if not isbn and back and back.isbn13:
        isbn = to_isbn13(back.isbn13) or ""
        if isbn and back.raw.get("isbn_from_barcode"):
            isbn_source = isbn_source or "barcode"
        elif isbn and back.raw.get("isbn_from_digit_ocr"):
            isbn_source = isbn_source or "digit_ocr"

    # Propagate source flags from cover OCR when path-level decode missed
    if not isbn_source:
        if (front.raw or {}).get("isbn_from_barcode") or (
            back and (back.raw or {}).get("isbn_from_barcode")
        ):
            isbn_source = "barcode"
        elif (front.raw or {}).get("isbn_from_digit_ocr") or (
            back and (back.raw or {}).get("isbn_from_digit_ocr")
        ):
            isbn_source = "digit_ocr"

    ocr_draft = merge_cover_drafts(front, back) if image_paths else front
    ocr_text = "\n---\n".join(texts)

    # Attach product barcode (ISBN or CNP/GTIN). Prefer path-level zbar hit.
    if product_bc:
        _apply_barcode_hit(ocr_draft, product_bc)
    elif not ocr_draft.barcode_raw:
        for side in (back, front):
            if side and side.barcode_raw:
                ocr_draft.barcode_raw = side.barcode_raw
                ocr_draft.barcode_symbology = side.barcode_symbology
                ocr_draft.barcode_kind = side.barcode_kind
                ocr_draft.raw = {
                    **(ocr_draft.raw or {}),
                    "barcode_detected": True,
                    "barcode_source": (side.raw or {}).get("barcode_source") or "ocr",
                }
                if side.barcode_kind == "isbn13":
                    ocr_draft.raw["isbn_from_barcode"] = True
                elif side.barcode_raw:
                    ocr_draft.raw["barcode_non_isbn"] = True
                break

    if isbn:
        ocr_draft.isbn13 = ocr_draft.isbn13 or isbn
        ocr_draft.raw["isbn_detected"] = True
        ocr_draft.raw.pop("isbn_not_detected", None)
        if isbn_source == "barcode" or client_isbn_hint:
            ocr_draft.raw["isbn_from_barcode"] = True
            ocr_draft.raw.pop("isbn_from_digit_ocr", None)
            ocr_draft.confidence = max(ocr_draft.confidence or 0, 0.85)
        elif isbn_source == "digit_ocr":
            ocr_draft.raw["isbn_from_digit_ocr"] = True
            ocr_draft.raw.pop("isbn_from_barcode", None)
            ocr_draft.confidence = min(ocr_draft.confidence or 0.25, 0.35)
        if client_isbn_hint:
            ocr_draft.raw["isbn_client_hint"] = True

    # Phase 15.5: language milestone after OCR (final apply still runs post-merge).
    _emit("language")
    from .language import apply_language_detected
    apply_language_detected(ocr_draft, front=ocr_draft, back=None)

    # Phase 15.3: Vision is a separate layer (not an in-place OCR overwrite).
    vision_draft = None
    if image_paths and _should_try_vision(ocr_draft, provider):
        _emit("vision")
        vision_draft = _maybe_vision_draft(image_paths, ocr_draft)
        if vision_draft:
            if not isbn and vision_draft.isbn13:
                isbn = to_isbn13(vision_draft.isbn13) or ""
            if vision_draft.raw.get("vision_text"):
                ocr_text = f"{ocr_text}\n---\n{vision_draft.raw.get('vision_text')}"

    _emit("metadata")
    metadata_draft = enrich(isbn) if isbn else None
    metadata_hit = metadata_draft is not None
    if metadata_hit and isbn_source == "digit_ocr":
        # Digit-OCR ISBN confirmed by OpenLibrary/Google → upgrade trust flag
        metadata_draft.raw = {
            **(metadata_draft.raw or {}),
            "isbn_from_digit_ocr": True,
            "isbn_digit_ocr_confirmed": True,
        }

    _emit("merge")
    draft = merge_scan_layers(
        metadata=metadata_draft,
        vision=vision_draft,
        ocr=ocr_draft,
        isbn_hint=isbn,
        isbn_source=isbn_source,
        client_isbn_hint=client_isbn_hint,
    )

    # No ISBN: try title/author metadata search (local Tunisian editions) — low confidence
    # Never search OpenLibrary with garbage Latin OCR (locks wrong language / wrong book).
    title_hit = False
    from .ocr import is_garbage_latin_ocr, is_usable_ocr_title

    title_ok = bool(draft.title) and is_usable_ocr_title(draft.title) and not (
        (draft.raw or {}).get("ocr_garbage_latin")
        or is_garbage_latin_ocr(draft.title or "")
    )
    if not isbn and title_ok and draft.source in ("tesseract", "vision", "manual", ""):
        found = enrich_title(draft.title, (draft.authors or [""])[0] if draft.authors else "")
        if found:
            title_hit = True
            price = draft.price
            langs = list(draft.languages or [])
            ocr_title = draft.title
            ocr_authors = list(draft.authors or [])
            raw_ocr = dict(draft.raw or {})
            field_sources = dict(raw_ocr.get("field_sources") or {})
            # OCR title/authors stay unless search is a strong match (see metadata)
            strong = (found.confidence or 0) >= 0.55 and not found.raw.get("title_search_weak")
            if strong:
                found.merge(draft)  # search wins bibliographic fields; OCR fills gaps
                for key in ("title", "authors", "publisher", "description", "subtitle"):
                    if getattr(found, key, None):
                        field_sources[key] = "metadata"
            else:
                # Keep OCR identity; only fill empty extras from search
                draft.merge(found)
                found = draft
                found.confidence = min(found.confidence or 0.35, 0.45)
                if ocr_authors:
                    found.authors = ocr_authors
                if ocr_title:
                    found.title = ocr_title
            if price:
                found.price = price
                field_sources.setdefault("price", "ocr")
            # Prefer OCR script tags (ar) over OL defaulting to eng from a bad query
            if langs:
                found.languages = langs
            elif not found.languages:
                found.languages = langs
            found.raw = {
                **(found.raw or {}), **raw_ocr, "title_search": True,
                **({"title_search_weak": True} if not strong else {}),
                "field_sources": field_sources,
            }
            draft = found
    elif not isbn and draft.title and not title_ok:
        draft.raw = {
            **(draft.raw or {}),
            "title_search_skipped_garbage": True,
        }

    if isbn:
        draft.isbn13 = draft.isbn13 or to_isbn13(isbn) or draft.isbn13
        draft.raw = {**(draft.raw or {}), "isbn_detected": True}
        draft.raw.pop("isbn_not_detected", None)
        if not metadata_hit:
            draft.raw["metadata_miss"] = True
            # Digit-OCR / unverified ISBN without OpenLibrary — clear or demote
            from_barcode = bool(
                draft.raw.get("isbn_from_barcode")
                or client_isbn_hint
                or isbn_source == "barcode"
                or (draft.raw.get("field_sources") or {}).get("isbn13") == "barcode"
            )
            from_digit = bool(
                draft.raw.get("isbn_from_digit_ocr")
                or isbn_source == "digit_ocr"
                or (draft.raw.get("field_sources") or {}).get("isbn13") == "digit_ocr"
            )
            if from_digit and not from_barcode:
                draft.raw["suggested_isbn"] = draft.isbn13
                draft.raw["isbn_unconfirmed"] = True
                draft.isbn13 = ""
                draft.confidence = min(draft.confidence or 0.2, 0.25)
                draft.raw["isbn_not_detected"] = True
                draft.raw.pop("isbn_detected", None)
                fs = dict(draft.raw.get("field_sources") or {})
                fs.pop("isbn13", None)
                draft.raw["field_sources"] = fs
            elif not from_barcode:
                # Unknown-source ISBN + metadata miss → low confidence
                draft.raw["suggested_isbn"] = draft.isbn13
                draft.raw["isbn_unconfirmed"] = True
                draft.confidence = min(draft.confidence or 0.3, 0.35)
    else:
        draft.raw = {**(draft.raw or {}), "isbn_not_detected": True}
        from .ocr import is_usable_ocr_title

        title_usable = bool(draft.title) and is_usable_ocr_title(draft.title) and not (
            (draft.raw or {}).get("ocr_garbage_latin")
            or is_garbage_latin_ocr(draft.title or "")
        )
        # Tess-only / no-ISBN: short template description only if title usable
        vision_used = bool((draft.raw or {}).get("vision_fallback")) or draft.source == "vision"
        if not vision_used and not draft.description:
            if title_usable:
                from .language import template_description

                draft.description = template_description(
                    draft.title,
                    language_detected=getattr(draft, "language_detected", "") or "",
                )
                if draft.description:
                    draft.raw["description_template"] = True
                    fs = dict(draft.raw.get("field_sources") or {})
                    fs.setdefault("description", "ocr")
                    draft.raw["field_sources"] = fs
            else:
                draft.raw["manual_assist"] = True
        elif draft.title and not title_hit and not metadata_hit:
            draft.raw["manual_assist"] = True
        # Never advertise high confidence without ISBN from fuzzy title alone
        if draft.raw.get("title_search") and not draft.isbn13:
            draft.confidence = min(draft.confidence or 0.4, 0.45)

    # Ensure barcode fields survive metadata merge
    if product_bc and not draft.barcode_raw:
        _apply_barcode_hit(draft, product_bc)
        fs = dict(draft.raw.get("field_sources") or {})
        fs["barcode_raw"] = "barcode"
        draft.raw["field_sources"] = fs
    elif ocr_draft.barcode_raw and not draft.barcode_raw:
        draft.barcode_raw = ocr_draft.barcode_raw
        draft.barcode_symbology = ocr_draft.barcode_symbology
        draft.barcode_kind = ocr_draft.barcode_kind

    scan_ms = int((time.perf_counter() - t_scan) * 1000)
    draft.raw = {**(draft.raw or {}), "scan_ms": scan_ms}
    # Prefer per-cover ocr_ms when present
    if ocr_draft.raw.get("ocr_ms") and "ocr_ms" not in (draft.raw or {}):
        draft.raw["ocr_ms"] = ocr_draft.raw["ocr_ms"]

    from .language import apply_language_detected

    apply_language_detected(draft, front=ocr_draft, back=None)
    _emit("done")

    logger.info(
        "scan_book done ms=%s title=%r isbn=%s barcode=%s conf=%s lang=%s sources=%s",
        scan_ms,
        (draft.title or "")[:40],
        draft.isbn13 or "",
        draft.barcode_raw or "",
        draft.confidence,
        getattr(draft, "language_detected", "") or "",
        (draft.raw or {}).get("field_sources") or {},
    )
    return draft, ocr_text


def _sanitize_vision_isbn(draft):
    """Drop Vision ISBN unless bookland checksum validates — never invent identity."""
    from .vision import sanitize_vision_isbn

    return sanitize_vision_isbn(draft)


def _maybe_vision_draft(image_paths, ocr_draft) -> BookDraft | None:
    """Run dual-image Vision-LLM once; return a separate layer (or None).

    Phase 15.4: one Ollama call with front+back (when present). Merged later via
    ``merge_scan_layers`` — do not overwrite Tess/barcode/price OCR in place.
    Never accept a Vision ISBN without checksum validation; never invent barcode_*.
    """
    from django.conf import settings

    from .ocr import arabic_char_ratio
    from .vision import analyze_covers, strip_vision_barcodes

    if not image_paths:
        return None

    base_timeout = float(getattr(settings, "VISION_FALLBACK_TIMEOUT", 28) or 28)
    hard_cap = float(getattr(settings, "VISION_TIMEOUT", 45) or 45)
    raw = ocr_draft.raw or {}
    arabic_hard = bool(
        raw.get("ocr_garbage_arabic")
        or raw.get("ocr_garbage_latin")
        or raw.get("ocr_arabic_likely")
        or raw.get("tess_missing_ara")
        or ("ar" in (ocr_draft.languages or []))
        or arabic_char_ratio(ocr_draft.title or "") >= 0.15
    )
    # Budget: Arabic/calligraphy up to ~1.5× base, always ≤ VISION_TIMEOUT.
    # Dual-image uses the same budget (one call, not two sequential).
    timeout = min(base_timeout * (1.5 if arabic_hard else 1.0), hard_cap)
    front = image_paths[0]
    back = image_paths[1] if len(image_paths) > 1 else None

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(analyze_covers, front, back, timeout=timeout)
            try:
                v_text, v_draft = fut.result(timeout=timeout)
            except FuturesTimeout:
                fut.cancel()
                ocr_draft.raw = {
                    **(ocr_draft.raw or {}),
                    "vision_fallback_error": f"timeout after {timeout}s",
                }
                return None
    except Exception as exc:
        ocr_draft.raw = {**(ocr_draft.raw or {}), "vision_fallback_error": str(exc)[:200]}
        return None

    v_draft = _sanitize_vision_isbn(v_draft)
    v_draft = strip_vision_barcodes(v_draft)

    if not (v_draft.title or v_draft.isbn13 or v_draft.description or v_draft.authors):
        return None

    v_draft.raw = {
        **(v_draft.raw or {}),
        "vision_fallback": True,
        "tesseract_title": ocr_draft.title,
        "vision_text": (v_text or "")[:2000],
        "vision_timeout_budget": timeout,
    }
    if not v_draft.source:
        v_draft.source = "vision"
    return v_draft



def _maybe_vision_upgrade(image_paths, ocr_draft, back):
    """Legacy wrapper: merge vision layer into a combined draft (tests / callers)."""
    vision = _maybe_vision_draft(image_paths, ocr_draft)
    if not vision:
        return ocr_draft
    # Prefer barcode ISBN / OCR price via merge_scan_layers hard overrides
    merged = merge_scan_layers(metadata=None, vision=vision, ocr=ocr_draft)
    if back and back.isbn13 and (back.raw or {}).get("isbn_from_barcode"):
        merged.isbn13 = back.isbn13
        merged.raw = {
            **(merged.raw or {}),
            "isbn_from_barcode": True,
            "isbn_from_vision": False,
        }
        fs = dict(merged.raw.get("field_sources") or {})
        fs["isbn13"] = "barcode"
        merged.raw["field_sources"] = fs
    return merged


def _should_try_vision(draft, provider) -> bool:
    """Phase 2E / 15.4 Vision gate: run when Tess path is weak; skip when strong.

    Skip Vision when:
      * strong barcode ISBN + usable title (metadata will enrich — fast path)
      * non-ISBN product barcode + usable title + decent confidence
      * usable Latin/French title without garbage flags

    ALWAYS prefer Vision when:
      * no ISBN / weak or missing title
      * Arabic calligraphy / weak ``ar`` confidence
      * garbage Latin/Arabic OCR
      * phone photo with no barcode and no usable title
    """
    from django.conf import settings

    from .ocr import (
        arabic_char_ratio,
        is_garbage_arabic_ocr,
        is_garbage_latin_ocr,
        is_usable_ocr_title,
    )

    if getattr(provider, "name", "") != "tesseract":
        return False
    if not getattr(settings, "OCR_VISION_FALLBACK", True):
        return False

    raw = draft.raw or {}
    title = (draft.title or "").strip()
    mean_conf = None
    try:
        mean_conf = float(raw.get("ocr_mean_confidence") or 0) or None
    except (TypeError, ValueError):
        mean_conf = None
    title_ok = is_usable_ocr_title(title, mean_conf=mean_conf)
    has_barcode_isbn = bool(draft.isbn13 and raw.get("isbn_from_barcode"))
    has_product_barcode = bool(
        raw.get("barcode_detected") or draft.barcode_raw or has_barcode_isbn
    )
    metadata_hit = bool(raw.get("metadata_hit"))

    garbage = bool(
        raw.get("ocr_garbage_latin")
        or raw.get("ocr_garbage_arabic")
        or raw.get("ocr_arabic_likely")
        or raw.get("ocr_title_unusable")
        or raw.get("tess_missing_ara")
        or (title and is_garbage_latin_ocr(title, mean_conf=mean_conf))
        or (title and is_garbage_arabic_ocr(title, mean_conf=mean_conf))
    )

    # Force Vision: no ISBN + weak/missing title, garbage, Arabic calligraphy
    if garbage:
        return True
    if not draft.isbn13 and not title_ok:
        return True

    # Strong barcode ISBN + usable title (+ optional metadata_hit) → skip
    if has_barcode_isbn and title_ok and not garbage:
        return False
    if has_barcode_isbn and metadata_hit and title_ok:
        return False
    if has_barcode_isbn:
        # ISBN barcode alone is enough for OpenLibrary unless title is garbage
        if not garbage:
            return False
    if (
        has_product_barcode
        and title_ok
        and (draft.confidence or 0) >= 0.45
        and not garbage
    ):
        return False

    # Digit-OCR ISBN is unverified — allow Vision for title / better ISBN below.
    if (
        draft.isbn13
        and not raw.get("isbn_from_digit_ocr")
        and not has_barcode_isbn
        and title_ok
        and not garbage
    ):
        return False

    # Arabic calligraphy / weak ar path — always prefer Vision
    is_ar = (
        "ar" in (draft.languages or [])
        or raw.get("arabic_script_detected")
        or arabic_char_ratio(title) >= 0.15
    )
    if is_ar and (draft.confidence or 0) < 0.5:
        return True
    if is_ar and mean_conf is not None and mean_conf < 50:
        return True
    if is_ar and not title_ok:
        return True

    # Phone photos: missing barcode + no usable title → Vision
    if not has_product_barcode and not title_ok:
        return True
    if not has_product_barcode and (draft.confidence or 0) < 0.45 and not title_ok:
        return True

    # Usable Latin/French title — prefer fast path + title search over Vision
    if title_ok:
        return False

    if raw.get("ocr_available") is False:
        return True
    if (draft.confidence or 0) < 0.5:
        return True
    if not title:
        return True
    return False


def _default_book_tax_rate_id():
    from teyssir.catalog.models import TaxRate
    tva7 = TaxRate.objects.filter(rate_percent=7).order_by("name").first()
    if tva7:
        return tva7.id
    default = TaxRate.objects.filter(is_default=True).first()
    return default.id if default else None


def _default_book_category_id():
    from teyssir.catalog.models import Category
    for name in ("Livres", "Books", "كتاب", "Manuels"):
        cat = Category.objects.filter(name_fr__iexact=name).first()
        if cat:
            return cat.id
    return None


@transaction.atomic
def create_book_from_draft(*, data, image_ids=(), sale_price="0", origin_terminal=""):
    """Create Product + Book + normalized Contributors from reviewed `data`, and link the draft
    images uploaded during the scan. Returns the new Product."""
    from teyssir.catalog.models import (
        Barcode, Book, BookContributor, Contributor, Product, ProductImage,
    )

    # ISBN only when bookland 978/979 checksum OK — never promote 619… to isbn13
    isbn13 = to_isbn13(data.get("isbn13") or "") or ""
    barcode_raw = (data.get("barcode_raw") or "").strip()
    barcode_symbology = (data.get("barcode_symbology") or "").strip()
    barcode_kind = (data.get("barcode_kind") or "").strip()
    # If client sent a 619 code as isbn13 by mistake, keep it as local barcode only
    raw_isbn_field = (data.get("isbn13") or "").strip()
    if not isbn13 and raw_isbn_field:
        digits = "".join(ch for ch in raw_isbn_field if ch.isdigit())
        if digits.startswith("619") and not barcode_raw:
            barcode_raw = digits[:13] if len(digits) >= 13 else digits
            barcode_kind = barcode_kind or "local_product"
            barcode_symbology = barcode_symbology or "EAN13"

    sku = (
        data.get("sku") or isbn13 or barcode_raw or f"BK-{uuid.uuid4().hex[:10]}"
    ).strip()
    tax_rate_id = data.get("tax_rate") or _default_book_tax_rate_id()
    category_id = data.get("category") or _default_book_category_id()
    product = Product.objects.create(
        sku=sku, name_fr=data.get("title", ""), name_ar=data.get("title_ar", ""),
        product_type=Product.BOOK, is_book=True, isbn=isbn13,
        sale_price=require_non_negative_money(sale_price or 0, label="sale_price"),
        tax_rate_id=tax_rate_id or None,
        category_id=category_id or None,
        origin_terminal=origin_terminal,
    )
    raw_meta = dict(data.get("raw") or {})
    if barcode_raw:
        raw_meta.setdefault("barcode_raw", barcode_raw)
        raw_meta.setdefault("barcode_symbology", barcode_symbology)
        raw_meta.setdefault("barcode_kind", barcode_kind)
    ld = (data.get("language_detected") or raw_meta.get("language_detected") or "").strip()
    if ld:
        raw_meta["language_detected"] = ld
    book = Book.objects.create(
        product=product, isbn13=isbn13, isbn10=data.get("isbn10", ""),
        subtitle=data.get("subtitle", ""), publisher=data.get("publisher", ""),
        series=data.get("series", ""), edition=data.get("edition", ""),
        languages=data.get("languages", []), pub_year=data.get("pub_year"),
        pages=data.get("pages"), dimensions=data.get("dimensions", ""),
        cover_type=data.get("cover_type", ""), subject=data.get("subject", ""),
        keywords=data.get("keywords", []), description=data.get("description", ""),
        source_provider=data.get("source", ""), ocr_confidence=data.get("confidence") or 0.0,
        raw_metadata=raw_meta,
    )
    for role, key in [(BookContributor.AUTHOR, "authors"), (BookContributor.TRANSLATOR, "translators")]:
        for i, name in enumerate(data.get(key, [])):
            if not name:
                continue
            contributor, _ = Contributor.objects.get_or_create(name=name)
            BookContributor.objects.get_or_create(
                book=book, contributor=contributor, role=role, defaults={"order": i})

    if image_ids:
        ProductImage.objects.filter(id__in=list(image_ids)).update(product=product)
        first = product.images.order_by("order", "created_at").first()
        if first:
            ProductImage.objects.filter(pk=first.pk).update(is_primary=True)

    if isbn13:
        Barcode.objects.get_or_create(value=isbn13, symbology="ISBN", defaults={"product": product})
    if barcode_raw and barcode_raw != isbn13:
        sym = barcode_symbology or (
            "EAN13" if barcode_raw.isdigit() and len(barcode_raw) in (12, 13) else "CODE128"
        )
        if barcode_kind == "isbn13":
            sym = "ISBN"
        Barcode.objects.get_or_create(
            value=barcode_raw, symbology=sym, defaults={"product": product},
        )
    # Optional secondary local codes (e.g. Math CNP side code 222231)
    for extra in data.get("extra_barcodes") or []:
        if isinstance(extra, dict):
            val = (extra.get("value") or "").strip()
            sym = (extra.get("symbology") or "CODE128").strip() or "CODE128"
        else:
            val = str(extra or "").strip()
            sym = "CODE128"
        if val and val not in (isbn13, barcode_raw):
            Barcode.objects.get_or_create(
                value=val, symbology=sym, defaults={"product": product},
            )
    return product
