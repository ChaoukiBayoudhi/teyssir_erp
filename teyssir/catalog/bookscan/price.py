"""Tunisian cover-price extraction (DT → millime-scale Decimal string)."""
from __future__ import annotations

import re

from teyssir.core.money import to_money

# Millime retail style: 12.500 / 12,500 (exactly 3 fractional digits).
# Reject implausible millime tails (e.g. 8.043 / 9.255 from OCR noise).
_BARE_MILLIME_RE = re.compile(r"\b(\d{1,3}[.,](\d{3}))\b")
_PLAUSIBLE_MILLIME_ENDS = frozenset(
    {
        "000", "100", "200", "250", "300", "400", "500",
        "600", "700", "750", "800", "900",
    }
)
_LABEL_RE = re.compile(
    r"p\s*\.?\s*v\s*\.?\s*p|prix|price|سعر|الثمن|ثمن|ttc|ht|"
    r"د\.?\s*ت\.?|dinars?|\bdt\b|\btnd\b|€|eur",
    re.IGNORECASE,
)
# Labeled amount (PVP / ثمن / Prix …) — allow words between label and number.
_LABELED_AMOUNT_RE = re.compile(
    r"(?:p\s*\.?\s*v\s*\.?\s*p|prix|price|سعر|الثمن|ثمن|ttc|ht)"
    r"[\s\S]{0,40}?"
    r"(\d{1,4}(?:[.,]\d{1,3})?)"
    r"(?:\s*(?:dt|d\.?t\.?|tnd|د\.?\s*ت\.?|دينار|€|eur|euros?))?",
    re.IGNORECASE,
)
_CURRENCY_AMOUNT_RE = re.compile(
    r"(?:"
    r"(\d{1,4}(?:[.,]\d{1,3})?)\s*(?:dt|d\.?t\.?|tnd|د\.?\s*ت\.?|دينار|€|eur|euros?)\b"
    r"|"
    r"(?:dt|d\.?t\.?|tnd|د\.?\s*ت\.?|دينار|€|eur|euros?)\s*[:=]?\s*(\d{1,4}(?:[.,]\d{1,3})?)\b"
    r")",
    re.IGNORECASE,
)
_CODE_CONTEXT_RE = re.compile(r"(?:isbn|ean|978|979|619)\d{0,12}", re.IGNORECASE)


def _plausible_bare_millime(frac: str) -> bool:
    """Tunisian shelf prices use a small set of millime tails — not any *0/*5."""
    return frac in _PLAUSIBLE_MILLIME_ENDS


def _to_amount(raw: str):
    raw = (raw or "").replace(",", ".")
    try:
        amount = to_money(raw)
    except Exception:
        return None
    if amount <= 0 or amount > 80:
        # School-book stickers are well below 80 DT; bigger figures are OCR/ISBN noise
        return None
    if amount >= 1900 and amount <= 2100 and "." not in raw:
        return None
    return amount


def _in_code_run(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - 8): min(len(text), end + 8)]
    digits = "".join(ch for ch in window if ch.isdigit())
    if digits.startswith(("978", "979", "619")) and len(digits) >= 10:
        return True
    return bool(_CODE_CONTEXT_RE.search(window))


def _append_unique(pool: list[str], amount) -> None:
    s = str(amount)
    if s not in pool:
        pool.append(s)


def extract_price_dt(text: str) -> str:
    """Return a millime-quantized TND amount as string, or '' if none found.

    Prefer amounts on a PVP / ثمن / Prix / د.ت line (sticker), then plausible
    millimes. Never invent a price from ISBN/CNP digit soup.
    """
    if not text:
        return ""
    labeled: list[str] = []
    unlabeled: list[str] = []

    for m in _LABELED_AMOUNT_RE.finditer(text):
        if _in_code_run(text, m.start(1), m.end(1)):
            continue
        amount = _to_amount(m.group(1))
        if amount is not None:
            _append_unique(labeled, amount)

    for m in _CURRENCY_AMOUNT_RE.finditer(text):
        raw = m.group(1) or m.group(2)
        if _in_code_run(text, m.start(), m.end()):
            continue
        amount = _to_amount(raw)
        if amount is not None:
            _append_unique(labeled, amount)

    for line in (text or "").splitlines() or [text]:
        line_labeled = bool(_LABEL_RE.search(line))
        for m in _BARE_MILLIME_RE.finditer(line):
            if not _plausible_bare_millime(m.group(2)):
                continue
            if _in_code_run(line, m.start(), m.end()):
                continue
            amount = _to_amount(m.group(1))
            if amount is None:
                continue
            _append_unique(labeled if line_labeled else unlabeled, amount)

    def _pick(pool: list[str]) -> str:
        if not pool:
            return ""
        # Round dinar (.000) beats off-by-one millime (17.900 vs 17.000) when both present
        zeros = [c for c in pool if c.endswith(".000")]
        if zeros and any(c.endswith(".000") for c in pool):
            # Prefer .000 only when it shares the same integer dinar as another candidate,
            # or when it is the only / first round price among labeled stickers.
            for z in zeros:
                whole = z.split(".", 1)[0]
                siblings = [c for c in pool if c.startswith(whole + ".") and c != z]
                if siblings:
                    return z
            if all(c.endswith(".000") for c in pool) or len(pool) == 1:
                return zeros[0]
        # Prefer explicit millime precision
        for c in pool:
            if "." in c and len(c.split(".")[-1]) == 3:
                return c
        return pool[0]

    if labeled:
        return _pick(labeled)
    # When ISBN/CNP digits are present, bare millimes are usually barcode OCR noise
    if re.search(r"(?:isbn|978\d{10}|979\d{10}|619\d{10})", text, re.I):
        return ""
    return _pick(unlabeled)
