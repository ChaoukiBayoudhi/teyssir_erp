"""Server-side barcode decode (pyzbar/zbar) with crop / rotate / upscale fallbacks.

Phone photos of book versos often show a small, angled EAN. Plain Tesseract digit OCR
misses those; a real barcode decoder + region crops recovers the code before metadata search.
Degrades gracefully when ``pyzbar`` / libzbar are not installed.

Phase 2A: when a ``CoverPreprocessResult`` is supplied, try white_label / barcode_band
crops first (budgeted variants) before the broader region search.

Phase 2B: retain non-ISBN product barcodes (Tunisian CNP ``619…``, other GTIN/EAN/UPC/Code128).
``isbn13`` is set only for checksum-valid bookland 978/979. Digit OCR never fills
``barcode_*`` fields and must never invent an ISBN from a ``619`` code.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator

from .isbn import extract_isbn, to_isbn13

if TYPE_CHECKING:
    from .preprocess import CoverPreprocessResult

# Small tilts + cardinal rotations for handheld / sideways verso shots.
_TILT_ROTATIONS = (-8, 8, -15, 15, -25, 25)
_CARDINAL_ROTATIONS = (90, 180, 270)

# Budgeted ROI-first variants (avoid unbounded explosion when preprocess ROIs exist).
_ROI_TILTS = (-12, 12, -20, 20)
_ROI_SCALES = (1.0, 2.0, 3.0, 4.0)

# pyzbar type → catalog symbology label
_SYMBOLOGY_MAP = {
    "EAN13": "EAN13",
    "EAN8": "EAN8",
    "UPCA": "UPCA",
    "UPCE": "UPCE",
    "CODE128": "CODE128",
    "CODE39": "CODE39",
    "I25": "I25",
    "QRCODE": "QRCODE",
}


@dataclass(frozen=True)
class DecodedBarcode:
    """One retained product barcode (never from digit-OCR)."""

    raw: str
    symbology: str
    kind: str  # isbn13 | gtin | local_product
    source: str = "barcode"


def ean13_check_ok(digits: str) -> bool:
    """GTIN-13 / EAN-13 check digit (any prefix, including Tunisian 619)."""
    s = "".join(ch for ch in (digits or "") if ch.isdigit())
    if len(s) != 13:
        return False
    total = sum((1 if i % 2 == 0 else 3) * int(ch) for i, ch in enumerate(s[:12]))
    check = (10 - (total % 10)) % 10
    return check == int(s[12])


def ean8_check_ok(digits: str) -> bool:
    s = "".join(ch for ch in (digits or "") if ch.isdigit())
    if len(s) != 8:
        return False
    total = sum((3 if i % 2 == 0 else 1) * int(ch) for i, ch in enumerate(s[:7]))
    check = (10 - (total % 10)) % 10
    return check == int(s[7])


def classify_barcode(raw: str, symbology: str = "") -> DecodedBarcode | None:
    """Normalize and classify a decoded barcode payload.

    - ``isbn13``: bookland 978/979 with ISBN check OK
    - ``local_product``: Tunisian CNP / local EAN starting with 619 (never ISBN)
    - ``gtin``: other EAN/UPC/Code128 product codes
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    sym = (symbology or "").upper().replace("_", "")
    if sym in ("EAN_13",):
        sym = "EAN13"
    if sym in ("EAN_8",):
        sym = "EAN8"
    if sym in ("UPC_A",):
        sym = "UPCA"
    if sym in ("UPC_E",):
        sym = "UPCE"
    if sym in ("CODE_128",):
        sym = "CODE128"
    sym = _SYMBOLOGY_MAP.get(sym, sym or "UNKNOWN")

    digits = "".join(ch for ch in raw if ch.isdigit())
    isbn = to_isbn13(raw) or to_isbn13(digits)
    if isbn:
        return DecodedBarcode(raw=isbn, symbology="ISBN", kind="isbn13", source="barcode")

    # Never invent ISBN from 619… (or any non-bookland digit soup)
    if digits.startswith("619") and len(digits) >= 12:
        # Prefer full EAN-13 when check digit OK; else keep digit run as local code
        if len(digits) >= 13 and ean13_check_ok(digits[:13]):
            return DecodedBarcode(
                raw=digits[:13], symbology=sym if sym != "UNKNOWN" else "EAN13",
                kind="local_product", source="barcode",
            )
        return DecodedBarcode(
            raw=digits, symbology=sym if sym != "UNKNOWN" else "EAN13",
            kind="local_product", source="barcode",
        )

    if len(digits) == 13 and ean13_check_ok(digits):
        return DecodedBarcode(
            raw=digits, symbology=sym if sym != "UNKNOWN" else "EAN13",
            kind="gtin", source="barcode",
        )
    if len(digits) == 12 and ean13_check_ok("0" + digits):
        # UPC-A as GTIN-13
        return DecodedBarcode(
            raw="0" + digits, symbology=sym if sym in ("UPCA", "EAN13") else "UPCA",
            kind="gtin", source="barcode",
        )
    if len(digits) == 8 and ean8_check_ok(digits):
        return DecodedBarcode(
            raw=digits, symbology=sym if sym != "UNKNOWN" else "EAN8",
            kind="gtin", source="barcode",
        )

    # Code128 / local alphanumeric product codes (e.g. CNP side codes)
    cleaned = "".join(ch for ch in raw if ch.isalnum())
    if len(cleaned) >= 4:
        return DecodedBarcode(
            raw=cleaned,
            symbology=sym if sym != "UNKNOWN" else "CODE128",
            kind="local_product" if cleaned.isdigit() and cleaned.startswith("619") else "gtin",
            source="barcode",
        )
    return None


def _pyzbar_decode_all(image) -> list[DecodedBarcode]:
    try:
        from pyzbar.pyzbar import decode as zbar_decode
    except Exception:
        return []
    try:
        hits = zbar_decode(image)
    except Exception:
        return []
    out: list[DecodedBarcode] = []
    seen: set[str] = set()
    for h in hits or []:
        raw = (h.data or b"").decode("utf-8", errors="ignore").strip()
        sym = getattr(h, "type", None) or ""
        if hasattr(sym, "name"):
            sym = sym.name
        classified = classify_barcode(raw, str(sym))
        if classified and classified.raw not in seen:
            seen.add(classified.raw)
            out.append(classified)
    return out


def _opencv_decode_all(image) -> list[DecodedBarcode]:
    """Optional OpenCV barcode detector (helps some phone crops when zbar misses)."""
    try:
        import cv2
        import numpy as np
    except Exception:
        return []
    if not hasattr(cv2, "barcode") or not hasattr(cv2.barcode, "BarcodeDetector"):
        return []
    try:
        arr = np.array(image.convert("RGB"))
        bgr = arr[:, :, ::-1].copy()
        det = cv2.barcode.BarcodeDetector()
        _ok, infos, types, _pts = det.detectAndDecodeWithType(bgr)
    except Exception:
        return []
    out: list[DecodedBarcode] = []
    seen: set[str] = set()
    infos = infos if isinstance(infos, (list, tuple)) else ([infos] if infos else [])
    types = types if isinstance(types, (list, tuple)) else ([types] if types else [])
    for raw, sym in zip(infos, types or [""] * len(infos)):
        if not raw:
            continue
        classified = classify_barcode(str(raw), str(sym or "EAN13"))
        if classified and classified.raw not in seen:
            seen.add(classified.raw)
            out.append(classified)
    return out


def _decode_hits(image) -> list[DecodedBarcode]:
    hits = _pyzbar_decode_all(image)
    if hits:
        return hits
    return _opencv_decode_all(image)


def _pyzbar_decode(image) -> list[str]:
    """ISBN-13 values only (legacy helper for ISBN-first callers)."""
    return [h.raw for h in _decode_hits(image) if h.kind == "isbn13"]


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
        crop = img.crop(clamped.as_tuple())
        yield name, crop
        # Sticker often has bars in the middle band — extra tight crops
        if name == "white_label" and crop.height > 40:
            cw, ch = crop.size
            yield "white_label_mid", crop.crop((0, int(ch * 0.15), cw, int(ch * 0.85)))
            yield "white_label_bars", crop.crop((int(cw * 0.08), int(ch * 0.2), int(cw * 0.98), int(ch * 0.75)))


def _roi_variants(region) -> Iterator[tuple[str, object]]:
    """Few upscale/tilt/binary variants for ROI crops (no cardinal explosion)."""
    from PIL import ImageEnhance, ImageFilter, ImageOps

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
        sharp = contrast.filter(ImageFilter.SHARPEN)
        yield f"x{int(scale)}_sharp", sharp.convert("RGB")
        bw = sharp.point(lambda x: 255 if x > 125 else 0)
        yield f"x{int(scale)}_binary", bw.convert("RGB")
    base = _up_min_side(region, 700)
    for angle in _ROI_TILTS:
        yield f"tilt{angle}", base.rotate(angle, expand=True, fillcolor="white")


def _pick_best(hits: list[DecodedBarcode]) -> DecodedBarcode | None:
    if not hits:
        return None
    # Prefer real ISBN when present on the same sticker/page
    for h in hits:
        if h.kind == "isbn13":
            return h
    for h in hits:
        if h.kind == "local_product":
            return h
    return hits[0]


def decode_product_barcode(
    image_path: str, prepare: "CoverPreprocessResult | None" = None
) -> DecodedBarcode | None:
    """Return the best retained product barcode (ISBN or non-ISBN), or None.

    Only real decoder hits (pyzbar / OpenCV). Digit OCR never populates this path.
    """
    try:
        from PIL import Image
    except Exception:
        return None

    try:
        img = Image.open(image_path).convert("RGB")
    except Exception:
        return None

    best = _pick_best(_decode_hits(img))
    if best:
        return best

    # Phase 2A/2B: preprocess ROIs first (white PVP/CNP stickers on Tunisian covers)
    seen: set[str] = set()
    for rlabel, region in _roi_band_regions(img, prepare):
        for vlabel, variant in _roi_variants(region):
            key = f"roi:{rlabel}:{vlabel}"
            if key in seen:
                continue
            seen.add(key)
            hit = _pick_best(_decode_hits(variant))
            if hit:
                return hit

    for rlabel, region in _barcode_regions(img):
        for vlabel, variant in _variants(region):
            key = f"{rlabel}:{vlabel}"
            if key in seen:
                continue
            seen.add(key)
            hit = _pick_best(_decode_hits(variant))
            if hit:
                return hit
    return None


def decode_isbn_barcode(image_path: str, prepare: "CoverPreprocessResult | None" = None) -> str:
    """Return validated ISBN-13 from a cover/verso image, or ''."""
    hit = decode_product_barcode(image_path, prepare=prepare)
    if hit and hit.kind == "isbn13":
        return hit.raw
    return ""


def ocr_isbn_digits_from_image(
    image_path: str, prepare: "CoverPreprocessResult | None" = None
) -> str:
    """Last-resort ISBN from digit OCR on barcode bands (when zbar misses).

    Never used to populate ``barcode_raw`` / product identity for non-ISBN codes.
    """
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
    Non-ISBN barcodes are available via :func:`decode_product_barcode` (Phase 2B).
    """
    hit = decode_product_barcode(image_path, prepare=prepare)
    if hit and hit.kind == "isbn13":
        return hit.raw, "barcode"
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
