from django.db import models

from teyssir.core.models import MONEY, TimeStampedModel, UUIDModel


class DocumentCounter(models.Model):
    """Atomic per-(terminal, year, month, doc_type) sequence (spec §4.4/§7.3).

    The ONLY allocator of fiscal numbers. Gapless within a month, resets monthly,
    allocated by a row-locked increment — works fully offline on each till.
    """

    terminal = models.CharField(max_length=8)
    year = models.IntegerField()
    month = models.IntegerField()
    doc_type = models.CharField(max_length=12)  # FACTURE / TICKET / AVOIR
    seq = models.IntegerField(default=0)

    class Meta:
        unique_together = [("terminal", "year", "month", "doc_type")]

    def __str__(self):
        return f"{self.terminal}-{self.year}{self.month:02d}/{self.doc_type}={self.seq}"


class FiscalStampConfig(models.Model):
    """Configurable timbre fiscal (spec §2 M2b). Default FACTURE = 1.000 DT, per-doc-type
    override; the resolved value is snapshotted onto each invoice at issue time."""

    doc_type = models.CharField(max_length=12, unique=True)  # FACTURE / TICKET / AVOIR
    amount = models.DecimalField(default=0, **MONEY)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.doc_type}: {self.amount}"


class Invoice(UUIDModel, TimeStampedModel):
    """Immutable fiscal document (spec §7.3). Corrections are made via a credit note (AVOIR),
    never by editing this row. `timbre_amount_snapshot` freezes the stamp that applied."""

    FACTURE = "FACTURE"
    TICKET = "TICKET"
    AVOIR = "AVOIR"
    DOC_TYPES = [(x, x) for x in (FACTURE, TICKET, AVOIR)]

    sale = models.OneToOneField("sales.Sale", on_delete=models.PROTECT, related_name="invoice")
    doc_type = models.CharField(max_length=12, choices=DOC_TYPES, default=FACTURE)
    terminal = models.CharField(max_length=8)
    year = models.IntegerField()
    month = models.IntegerField()
    seq = models.IntegerField()
    fiscal_number = models.CharField(max_length=32, unique=True)  # C1-YYYYMM-XXXX
    timbre_amount_snapshot = models.DecimalField(default=0, **MONEY)
    immutable = models.BooleanField(default=True)

    def __str__(self):
        return self.fiscal_number
