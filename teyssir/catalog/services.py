"""Catalogue services — register ANY article (book or supply) from a scanned barcode.

The barcode is read either by a hardware USB/Bluetooth scanner (which types the digits + Enter into
the barcode field) or by the camera. A book gets its rich bibliographic profile via the OCR flow
(bookscan.services); this creates the plain Product + Barcode + optional opening stock that every
article — books and school/office supplies (fournitures) alike — needs to be sold at the POS.
"""
import uuid
from decimal import InvalidOperation

from django.db import transaction

from teyssir.core.money import require_non_negative_money, to_money
from teyssir.core.qty import to_qty

from .models import Barcode, Product


def _dec(value, default="0"):
    try:
        return to_money(value if value not in (None, "") else default)
    except (InvalidOperation, ValueError, TypeError):
        return to_money(default)


@transaction.atomic
def create_product(*, name_fr, name_ar="", category_id=None, tax_rate_id=None, sale_price="0",
                   is_book=False, barcode="", symbology="", initial_qty="0", cost="0",
                   reorder_point="0", origin_terminal=""):
    """Create a product and (if given) its barcode + opening stock. Raises ValueError if the
    barcode already belongs to another product (so a scan can't silently duplicate an article)."""
    barcode = (barcode or "").strip()
    if barcode:
        clash = Barcode.objects.filter(value=barcode).select_related("product").first()
        if clash:
            raise ValueError(f"Le code-barres {barcode} est déjà attribué à « {clash.product.name_fr} ».")

    sku = barcode or f"ART-{uuid.uuid4().hex[:10].upper()}"
    if Product.objects.filter(sku=sku).exists():                 # barcode reused as sku elsewhere
        sku = f"ART-{uuid.uuid4().hex[:10].upper()}"

    price = require_non_negative_money(sale_price or 0, label="sale_price")
    reorder = to_qty(reorder_point or 0, label="reorder_point") if str(reorder_point or "0") not in ("",) else 0
    product = Product.objects.create(
        sku=sku, name_fr=name_fr.strip(), name_ar=(name_ar or "").strip(),
        category_id=category_id or None, tax_rate_id=tax_rate_id or None,
        sale_price=price, reorder_point=reorder,
        is_book=bool(is_book), origin_terminal=origin_terminal or "",
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
