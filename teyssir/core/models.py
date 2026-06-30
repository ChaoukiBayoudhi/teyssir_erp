"""Abstract base models shared across apps.

`SyncableModel` is the base for any row that replicates between till nodes and the hub
(spec §4.4): a client-generated UUID primary key makes sync *idempotent* (re-delivery is
a no-op), and `origin_terminal` records provenance.
"""
import uuid

from django.db import models

# Reusable column kwargs: TND money is stored at 3 dp (millime); quantities at 3 dp.
MONEY = dict(max_digits=14, decimal_places=3)
QTY = dict(max_digits=14, decimal_places=3)


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SyncableModel(UUIDModel, TimeStampedModel):
    origin_terminal = models.CharField(max_length=8, blank=True, default="")

    class Meta:
        abstract = True
