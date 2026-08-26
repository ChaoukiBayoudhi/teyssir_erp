"""Integer quantity helpers (pieces). Prices stay millime Decimals in ``money.py``.

Quantities are whole units only — no fractional stock for the current piece UOM.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


class QtyError(ValueError):
    """Non-integer or otherwise invalid quantity."""


def to_qty(value, *, allow_negative: bool = False, label: str = "qty") -> int:
    """Coerce to a whole-number quantity. Rejects fractional values (1.5, '2.3')."""
    if value is None or value == "":
        raise QtyError(f"{label} required")
    if isinstance(value, bool):
        raise QtyError(f"{label} invalid")
    if isinstance(value, int):
        n = value
    elif isinstance(value, float):
        if not value.is_integer():
            raise QtyError(f"{label} must be an integer (got {value})")
        n = int(value)
    else:
        text = str(value).strip().replace(",", ".")
        try:
            d = Decimal(text)
        except (InvalidOperation, ValueError) as exc:
            raise QtyError(f"{label} invalid") from exc
        if d != d.to_integral_value():
            raise QtyError(f"{label} must be an integer (got {value})")
        n = int(d)
    if not allow_negative and n < 0:
        raise QtyError(f"{label} cannot be negative ({n})")
    return n


def format_qty(value) -> str:
    """Render a quantity for API/UI without trailing decimals (1 not 1.000)."""
    try:
        return str(to_qty(value, allow_negative=True))
    except QtyError:
        # Best-effort for legacy fractional rows during transition.
        try:
            return str(int(Decimal(str(value))))
        except Exception:
            return "0"
