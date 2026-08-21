import os

from django.conf import settings
from django.db import connection
from django.http import JsonResponse

from teyssir.catalog.bookscan.tesseract_status import tesseract_status
from teyssir.core.llm import status as llm_status


def health(request):
    """Liveness/readiness probe (spec §21). Reports node role + DB + OCR runtime."""
    db_ok = True
    try:
        connection.cursor().execute("SELECT 1")
    except Exception:  # pragma: no cover
        db_ok = False
    tess = tesseract_status(include_langs=True)
    printer_target = os.environ.get("TEYSSIR_PRINTER", "dummy")
    return JsonResponse(
        {
            "status": "ok" if db_ok else "degraded",
            "role": settings.ROLE,
            "terminal": settings.TERMINAL if settings.ROLE == "till" else None,
            "db": connection.vendor,
            "currency": settings.CURRENCY,
            "printer": {"target": printer_target},
            "llm": llm_status(ping=False),
            "tesseract": {
                "installed": tess.get("installed", False),
                "path": tess.get("path"),
                "langs": tess.get("langs") or [],
                "missing_langs": tess.get("missing_langs") or [],
                "recommended_langs": tess.get("recommended_langs") or ["eng", "fra", "ara"],
            },
        },
        status=200 if db_ok else 503,   # so monitors/probes see the failure in the HTTP status
    )
