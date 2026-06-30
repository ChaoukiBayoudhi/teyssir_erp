from django.conf import settings
from django.db import models

from teyssir.catalog.models import Product
from teyssir.core.models import MONEY, QTY, SyncableModel


class Supplier(SyncableModel):
    name = models.CharField(max_length=200)
    matricule_fiscal = models.CharField(max_length=32, blank=True, default="")
    phone = models.CharField(max_length=32, blank=True, default="")
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class PurchaseOrder(SyncableModel):
    DRAFT = "DRAFT"
    ORDERED = "ORDERED"
    RECEIVED = "RECEIVED"
    CLOSED = "CLOSED"
    STATUS = [(x, x) for x in (DRAFT, ORDERED, RECEIVED, CLOSED)]

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="orders")
    terminal = models.CharField(max_length=8, blank=True, default="")
    status = models.CharField(max_length=10, choices=STATUS, default=DRAFT)
    note = models.CharField(max_length=200, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    def __str__(self):
        return f"PO {self.id} ({self.status})"


class PurchaseOrderLine(SyncableModel):
    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    qty_ordered = models.DecimalField(**QTY)
    unit_cost = models.DecimalField(default=0, **MONEY)
    qty_received = models.DecimalField(default=0, **QTY)


class GoodsReceipt(SyncableModel):
    po = models.ForeignKey(
        PurchaseOrder, null=True, blank=True, on_delete=models.SET_NULL, related_name="receipts"
    )
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT)
    terminal = models.CharField(max_length=8, blank=True, default="")
    note = models.CharField(max_length=200, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )


class GoodsReceiptLine(SyncableModel):
    gr = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    qty = models.DecimalField(**QTY)
    unit_cost = models.DecimalField(default=0, **MONEY)


class PurchaseInvoice(SyncableModel):
    UNPAID = "UNPAID"
    PAID = "PAID"
    STATUS = [(UNPAID, UNPAID), (PAID, PAID)]

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="invoices")
    po = models.ForeignKey(PurchaseOrder, null=True, blank=True, on_delete=models.SET_NULL)
    supplier_number = models.CharField(max_length=64)     # the supplier's own invoice number
    invoice_date = models.DateField(null=True, blank=True)
    subtotal = models.DecimalField(default=0, **MONEY)
    tva_total = models.DecimalField(default=0, **MONEY)
    total = models.DecimalField(default=0, **MONEY)
    status = models.CharField(max_length=8, choices=STATUS, default=UNPAID)
