"""(De)serialization for sync (spec §4.4).

We use Django's own JSON serializer: it is FK-aware (relations are stored by PK, and
our PKs are client-generated UUIDs → idempotent), and `deserialize(...).save()` uses a
*raw* save that preserves `created_at`/`updated_at` (so the hub timestamp survives and can
drive the pull cursor). Payloads travel as JSON strings — no Decimal/UUID encoding issues.
"""
from django.core import serializers


def dump(objs) -> str:
    """Serialize an ordered list of model instances to a JSON string."""
    return serializers.serialize("json", objs)


def load(records_json):
    """Yield DeserializedObjects from a JSON string (call .save() to persist)."""
    return list(serializers.deserialize("json", records_json))
