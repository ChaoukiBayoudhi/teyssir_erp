from django.conf import settings
from django.db import models

from teyssir.catalog.models import Product
from teyssir.core.models import MONEY, QTY, SyncableModel


class Quotation(SyncableModel):
    """A non-binding price quote (devis) that can convert into a sale (spec §13.2)."""

    OPEN = "OPEN"
    CONVERTED = "CONVERTED"
    EXPIRED = "EXPIRED"
    STATUS = [(x, x) for x in (OPEN, CONVERTED, EXPIRED)]

    customer_id = models.CharField(max_length=64, blank=True, default="")
    terminal = models.CharField(max_length=8, blank=True, default="")
    status = models.CharField(max_length=10, choices=STATUS, default=OPEN)
    valid_until = models.DateField(null=True, blank=True)
    subtotal = models.DecimalField(default=0, **MONEY)
    tax_total = models.DecimalField(default=0, **MONEY)
    total = models.DecimalField(default=0, **MONEY)          # ex-timbre (estimate)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )


class QuotationLine(SyncableModel):
    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    qty = models.DecimalField(default=1, **QTY)
    unit_price = models.DecimalField(default=0, **MONEY)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    line_total = models.DecimalField(default=0, **MONEY)


class Reservation(SyncableModel):
    """A stock hold for a customer with an expiry (spec §13.2)."""

    HELD = "HELD"
    RELEASED = "RELEASED"
    FULFILLED = "FULFILLED"
    STATUS = [(x, x) for x in (HELD, RELEASED, FULFILLED)]

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="reservations")
    customer_id = models.CharField(max_length=64, blank=True, default="")
    terminal = models.CharField(max_length=8, blank=True, default="")
    qty = models.DecimalField(default=1, **QTY)
    status = models.CharField(max_length=10, choices=STATUS, default=HELD)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
