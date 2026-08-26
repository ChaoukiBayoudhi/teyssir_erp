# Generated manually for ConvertJob (async PDF → Word).

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="ConvertJob",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "PENDING"),
                            ("RUNNING", "RUNNING"),
                            ("DONE", "DONE"),
                            ("FAILED", "FAILED"),
                        ],
                        default="PENDING",
                        max_length=8,
                    ),
                ),
                (
                    "mode",
                    models.CharField(
                        choices=[("fast", "fast"), ("layout", "layout"), ("auto", "auto")],
                        default="auto",
                        max_length=8,
                    ),
                ),
                ("mode_used", models.CharField(blank=True, default="", max_length=8)),
                ("original_name", models.CharField(blank=True, default="", max_length=255)),
                ("input_path", models.CharField(blank=True, default="", max_length=512)),
                ("output_path", models.CharField(blank=True, default="", max_length=512)),
                ("error", models.TextField(blank=True, default="")),
                ("page_count", models.IntegerField(default=0)),
                ("elapsed_ms", models.IntegerField(default=0)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
