from django.db import models

from teyssir.core.models import TimeStampedModel, UUIDModel


class SyncOutbox(UUIDModel, TimeStampedModel):
    """Append-only change log pushed from a till to the hub (spec §4.4).

    Idempotent by UUID (re-delivery is a no-op at the hub). `seq` is a per-node monotonic
    counter for ordering; `payload` is a Django-serialized JSON string of the row aggregate.
    """

    entity = models.CharField(max_length=64)        # e.g. "sales.Sale"
    entity_id = models.CharField(max_length=64)
    op = models.CharField(max_length=8, default="UPSERT")
    payload = models.TextField(default="[]")        # serialized JSON aggregate
    origin_terminal = models.CharField(max_length=8, default="")
    seq = models.BigIntegerField()
    pushed = models.BooleanField(default=False)
    pushed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["pushed", "seq"])]

    def __str__(self):
        return f"{self.entity}#{self.entity_id} seq={self.seq}"


class SyncState(models.Model):
    """Per-node sync bookkeeping (one row per till). Tracks the last pull cursor."""

    singleton = models.BooleanField(default=True, unique=True)
    last_pull_cursor = models.CharField(max_length=64, blank=True, default="")
    last_push_at = models.DateTimeField(null=True, blank=True)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(singleton=True)
        return obj
