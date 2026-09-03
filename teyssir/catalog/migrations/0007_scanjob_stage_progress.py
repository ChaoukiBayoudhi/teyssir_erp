# Phase 15.5 — additive nullable stage/progress on ScanJob (poll UI). Safe for existing rows.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0006_product_search_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="scanjob",
            name="stage",
            field=models.CharField(blank=True, default="queued", max_length=32, null=True),
        ),
        migrations.AddField(
            model_name="scanjob",
            name="progress",
            field=models.PositiveSmallIntegerField(blank=True, default=0, null=True),
        ),
    ]
