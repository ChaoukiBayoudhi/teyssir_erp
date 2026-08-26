"""Tunisian cover-price extraction (DT → millime-scale Decimal string)."""
from __future__ import annotations

import re

from teyssir.core.money import to_money

# Require currency cue OR millime-style decimals — avoid bare ISBN digits.
_PRICE_RES = [
    # Explicit currency / label
    re.compile(
        r"(?:prix|price|سعر|الثمن)\s*[:=]?\s*"
        r"(\d{1,4}(?:[.,]\d{1,3})?)\s*(?:dt|d\.?t\.?|tnd|د\.?\s*ت\.?|دينار)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(\d{1,4}(?:[.,]\d{1,3})?)\s*(?:dt|d\.?t\.?|tnd|د\.?\s*ت\.?|دينار)\b",
        re.IGNORECASE,
    ),
    # Millime retail style: 12.500 / 12,500 (exactly 3 fractional digits)
    re.compile(r"\b(\d{1,3}[.,]\d{3})\b"),
]


def extract_price_dt(text: str) -> str:
    """Return a millime-quantized TND amount as string, or '' if none found."""
    if not text:
        return ""
    candidates: list[str] = []
    for rx in _PRICE_RES:
        for m in rx.finditer(text):
            raw = m.group(1).replace(",", ".")
            try:
                amount = to_money(raw)
            except Exception:
                continue
            # Bookstore covers: ignore absurd outliers / bare years
            if amount <= 0 or amount > 500:
                continue
            if amount >= 1900 and amount <= 2100 and "." not in raw:
                continue
            candidates.append(str(amount))
    if not candidates:
        return ""
    # Prefer amounts with millime precision (3 dp) when present
    for c in candidates:
        if "." in c and len(c.split(".")[-1]) == 3 and c.split(".")[-1] != "000":
            return c
    for c in candidates:
        if "." in c and len(c.split(".")[-1]) == 3:
            return c
    return candidates[0]
