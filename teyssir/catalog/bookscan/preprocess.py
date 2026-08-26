"""Book-cover preprocessing for scan: orient, crop/warp, deskew, CLAHE, band ROIs.

OpenCV path preferred (``opencv-python-headless``). Falls back to Pillow-only if cv2
is unavailable. Produces a rectified cover plus title / barcode / price band boxes,
and optionally a bright white-label blob (Tunisian PVP / CNP stickers).
"""
from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from typing import Iterator, Sequence

log = logging.getLogger(__name__)

# Phone covers: clamp longest edge into this band (OCR/barcode friendly).
_DEFAULT_MAX_EDGE = 1800
_MAX_EDGE_LO = 1600
_MAX_EDGE_HI = 2000

# Deskew beyond the old ±4° Pillow search.
_DESKEW_MIN_ABS = 4.0
_DESKEW_MAX_ABS = 35.0


@dataclass(frozen=True)
class RoiBox:
    """Axis-aligned box in preprocessed image coordinates (inclusive-exclusive)."""

    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return max(0, self.x1 - self.x0)

    @property
    def height(self) -> int:
        return max(0, self.y1 - self.y0)

    def clamp(self, w: int, h: int) -> "RoiBox":
        x0 = max(0, min(self.x0, w))
        y0 = max(0, min(self.y0, h))
        x1 = max(x0 + 1, min(self.x1, w))
        y1 = max(y0 + 1, min(self.y1, h))
        return RoiBox(x0, y0, x1, y1)

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x0, self.y0, self.x1, self.y1)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CoverPreprocessResult:
    """Result of one cover preprocess pass."""

    path: str
    original_path: str
    width: int
    height: int
    title_band: RoiBox
    barcode_band: RoiBox
    price_band: RoiBox
    white_label: RoiBox | None
    method: str
    deskew_deg: float = 0.0
    is_temp: bool = True

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "original_path": self.original_path,
            "width": self.width,
            "height": self.height,
            "title_band": self.title_band.to_dict(),
            "barcode_band": self.barcode_band.to_dict(),
            "price_band": self.price_band.to_dict(),
            "white_label": self.white_label.to_dict() if self.white_label else None,
            "method": self.method,
            "deskew_deg": self.deskew_deg,
        }


def opencv_available() -> bool:
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401

        return True
    except Exception:
        return False


def _clamp_max_edge(max_edge: int) -> int:
    return max(_MAX_EDGE_LO, min(int(max_edge or _DEFAULT_MAX_EDGE), _MAX_EDGE_HI))


def _band_rois(w: int, h: int, white_label: RoiBox | None = None) -> tuple[RoiBox, RoiBox, RoiBox]:
    """Fixed-ratio bands on a rectified cover; expand barcode_band if white label found."""
    title = RoiBox(0, 0, w, max(1, int(h * 0.40))).clamp(w, h)
    barcode = RoiBox(0, int(h * 0.70), w, h).clamp(w, h)
    # Price often sits with the sticker or mid-lower shelf tags
    price = RoiBox(0, int(h * 0.55), w, h).clamp(w, h)
    if white_label is not None:
        # Ensure barcode/price bands cover the sticker (pad slightly)
        pad_x = max(4, int(w * 0.02))
        pad_y = max(4, int(h * 0.02))
        lx0 = max(0, white_label.x0 - pad_x)
        ly0 = max(0, white_label.y0 - pad_y)
        lx1 = min(w, white_label.x1 + pad_x)
        ly1 = min(h, white_label.y1 + pad_y)
        barcode = RoiBox(
            min(barcode.x0, lx0),
            min(barcode.y0, ly0),
            max(barcode.x1, lx1),
            max(barcode.y1, ly1),
        ).clamp(w, h)
        price = RoiBox(
            min(price.x0, lx0),
            min(price.y0, ly0),
            max(price.x1, lx1),
            max(price.y1, ly1),
        ).clamp(w, h)
    return title, barcode, price


def _load_bgr_exif(path: str):
    """Load BGR ndarray with EXIF orientation applied (PIL → OpenCV)."""
    import cv2
    import numpy as np
    from PIL import Image, ImageOps

    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        rgb = np.asarray(im.convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _resize_max_edge(bgr, max_edge: int):
    import cv2

    h, w = bgr.shape[:2]
    m = max(h, w)
    if m <= max_edge:
        return bgr
    scale = max_edge / float(m)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    return cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_AREA)


def _order_quad_points(pts):
    """Order 4 points as TL, TR, BR, BL."""
    import numpy as np

    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).reshape(-1)
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(diff)]
    bl = pts[np.argmax(diff)]
    return np.stack([tl, tr, br, bl]).astype(np.float32)


def _quad_size(ordered):
    import numpy as np

    (tl, tr, br, bl) = ordered
    w_top = float(np.linalg.norm(tr - tl))
    w_bot = float(np.linalg.norm(br - bl))
    h_left = float(np.linalg.norm(bl - tl))
    h_right = float(np.linalg.norm(br - tr))
    return max(1, int(max(w_top, w_bot))), max(1, int(max(h_left, h_right)))


def _find_document_quad(bgr):
    """Largest convex quadrilateral that looks like a book/page, or None."""
    import cv2
    import numpy as np

    h, w = bgr.shape[:2]
    area_img = float(h * w)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_area = 0.0
    for cnt in contours:
        area = float(cv2.contourArea(cnt))
        if area < area_img * 0.12:
            continue
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        if area > best_area:
            best_area = area
            best = approx.reshape(4, 2)

    # Fallback: min-area rectangle of the largest solid blob after adaptive thresh
    if best is None:
        thr = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 7
        )
        thr = cv2.morphologyEx(thr, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8), iterations=2)
        contours, _ = cv2.findContours(thr, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            cnt = max(contours, key=cv2.contourArea)
            if cv2.contourArea(cnt) >= area_img * 0.15:
                rect = cv2.minAreaRect(cnt)
                box = cv2.boxPoints(rect)
                best = box

    if best is None:
        return None
    ordered = _order_quad_points(best)
    qw, qh = _quad_size(ordered)
    # Reject degenerate / tiny quads
    if qw * qh < area_img * 0.12:
        return None
    return ordered


def _warp_quad(bgr, ordered):
    import cv2
    import numpy as np

    qw, qh = _quad_size(ordered)
    # Keep portrait-ish book proportions when warped
    dst = np.array(
        [[0, 0], [qw - 1, 0], [qw - 1, qh - 1], [0, qh - 1]],
        dtype=np.float32,
    )
    M = cv2.getPerspectiveTransform(ordered, dst)
    return cv2.warpPerspective(bgr, M, (qw, qh), flags=cv2.INTER_CUBIC)


def _center_fallback(bgr, frac: float = 0.88):
    """Crop a centered window when no document quad is found."""
    h, w = bgr.shape[:2]
    mw = int(w * frac)
    mh = int(h * frac)
    x0 = max(0, (w - mw) // 2)
    y0 = max(0, (h - mh) // 2)
    return bgr[y0 : y0 + mh, x0 : x0 + mw].copy()


def _estimate_skew_deg(gray) -> float:
    """Estimate residual skew via minAreaRect on ink; 0 if unclear."""
    import cv2
    import numpy as np

    # Prefer darker ink as foreground
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, bw = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = cv2.findNonZero(bw)
    if coords is None or len(coords) < 80:
        return 0.0
    rect = cv2.minAreaRect(coords)
    angle = float(rect[-1])
    # OpenCV minAreaRect angle is in [-90, 0); normalize to small deskew
    if angle < -45:
        angle = 90.0 + angle
    if abs(angle) < _DESKEW_MIN_ABS or abs(angle) > _DESKEW_MAX_ABS:
        return 0.0
    return angle


def _rotate_bound(bgr, angle_deg: float):
    import cv2
    import numpy as np

    h, w = bgr.shape[:2]
    center = (w / 2.0, h / 2.0)
    M = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    cos = abs(M[0, 0])
    sin = abs(M[0, 1])
    nw = int((h * sin) + (w * cos))
    nh = int((h * cos) + (w * sin))
    M[0, 2] += (nw / 2) - center[0]
    M[1, 2] += (nh / 2) - center[1]
    return cv2.warpAffine(
        bgr, M, (nw, nh), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def _apply_clahe(bgr):
    """CLAHE on L channel — helps glare / uneven phone lighting."""
    import cv2

    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l2 = clahe.apply(l)
    return cv2.cvtColor(cv2.merge([l2, a, b]), cv2.COLOR_LAB2BGR)


def _find_white_label(bgr) -> RoiBox | None:
    """Bright compact sticker in the lower cover (PVP / CNP barcode labels).

    Prefers high-fill, barcode-textured rectangles over large left-sleeve /
    edge-hugging tall blobs (common phone-photo false positives).
    """
    import cv2
    import numpy as np

    h, w = bgr.shape[:2]
    y0 = int(h * 0.52)
    roi = bgr[y0:h, 0:w]
    if roi.size == 0:
        return None
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    # White / near-white paper (allow slightly lower V on cream covers)
    mask_hsv = cv2.inRange(hsv, (0, 0, 165), (180, 75, 255))
    _, mask_abs = cv2.threshold(gray, 195, 255, cv2.THRESH_BINARY)
    mask = cv2.bitwise_or(mask_hsv, mask_abs)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    area_roi = float(roi.shape[0] * roi.shape[1])
    best = None
    best_score = 0.0
    for cnt in contours:
        area = float(cv2.contourArea(cnt))
        # Compact stickers — reject huge sleeve/torso blobs by area alone
        if area < area_roi * 0.006 or area > area_roi * 0.28:
            continue
        x, y, bw, bh = cv2.boundingRect(cnt)
        if bw < 28 or bh < 18:
            continue
        ar = bw / float(bh)
        fill = area / float(max(bw * bh, 1))
        if fill < 0.38 or ar < 0.55 or ar > 5.5:
            continue

        ax0, ay0 = x, y + y0
        ax1, ay1 = x + bw, y + y0 + bh
        left_margin = ax0 <= max(6, int(w * 0.045))
        right_margin = ax1 >= w - max(6, int(w * 0.045))
        tall_frac = bh / float(roi.shape[0])
        wide_frac = bw / float(w)

        # Hard reject left-margin torso / grey sleeve FPs
        if left_margin and (tall_frac > 0.40 or ar < 1.15):
            continue
        if tall_frac > 0.65:
            continue
        if wide_frac > 0.40 and fill < 0.65:
            continue
        if (left_margin or right_margin) and area > area_roi * 0.08:
            continue

        patch_g = gray[y : y + bh, x : x + bw]
        patch_hsv = hsv[y : y + bh, x : x + bw]
        mean_v = float(patch_hsv[:, :, 2].mean())
        mean_s = float(patch_hsv[:, :, 1].mean())
        if mean_v < 145:
            continue

        # Ink / barcode texture inside the white patch (sleeves are smoother)
        dark = float(np.count_nonzero(patch_g < 110)) / max(patch_g.size, 1)
        gx = cv2.Sobel(patch_g, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(patch_g, cv2.CV_32F, 0, 1, ksize=3)
        h_energy = float(np.mean(np.abs(gx)))
        v_energy = float(np.mean(np.abs(gy)))
        barcodeish = h_energy / max(v_energy, 1e-3)

        submask = mask[y : y + bh, x : x + bw]
        bright_frac = float(np.count_nonzero(submask)) / max(submask.size, 1)

        cy = (ay0 + ay1) / 2.0 / h
        lower_bonus = 1.0 + 0.35 * max(0.0, (cy - 0.65) / 0.35)
        ar_bonus = 1.35 if 1.15 <= ar <= 4.2 else (0.55 if ar < 0.85 else 1.0)
        afrac = area / area_roi
        size_score = float(np.exp(-((np.log(max(afrac, 1e-6) / 0.035)) ** 2)))
        texture = 0.4 + 2.5 * dark + 0.015 * h_energy + 0.25 * min(barcodeish, 3.0)
        skin_pen = 0.55 if mean_s > 55 and mean_v > 160 else 1.0
        edge_pen = 1.0
        if left_margin:
            edge_pen *= 0.2
        if right_margin and tall_frac > 0.45:
            edge_pen *= 0.4

        score = (
            (fill ** 1.4)
            * bright_frac
            * ar_bonus
            * size_score
            * texture
            * lower_bonus
            * edge_pen
            * skin_pen
            * (mean_v / 255.0)
        )
        if score > best_score:
            best_score = score
            best = (ax0, ay0, ax1, ay1)

    if best is None:
        return None
    return RoiBox(*best).clamp(w, h)


def _save_bgr_jpeg(bgr, path: str, quality: int = 92) -> None:
    import cv2

    cv2.imwrite(path, bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])


def _preprocess_opencv(image_path: str, *, max_edge: int, out_path: str) -> CoverPreprocessResult:
    import cv2

    max_edge = _clamp_max_edge(max_edge)
    bgr = _load_bgr_exif(image_path)
    bgr = _resize_max_edge(bgr, max_edge)

    method = "opencv_center"
    deskew = 0.0
    quad = _find_document_quad(bgr)
    if quad is not None:
        try:
            warped = _warp_quad(bgr, quad)
            if warped is not None and warped.size > 0:
                bgr = warped
                method = "opencv_quad"
        except Exception:
            log.debug("quad warp failed; using center fallback", exc_info=True)
            bgr = _center_fallback(bgr)
            method = "opencv_center"
    else:
        bgr = _center_fallback(bgr)

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    skew = _estimate_skew_deg(gray)
    if abs(skew) >= _DESKEW_MIN_ABS:
        bgr = _rotate_bound(bgr, skew)
        deskew = skew
        method = f"{method}+deskew"

    bgr = _apply_clahe(bgr)
    # Re-clamp after warp/rotate (warp can enlarge)
    bgr = _resize_max_edge(bgr, max_edge)

    white = _find_white_label(bgr)
    h, w = bgr.shape[:2]
    title, barcode, price = _band_rois(w, h, white)
    _save_bgr_jpeg(bgr, out_path)
    return CoverPreprocessResult(
        path=out_path,
        original_path=image_path,
        width=w,
        height=h,
        title_band=title,
        barcode_band=barcode,
        price_band=price,
        white_label=white,
        method=method,
        deskew_deg=deskew,
        is_temp=True,
    )


def _preprocess_pillow(image_path: str, *, max_edge: int, out_path: str) -> CoverPreprocessResult:
    """Pillow-only fallback: EXIF, clamp, center crop, light deskew, autocontrast."""
    from PIL import Image, ImageOps

    max_edge = _clamp_max_edge(max_edge)
    with Image.open(image_path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        w, h = im.size
        m = max(w, h)
        if m > max_edge:
            scale = max_edge / float(m)
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
        # Center crop ~88%
        w, h = im.size
        mw, mh = int(w * 0.88), int(h * 0.88)
        x0, y0 = (w - mw) // 2, (h - mh) // 2
        im = im.crop((x0, y0, x0 + mw, y0 + mh))
        # Light deskew (±12°) via projection variance (stronger than old ±4°)
        deskew = 0.0
        try:
            import statistics

            g = im.convert("L")
            best_angle = 0
            best_score = -1.0
            for angle in (0, -6, 6, -10, 10, -15, 15, -25, 25):
                rot = g.rotate(angle, expand=True, fillcolor=255) if angle else g
                rw, rh = rot.size
                step = max(1, rh // 40)
                rows = []
                for y in range(0, rh, step):
                    band = list(rot.crop((0, y, rw, min(rh, y + step))).getdata())
                    rows.append(sum(1 for p in band if p < 128) / max(len(band), 1))
                if len(rows) < 3:
                    continue
                score = statistics.pstdev(rows)
                if score > best_score:
                    best_score = score
                    best_angle = angle
            if abs(best_angle) >= _DESKEW_MIN_ABS:
                im = im.rotate(best_angle, expand=True, fillcolor="white")
                deskew = float(best_angle)
        except Exception:
            pass
        im = ImageOps.autocontrast(im)
        im.save(out_path, format="JPEG", quality=92)
        w, h = im.size

    # White-label heuristic on luminance (no OpenCV)
    white = None
    try:
        from PIL import Image as PILImage

        g = PILImage.open(out_path).convert("L")
        yw0 = int(h * 0.55)
        region = g.crop((0, yw0, w, h))
        # Coarse bright blob via resize + threshold
        small = region.resize((max(1, w // 8), max(1, (h - yw0) // 8)))
        px = list(small.getdata())
        sw, sh = small.size
        # Find bbox of bright pixels
        xs, ys = [], []
        for i, v in enumerate(px):
            if v >= 200:
                xs.append(i % sw)
                ys.append(i // sw)
        if len(xs) >= 8:
            sx0, sx1 = min(xs), max(xs) + 1
            sy0, sy1 = min(ys), max(ys) + 1
            scale_x = w / float(sw)
            scale_y = (h - yw0) / float(sh)
            white = RoiBox(
                int(sx0 * scale_x),
                yw0 + int(sy0 * scale_y),
                int(sx1 * scale_x),
                yw0 + int(sy1 * scale_y),
            ).clamp(w, h)
            # Reject tiny / huge blobs
            if white.width * white.height < (w * h) * 0.008:
                white = None
    except Exception:
        white = None

    title, barcode, price = _band_rois(w, h, white)
    method = "pillow_center+deskew" if abs(deskew) >= _DESKEW_MIN_ABS else "pillow_center"
    return CoverPreprocessResult(
        path=out_path,
        original_path=image_path,
        width=w,
        height=h,
        title_band=title,
        barcode_band=barcode,
        price_band=price,
        white_label=white,
        method=method,
        deskew_deg=deskew,
        is_temp=True,
    )


def preprocess_cover(
    image_path: str,
    *,
    max_edge: int = _DEFAULT_MAX_EDGE,
    out_dir: str | None = None,
) -> CoverPreprocessResult:
    """Preprocess one cover image; writes a JPEG and returns band ROIs.

    Prefer OpenCV when installed; otherwise Pillow-only path.
    """
    if not image_path or not os.path.isfile(image_path):
        raise FileNotFoundError(image_path)

    fd, tmp = tempfile.mkstemp(suffix=".jpg", prefix="teyssir_prep_", dir=out_dir)
    os.close(fd)
    try:
        if opencv_available():
            return _preprocess_opencv(image_path, max_edge=max_edge, out_path=tmp)
        return _preprocess_pillow(image_path, max_edge=max_edge, out_path=tmp)
    except Exception:
        # Last resort: Pillow path even if OpenCV partially failed
        with contextlib.suppress(OSError):
            os.remove(tmp)
        fd, tmp = tempfile.mkstemp(suffix=".jpg", prefix="teyssir_prep_", dir=out_dir)
        os.close(fd)
        return _preprocess_pillow(image_path, max_edge=max_edge, out_path=tmp)


def preprocess_cover_paths(
    image_paths: Sequence[str],
    *,
    max_edge: int = _DEFAULT_MAX_EDGE,
) -> list[CoverPreprocessResult]:
    """Preprocess each path; on failure, pass through the original path (no temp)."""
    out: list[CoverPreprocessResult] = []
    for path in image_paths or []:
        if not path or not os.path.isfile(path):
            # Tests / callers may pass placeholders; keep path so mocks still run.
            title, barcode, price = _band_rois(1, 1, None)
            out.append(
                CoverPreprocessResult(
                    path=path or "",
                    original_path=path or "",
                    width=1,
                    height=1,
                    title_band=title,
                    barcode_band=barcode,
                    price_band=price,
                    white_label=None,
                    method="passthrough",
                    deskew_deg=0.0,
                    is_temp=False,
                )
            )
            continue
        try:
            out.append(preprocess_cover(path, max_edge=max_edge))
        except Exception:
            log.warning("preprocess failed for %s; using raw copy", path, exc_info=True)
            fd, tmp = tempfile.mkstemp(suffix=".jpg", prefix="teyssir_prep_raw_")
            os.close(fd)
            try:
                from PIL import Image, ImageOps

                with Image.open(path) as im:
                    im = ImageOps.exif_transpose(im).convert("RGB")
                    im.save(tmp, format="JPEG", quality=90)
                    w, h = im.size
            except Exception:
                # Binary copy
                try:
                    with open(path, "rb") as src, open(tmp, "wb") as dst:
                        dst.write(src.read())
                    w = h = 0
                except Exception:
                    with contextlib.suppress(OSError):
                        os.remove(tmp)
                    title, barcode, price = _band_rois(1, 1, None)
                    out.append(
                        CoverPreprocessResult(
                            path=path,
                            original_path=path,
                            width=1,
                            height=1,
                            title_band=title,
                            barcode_band=barcode,
                            price_band=price,
                            white_label=None,
                            method="passthrough",
                            deskew_deg=0.0,
                            is_temp=False,
                        )
                    )
                    continue
            title, barcode, price = _band_rois(max(w, 1), max(h, 1), None)
            out.append(
                CoverPreprocessResult(
                    path=tmp,
                    original_path=path,
                    width=w or 1,
                    height=h or 1,
                    title_band=title,
                    barcode_band=barcode,
                    price_band=price,
                    white_label=None,
                    method="raw_copy",
                    deskew_deg=0.0,
                    is_temp=True,
                )
            )
    return out


def cleanup_preprocess(results: Sequence[CoverPreprocessResult] | None) -> None:
    """Remove temp files produced by preprocess_cover*."""
    for r in results or []:
        if r and r.is_temp and r.path:
            with contextlib.suppress(OSError):
                os.remove(r.path)


@contextlib.contextmanager
def prepared_cover_paths(image_paths: Sequence[str], *, max_edge: int = _DEFAULT_MAX_EDGE):
    """Context manager: yield (prep_paths, results); always cleans temp files."""
    results = preprocess_cover_paths(image_paths, max_edge=max_edge) if image_paths else []
    try:
        yield [r.path for r in results], results
    finally:
        cleanup_preprocess(results)


def iter_roi_crops(result: CoverPreprocessResult) -> Iterator[tuple[str, RoiBox]]:
    """Yield named ROI boxes (for validation scripts / budgeted barcode tries)."""
    yield "title_band", result.title_band
    yield "barcode_band", result.barcode_band
    yield "price_band", result.price_band
    if result.white_label is not None:
        yield "white_label", result.white_label
