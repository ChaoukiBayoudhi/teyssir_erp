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


def _price_digits(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def _barcode_digit_runs(text: str) -> list[str]:
    """Long digit runs that look like EAN / CNP human-readable lines."""
    runs: list[str] = []
    for m in re.finditer(r"\d{8,}", text or ""):
        runs.append(m.group(0))
    compact = re.sub(r"[\s\-]", "", text or "")
    for m in re.finditer(r"(?:978|979|619)\d{8,12}", compact, re.I):
        runs.append(m.group(0))
    for m in re.finditer(r"\d{10,14}", compact):
        runs.append(m.group(0))
    # Dedupe preserving order
    return list(dict.fromkeys(runs))


def _has_barcodeish_digit_soup(text: str) -> bool:
    """True when ISBN/CNP markers or enough digit soup to be barcode OCR."""
    if not text:
        return False
    if re.search(r"(?:isbn|ean|978\d{10}|979\d{10}|619\d{8,})", text, re.I):
        return True
    if _barcode_digit_runs(text):
        return True
    # Scattered barcode OCR (e.g. "3\\n. , 14,9002") without one continuous run
    digit_count = sum(1 for ch in text if ch.isdigit())
    return digit_count >= 10


def _is_barcode_fragment_price(raw: str, text: str) -> bool:
    """Reject millimes that look like EAN fragments glued onto a shelf price.

    Classic CNP sticker FP: barcode digit ``3`` above price ``4,900`` → ``34.900``.
    """
    digs = _price_digits(raw)
    if len(digs) < 4:
        return False
    runs = _barcode_digit_runs(text)
    # 3+ leading digits of the candidate collide with an EAN / digit run
    for n in range(3, len(digs) + 1):
        pref = digs[:n]
        if any(pref in run for run in runs):
            return True
    # Leading barcode digit glued on: ZX.YYY where X.YYY is a plausible millime
    # and Z appears in barcode digit runs (not merely in the price itself).
    norm = (raw or "").replace(",", ".")
    if "." not in norm or not runs:
        return False
    whole, _, frac = norm.partition(".")
    if len(frac) != 3 or not _plausible_bare_millime(frac) or len(whole) < 2:
        return False
    rest_raw = f"{whole[1:]}.{frac}"
    if _to_amount(rest_raw) is None:
        return False
    lead = whole[0]
    rest_digs = _price_digits(rest_raw)
    for run in runs:
        if digs in run:
            continue
        # Lead digit from barcode line; rest must not be an EAN substring either
        if lead in run and rest_digs not in run:
            # Prefer rejecting only when the tens+ form is clearly barcode-adjacent
            # (lead appears outside the price token's own digits in the run).
            if run.count(lead) >= 1 and len(whole) == 2:
                return True
    return False


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


def _drop_barcode_prefixed_siblings(pool: list[str]) -> list[str]:
    """Prefer 4.900 over 34.900 / 24.900 when both appear (barcode digit bleed)."""
    if len(pool) < 2:
        return pool
    dig_map = {c: _price_digits(c) for c in pool}
    drop: set[str] = set()
    for long_c, long_d in dig_map.items():
        for short_c, short_d in dig_map.items():
            if long_c == short_c or len(long_d) <= len(short_d):
                continue
            # long = one-or-two digit prefix + short digits
            if long_d.endswith(short_d) and 1 <= len(long_d) - len(short_d) <= 2:
                drop.add(long_c)
    return [c for c in pool if c not in drop]


def extract_price_dt(text: str) -> str:
    """Return a millime-quantized TND amount as string, or '' if none found.

    Prefer amounts on a PVP / ثمن / Prix / د.ت line (sticker), then plausible
    millimes. Never invent a price from ISBN/CNP digit soup.
    """
    if not text:
        return ""
    labeled: list[str] = []
    unlabeled: list[str] = []
    barcodeish = _has_barcodeish_digit_soup(text)

    for m in _LABELED_AMOUNT_RE.finditer(text):
        if _in_code_run(text, m.start(1), m.end(1)):
            continue
        raw = m.group(1)
        if _is_barcode_fragment_price(raw, text):
            continue
        amount = _to_amount(raw)
        if amount is not None:
            _append_unique(labeled, amount)

    for m in _CURRENCY_AMOUNT_RE.finditer(text):
        raw = m.group(1) or m.group(2)
        if _in_code_run(text, m.start(), m.end()):
            continue
        if _is_barcode_fragment_price(raw, text):
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
            raw = m.group(1)
            if _is_barcode_fragment_price(raw, text):
                continue
            amount = _to_amount(raw)
            if amount is None:
                continue
            _append_unique(labeled if line_labeled else unlabeled, amount)

    labeled = _drop_barcode_prefixed_siblings(labeled)
    unlabeled_n = len(unlabeled)
    unlabeled = _drop_barcode_prefixed_siblings(unlabeled)
    dropped_prefixed = len(unlabeled) < unlabeled_n

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
    # When ISBN/CNP digits / barcode soup are present, bare millimes are OCR noise —
    # unless we already stripped barcode-prefixed siblings (34.900→4.900), or the
    # only survivors are single-dinar sticker amounts (4.900 not 34.900).
    if barcodeish and not dropped_prefixed:
        small = [
            c for c in unlabeled
            if "." in c and len(c.split(".", 1)[0]) == 1
        ]
        return _pick(small)
    return _pick(unlabeled)
