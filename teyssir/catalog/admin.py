from django.contrib import admin

from .models import Barcode, Category, Product, TaxRate


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("sku", "reference", "name_fr", "product_type", "brand", "color",
                    "category", "tax_rate", "sale_price", "qty_on_hand", "active")
    search_fields = ("sku", "reference", "internal_code", "name_fr", "name_ar", "isbn", "brand")
    list_filter = ("active", "product_type", "is_book", "category")


admin.site.register([Category, TaxRate, Barcode])
