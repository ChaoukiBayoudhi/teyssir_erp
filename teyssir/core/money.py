"""
Money for Teyssir — Tunisian Dinar (TND).

Storage = millime-exact amounts. 1 DT = 1000 millimes. A 0.850 DT pen is
unrepresentable at 2 dp, so every amount is quantized to the millime.

Internally we keep Django ``Decimal(14,3)`` columns (1.000 DT ≡ 1000 millimes) for
backward-compatible ORM/migrations, but **all arithmetic** goes through integer
millimes via ``to_millimes`` / ``from_millimes`` so there is no floating drift.
Display rounds HALF_UP to 2 dp for UI/receipts. ``float`` is never used for money
(spec §7.2).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

CURRENCY = "TND"
STORE_Q = Decimal("0.001")    # storage quantum (millime)
DISPLAY_Q = Decimal("0.01")   # display quantum
MILLIMES_PER_DT = 1000
# Tunisian fiscal TVA rates (whitelist for validation).
TUNISIA_VAT_RATES = frozenset({Decimal("0"), Decimal("7"), Decimal("13"), Decimal("19")})


def to_money(value) -> Decimal:
    """Coerce any value to a TND amount at storage scale (3 dp), ROUND_HALF_UP.

    A ``float`` is accepted but routed through ``str`` first to avoid binary-float
    artifacts (e.g. 0.1 + 0.2). Prefer passing ``Decimal``/``str``/``int``.
    """
    if isinstance(value, float):
        value = str(value)
    return Decimal(value).quantize(STORE_Q, rounding=ROUND_HALF_UP)


def to_millimes(value) -> int:
    """Convert a TND amount to an integer millime count (exact, no float)."""
    return int(to_money(value) * MILLIMES_PER_DT)


def from_millimes(millimes: int) -> Decimal:
    """Convert an integer millime count back to a Decimal(14,3) TND amount."""
    return (Decimal(int(millimes)) / MILLIMES_PER_DT).quantize(STORE_Q, rounding=ROUND_HALF_UP)


def add_money(*values) -> Decimal:
    """Sum amounts in integer millimes (exact), return Decimal millime scale."""
    return from_millimes(sum(to_millimes(v) for v in values))


def sub_money(a, b) -> Decimal:
    """Subtract b from a in integer millimes."""
    return from_millimes(to_millimes(a) - to_millimes(b))


def mul_qty_price(qty, unit_price) -> Decimal:
    """qty × unit_price via millime-exact quantization of the result."""
    return to_money(Decimal(qty) * to_money(unit_price))


def display(value) -> str:
    """Render an amount at 2 dp for receipts/UI (storage stays lossless at 3 dp)."""
    return f"{Decimal(value).quantize(DISPLAY_Q, rounding=ROUND_HALF_UP):.2f}"


def line_tax(base, rate_percent) -> Decimal:
    """TVA on a base amount at a percentage rate (7/13/19/0), quantized to the millime.

    Computed as integer millimes: tax_millimes = round_half_up(base_m * rate / 100).
    """
    base_m = to_millimes(base)
    rate = Decimal(rate_percent)
    # HALF_UP on the millime: (base_m * rate) / 100, then quantize.
    raw = (Decimal(base_m) * rate) / Decimal(100)
    tax_m = int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return from_millimes(tax_m)


def normalize_vat_rate(rate_percent) -> Decimal:
    """Normalize a TVA rate to 2 dp; used when snapshotting onto sale lines."""
    return Decimal(rate_percent).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def is_allowed_vat_rate(rate_percent) -> bool:
    """True if rate is one of the Tunisian fiscal rates (0/7/13/19), ignoring .00 padding."""
    allowed = {normalize_vat_rate(x) for x in TUNISIA_VAT_RATES}
    return normalize_vat_rate(rate_percent) in allowed
