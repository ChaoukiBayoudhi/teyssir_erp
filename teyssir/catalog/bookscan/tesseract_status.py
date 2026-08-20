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
    }
    cmd = resolve_tesseract_cmd()
    if not cmd:
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
    except Exception as exc:
        out["langs_error"] = str(exc)[:160]
    return out


def warn_if_tesseract_missing() -> None:
    """Startup log: warn once if the binary is missing (never raise)."""
    try:
        st = tesseract_status(include_langs=False)
        if not st.get("installed"):
            logger.warning(
                "Tesseract binary not found (configured=%r). "
                "Book OCR will fall back to manual/vision. "
                "Set TEYSSIR_TESSERACT_CMD or install Tesseract.",
                getattr(settings, "TESSERACT_CMD", None),
            )
        else:
            logger.info("Tesseract ready at %s", st.get("path"))
    except Exception as exc:  # pragma: no cover
        logger.warning("Tesseract startup check failed: %s", exc)
