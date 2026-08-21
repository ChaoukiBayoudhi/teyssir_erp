"""Server-side ISBN barcode decode (pyzbar/zbar) with crop / rotate / upscale fallbacks.

Phone photos of book versos often show a small, angled EAN-13. Plain Tesseract digit OCR
misses those; a real barcode decoder + region crops recovers the ISBN before metadata search.
Degrades gracefully when ``pyzbar`` / libzbar are not installed.
"""
from __future__ import annotations

from typing import Iterator

from .isbn import to_isbn13

# Angles that commonly fix handheld / tilted verso shots.
_ROTATIONS = (0, -8, 8, -15, 15, -25, 25)


def _pyzbar_decode(image) -> list[str]:
    try:
        from pyzbar.pyzbar import decode as zbar_decode
    except Exception:
        return []
    try:
        hits = zbar_decode(image)
    except Exception:
        return []
    out: list[str] = []
    for h in hits or []:
        raw = (h.data or b"").decode("utf-8", errors="ignore").strip()
        isbn = to_isbn13(raw)
        if isbn:
            out.append(isbn)
        elif raw.isdigit() and len(raw) in (12, 13):
            # UPC-A / EAN without bookland — keep if convertible via to_isbn13 later
            maybe = to_isbn13(raw) or ""
            if maybe:
                out.append(maybe)
    return out


def _barcode_regions(img) -> Iterator[tuple[str, object]]:
    """Yield (label, PIL image) candidate crops where EAN barcodes usually sit."""
    from PIL import ImageOps

    w, h = img.size
    yield "full", img
    if h > 60:
        yield "lower40", img.crop((0, int(h * 0.55), w, h))
        yield "lower30", img.crop((0, int(h * 0.68), w, h))
    if w > 80 and h > 60:
        # Center strip of lower half (typical ISBN barcode placement)
        yield "lower_center", img.crop((int(w * 0.15), int(h * 0.55), int(w * 0.85), h))
    if w > 100 and h > 80:
        yield "bottom_band", img.crop((0, int(h * 0.78), w, h))
    # High-contrast grayscale of full image (helps faded ink)
    yield "gray", ImageOps.autocontrast(ImageOps.grayscale(img).convert("RGB"))


def _variants(region) -> Iterator[tuple[str, object]]:
    from PIL import Image, ImageEnhance, ImageOps

    def _up(im, min_side=900):
        mw = max(im.size)
        if mw < min_side:
            scale = min_side / mw
            return im.resize(
                (max(1, int(im.width * scale)), max(1, int(im.height * scale))),
                Image.Resampling.LANCZOS,
            )
        return im

    base = _up(region)
    yield "up", base
    g = ImageOps.autocontrast(ImageOps.grayscale(base))
    yield "gray_up", g.convert("RGB")
    sharp = ImageEnhance.Contrast(g).enhance(2.2)
    yield "contrast", sharp.convert("RGB")
    # Binary threshold — helps low-contrast phone photos
    bw = sharp.point(lambda x: 255 if x > 130 else 0)
    yield "binary", bw.convert("RGB")

    for angle in _ROTATIONS[1:]:  # skip 0 (already tried as up)
        rotated = base.rotate(angle, expand=True, fillcolor="white")
        yield f"rot{angle}", _up(rotated, min_side=700)


def decode_isbn_barcode(image_path: str) -> str:
    """Return validated ISBN-13 from a cover/verso image, or ''."""
    try:
        from PIL import Image
    except Exception:
        return ""

    try:
        img = Image.open(image_path).convert("RGB")
    except Exception:
        return ""

    # Fast path: whole image
    for isbn in _pyzbar_decode(img):
        return isbn

    seen: set[str] = set()
    for rlabel, region in _barcode_regions(img):
        for vlabel, variant in _variants(region):
            key = f"{rlabel}:{vlabel}"
            if key in seen:
                continue
            seen.add(key)
            for isbn in _pyzbar_decode(variant):
                return isbn
    return ""


def barcode_engine_available() -> bool:
    """True when pyzbar can import (libzbar may still fail at decode time)."""
    try:
        from pyzbar import pyzbar  # noqa: F401
        return True
    except Exception:
        return False
