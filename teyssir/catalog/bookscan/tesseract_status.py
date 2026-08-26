"""Cheap Tesseract runtime probe for /health/ and Diagnostics (no OCR of images)."""
from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings

logger = logging.getLogger("teyssir.ocr")


def resolve_tesseract_cmd() -> str | None:
    """Return an absolute (or PATH-resolved) tesseract binary, or None."""
    from .ocr import configure_tesseract

    try:
        import pytesseract
    except Exception:
        return None
    configured = (getattr(settings, "TESSERACT_CMD", None) or "").strip()
    if configured and configured != "tesseract" and Path(configured).is_file():
        pytesseract.pytesseract.tesseract_cmd = configured
        return configured
    return configure_tesseract(pytesseract)


def tesseract_status(*, include_langs: bool = True) -> dict:
    """Snapshot used by /health/ and /api/v1/diagnostics."""
    out: dict = {
        "installed": False,
        "path": None,
        "langs": [],
        "configured_cmd": getattr(settings, "TESSERACT_CMD", None),
        "recommended_langs": ["eng", "fra", "ara"],
        "missing_langs": [],
    }
    cmd = resolve_tesseract_cmd()
    if not cmd:
        out["missing_langs"] = list(out["recommended_langs"])
        return out
    out["path"] = cmd
    out["installed"] = True
    if not include_langs:
        return out
    try:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = cmd
        langs = list(pytesseract.get_languages(config="") or [])
        out["langs"] = sorted(c for c in langs if c not in ("osd", "snum"))
        out["missing_langs"] = [
            c for c in out["recommended_langs"] if c not in out["langs"]
        ]
    except Exception as exc:
        out["langs_error"] = str(exc)[:160]
        out["missing_langs"] = list(out["recommended_langs"])
    return out


def warn_if_tesseract_missing() -> None:
    """Startup log: warn once if the binary or ara/fra packs are missing (never raise)."""
    try:
        st = tesseract_status(include_langs=True)
        if not st.get("installed"):
            logger.warning(
                "Tesseract binary not found (configured=%r). "
                "Book OCR will fall back to manual/vision. "
                "Set TEYSSIR_TESSERACT_CMD or install Tesseract.",
                getattr(settings, "TESSERACT_CMD", None),
            )
        else:
            logger.info("Tesseract ready at %s (langs=%s)", st.get("path"), st.get("langs"))
            missing = st.get("missing_langs") or []
            if "ara" in missing or "fra" in missing:
                logger.warning(
                    "Tesseract missing language packs %s — Arabic/French covers will "
                    "OCR as Latin garbage. Install: brew install tesseract-lang "
                    "(macOS) or UB Mannheim installer with eng+fra+ara (Windows).",
                    missing,
                )
    except Exception as exc:  # pragma: no cover
        logger.warning("Tesseract startup check failed: %s", exc)
