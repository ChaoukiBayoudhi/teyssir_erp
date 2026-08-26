"""Phase 2F — books_photos regression helpers (offline Tess, Vision optional)."""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from django.conf import settings

from .draft import BookDraft
from .isbn import to_isbn13


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def fixtures_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / "fixtures" / "bookscan" / "expected"


def books_photos_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / "books_photos"


def load_fixture(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_all_fixtures(directory: Path | None = None) -> list[dict]:
    d = directory or fixtures_dir()
    if not d.is_dir():
        return []
    out = []
    for path in sorted(d.glob("*.json")):
        data = load_fixture(path)
        data["_path"] = str(path)
        out.append(data)
    return out


def find_photo(root: Path, needles: list[str], exclude: list[str] | None = None) -> Path | None:
    exclude = exclude or []
    if not root.is_dir():
        return None
    for p in sorted(root.iterdir()):
        if not p.is_file():
            continue
        name = p.name
        if all(n in name for n in needles) and not any(x in name for x in exclude):
            return p
    return None


def resolve_fixture_images(fixture: dict, photos_dir: Path | None = None) -> list[Path]:
    photos = photos_dir or books_photos_dir()
    images = fixture.get("images") or {}
    paths: list[Path] = []
    for role in ("front", "back"):
        spec = images.get(role) or {}
        needles = list(spec.get("needles") or [])
        exclude = list(spec.get("exclude") or [])
        if not needles:
            continue
        hit = find_photo(photos, needles, exclude)
        if hit is None:
            raise FileNotFoundError(
                f"{fixture.get('id')}: no photo matching needles={needles} exclude={exclude} in {photos}"
            )
        paths.append(hit)
    return paths


@dataclass
class CheckResult:
    ok: bool
    code: str
    detail: str = ""


@dataclass
class BookScanRegressionResult:
    fixture_id: str
    label: str
    ok: bool
    ms: float = 0.0
    draft: dict = field(default_factory=dict)
    checks: list[CheckResult] = field(default_factory=list)
    image_names: list[str] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.fixture_id,
            "label": self.label,
            "ok": self.ok,
            "ms": round(self.ms, 1),
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "images": self.image_names,
            "draft": self.draft,
            "checks": [asdict(c) for c in self.checks],
        }


def _title_usable(title: str, raw: dict) -> bool:
    if not (title or "").strip():
        return False
    if raw.get("ocr_garbage_latin") or raw.get("ocr_garbage_arabic") or raw.get("ocr_title_unusable"):
        return False
    return True


def assert_honesty(draft: BookDraft, honesty: dict | None = None) -> list[CheckResult]:
    """Hard rules: checksum ISBN, 619≠ISBN, digit-OCR confidence cap."""
    honesty = honesty or {}
    raw = draft.raw or {}
    checks: list[CheckResult] = []
    isbn = (draft.isbn13 or "").strip()
    barcode = (draft.barcode_raw or "").strip()
    conf = float(draft.confidence or 0)

    never_pref = list(honesty.get("never_isbn13_prefix") or ["619"])
    for pref in never_pref:
        if isbn.startswith(pref):
            checks.append(CheckResult(False, "isbn13_banned_prefix", f"{isbn} starts with {pref}"))
        else:
            checks.append(CheckResult(True, "isbn13_banned_prefix", f"ok vs {pref}"))

    if isbn:
        valid = to_isbn13(isbn)
        if not valid:
            checks.append(CheckResult(False, "isbn13_checksum", f"invalid isbn13={isbn}"))
        elif valid != isbn:
            checks.append(CheckResult(False, "isbn13_checksum", f"normalized {valid} != {isbn}"))
        else:
            checks.append(CheckResult(True, "isbn13_checksum", isbn))
    else:
        checks.append(CheckResult(True, "isbn13_checksum", "empty"))

    if barcode.startswith("619") or (honesty.get("cnp_619_never_isbn13") and barcode.startswith("619")):
        if isbn == barcode or (isbn and isbn.startswith("619")):
            checks.append(CheckResult(False, "cnp_not_isbn", f"CNP {barcode} promoted to isbn13={isbn}"))
        else:
            checks.append(CheckResult(True, "cnp_not_isbn", "619 kept out of isbn13"))
        if draft.barcode_kind and draft.barcode_kind == "isbn13":
            checks.append(CheckResult(False, "cnp_kind", "619 classified as isbn13"))
        elif barcode.startswith("619"):
            checks.append(CheckResult(True, "cnp_kind", draft.barcode_kind or "unset"))

    digit_max = float(honesty.get("digit_ocr_confidence_max", 0.35))
    if raw.get("isbn_from_digit_ocr") and not raw.get("isbn_digit_ocr_confirmed"):
        if conf > digit_max + 1e-9:
            checks.append(
                CheckResult(False, "digit_ocr_confidence", f"conf={conf} > {digit_max}")
            )
        else:
            checks.append(CheckResult(True, "digit_ocr_confidence", f"conf={conf}"))
    else:
        checks.append(CheckResult(True, "digit_ocr_confidence", "n/a"))

    strong = bool(
        raw.get("isbn_from_barcode")
        or raw.get("isbn_digit_ocr_confirmed")
        or (draft.source or "").startswith("openlibrary")
        or raw.get("metadata_hit")
    )
    max_weak = float(honesty.get("max_confidence_without_strong_id", 0.55))
    if honesty.get("high_confidence_requires") and not strong and conf > max_weak + 1e-9:
        checks.append(
            CheckResult(False, "confidence_honesty", f"conf={conf} without strong id (max {max_weak})")
        )
    else:
        checks.append(CheckResult(True, "confidence_honesty", f"conf={conf} strong={strong}"))

    return checks


def assert_expect(draft: BookDraft, expect: dict) -> list[CheckResult]:
    """Soft bibliographic expects with allow_empty."""
    checks: list[CheckResult] = []
    raw = draft.raw or {}
    title = (draft.title or "").strip()
    needles = list(expect.get("title_contains") or [])
    if needles:
        hit = any(n.lower() in title.lower() for n in needles if n)
        if hit:
            checks.append(CheckResult(True, "title_contains", title[:80]))
        elif expect.get("title_allow_empty"):
            # Phone Tess often returns garbage; allow_empty means "do not require hit".
            checks.append(CheckResult(True, "title_contains", f"miss allowed ({title[:40]!r})"))
        else:
            checks.append(CheckResult(False, "title_contains", f"got {title!r}"))

    want_langs = list(expect.get("languages") or [])
    got_langs = [str(x) for x in (draft.languages or [])]
    mode = expect.get("languages_mode") or "includes_any"
    if want_langs:
        if mode == "includes_all":
            ok = all(l in got_langs for l in want_langs)
        else:
            ok = any(l in got_langs for l in want_langs)
        if ok:
            checks.append(CheckResult(True, "languages", str(got_langs)))
        elif expect.get("languages_allow_empty") and not got_langs:
            checks.append(CheckResult(True, "languages", "empty allowed"))
        elif expect.get("languages_allow_empty") and not _title_usable(title, raw):
            # Weak OCR often invents stray lang tags — tolerate when title unusable
            checks.append(CheckResult(True, "languages", f"weak path {got_langs}"))
        else:
            checks.append(CheckResult(False, "languages", f"want {want_langs} got {got_langs}"))

    exp_isbn = (expect.get("isbn13") or "").strip()
    got_isbn = (draft.isbn13 or "").strip()
    if not got_isbn:
        if expect.get("isbn13_allow_empty", True) or not exp_isbn:
            checks.append(CheckResult(True, "isbn13", "empty"))
        else:
            checks.append(CheckResult(False, "isbn13", f"missing expected {exp_isbn}"))
    elif exp_isbn and got_isbn == exp_isbn:
        checks.append(CheckResult(True, "isbn13", got_isbn))
    elif exp_isbn and got_isbn != exp_isbn:
        checks.append(CheckResult(False, "isbn13", f"want {exp_isbn} got {got_isbn}"))
    else:
        # expected empty but got a value — only OK if checksum-valid bookland
        if to_isbn13(got_isbn):
            checks.append(CheckResult(True, "isbn13", f"unexpected but valid {got_isbn}"))
        else:
            checks.append(CheckResult(False, "isbn13", f"invalid unexpected {got_isbn}"))

    exp_bc = (expect.get("barcode_raw") or "").strip()
    got_bc = (draft.barcode_raw or "").strip()
    if not got_bc:
        if expect.get("barcode_raw_allow_empty", True):
            checks.append(CheckResult(True, "barcode_raw", "empty"))
        else:
            checks.append(CheckResult(False, "barcode_raw", f"missing {exp_bc}"))
    elif exp_bc and got_bc == exp_bc:
        checks.append(CheckResult(True, "barcode_raw", got_bc))
    elif exp_bc and got_bc != exp_bc:
        # Same family (both 619 or both 978) counts as soft miss → fail
        checks.append(CheckResult(False, "barcode_raw", f"want {exp_bc} got {got_bc}"))
    else:
        checks.append(CheckResult(True, "barcode_raw", got_bc))

    exp_kind = (expect.get("barcode_kind") or "").strip()
    if got_bc and exp_kind and draft.barcode_kind and draft.barcode_kind != exp_kind:
        checks.append(
            CheckResult(False, "barcode_kind", f"want {exp_kind} got {draft.barcode_kind}")
        )
    elif got_bc and exp_kind:
        checks.append(CheckResult(True, "barcode_kind", draft.barcode_kind or exp_kind))

    exp_price = (expect.get("price") or "").strip()
    got_price = (draft.price or "").strip()
    if not got_price:
        if expect.get("price_allow_empty", True):
            checks.append(CheckResult(True, "price", "empty"))
        else:
            checks.append(CheckResult(False, "price", f"missing {exp_price}"))
    elif exp_price and got_price == exp_price:
        checks.append(CheckResult(True, "price", got_price))
    elif exp_price and got_price != exp_price:
        checks.append(CheckResult(False, "price", f"want {exp_price} got {got_price}"))
    else:
        checks.append(CheckResult(True, "price", got_price))

    return checks


def _offline_enrich(_isbn: str = ""):
    return None


def _offline_enrich_title(_title: str = "", _author: str = ""):
    return None


def run_fixture_scan(
    fixture: dict,
    *,
    photos_dir: Path | None = None,
    vision: bool = False,
    strict_fields: bool = True,
) -> BookScanRegressionResult:
    """Scan one fixture offline (no OpenLibrary). Vision off unless ``vision=True``."""
    from django.test.utils import override_settings

    from .services import scan_book

    fid = fixture.get("id") or "unknown"
    label = fixture.get("label") or fid
    try:
        paths = resolve_fixture_images(fixture, photos_dir)
    except FileNotFoundError as exc:
        return BookScanRegressionResult(
            fixture_id=fid, label=label, ok=False, skipped=True, skip_reason=str(exc)
        )

    overrides = {
        "OCR_VISION_FALLBACK": bool(vision),
        "OCR_PROVIDER": getattr(settings, "OCR_PROVIDER", "tesseract") or "tesseract",
    }
    # Prefer tesseract for regression unless explicitly vision-primary
    if not vision and str(overrides["OCR_PROVIDER"]).lower() == "vision":
        overrides["OCR_PROVIDER"] = "tesseract"

    t0 = time.perf_counter()
    with override_settings(**overrides):
        draft, _text = scan_book(
            [str(p) for p in paths],
            enrich=_offline_enrich,
            enrich_title=_offline_enrich_title,
        )
    ms = (time.perf_counter() - t0) * 1000.0

    checks = assert_honesty(draft, fixture.get("honesty"))
    if strict_fields:
        checks.extend(assert_expect(draft, fixture.get("expect") or {}))
    else:
        # Still run expects but mark as soft (ok always True for reporting)
        for c in assert_expect(draft, fixture.get("expect") or {}):
            checks.append(CheckResult(True, f"soft_{c.code}", c.detail if c.ok else f"SOFT {c.detail}"))

    ok = all(c.ok for c in checks)
    return BookScanRegressionResult(
        fixture_id=fid,
        label=label,
        ok=ok,
        ms=ms,
        draft={
            "title": draft.title,
            "languages": draft.languages,
            "isbn13": draft.isbn13,
            "barcode_raw": draft.barcode_raw,
            "barcode_kind": draft.barcode_kind,
            "price": draft.price,
            "confidence": draft.confidence,
            "source": draft.source,
            "raw": {
                k: (draft.raw or {}).get(k)
                for k in (
                    "isbn_from_barcode",
                    "isbn_from_digit_ocr",
                    "isbn_digit_ocr_confirmed",
                    "ocr_garbage_latin",
                    "ocr_garbage_arabic",
                    "ocr_title_unusable",
                    "vision_fallback",
                )
            },
        },
        checks=checks,
        image_names=[p.name for p in paths],
    )


def run_all_fixtures(
    *,
    photos_dir: Path | None = None,
    fixtures: Path | None = None,
    vision: bool = False,
    strict_fields: bool = True,
    only: set[str] | None = None,
) -> list[BookScanRegressionResult]:
    results = []
    for fx in load_all_fixtures(fixtures):
        if only and fx.get("id") not in only:
            continue
        results.append(
            run_fixture_scan(
                fx,
                photos_dir=photos_dir,
                vision=vision,
                strict_fields=strict_fields,
            )
        )
    return results


def env_wants_live_regression() -> bool:
    return os.environ.get("TEYSSIR_BOOKSCAN_REGRESSION", "").strip().lower() in (
        "1", "true", "yes", "on",
    )
