"""Book-scan orchestration: ISBN-first, OCR-fallback → reviewable draft → create on save."""
import uuid

from django.db import transaction

from teyssir.core.money import to_money

from .draft import BookDraft
from .metadata import enrich_by_isbn
from .ocr import get_ocr_provider


def scan_book(image_paths, isbn="", enrich=enrich_by_isbn):
    """Produce a (BookDraft, ocr_text) from image path(s) + an optional ISBN.

    Strategy: OCR the first image (also to discover an ISBN if none was scanned); if we have an
    ISBN, enrich from a metadata provider (high confidence) and backfill any missing fields from
    OCR; otherwise return the OCR draft. Pure function of its inputs → easy to test/async.
    """
    ocr_text, ocr_draft = "", BookDraft()
    if image_paths:
        ocr_text, ocr_draft = get_ocr_provider().extract(image_paths[0])
        if not isbn and ocr_draft.isbn13:
            isbn = ocr_draft.isbn13

    draft = enrich(isbn) if isbn else None
    if draft is None:
        draft = ocr_draft
    elif image_paths:
        draft.merge(ocr_draft)
    if isbn and not draft.isbn13:
        draft.isbn13 = isbn
    return draft, ocr_text


@transaction.atomic
def create_book_from_draft(*, data, image_ids=(), sale_price="0", origin_terminal=""):
    """Create Product + Book + normalized Contributors from reviewed `data`, and link the draft
    images uploaded during the scan. Returns the new Product."""
    from teyssir.catalog.models import (
        Barcode, Book, BookContributor, Contributor, Product, ProductImage,
    )

    isbn13 = (data.get("isbn13") or "").strip()
    sku = (data.get("sku") or isbn13 or f"BK-{uuid.uuid4().hex[:10]}").strip()
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
