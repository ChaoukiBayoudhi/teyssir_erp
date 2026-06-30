from django.db import models

from teyssir.core.models import MONEY, QTY, SyncableModel


class TaxRate(SyncableModel):
    """TVA rate (spec §15). Tunisia: 7% (books/manuels/journaux/fournitures scolaires),
    13%, 19%, 0%/exonéré."""

    name = models.CharField(max_length=32)
    rate_percent = models.DecimalField(max_digits=5, decimal_places=2)
    is_default = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class Category(SyncableModel):
    name_fr = models.CharField(max_length=128)
    name_ar = models.CharField(max_length=128, blank=True, default="")
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )

    def __str__(self):
        return self.name_fr


class Product(SyncableModel):
    sku = models.CharField(max_length=48, unique=True)
    internal_code = models.CharField(max_length=48, blank=True, default="")
    name_fr = models.CharField(max_length=200)
    name_ar = models.CharField(max_length=200, blank=True, default="")
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL)
    tax_rate = models.ForeignKey(TaxRate, null=True, blank=True, on_delete=models.SET_NULL)

    cost_avg = models.DecimalField(default=0, **MONEY)        # weighted average cost (§14.2)
    sale_price = models.DecimalField(default=0, **MONEY)
    qty_on_hand = models.DecimalField(default=0, **QTY)        # cached fold over the ledger
    reorder_point = models.DecimalField(default=0, **QTY)
    reorder_qty = models.DecimalField(default=0, **QTY)

    is_book = models.BooleanField(default=False)
    isbn = models.CharField(max_length=13, blank=True, default="")
    allow_negative = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.sku} — {self.name_fr}"


class Barcode(SyncableModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="barcodes")
    value = models.CharField(max_length=64, db_index=True)
    symbology = models.CharField(max_length=16, default="EAN13")  # EAN13 / ISBN / CODE128

    class Meta:
        unique_together = [("value", "symbology")]

    def __str__(self):
        return f"{self.symbology}:{self.value}"
