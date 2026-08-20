"""Book-scan orchestration: ISBN-first, multi-cover merge, title-search fallback → draft."""
import uuid

from django.db import transaction

from teyssir.core.money import require_non_negative_money

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
        if not out.languages and back.languages:
            out.languages = list(back.languages)
        out.raw["covers"] = {"front": True, "back": True}
        out.raw["back"] = {k: back.raw.get(k) for k in
                           ("isbn_detected", "isbn_not_detected", "price_detected", "ocr_langs")
                           if back.raw}

    # Confidence: boost when we have ISBN or a solid title
    if out.isbn13:
        out.confidence = max(front.confidence, (back.confidence if back else 0), 0.7)
    elif out.title:
        out.confidence = max(front.confidence, 0.35)
    else:
        out.confidence = max(front.confidence, (back.confidence if back else 0))

    if out.isbn13:
        out.raw["isbn_detected"] = True
        out.raw.pop("isbn_not_detected", None)
    else:
        out.raw["isbn_not_detected"] = True
    return out


def scan_book(image_paths, isbn="", enrich=enrich_by_isbn, enrich_title=enrich_by_title):
    """Produce a (BookDraft, ocr_text) from image path(s) + an optional ISBN.

    Multi-cover (Phase 6):
      * image[0] → front cover (title / author / language)
      * image[1] → back cover (ISBN / barcode / price)
      * merge → enrich by ISBN, else title search
      * optional Vision-LLM upgrade when Tesseract is weak and Ollama is up
    """
    isbn = to_isbn13(isbn) or (isbn or "").strip()
    provider = get_ocr_provider()
    texts = []
    front = BookDraft(raw={"isbn_not_detected": not bool(isbn)})
    back = None

    if image_paths:
        role0 = "front" if len(image_paths) > 1 else "auto"
        t0, front = provider.extract(image_paths[0], role=role0)
        texts.append(t0 or "")
        if not isbn and front.isbn13:
            isbn = to_isbn13(front.isbn13) or front.isbn13

        if len(image_paths) > 1:
            t1, back = provider.extract(image_paths[1], role="back")
            texts.append(t1 or "")
            if not isbn and back.isbn13:
                isbn = to_isbn13(back.isbn13) or back.isbn13

    ocr_draft = _merge_cover_drafts(front, back) if image_paths else front
    ocr_text = "\n---\n".join(texts)

    # Phone-camera covers often defeat Tesseract; upgrade via local Ollama vision when weak.
    if image_paths and _should_try_vision(ocr_draft, provider):
        try:
            from .ocr import VisionLlmOcrProvider

            v_text, v_front = VisionLlmOcrProvider().extract(image_paths[0], role="front")
            # Keep Tesseract back-cover ISBN/price; only upgrade bibliographic fields via vision.
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
                }
                ocr_draft = vision_draft
                if v_text:
                    ocr_text = f"{ocr_text}\n---\n{v_text}"
                if not isbn and ocr_draft.isbn13:
                    isbn = to_isbn13(ocr_draft.isbn13) or ocr_draft.isbn13
        except Exception as exc:
            ocr_draft.raw = {**(ocr_draft.raw or {}), "vision_fallback_error": str(exc)[:200]}

    draft = enrich(isbn) if isbn else None
    metadata_hit = draft is not None
    if draft is None:
        draft = ocr_draft
    else:
        draft.merge(ocr_draft)
        if ocr_draft.price and not draft.price:
            draft.price = ocr_draft.price

    # No ISBN: try title/author metadata search (local Tunisian editions)
    title_hit = False
    if not isbn and draft.title and draft.source in ("tesseract", "vision", "manual", ""):
        found = enrich_title(draft.title, (draft.authors or [""])[0] if draft.authors else "")
        if found:
            title_hit = True
            price = draft.price
            langs = list(draft.languages or [])
            raw_ocr = dict(draft.raw or {})
            found.merge(draft)  # search wins; OCR fills empty gaps
            if price:
                found.price = price
            if langs and not found.languages:
                found.languages = langs
            found.raw = {**(found.raw or {}), **raw_ocr, "title_search": True}
            draft = found

    if isbn:
        draft.isbn13 = draft.isbn13 or isbn
        draft.raw = {**(draft.raw or {}), "isbn_detected": True}
        if not metadata_hit:
            draft.raw["metadata_miss"] = True
    else:
        draft.raw = {**(draft.raw or {}), "isbn_not_detected": True}
        if draft.title and not title_hit and not metadata_hit:
            draft.raw["manual_assist"] = True

    return draft, ocr_text


def _should_try_vision(draft, provider) -> bool:
    """Use local Vision-LLM when primary OCR is empty/weak (phone photos of covers)."""
    from django.conf import settings

    if getattr(provider, "name", "") != "tesseract":
        return False
    if not getattr(settings, "OCR_VISION_FALLBACK", True):
        return False
    if draft.isbn13 and (draft.confidence or 0) >= 0.7 and draft.title:
        return False
    if (draft.confidence or 0) >= 0.55 and draft.title and len(draft.title) >= 6:
        return False
    # Empty / manual / very weak tesseract
    if draft.raw.get("ocr_available") is False:
        return True
    if (draft.confidence or 0) < 0.5:
        return True
    if not (draft.title or "").strip():
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
        is_book=True, isbn=isbn13,
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
