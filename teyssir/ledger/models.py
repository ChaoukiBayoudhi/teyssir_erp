from django.db import models

from teyssir.core.models import MONEY


class Account(models.Model):
    """A chart-of-accounts account (Tunisian PCG-flavoured codes). Spec §15 (Phase 5)."""

    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    REVENUE = "REVENUE"
    EXPENSE = "EXPENSE"
    TYPES = [(x, x) for x in (ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE)]

    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=120)
    type = models.CharField(max_length=10, choices=TYPES)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"


class JournalEntry(models.Model):
    """A balanced journal entry (sum of debits == sum of credits across its lines).
    `ref_type`/`ref_id` make posting idempotent (one entry per source document)."""

    date = models.DateField()
    memo = models.CharField(max_length=200, blank=True, default="")
    ref_type = models.CharField(max_length=24, blank=True, default="")
    ref_id = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["ref_type", "ref_id"])]


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    debit = models.DecimalField(default=0, **MONEY)
    credit = models.DecimalField(default=0, **MONEY)
