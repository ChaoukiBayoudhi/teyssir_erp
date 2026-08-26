"""Async execution seam for PDF → Word conversions (mirrors catalog.bookscan.jobs).

``enqueue_convert`` dispatches to the backend named by ``settings.CONVERT_EXECUTOR``:

* ``inline`` — run synchronously (default on non-Windows / tests).
* ``thread`` — background daemon thread so a long convert never blocks waitress
  (default on Windows Hub via settings).

Celery/django-q can be added later without changing the HTTP job/poll contract.
"""
from __future__ import annotations

import os
import time

from django.conf import settings
from django.utils import timezone


def run_convert_job(job_id) -> None:
    """Execute one ConvertJob to completion. Never raises — FAILED + error is persisted."""
    from teyssir.core.models import ConvertJob
    from teyssir.core.pdfconvert import convert_pdf_file

    job = ConvertJob.objects.get(pk=job_id)
    job.status = ConvertJob.RUNNING
    job.save(update_fields=["status", "updated_at"])
    t0 = time.perf_counter()
    try:
        media = str(settings.MEDIA_ROOT)
        src = os.path.join(media, job.input_path) if not os.path.isabs(job.input_path) else job.input_path
        if not job.output_path:
            job.output_path = os.path.join("convert", str(job.id), "out.docx")
        dst = os.path.join(media, job.output_path)
        used, profile = convert_pdf_file(src, dst, mode=job.mode or ConvertJob.AUTO)
        job.mode_used = used
        job.page_count = profile.pages
        job.status = ConvertJob.DONE
        job.error = ""
    except Exception as exc:  # noqa: BLE001 — record, never lose the job
        job.status = ConvertJob.FAILED
        job.error = str(exc)[:2000]
    job.elapsed_ms = int((time.perf_counter() - t0) * 1000)
    job.finished_at = timezone.now()
    job.save()


def enqueue_convert(job_id) -> None:
    if getattr(settings, "CONVERT_EXECUTOR", "inline") == "thread":
        import threading

        from django.db import connection

        def _worker():
            try:
                run_convert_job(job_id)
            finally:
                connection.close()
        threading.Thread(target=_worker, daemon=True, name=f"pdfconvert-{job_id}").start()
    else:
        run_convert_job(job_id)
