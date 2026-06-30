from django.db import models

from teyssir.core.models import MONEY, SyncableModel


class Customer(SyncableModel):
    """A customer who may buy on credit (e.g. a school account, spec §M9)."""

    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=32, blank=True, default="")
    matricule_fiscal = models.CharField(max_length=32, blank=True, default="")
    credit_limit = models.DecimalField(default=0, **MONEY)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class AccountEntry(SyncableModel):
    """Append-only customer-account ledger. Balance = Σ CHARGE − Σ PAYMENT."""

    CHARGE = "CHARGE"
    PAYMENT = "PAYMENT"
    TYPES = [(CHARGE, CHARGE), (PAYMENT, PAYMENT)]

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="entries")
    entry_type = models.CharField(max_length=8, choices=TYPES)
    amount = models.DecimalField(**MONEY)        # positive magnitude
    ref_type = models.CharField(max_length=24, blank=True, default="")
    ref_id = models.CharField(max_length=64, blank=True, default="")
    note = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["customer", "created_at"])]

    def __str__(self):
        return f"{self.entry_type} {self.amount} ({self.customer_id})"
