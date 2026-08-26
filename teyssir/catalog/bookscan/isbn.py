"""ISBN-10 / ISBN-13 detection, normalization, and check-digit validation."""
from __future__ import annotations

import re

# ISBN-13 bookland prefixes + ISBN-10 (with optional X check digit).
_ISBN13_RE = re.compile(r"(?<!\d)(97[89][-\s]?(?:\d[-\s]?){9}\d)(?!\d)")
_ISBN10_RE = re.compile(r"(?<!\d)((?:\d[-\s]?){9}[\dXx])(?!\d)")
# Digit-dense blob (OCR noise often drops hyphens).
_DIGIT_BLOB_RE = re.compile(r"(?<!\d)(\d{10}|\d{13})(?!\d)")


def normalize_isbn(raw: str) -> str:
    """Strip separators; uppercase X check digit."""
    return re.sub(r"[-\s]", "", (raw or "")).upper()


def isbn10_check_ok(isbn10: str) -> bool:
    s = normalize_isbn(isbn10)
    if len(s) != 10 or not re.fullmatch(r"\d{9}[\dX]", s):
        return False
    total = 0
    for i, ch in enumerate(s[:9]):
        total += (10 - i) * int(ch)
    check = s[9]
    total += 10 if check == "X" else int(check)
    return total % 11 == 0


def isbn13_check_ok(isbn13: str) -> bool:
    s = normalize_isbn(isbn13)
    if len(s) != 13 or not s.isdigit() or s[:3] not in ("978", "979"):
        return False
    total = sum((1 if i % 2 == 0 else 3) * int(ch) for i, ch in enumerate(s[:12]))
    check = (10 - (total % 10)) % 10
    return check == int(s[12])


def isbn10_to_isbn13(isbn10: str) -> str | None:
    s = normalize_isbn(isbn10)
    if not isbn10_check_ok(s):
        return None
    body = "978" + s[:9]
    total = sum((1 if i % 2 == 0 else 3) * int(ch) for i, ch in enumerate(body))
    check = (10 - (total % 10)) % 10
    return body + str(check)


def to_isbn13(raw: str) -> str:
    """Return a validated ISBN-13, or '' if invalid / not convertible."""
    s = normalize_isbn(raw)
    if len(s) == 13 and isbn13_check_ok(s):
        return s
    if len(s) == 10:
        return isbn10_to_isbn13(s) or ""
    return ""


def extract_isbns(text: str) -> list[str]:
    """Find candidate ISBN-13 values in OCR / free text (best first)."""
    found: list[str] = []
    seen: set[str] = set()

    def _add(raw: str):
        isbn = to_isbn13(raw)
        if isbn and isbn not in seen:
            seen.add(isbn)
            found.append(isbn)

    for m in _ISBN13_RE.finditer(text or ""):
        _add(m.group(1))
    for m in _ISBN10_RE.finditer(text or ""):
        _add(m.group(1))
    for m in _DIGIT_BLOB_RE.finditer(re.sub(r"[-\s]", "", text or "")):
        _add(m.group(1))
    return found


def extract_isbn(text: str) -> str:
    """First validated ISBN-13 found in text, or ''."""
    hits = extract_isbns(text)
    return hits[0] if hits else ""
