# Generated manually for Phase 7 furniture / reference-based products.

from django.db import migrations, models


def backfill_product_type_and_reference(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    for p in Product.objects.all():
        if p.is_book or (p.isbn or "").strip():
            p.product_type = "book"
            p.is_book = True
            p.reference = ""
        else:
            p.product_type = "furniture"
            p.is_book = False
            p.reference = p.sku or ""
        p.save(update_fields=["product_type", "is_book", "reference"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0004_qty_integer_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="brand",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="product",
            name="color",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="product",
            name="product_type",
            field=models.CharField(
                choices=[("book", "Book"), ("furniture", "Furniture")],
                db_index=True,
                default="furniture",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="reference",
            field=models.CharField(blank=True, db_index=True, default="", max_length=48),
        ),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.UniqueConstraint(
                condition=~models.Q(reference=""),
                fields=("reference",),
                name="catalog_product_reference_uniq",
            ),
        ),
        migrations.RunPython(backfill_product_type_and_reference, noop),
    ]
