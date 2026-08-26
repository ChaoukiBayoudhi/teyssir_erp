#!/usr/bin/env python3
"""Validate book-cover preprocess ROIs on books_photos samples.

Usage (from repo root)::

    .venv/bin/python tools/check_book_rois.py
    .venv/bin/python tools/check_book_rois.py --dir books_photos --save-debug /tmp/rois

Phase 1A corpus ground truth (4 pairs):
  Book A Beauty  — 12.39 + #2 verso (ISBN + TN EAN, price 2.000 DT)
  Book B Premier — 12.40 + #2 (ISBN 9789973352743, 17.000 DT)
  Book C History — 12.40 #3 recto + 12.41 verso (NO ISBN, white sticker 4.900 DT)
  Book D Math    — 12.41 #2 + 12.42 verso (NO ISBN, white PVP 4.200 DT)

Critical 2A checks: versos matching *12.41* / *12.42* must detect white_label and
keep it inside barcode_band + price_band.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# Filenames may use a narrow no-break space before "PM" (macOS Photos).


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _is_critical_verso(path: Path) -> bool:
    name = path.name
    # Prefer exact History verso (12.41 without #2) and Math verso (12.42)
    if "12.42" in name and name.endswith(".jpg"):
        return True
    if "12.41" in name and "#2" not in name and name.endswith(".jpg"):
        return True
    return False


def _roi_contains(outer, inner, *, pad: int = 2) -> bool:
    return (
        outer["x0"] - pad <= inner["x0"]
        and outer["y0"] - pad <= inner["y0"]
        and outer["x1"] + pad >= inner["x1"]
        and outer["y1"] + pad >= inner["y1"]
    )


def _sticker_like_reason(prep, wl) -> str | None:
    """Return failure reason if white_label looks like sleeve/torso, else None.

    Critical versos must pick a compact PVP/CNP sticker near the lower cover,
    not a hollow \"any white_label\" (left-edge tall blob).
    """
    w, h = prep.width, prep.height
    cover_area = max(w * h, 1)
    label_area = wl.width * wl.height
    ar = wl.width / float(max(wl.height, 1))
    cx = (wl.x0 + wl.x1) / 2.0 / w
    cy = (wl.y0 + wl.y1) / 2.0 / h
    left_hug = wl.x0 <= max(6, int(w * 0.045))
    tall_frac = wl.height / float(max(h, 1))

    if left_hug and (tall_frac > 0.25 or ar < 1.15):
        return (
            f"looks like left-margin sleeve/torso "
            f"(x0={wl.x0}, tall={tall_frac:.2f}, ar={ar:.2f})"
        )
    if label_area > cover_area * 0.12:
        return f"too large for sticker ({label_area}/{cover_area})"
    if label_area < cover_area * 0.004:
        return f"suspiciously tiny ({label_area}/{cover_area})"
    if ar < 0.7 or ar > 5.0:
        return f"aspect not sticker-like (ar={ar:.2f})"
    if cy < 0.55:
        return f"not near lower cover (cy={cy:.2f})"
    if cx < 0.12 and left_hug:
        return f"edge-hugging left blob (cx={cx:.2f})"
    return None


def _draw_debug(prep, out_path: Path) -> None:
    try:
        import cv2
    except Exception:
        return
    img = cv2.imread(prep.path)
    if img is None:
        return
    colors = {
        "title_band": (255, 180, 0),
        "barcode_band": (0, 200, 0),
        "price_band": (0, 140, 255),
        "white_label": (0, 0, 255),
    }
    for name, box in (
        ("title_band", prep.title_band),
        ("barcode_band", prep.barcode_band),
        ("price_band", prep.price_band),
        ("white_label", prep.white_label),
    ):
        if box is None:
            continue
        cv2.rectangle(
            img,
            (box.x0, box.y0),
            (box.x1, box.y1),
            colors[name],
            2 if name != "white_label" else 3,
        )
        cv2.putText(
            img,
            name,
            (box.x0 + 4, max(16, box.y0 + 18)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            colors[name],
            1,
            cv2.LINE_AA,
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dir",
        type=Path,
        default=_repo_root() / "books_photos",
        help="Sample photo directory",
    )
    ap.add_argument(
        "--save-debug",
        type=Path,
        default=None,
        help="Optional directory for ROI overlay JPEGs",
    )
    ap.add_argument("--json", action="store_true", help="Emit JSON summary")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(_repo_root()))
    from teyssir.catalog.bookscan.preprocess import (  # noqa: E402
        cleanup_preprocess,
        opencv_available,
        preprocess_cover,
    )

    sample_dir: Path = args.dir
    if not sample_dir.is_dir():
        print(f"FAIL: sample dir missing: {sample_dir}", file=sys.stderr)
        return 2

    paths = sorted(sample_dir.glob("*.jpg")) + sorted(sample_dir.glob("*.JPG"))
    # Dedupe while preserving order
    seen = set()
    unique = []
    for p in paths:
        if p.resolve() in seen:
            continue
        seen.add(p.resolve())
        unique.append(p)
    paths = unique

    if not paths:
        print(f"FAIL: no JPGs in {sample_dir}", file=sys.stderr)
        return 2

    print(f"opencv_available={opencv_available()}")
    print(f"samples={len(paths)} dir={sample_dir}")

    rows = []
    failures = []
    critical_ok = 0
    critical_n = 0

    for path in paths:
        prep = None
        try:
            prep = preprocess_cover(str(path))
            d = prep.to_dict()
            row = {
                "file": path.name,
                "method": prep.method,
                "size": [prep.width, prep.height],
                "deskew_deg": prep.deskew_deg,
                "white_label": d["white_label"],
                "barcode_band": d["barcode_band"],
                "price_band": d["price_band"],
                "title_band": d["title_band"],
                "critical_verso": _is_critical_verso(path),
            }
            # Structural checks every sample
            assert prep.width >= 200 and prep.height >= 200, "too small after preprocess"
            assert prep.title_band.height >= prep.height * 0.25, "title_band too short"
            assert prep.barcode_band.y0 >= int(prep.height * 0.45), "barcode_band not lower"
            assert prep.price_band.height > 0, "empty price_band"

            if row["critical_verso"]:
                critical_n += 1
                if prep.white_label is None:
                    failures.append(f"{path.name}: expected white_label on verso sticker")
                else:
                    wl_box = prep.white_label
                    wl = wl_box.to_dict()
                    bb = prep.barcode_band.to_dict()
                    pb = prep.price_band.to_dict()
                    bad = _sticker_like_reason(prep, wl_box)
                    if bad:
                        failures.append(f"{path.name}: white_label not sticker-like: {bad}")
                    elif not _roi_contains(bb, wl, pad=8):
                        failures.append(
                            f"{path.name}: white_label not inside barcode_band "
                            f"(label={wl} band={bb})"
                        )
                    elif not _roi_contains(pb, wl, pad=8):
                        failures.append(
                            f"{path.name}: white_label not inside price_band "
                            f"(label={wl} band={pb})"
                        )
                    else:
                        critical_ok += 1
                        row["white_label_sticker_ok"] = True

            rows.append(row)
            wl_info = "no"
            if prep.white_label:
                wl = prep.white_label
                wl_info = (
                    f"yes[{wl.x0},{wl.y0}-{wl.x1},{wl.y1} "
                    f"ar={wl.width/max(wl.height,1):.2f}]"
                )
            print(
                f"OK  {path.name}: method={prep.method} "
                f"{prep.width}x{prep.height} deskew={prep.deskew_deg:.1f} "
                f"white_label={wl_info}"
                + (" [critical verso]" if row["critical_verso"] else "")
            )
            if args.save_debug is not None:
                safe = path.stem.replace(" ", "_")[:80]
                _draw_debug(prep, args.save_debug / f"{safe}_rois.jpg")
        except Exception as exc:
            failures.append(f"{path.name}: {exc}")
            print(f"ERR {path.name}: {exc}", file=sys.stderr)
        finally:
            if prep is not None:
                cleanup_preprocess([prep])

    if args.json:
        print(json.dumps({"rows": rows, "failures": failures}, indent=2, ensure_ascii=False))

    print(
        f"\ncritical versos with sticker in bands: {critical_ok}/{critical_n} "
        f"(expect History 12.41 + Math 12.42)"
    )
    if critical_n < 2:
        failures.append(
            f"expected ≥2 critical verso samples (*12.41* / *12.42*), found {critical_n}"
        )

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nAll ROI checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
