"""Book-scan orchestration: ISBN-first (barcode → enrich), multi-cover merge, title fallback."""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from concurrent.futures import as_completed

from django.db import transaction

from teyssir.core.money import require_non_negative_money

from .barcode import decode_isbn_with_source
from .draft import BookDraft
from .isbn import to_isbn13
from .metadata import enrich_by_isbn, enrich_by_title
from .ocr import get_ocr_provider


def _merge_cover_drafts(front: BookDraft, back: BookDraft | None) -> BookDraft:
    """Combine front (title/author) + back (ISBN/price) into one reviewable draft."""
    out = BookDraft(source=front.source or (back.source if back else ""), confidence=0.0)
    # Bibliographic identity from front
    for key in ("title", "subtitle", "publisher", "series", "edition", "subject",
                "description", "isbn13", "isbn10", "price"):
        setattr(out, key, getattr(front, key) or "")
    out.authors = list(front.authors or [])
    out.translators = list(front.translators or [])
    out.languages = list(front.languages or [])
    out.pub_year = front.pub_year
    out.pages = front.pages
    out.raw = {**(front.raw or {}), "covers": {"front": True}}

    if back:
        # Back wins for ISBN + price; fills empty bib fields
        if back.isbn13:
            out.isbn13 = back.isbn13
        if back.price:
            out.price = back.price
        if back.isbn10 and not out.isbn10:
            out.isbn10 = back.isbn10
        for key in ("title", "subtitle", "publisher", "subject", "description"):
            if not getattr(out, key) and getattr(back, key):
                setattr(out, key, getattr(back, key))
        if not out.authors and back.authors:
            out.authors = list(back.authors)
        # Union language tags from both covers (bilingual FR+AR)
        if back.languages:
            merged_langs = list(out.languages or [])
            for lang in back.languages:
                if lang not in merged_langs:
                    merged_langs.append(lang)
            out.languages = merged_langs
        out.raw["covers"] = {"front": True, "back": True}
        out.raw["back"] = {k: back.raw.get(k) for k in
                           ("isbn_detected", "isbn_not_detected", "price_detected",
                            "isbn_from_barcode", "isbn_from_digit_ocr", "ocr_langs")
                           if back.raw}
        if back.raw.get("isbn_from_barcode"):
            out.raw["isbn_from_barcode"] = True
        if back.raw.get("isbn_from_digit_ocr"):
            out.raw["isbn_from_digit_ocr"] = True

    from .ocr import is_usable_ocr_title, merge_bilingual_title

    # Prefer bilingual merge when front/back each have one script
    if front.title and back and back.title:
        merged = merge_bilingual_title(front.title, back.title)
        if merged and ("(" in merged or "/" in merged):
            out.title = merged
            out.raw["bilingual_title"] = True

    # Confidence: real barcode ISBN is high-trust; digit OCR is not
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
    return out


def _barcode_isbn_from_paths(image_paths, prepared=None) -> tuple[str, str]:
    """Try barcode (+ digit OCR) on all images (prefer last = verso).

    Returns ``(isbn13, source)`` with ``source`` in ``barcode`` | ``digit_ocr`` | ``''``.
    Prefers a real pyzbar hit over digit OCR across all frames.
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


def _extract_pair(provider, image_paths):
    """OCR front (+ optional back) in parallel when two covers are present."""
    texts: list[str] = []
    front = BookDraft(raw={"isbn_not_detected": True})
    back = None

    if not image_paths:
        return texts, front, back

    role0 = "front" if len(image_paths) > 1 else "auto"

    if len(image_paths) == 1:
        t0, front = provider.extract(image_paths[0], role=role0)
        texts.append(t0 or "")
        return texts, front, back

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_front = pool.submit(provider.extract, image_paths[0], role0)
        fut_back = pool.submit(provider.extract, image_paths[1], "back")
        results = {}
        for fut in as_completed([fut_front, fut_back]):
            results[fut] = fut.result()
        t0, front = results[fut_front]
        t1, back = results[fut_back]
        texts.extend([t0 or "", t1 or ""])
    return texts, front, back


def scan_book(image_paths, isbn="", enrich=enrich_by_isbn, enrich_title=enrich_by_title):
    """Produce a (BookDraft, ocr_text) from image path(s) + an optional ISBN.

    Multi-cover (Phase 6):
      * Phase 2A preprocess (orient / crop / deskew / CLAHE / band ROIs) first
      * barcode decode on images first (ISBN-13 from EAN)
      * image[0] → front cover (title / author / language)
      * image[1] → back cover (ISBN / barcode / price)
      * merge → enrich by ISBN, else cautious title search
      * optional Vision-LLM only when OCR has no usable title/ISBN (short timeout)
    """
    from .preprocess import prepared_cover_paths

    # Client / caller hint: only accept checksum-valid bookland ISBNs
    client_raw = (isbn or "").strip()
    isbn = to_isbn13(client_raw) or ""
    client_isbn_hint = bool(isbn)
    if client_raw and not isbn:
        client_isbn_hint = False

    provider = get_ocr_provider()

    # Phase 2A: rectify covers before barcode + OCR (temps cleaned on exit).
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
):
    """Inner scan on already-preprocessed paths (+ optional ROI metadata)."""
    # ISBN-first: real barcode beats OCR digits and wrong title-only OL hits.
    barcode_isbn = ""
    isbn_source = ""
    if not isbn and image_paths:
        barcode_isbn, isbn_source = _barcode_isbn_from_paths(image_paths, prepared)
        isbn = barcode_isbn

    texts, front, back = _extract_pair(provider, image_paths)
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

    ocr_draft = _merge_cover_drafts(front, back) if image_paths else front
    ocr_text = "\n---\n".join(texts)
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

    # Phone-camera covers often defeat Tesseract; upgrade via local Ollama vision when weak.
    if image_paths and _should_try_vision(ocr_draft, provider):
        ocr_draft = _maybe_vision_upgrade(image_paths, ocr_draft, back)
        if not isbn and ocr_draft.isbn13:
            isbn = to_isbn13(ocr_draft.isbn13) or ""
        # Append vision text marker if present
        if ocr_draft.raw.get("vision_fallback") and ocr_draft.raw.get("vision_text"):
            ocr_text = f"{ocr_text}\n---\n{ocr_draft.raw.get('vision_text')}"

    draft = enrich(isbn) if isbn else None
    metadata_hit = draft is not None
    if draft is None:
        draft = ocr_draft
    else:
        draft.merge(ocr_draft)
        if ocr_draft.price and not draft.price:
            draft.price = ocr_draft.price
        # Prefer barcode/OCR ISBN; keep OCR price
        if isbn:
            draft.isbn13 = draft.isbn13 or isbn
        # Metadata by ISBN is high-trust — reflect that in confidence/source
        draft.confidence = max(draft.confidence or 0, 0.85)
        if ocr_draft.raw.get("isbn_from_barcode") or client_isbn_hint:
            draft.raw = {**(draft.raw or {}), "isbn_from_barcode": True}
            draft.confidence = max(draft.confidence or 0, 0.9)
        # Confirmed digit-OCR ISBN via OpenLibrary → upgrade trust
        if ocr_draft.raw.get("isbn_from_digit_ocr"):
            draft.raw = {
                **(draft.raw or {}),
                "isbn_from_digit_ocr": True,
                "isbn_digit_ocr_confirmed": True,
            }

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
            # OCR title/authors stay unless search is a strong match (see metadata)
            strong = (found.confidence or 0) >= 0.55 and not found.raw.get("title_search_weak")
            if strong:
                found.merge(draft)  # search wins bibliographic fields; OCR fills gaps
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
            # Prefer OCR script tags (ar) over OL defaulting to eng from a bad query
            if langs:
                found.languages = langs
            elif not found.languages:
                found.languages = langs
            found.raw = {
                **(found.raw or {}), **raw_ocr, "title_search": True,
                **({"title_search_weak": True} if not strong else {}),
            }
            draft = found
    elif not isbn and draft.title and not title_ok:
        draft.raw = {
            **(draft.raw or {}),
            "title_search_skipped_garbage": True,
        }

    if isbn:
        draft.isbn13 = draft.isbn13 or isbn
        draft.raw = {**(draft.raw or {}), "isbn_detected": True}
        draft.raw.pop("isbn_not_detected", None)
        if not metadata_hit:
            draft.raw["metadata_miss"] = True
            # Digit-OCR / unverified ISBN without OpenLibrary — clear or demote
            from_barcode = bool(
                draft.raw.get("isbn_from_barcode")
                or client_isbn_hint
                or isbn_source == "barcode"
            )
            from_digit = bool(
                draft.raw.get("isbn_from_digit_ocr") or isbn_source == "digit_ocr"
            )
            if from_digit and not from_barcode:
                draft.raw["suggested_isbn"] = draft.isbn13
                draft.raw["isbn_unconfirmed"] = True
                draft.isbn13 = ""
                draft.confidence = min(draft.confidence or 0.2, 0.25)
                draft.raw["isbn_not_detected"] = True
                draft.raw.pop("isbn_detected", None)
            elif not from_barcode:
                # Unknown-source ISBN + metadata miss → low confidence
                draft.raw["suggested_isbn"] = draft.isbn13
                draft.raw["isbn_unconfirmed"] = True
                draft.confidence = min(draft.confidence or 0.3, 0.35)
    else:
        draft.raw = {**(draft.raw or {}), "isbn_not_detected": True}
        if draft.title and not title_hit and not metadata_hit:
            draft.raw["manual_assist"] = True
        # Never advertise high confidence without ISBN from fuzzy title alone
        if draft.raw.get("title_search") and not draft.isbn13:
            draft.confidence = min(draft.confidence or 0.4, 0.45)

    return draft, ocr_text


def _maybe_vision_upgrade(image_paths, ocr_draft, back):
    """Run Vision-LLM with a hard timeout; never block the scan for long.

    Arabic / garbage OCR gets a longer timeout (calligraphy covers need it).
    Also tries the verso frame for ISBN when front vision misses it.
    """
    from django.conf import settings

    from .ocr import VisionLlmOcrProvider, arabic_char_ratio

    base_timeout = float(getattr(settings, "VISION_FALLBACK_TIMEOUT", 28) or 28)
    raw = ocr_draft.raw or {}
    arabic_hard = bool(
        raw.get("ocr_garbage_arabic")
        or raw.get("ocr_garbage_latin")
        or raw.get("ocr_arabic_likely")
        or raw.get("tess_missing_ara")
        or ("ar" in (ocr_draft.languages or []))
        or arabic_char_ratio(ocr_draft.title or "") >= 0.15
    )
    timeout = base_timeout * (1.6 if arabic_hard else 1.0)
    timeout = min(timeout, float(getattr(settings, "VISION_TIMEOUT", 45) or 45))

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(VisionLlmOcrProvider().extract, image_paths[0], "front")
            try:
                v_text, v_front = fut.result(timeout=timeout)
            except FuturesTimeout:
                fut.cancel()
                ocr_draft.raw = {
                    **(ocr_draft.raw or {}),
                    "vision_fallback_error": f"timeout after {timeout}s",
                }
                return ocr_draft
    except Exception as exc:
        ocr_draft.raw = {**(ocr_draft.raw or {}), "vision_fallback_error": str(exc)[:200]}
        return ocr_draft

    # Optional verso pass for ISBN when front vision has none
    if not v_front.isbn13 and len(image_paths) > 1:
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                fut_b = pool.submit(VisionLlmOcrProvider().extract, image_paths[1], "back")
                try:
                    _, v_back = fut_b.result(timeout=min(timeout, 20))
                    if v_back.isbn13:
                        v_front.isbn13 = v_back.isbn13
                        v_front.raw = {
                            **(v_front.raw or {}),
                            "vision_isbn_from_verso": True,
                        }
                    if v_back.price and not v_front.price:
                        v_front.price = v_back.price
                except FuturesTimeout:
                    fut_b.cancel()
        except Exception:
            pass

    vision_draft = _merge_cover_drafts(v_front, back if back and back.source != "manual" else None)
    if not vision_draft.isbn13 and back and back.isbn13:
        vision_draft.isbn13 = back.isbn13
    if not vision_draft.price and back and back.price:
        vision_draft.price = back.price
    if vision_draft.title or vision_draft.isbn13 or vision_draft.confidence > ocr_draft.confidence:
        vision_draft.raw = {
            **(ocr_draft.raw or {}),
            **(vision_draft.raw or {}),
            "vision_fallback": True,
            "tesseract_title": ocr_draft.title,
            "vision_text": (v_text or "")[:2000],
        }
        return vision_draft
    return ocr_draft


def _should_try_vision(draft, provider) -> bool:
    """Use local Vision-LLM when primary OCR has no *usable* title/ISBN.

    Garbage Latin/Arabic titles must NOT block Vision — they are not usable
    bibliographic text. Prefer Vision for Arabic covers when tess conf < 0.5.
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
    # Real barcode ISBN → skip vision (metadata will enrich)
    if draft.isbn13 and (draft.raw or {}).get("isbn_from_barcode"):
        return False
    # Digit-OCR ISBN is unverified — still allow Vision for title / better ISBN
    if draft.isbn13 and not (draft.raw or {}).get("isbn_from_digit_ocr"):
        return False
    title = (draft.title or "").strip()
    mean_conf = None
    try:
        mean_conf = float((draft.raw or {}).get("ocr_mean_confidence") or 0) or None
    except (TypeError, ValueError):
        mean_conf = None
    raw = draft.raw or {}
    # Explicit garbage / Arabic-likely / missing ara → always try vision
    if (
        raw.get("ocr_garbage_latin")
        or raw.get("ocr_garbage_arabic")
        or raw.get("ocr_arabic_likely")
        or raw.get("ocr_title_unusable")
        or raw.get("tess_missing_ara")
        or (title and is_garbage_latin_ocr(title, mean_conf=mean_conf))
        or (title and is_garbage_arabic_ocr(title, mean_conf=mean_conf))
    ):
        return True
    # Arabic script + weak tess confidence → Vision (calligraphy covers)
    is_ar = (
        "ar" in (draft.languages or [])
        or raw.get("arabic_script_detected")
        or arabic_char_ratio(title) >= 0.15
    )
    if is_ar and (draft.confidence or 0) < 0.5:
        return True
    if is_ar and mean_conf is not None and mean_conf < 50:
        return True
    # Usable Latin/French title — prefer fast path + optional title search over vision
    if is_usable_ocr_title(title, mean_conf=mean_conf):
        return False
    if (draft.confidence or 0) >= 0.55 and is_usable_ocr_title(title, mean_conf=mean_conf):
        return False
    if draft.raw.get("ocr_available") is False:
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

    isbn13 = to_isbn13(data.get("isbn13") or "") or (data.get("isbn13") or "").strip()
    sku = (data.get("sku") or isbn13 or f"BK-{uuid.uuid4().hex[:10]}").strip()
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
    book = Book.objects.create(
        product=product, isbn13=isbn13, isbn10=data.get("isbn10", ""),
        subtitle=data.get("subtitle", ""), publisher=data.get("publisher", ""),
        series=data.get("series", ""), edition=data.get("edition", ""),
        languages=data.get("languages", []), pub_year=data.get("pub_year"),
        pages=data.get("pages"), dimensions=data.get("dimensions", ""),
        cover_type=data.get("cover_type", ""), subject=data.get("subject", ""),
        keywords=data.get("keywords", []), description=data.get("description", ""),
        source_provider=data.get("source", ""), ocr_confidence=data.get("confidence") or 0.0,
        raw_metadata=data.get("raw", {}),
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
    return product
