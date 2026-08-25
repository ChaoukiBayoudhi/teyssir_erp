# Indexes for POS / catalogue product search (portable SQLite + Postgres).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0005_furniture_reference"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="sku",
            field=models.CharField(db_index=True, max_length=48, unique=True),
        ),
        migrations.AlterField(
            model_name="product",
            name="name_fr",
            field=models.CharField(db_index=True, max_length=200),
        ),
        migrations.AlterField(
            model_name="product",
            name="name_ar",
            field=models.CharField(blank=True, db_index=True, default="", max_length=200),
        ),
        migrations.AlterField(
            model_name="product",
            name="internal_code",
            field=models.CharField(blank=True, db_index=True, default="", max_length=48),
        ),
    ]
