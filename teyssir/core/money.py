"""
Money for Teyssir — Tunisian Dinar (TND).

Storage scale = 3 decimals (the *millime*: 1 DT = 1000 millimes). A 0.850 DT pen is
unrepresentable at 2 dp, so we store at 3 dp (lossless) and *display* at 2 dp. All
rounding is ROUND_HALF_UP. `float` is never used for money (spec §7.2).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

CURRENCY = "TND"
STORE_Q = Decimal("0.001")    # storage quantum (millime)
DISPLAY_Q = Decimal("0.01")   # display quantum


def to_money(value) -> Decimal:
    """Coerce any value to a TND amount at storage scale (3 dp), ROUND_HALF_UP.

    A ``float`` is accepted but routed through ``str`` first to avoid binary-float
    artifacts (e.g. 0.1 + 0.2). Prefer passing ``Decimal``/``str``/``int``.
    """
    if isinstance(value, float):
        value = str(value)
    return Decimal(value).quantize(STORE_Q, rounding=ROUND_HALF_UP)


def display(value) -> str:
    """Render an amount at 2 dp for receipts/UI (storage stays lossless at 3 dp)."""
    return f"{Decimal(value).quantize(DISPLAY_Q, rounding=ROUND_HALF_UP):.2f}"


def line_tax(base, rate_percent) -> Decimal:
    """TVA on a base amount at a percentage rate (7/13/19/0), quantized to the millime."""
    return to_money(Decimal(base) * Decimal(rate_percent) / Decimal(100))
