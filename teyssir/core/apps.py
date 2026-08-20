from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "teyssir.core"

    def ready(self):
        from . import signals  # noqa: F401  (wire SQLite PRAGMAs)
        # Cheap OCR binary check so LaunchAgent / NSSM misconfig shows up in logs early.
        try:
            from teyssir.catalog.bookscan.tesseract_status import warn_if_tesseract_missing

            warn_if_tesseract_missing()
        except Exception:  # pragma: no cover
            pass
