"""Unified product search for POS and catalogue (name, reference, barcode/SKU/ISBN).

Ranking: exact identifier/name → startswith → contains. Limit defaults to 20.
Numeric-ish queries prefer barcode / reference / SKU / ISBN exact matches first.
"""
from __future__ import annotations

import re

from django.db.models import Case, IntegerField, Q, QuerySet, Value, When

from .models import Barcode, Product

# Digits-heavy codes (EAN/ISBN/numeric refs) and short alphanumeric refs (PEN-001, TZ-9).
_NUMERICISH_RE = re.compile(r"^\d{4,}$")
_CODEISH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$")

POS_SEARCH_LIMIT = 20


def normalize_query(q: str | None) -> str:
    return (q or "").strip()


def looks_like_code(q: str) -> bool:
    """True when the query is likely a barcode, SKU, reference, or ISBN (not free text)."""
    q = normalize_query(q)
    if not q or any(ch.isspace() for ch in q):
        return False
    if _NUMERICISH_RE.match(q):
        return True
    if _CODEISH_RE.match(q) and any(ch.isdigit() for ch in q):
        return True
    return False


def lookup_by_code(code: str, *, base: QuerySet | None = None) -> QuerySet:
    """Exact barcode / SKU / reference / ISBN match (POS hardware scanner path)."""
    code = normalize_query(code)
    qs = base if base is not None else Product.objects.filter(active=True).select_related("tax_rate")
    if not code:
        return qs.none()
    ids = list(Barcode.objects.filter(value=code).values_list("product_id", flat=True))
    return qs.filter(
        Q(id__in=ids) | Q(sku__iexact=code) | Q(reference__iexact=code) | Q(isbn__iexact=code)
    ).distinct()


def _match_q(q: str) -> Q:
    bc_ids = list(
        Barcode.objects.filter(value__icontains=q).values_list("product_id", flat=True)[:200]
    )
    return (
        Q(name_fr__icontains=q)
        | Q(name_ar__icontains=q)
        | Q(sku__icontains=q)
        | Q(reference__icontains=q)
        | Q(isbn__icontains=q)
        | Q(internal_code__icontains=q)
        | Q(id__in=bc_ids)
    )


def _rank_annotation(q: str):
    exact_bc = list(Barcode.objects.filter(value__iexact=q).values_list("product_id", flat=True)[:50])
    return Case(
        When(
            Q(sku__iexact=q) | Q(reference__iexact=q) | Q(isbn__iexact=q) | Q(id__in=exact_bc),
            then=Value(0),
        ),
        When(Q(name_fr__iexact=q) | Q(name_ar__iexact=q), then=Value(1)),
        When(
            Q(name_fr__istartswith=q)
            | Q(name_ar__istartswith=q)
            | Q(sku__istartswith=q)
            | Q(reference__istartswith=q)
            | Q(isbn__istartswith=q),
            then=Value(2),
        ),
        default=Value(3),
        output_field=IntegerField(),
    )


def search_products(q: str | None, *, limit: int = POS_SEARCH_LIMIT, base: QuerySet | None = None) -> QuerySet:
    """Partial, case-insensitive search across name (FR/AR), SKU, reference, ISBN, barcode.

    When ``q`` looks like a code, exact identifier hits are ranked first (still includes
    name contains so a typed fragment can surface names that share digits).
    """
    q = normalize_query(q)
    qs = base if base is not None else Product.objects.filter(active=True).select_related("tax_rate")
    if not q:
        return qs.none()

    # Code-like: surface exact barcode/ref hits first, then broader contains.
    if looks_like_code(q):
        exact = lookup_by_code(q, base=qs)
        if exact.exists():
            # Prefer exact identifiers; still annotate for stable ordering when multiple.
            return exact.annotate(rank=Value(0, output_field=IntegerField())).order_by("name_fr")[:limit]

    matched = qs.filter(_match_q(q)).distinct().annotate(rank=_rank_annotation(q))
    return matched.order_by("rank", "name_fr")[:limit]


def catalog_text_filter(qs: QuerySet, q: str) -> QuerySet:
    """Broader catalogue browser filter (also authors / book metadata). Caller paginates."""
    from .models import BookContributor

    q = normalize_query(q)
    if not q:
        return qs
    barcode_ids = list(Barcode.objects.filter(value__icontains=q).values_list("product_id", flat=True))
    author_ids = list(
        BookContributor.objects.filter(contributor__name__icontains=q)
        .values_list("book__product_id", flat=True)
    )
    return qs.filter(
        _match_q(q)
        | Q(book__isbn13__icontains=q)
        | Q(book__publisher__icontains=q)
        | Q(book__subtitle__icontains=q)
        | Q(id__in=author_ids)
        | Q(id__in=barcode_ids)
    ).distinct()
