"""Async execution seam for book scans (docs/BOOK-OCR-ARCHITECTURE.md §6).

`enqueue_scan` dispatches the OCR work to the backend named by ``settings.SCAN_EXECUTOR``:

* ``inline``  — run synchronously (default). Deterministic for tests and fine for fast OCR.
* ``thread``  — run in a background daemon thread so a slow OCR engine (a vision LLM ~tens of
  seconds) doesn't block the HTTP request; the client polls the ScanJob until it's DONE.

To scale later (e.g. on the hub), add a ``celery``/``django-q`` backend here — the HTTP API
(a job id you poll) does not change. Uses only the stdlib (threading), no extra dependency.
"""
import contextlib
import os
import tempfile

from django.conf import settings


@contextlib.contextmanager
def local_image_paths(image_fields):
    """Yield real filesystem paths for a list of ImageField values. Local storage exposes ``.path``
    directly; remote storage (S3/MinIO) has none, so we stream each file to a temp copy that OCR can
    read, and clean the temps up afterwards. Keeps the OCR engines storage-agnostic (Phase 6)."""
    temps, paths = [], []
    try:
        for field in image_fields:
            try:
                paths.append(field.path)                     # local FileSystemStorage
            except (NotImplementedError, ValueError):
                suffix = os.path.splitext(field.name)[1] or ".img"
                fd, tmp = tempfile.mkstemp(suffix=suffix)
                field.open("rb")
                with os.fdopen(fd, "wb") as out:
                    out.write(field.read())
                field.close()
                temps.append(tmp)
                paths.append(tmp)
        yield paths
    finally:
        for tmp in temps:
            with contextlib.suppress(OSError):
                os.remove(tmp)


def run_scan_job(job_id):
    """Execute one scan job to completion, persisting the draft (or the failure). Never raises —
    a failed OCR records FAILED + the error rather than losing the job."""
    from teyssir.catalog.models import ProductImage, ScanJob

    from .services import scan_book

    job = ScanJob.objects.get(pk=job_id)
    try:
        images = ProductImage.objects.filter(id__in=job.image_ids).order_by("order")
        with local_image_paths([img.image for img in images]) as paths:
            draft, ocr_text = scan_book(paths, isbn=job.isbn)
        job.result = draft.as_dict()
        job.ocr_text = ocr_text or ""
        job.status = ScanJob.DONE
        if job.image_ids and ocr_text:
            ProductImage.objects.filter(pk=job.image_ids[0]).update(ocr_text=ocr_text)
    except Exception as exc:                       # noqa: BLE001 — record, never lose the job
        job.status = ScanJob.FAILED
        job.error = str(exc)
    job.save()
    return job


def enqueue_scan(job_id):
    if settings.SCAN_EXECUTOR == "thread":
        import threading

        from django.db import connection

        def _worker():
            try:
                run_scan_job(job_id)
            finally:
                connection.close()                 # release this thread's own DB connection
        threading.Thread(target=_worker, daemon=True).start()
    else:
        run_scan_job(job_id)                       # inline (synchronous) — the default
