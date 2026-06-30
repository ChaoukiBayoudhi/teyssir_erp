from django.contrib import admin

from .models import Barcode, Category, Product, TaxRate


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("sku", "name_fr", "category", "tax_rate", "sale_price", "qty_on_hand", "active")
    search_fields = ("sku", "internal_code", "name_fr", "name_ar", "isbn")
    list_filter = ("active", "is_book", "category")


admin.site.register([Category, TaxRate, Barcode])
