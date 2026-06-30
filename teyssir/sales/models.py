from django.conf import settings
from django.db import models

from teyssir.catalog.models import Product
from teyssir.core.models import MONEY, QTY, SyncableModel


class CashSession(SyncableModel):
    """Cashier shift with opening float and Z-close variance (spec §13.3)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    terminal = models.CharField(max_length=8)
    opened_at = models.DateTimeField(auto_now_add=True)
    opening_float = models.DecimalField(default=0, **MONEY)
    closed_at = models.DateTimeField(null=True, blank=True)
    counted_cash = models.DecimalField(null=True, blank=True, **MONEY)
    variance = models.DecimalField(null=True, blank=True, **MONEY)


class Sale(SyncableModel):
    DRAFT = "DRAFT"
    FINALIZED = "FINALIZED"
    VOIDED = "VOIDED"
    REFUNDED = "REFUNDED"
    STATUS = [(x, x) for x in (DRAFT, FINALIZED, VOIDED, REFUNDED)]

    terminal = models.CharField(max_length=8)
    cash_session = models.ForeignKey(
        CashSession, null=True, blank=True, on_delete=models.SET_NULL
    )
    customer_id = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=10, choices=STATUS, default=DRAFT)

    subtotal = models.DecimalField(default=0, **MONEY)
    discount = models.DecimalField(default=0, **MONEY)
    tax_total = models.DecimalField(default=0, **MONEY)
    timbre_amount_snapshot = models.DecimalField(default=0, **MONEY)
    total = models.DecimalField(default=0, **MONEY)
    currency = models.CharField(max_length=3, default="TND")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    def __str__(self):
        return f"Sale {self.id} ({self.status})"


class SaleLine(SyncableModel):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    qty = models.DecimalField(default=1, **QTY)
    unit_price = models.DecimalField(default=0, **MONEY)
    discount = models.DecimalField(default=0, **MONEY)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    line_total = models.DecimalField(default=0, **MONEY)


class Payment(SyncableModel):
    CASH = "CASH"
    CARD = "CARD"        # recorded tender only; processed on the bank TPE (spec §13.4)
    ACCOUNT = "ACCOUNT"
    VOUCHER = "VOUCHER"
    METHODS = [(x, x) for x in (CASH, CARD, ACCOUNT, VOUCHER)]

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="payments")
    method = models.CharField(max_length=8, choices=METHODS)
    amount = models.DecimalField(default=0, **MONEY)
    tpe_ref = models.CharField(max_length=64, blank=True, default="")
    received_at = models.DateTimeField(auto_now_add=True)


class Return(SyncableModel):
    """Credit note (AVOIR) — reverses part/all of a sale. The original invoice is never
    edited (spec §7.3/§13). Carries its own per-terminal+month AVOIR series number."""

    original_sale = models.ForeignKey(
        Sale, null=True, blank=True, on_delete=models.SET_NULL, related_name="returns"
    )
    terminal = models.CharField(max_length=8)
    reason = models.CharField(max_length=200, blank=True, default="")

    number = models.CharField(max_length=32, blank=True, default="")  # C1-YYYYMM-XXXX (AVOIR)
    year = models.IntegerField(null=True, blank=True)
    month = models.IntegerField(null=True, blank=True)
    seq = models.IntegerField(null=True, blank=True)

    subtotal = models.DecimalField(default=0, **MONEY)
    tax_total = models.DecimalField(default=0, **MONEY)
    timbre_amount_snapshot = models.DecimalField(default=0, **MONEY)
    total = models.DecimalField(default=0, **MONEY)            # refunded amount (magnitude)
    refund_method = models.CharField(max_length=8, default=Payment.CASH)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    def __str__(self):
        return self.number or f"Return {self.id}"


class ReturnLine(SyncableModel):
    ret = models.ForeignKey(Return, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    qty = models.DecimalField(default=1, **QTY)
    unit_price = models.DecimalField(default=0, **MONEY)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    line_total = models.DecimalField(default=0, **MONEY)
