from django.conf import settings
from django.db import connection
from django.http import JsonResponse


def health(request):
    """Liveness/readiness probe (spec §21). Reports node role + DB vendor."""
    db_ok = True
    try:
        connection.cursor().execute("SELECT 1")
    except Exception:  # pragma: no cover
        db_ok = False
    return JsonResponse(
        {
            "status": "ok" if db_ok else "degraded",
            "role": settings.ROLE,
            "terminal": settings.TERMINAL if settings.ROLE == "till" else None,
            "db": connection.vendor,
            "currency": settings.CURRENCY,
        }
    )
