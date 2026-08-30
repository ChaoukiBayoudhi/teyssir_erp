"""Node diagnostics for admin UI (camera note is client-side; rest from server)."""
from __future__ import annotations

import logging
import os
import socket
from pathlib import Path

from django.conf import settings
from django.db import connection

from teyssir.catalog.bookscan.tesseract_status import tesseract_status
try:
    from teyssir.core.llm import status as llm_status
except ImportError:  # LLM slice not merged
    def llm_status(*, ping=False):
        return {"enabled": False, "provider": None, "model": None, "available": False}

logger = logging.getLogger("teyssir.diagnostics")


def _db_status() -> dict:
    try:
        connection.cursor().execute("SELECT 1")
        return {"ok": True, "vendor": connection.vendor, "role": settings.ROLE}
    except Exception as exc:
        return {"ok": False, "vendor": getattr(connection, "vendor", None), "error": str(exc)[:160]}


def _printer_status() -> dict:
    target = os.environ.get("TEYSSIR_PRINTER", "dummy")
    out = {"target": target, "reachable": False, "detail": ""}
    if target == "dummy":
        out["reachable"] = True
        out["detail"] = "dummy (dev/tests — no hardware)"
        return out
    if target.startswith("file:"):
        path = target[len("file:"):]
        parent = str(Path(path).parent)
        out["reachable"] = Path(parent).exists()
        out["detail"] = f"file backend → {path}"
        return out
    if target.startswith("tcp:"):
        try:
            host, port_s = target[len("tcp:"):].split(":")
            port = int(port_s)
            with socket.create_connection((host, port), timeout=2):
                out["reachable"] = True
                out["detail"] = f"tcp {host}:{port} open"
        except Exception as exc:
            out["detail"] = f"tcp unreachable: {exc}"[:160]
        return out
    out["detail"] = f"unknown target {target!r}"
    return out


def _ocr_smoke() -> dict:
    """Tiny synthetic OCR when Tesseract is present (best-effort, never raises)."""
    tess = tesseract_status(include_langs=True)
    out = {"working": False, "provider": getattr(settings, "OCR_PROVIDER", "tesseract"),
           "tesseract": tess}
    if not tess.get("installed"):
        out["detail"] = "tesseract missing"
        return out
    try:
        import tempfile

        from PIL import Image, ImageDraw

        from teyssir.catalog.bookscan.ocr import TesseractOcrProvider

        img = Image.new("RGB", (320, 80), "white")
        ImageDraw.Draw(img).text((10, 25), "Teyssir OCR", fill="black")
        path = os.path.join(tempfile.mkdtemp(), "ocr-smoke.png")
        img.save(path)
        text, draft = TesseractOcrProvider().extract(path, role="front")
        blob = f"{text} {draft.title}".lower()
        out["working"] = "teyssir" in blob or "ocr" in blob or bool((draft.title or "").strip())
        out["detail"] = "ok" if out["working"] else "no text from smoke image"
        out["confidence"] = draft.confidence
    except Exception as exc:
        out["detail"] = str(exc)[:160]
    return out


def collect_diagnostics(*, ping_llm: bool = True) -> dict:
    """Full snapshot for the Diagnostics page."""
    return {
        "status": "ok",
        "role": settings.ROLE,
        "terminal": settings.TERMINAL if settings.ROLE == "till" else None,
        "store_code": settings.STORE_CODE,
        "db": _db_status(),
        "tesseract": tesseract_status(include_langs=True),
        "ocr": _ocr_smoke(),
        "printer": _printer_status(),
        "llm": llm_status(ping=ping_llm),
        "camera": {
            "note": "Browser MediaDevices — checked in the Diagnostics UI (HTTPS or localhost).",
        },
    }
