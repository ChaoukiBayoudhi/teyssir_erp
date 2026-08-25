"""Server-side ISBN barcode decode (pyzbar/zbar) with crop / rotate / upscale fallbacks.

Phone photos of book versos often show a small, angled EAN-13. Plain Tesseract digit OCR
misses those; a real barcode decoder + region crops recovers the ISBN before metadata search.
Degrades gracefully when ``pyzbar`` / libzbar are not installed.

Phase 2A: when a ``CoverPreprocessResult`` is supplied, try white_label / barcode_band
crops first (budgeted variants) before the broader region search. ISBN-only retention
is unchanged (non-ISBN / CNP codes still discarded — Phase 2B).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterator

from .isbn import extract_isbn, to_isbn13

if TYPE_CHECKING:
    from .preprocess import CoverPreprocessResult

# Small tilts + cardinal rotations for handheld / sideways verso shots.
_TILT_ROTATIONS = (-8, 8, -15, 15, -25, 25)
_CARDINAL_ROTATIONS = (90, 180, 270)

# Budgeted ROI-first variants (avoid unbounded explosion when preprocess ROIs exist).
_ROI_TILTS = (-12, 12)
_ROI_SCALES = (1.0, 2.0, 3.0)


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
        yield "lower20", img.crop((0, int(h * 0.78), w, h))
    if w > 80 and h > 60:
        yield "lower_center", img.crop((int(w * 0.15), int(h * 0.55), int(w * 0.85), h))
    if w > 100 and h > 80:
        yield "bottom_band", img.crop((0, int(h * 0.78), w, h))
        # Corner placements (common on Arabic / school books)
        yield "br_corner", img.crop((int(w * 0.45), int(h * 0.65), w, h))
        yield "bl_corner", img.crop((0, int(h * 0.65), int(w * 0.55), h))
        yield "tr_corner", img.crop((int(w * 0.45), 0, w, int(h * 0.35)))
        yield "tl_corner", img.crop((0, 0, int(w * 0.55), int(h * 0.35)))
    # High-contrast grayscale of full image (helps faded ink)
    yield "gray", ImageOps.autocontrast(ImageOps.grayscale(img).convert("RGB"))


def _scale(im, factor: float):
    from PIL import Image

    if factor <= 1.01:
        return im
    return im.resize(
        (max(1, int(im.width * factor)), max(1, int(im.height * factor))),
        Image.Resampling.LANCZOS,
    )


def _up_min_side(im, min_side=900):
    mw = max(im.size)
    if mw < min_side:
        return _scale(im, min_side / mw)
    return im


def _variants(region) -> Iterator[tuple[str, object]]:
    from PIL import ImageEnhance, ImageFilter, ImageOps

    base = _up_min_side(region, 900)
    yield "up", base
    # Force 2× / 3× even on already-large phone photos (tiny barcode bars)
    yield "x2", _scale(region, 2.0)
    yield "x3", _scale(region, 3.0)

    g = ImageOps.autocontrast(ImageOps.grayscale(base))
    yield "gray_up", g.convert("RGB")
    sharp = ImageEnhance.Contrast(g).enhance(2.2)
    yield "contrast", sharp.convert("RGB")
    sharpened = sharp.filter(ImageFilter.SHARPEN)
    yield "sharpen", sharpened.convert("RGB")
    # Binary threshold — helps low-contrast phone photos
    bw = sharp.point(lambda x: 255 if x > 130 else 0)
    yield "binary", bw.convert("RGB")
    bw2 = ImageOps.autocontrast(g).point(lambda x: 255 if x > 110 else 0)
    yield "binary_soft", bw2.convert("RGB")

    for angle in _TILT_ROTATIONS:
        rotated = base.rotate(angle, expand=True, fillcolor="white")
        yield f"rot{angle}", _up_min_side(rotated, min_side=700)

    for angle in _CARDINAL_ROTATIONS:
        rotated = base.rotate(angle, expand=True, fillcolor="white")
        yield f"card{angle}", _up_min_side(rotated, min_side=700)
        yield f"card{angle}_x2", _scale(rotated, 2.0)


def _roi_band_regions(img, prepare: "CoverPreprocessResult | None") -> Iterator[tuple[str, object]]:
    """Yield preprocess ROI crops (white sticker first) — budgeted barcode tries."""
    if prepare is None:
        return
    from .preprocess import iter_roi_crops

    w, h = img.size
    # Prefer sticker → barcode_band → price_band (title unused for barcodes)
    order = ("white_label", "barcode_band", "price_band")
    boxes = {name: box for name, box in iter_roi_crops(prepare)}
    for name in order:
        box = boxes.get(name)
        if box is None:
            continue
        clamped = box.clamp(w, h)
        if clamped.width < 12 or clamped.height < 12:
            continue
        yield name, img.crop(clamped.as_tuple())


def _roi_variants(region) -> Iterator[tuple[str, object]]:
    """Few upscale/tilt variants for ROI crops (no cardinal explosion)."""
    from PIL import ImageEnhance, ImageOps

    yield "raw", region
    for scale in _ROI_SCALES:
        if scale <= 1.01:
            continue
        up = _scale(region, scale)
        yield f"x{int(scale)}", up
        g = ImageOps.autocontrast(ImageOps.grayscale(up))
        yield f"x{int(scale)}_gray", g.convert("RGB")
        contrast = ImageEnhance.Contrast(g).enhance(2.2)
        yield f"x{int(scale)}_contrast", contrast.convert("RGB")
    base = _up_min_side(region, 700)
    for angle in _ROI_TILTS:
        yield f"tilt{angle}", base.rotate(angle, expand=True, fillcolor="white")


def decode_isbn_barcode(image_path: str, prepare: "CoverPreprocessResult | None" = None) -> str:
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

    # Phase 2A: preprocess ROIs first (white PVP/CNP stickers on Tunisian covers)
    seen: set[str] = set()
    for rlabel, region in _roi_band_regions(img, prepare):
        for vlabel, variant in _roi_variants(region):
            key = f"roi:{rlabel}:{vlabel}"
            if key in seen:
                continue
            seen.add(key)
            for isbn in _pyzbar_decode(variant):
                return isbn

    for rlabel, region in _barcode_regions(img):
        for vlabel, variant in _variants(region):
            key = f"{rlabel}:{vlabel}"
            if key in seen:
                continue
            seen.add(key)
            for isbn in _pyzbar_decode(variant):
                return isbn
    return ""


def ocr_isbn_digits_from_image(
    image_path: str, prepare: "CoverPreprocessResult | None" = None
) -> str:
    """Last-resort ISBN from digit OCR on barcode bands (when zbar misses)."""
    try:
        import pytesseract
        from PIL import Image, ImageEnhance, ImageOps
    except Exception:
        return ""

    try:
        from .ocr import configure_tesseract

        configure_tesseract(pytesseract)
    except Exception:
        pass

    try:
        img = Image.open(image_path).convert("RGB")
    except Exception:
        return ""

    w, h = img.size
    bands = [img]
    # Prefer preprocess ROI crops when available
    for _label, region in _roi_band_regions(img, prepare):
        bands.append(region)
    if h > 80:
        bands.append(img.crop((0, int(h * 0.65), w, h)))
        bands.append(img.crop((0, int(h * 0.78), w, h)))
    if w > 100 and h > 80:
        bands.append(img.crop((int(w * 0.4), int(h * 0.65), w, h)))
        bands.append(img.crop((0, int(h * 0.65), int(w * 0.6), h)))

    cfg = "--psm 6 -c tessedit_char_whitelist=0123456789Xx-"
    blob = ""
    # Cap band count — ROI + fixed bands, no explosion
    for band in bands[:8]:
        for scale in (1.0, 2.0, 3.0):
            im = _scale(band, scale) if scale > 1 else band
            g = ImageOps.autocontrast(ImageOps.grayscale(im))
            g = ImageEnhance.Contrast(g).enhance(2.0)
            try:
                blob += "\n" + (pytesseract.image_to_string(g, config=cfg) or "")
            except Exception:
                continue
            isbn = extract_isbn(blob)
            if isbn:
                return isbn
    return extract_isbn(blob) or ""


def decode_isbn_with_source(
    image_path: str, prepare: "CoverPreprocessResult | None" = None
) -> tuple[str, str]:
    """Return ``(isbn13, source)`` with ``source`` in ``barcode`` | ``digit_ocr`` | ``''``.

    Prefer pyzbar (real EAN bars). Digit OCR is a last resort and must never be
    treated as a barcode hit for confidence boosting — checksum-valid OCR noise
    (e.g. ``9787723827435``) can still be wrong.
    Non-ISBN barcodes (CNP / TN EAN 619…) remain discarded until Phase 2B.
    """
    isbn = decode_isbn_barcode(image_path, prepare=prepare)
    if isbn:
        return isbn, "barcode"
    isbn = ocr_isbn_digits_from_image(image_path, prepare=prepare)
    if isbn:
        return isbn, "digit_ocr"
    return "", ""


def decode_isbn_barcode_or_digits(
    image_path: str, prepare: "CoverPreprocessResult | None" = None
) -> str:
    """Barcode first, then digit OCR on verso bands (ISBN only, no source)."""
    isbn, _src = decode_isbn_with_source(image_path, prepare=prepare)
    return isbn


def barcode_engine_available() -> bool:
    """True when pyzbar can import (libzbar may still fail at decode time)."""
    try:
        from pyzbar import pyzbar  # noqa: F401
        return True
    except Exception:
        return False
