"""Catalogue services — register ANY article (book or furniture/supply).

Books are identified by ISBN (OCR flow in bookscan.services). Furniture and school/office
supplies are identified by a unique *reference* (numeric or alphanumeric). A barcode is
optional on furniture and must never trigger ISBN logic.
"""
import re
import uuid
from decimal import InvalidOperation

from django.db import transaction
from django.db.models import Q

from teyssir.core.money import require_non_negative_money, to_money
from teyssir.core.qty import to_qty

from .models import Barcode, Product

# 1001, SAC-001, PEN.12 — no spaces; letters, digits, . _ -
_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$")


def normalize_reference(value):
    return (value or "").strip()


def _dec(value, default="0"):
    try:
        return to_money(value if value not in (None, "") else default)
    except (InvalidOperation, ValueError, TypeError):
        return to_money(default)


def _resolve_type(*, is_book, product_type, isbn=""):
    raw = (product_type or "").strip().lower()
    if raw in (Product.BOOK, Product.FURNITURE):
        return raw
    if is_book or (isbn or "").strip():
        return Product.BOOK
    return Product.FURNITURE


@transaction.atomic
def create_product(*, name_fr, name_ar="", category_id=None, tax_rate_id=None, sale_price="0",
                   is_book=False, product_type="", reference="", color="", brand="",
                   barcode="", symbology="", initial_qty="0", cost="0",
                   reorder_point="0", isbn="", origin_terminal=""):
    """Create a product and (if given) its barcode + opening stock.

    Furniture requires a unique reference. Duplicate reference or barcode raises ValueError.
    """
    barcode = (barcode or "").strip()
    isbn = (isbn or "").strip()
    ptype = _resolve_type(is_book=is_book, product_type=product_type, isbn=isbn)
    is_book = ptype == Product.BOOK
    reference = normalize_reference(reference)

    if barcode:
        clash = Barcode.objects.filter(value=barcode).select_related("product").first()
        if clash:
            raise ValueError(f"Le code-barres {barcode} est déjà attribué à « {clash.product.name_fr} ».")

    if not is_book:
        if not reference:
            reference = barcode
        if not reference:
            raise ValueError("La référence est obligatoire pour un article (fourniture).")
        if not _REFERENCE_RE.match(reference):
            raise ValueError("Référence invalide — utilisez des lettres, chiffres, ., _ ou - (ex. 1001, SAC-001).")
        taken = Product.objects.filter(Q(reference__iexact=reference) | Q(sku__iexact=reference)).exists()
        if taken:
            raise ValueError(f"La référence « {reference} » existe déjà.")
        sku = reference
    else:
        sku = barcode or isbn or f"ART-{uuid.uuid4().hex[:10].upper()}"
        if Product.objects.filter(sku=sku).exists():
            sku = f"ART-{uuid.uuid4().hex[:10].upper()}"
        reference = ""

    price = require_non_negative_money(sale_price or 0, label="sale_price")
    reorder = to_qty(reorder_point or 0, label="reorder_point") if str(reorder_point or "0") not in ("",) else 0
    product = Product.objects.create(
        sku=sku, reference=reference,
        name_fr=name_fr.strip(), name_ar=(name_ar or "").strip(),
        category_id=category_id or None, tax_rate_id=tax_rate_id or None,
        sale_price=price, reorder_point=reorder,
        product_type=ptype, is_book=is_book, isbn=isbn if is_book else "",
        color=(color or "").strip(), brand=(brand or "").strip(),
        origin_terminal=origin_terminal or "",
    )
    if barcode:
        Barcode.objects.create(
            product=product, value=barcode,
            symbology=(symbology or ("ISBN" if is_book else "EAN13")),
        )
    qty = to_qty(initial_qty or 0, label="initial_qty")
    if qty > 0:
        from teyssir.purchasing.services import receive_goods   # rolls weighted-avg cost + ledger
        receive_goods(product_id=product.id, qty=qty, unit_cost=require_non_negative_money(cost or 0, label="cost"))
    return product


@transaction.atomic
def update_product(product, *, name_fr=None, name_ar=None, category_id=None, tax_rate_id=None,
                   sale_price=None, reorder_point=None, reference=None, color=None, brand=None,
                   isbn=None, clear_category=False, clear_tax_rate=False):
    """Update catalogue fields for a book or furniture/supply product.

    Stock qty is intentionally not edited here (stocktake / receiving own that path).
    Furniture reference uniqueness is enforced; books keep ISBN on Product (+ Book row if any).
    """
    if name_fr is not None:
        name_fr = (name_fr or "").strip()
        if not name_fr:
            raise ValueError("name_fr is required")
        product.name_fr = name_fr
    if name_ar is not None:
        product.name_ar = (name_ar or "").strip()
    if clear_category:
        product.category_id = None
    elif category_id is not None:
        product.category_id = category_id or None
    if clear_tax_rate:
        product.tax_rate_id = None
    elif tax_rate_id is not None:
        product.tax_rate_id = tax_rate_id or None
    if sale_price is not None and sale_price != "":
        product.sale_price = require_non_negative_money(sale_price, label="sale_price")
    if reorder_point is not None and reorder_point != "":
        product.reorder_point = to_qty(reorder_point, label="reorder_point")
    if color is not None:
        product.color = (color or "").strip()
    if brand is not None:
        product.brand = (brand or "").strip()

    is_book = product.is_book or product.product_type == Product.BOOK
    if is_book:
        if isbn is not None:
            product.isbn = (isbn or "").strip()
            book = getattr(product, "book", None)
            if book is not None:
                book.isbn13 = product.isbn
                book.save(update_fields=["isbn13", "updated_at"])
    elif reference is not None:
        reference = normalize_reference(reference)
        if not reference:
            raise ValueError("La référence est obligatoire pour un article (fourniture).")
        if not _REFERENCE_RE.match(reference):
            raise ValueError(
                "Référence invalide — utilisez des lettres, chiffres, ., _ ou - (ex. 1001, SAC-001)."
            )
        taken = (
            Product.objects.filter(Q(reference__iexact=reference) | Q(sku__iexact=reference))
            .exclude(pk=product.pk)
            .exists()
        )
        if taken:
            raise ValueError(f"La référence « {reference} » existe déjà.")
        product.reference = reference
        product.sku = reference

    product.save()
    return product


@transaction.atomic
def deactivate_product(product):
    """Soft-delete: hide from catalogue / POS search. Hard delete is unsafe (sales PROTECT)."""
    if not product.active:
        return product
    product.active = False
    product.save(update_fields=["active", "updated_at"])
    return product
