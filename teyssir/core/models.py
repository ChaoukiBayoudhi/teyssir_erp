"""Abstract base models shared across apps + local-only tool jobs.

`SyncableModel` is the base for any row that replicates between till nodes and the hub
(spec §4.4): a client-generated UUID primary key makes sync *idempotent* (re-delivery is
a no-op), and `origin_terminal` records provenance.

`ConvertJob` is local-only (never synced) — same pattern as catalog.ScanJob for async OCR.
"""
import uuid

from django.db import models

# Reusable column kwargs: TND money is stored at 3 dp (millime).
# Quantities are whole pieces (IntegerField on models) — QTY kept only for docs/legacy imports.
MONEY = dict(max_digits=14, decimal_places=3)
QTY = dict(max_digits=14, decimal_places=0)  # legacy; prefer models.IntegerField for qty columns


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SyncableModel(UUIDModel, TimeStampedModel):
    origin_terminal = models.CharField(max_length=8, blank=True, default="")

    class Meta:
        abstract = True


class ConvertJob(UUIDModel, TimeStampedModel):
    """Async PDF → Word conversion job (local-only; never synced to the hub).

    Mirrors ScanJob: POST creates PENDING, a worker (inline|thread) runs the conversion,
    the PWA polls until DONE/FAILED, then downloads via FileResponse.
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    STATUSES = [(x, x) for x in (PENDING, RUNNING, DONE, FAILED)]

    FAST = "fast"       # text-dense → PyMuPDF → python-docx
    LAYOUT = "layout"   # full pdf2docx layout fidelity
    AUTO = "auto"       # pick fast vs layout by content analysis
    MODES = [(x, x) for x in (FAST, LAYOUT, AUTO)]

    status = models.CharField(max_length=8, choices=STATUSES, default=PENDING)
    mode = models.CharField(max_length=8, choices=MODES, default=AUTO)
    # Mode actually used by the worker (filled on completion).
    mode_used = models.CharField(max_length=8, blank=True, default="")
    original_name = models.CharField(max_length=255, blank=True, default="")
    # Paths relative to MEDIA_ROOT (portable across Windows/macOS).
    input_path = models.CharField(max_length=512, blank=True, default="")
    output_path = models.CharField(max_length=512, blank=True, default="")
    error = models.TextField(blank=True, default="")
    page_count = models.IntegerField(default=0)
    elapsed_ms = models.IntegerField(default=0)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"ConvertJob {self.id} ({self.status})"
