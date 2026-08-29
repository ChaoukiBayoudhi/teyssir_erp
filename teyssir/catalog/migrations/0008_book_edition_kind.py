# Additive: optional ISBN path — edition_kind for CNP school vs ISBN novels.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0007_scanjob_stage_progress"),
    ]

    operations = [
        migrations.AddField(
            model_name="book",
            name="edition_kind",
            field=models.CharField(
                blank=True,
                choices=[
                    ("school_cnp", "School / CNP (barcode-only)"),
                    ("isbn_edition", "ISBN edition (978/979)"),
                    ("unknown", "Unknown"),
                ],
                db_index=True,
                default="",
                max_length=16,
            ),
        ),
    ]
