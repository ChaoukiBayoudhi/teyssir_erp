"""Till-side sync client (spec §4.4).

Runs on each till: pushes the local outbox to the hub and pulls master-data changes.
Uses stdlib urllib (no extra dependency). The HTTP calls are isolated in `_post`/`_get`
so the service-layer logic stays unit-testable without a network.
"""
import json
import urllib.request
from urllib.parse import quote

from django.utils import timezone

from .models import SyncOutbox, SyncState
from .services import apply_master_changes


def _post(url, key, data):
    req = urllib.request.Request(
        url, data=json.dumps(data).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-Sync-Key": key},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _get(url, key):
    req = urllib.request.Request(url, headers={"X-Sync-Key": key})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_missing_media(hub_url, key, fetch=None):
    """Download any product-image files whose row synced but whose file is absent locally
    (media replication, docs/BOOK-OCR §5). `fetch(name) -> bytes` is injectable for tests."""
    from django.core.files.base import ContentFile

    from teyssir.catalog.models import ProductImage

    def _default(name):
        req = urllib.request.Request(f"{hub_url}/media/{name}", headers={"X-Sync-Key": key})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()

    fetch = fetch or _default
    fetched = 0
    for img in ProductImage.objects.filter(product__isnull=False).exclude(image=""):
        if img.image and not img.image.storage.exists(img.image.name):
            try:
                data = fetch(img.image.name)
            except Exception:
                continue
            img.image.storage.save(img.image.name, ContentFile(data))
            fetched += 1
    return fetched


def push_outbox(hub_url, key):
    pending = list(SyncOutbox.objects.filter(pushed=False).order_by("seq"))
    if not pending:
        return {"pushed": 0}
    entries = [
        {"id": str(e.id), "entity": e.entity, "entity_id": e.entity_id, "op": e.op,
         "payload": e.payload, "origin_terminal": e.origin_terminal, "seq": e.seq}
        for e in pending
    ]
    resp = _post(f"{hub_url}/api/v1/sync/push", key, {"entries": entries})
    acked = {a["id"] for a in resp.get("applied", [])}
    SyncOutbox.objects.filter(id__in=acked).update(pushed=True, pushed_at=timezone.now())
    return {"pushed": len(acked), "reconciliation_warnings": resp.get("reconciliation_warnings", [])}


def pull_master(hub_url, key):
    state = SyncState.get()
    url = f"{hub_url}/api/v1/sync/pull"
    if state.last_pull_cursor:
        url += f"?since={quote(state.last_pull_cursor)}"
    resp = _get(url, key)
    applied = apply_master_changes(resp["records"], config=resp.get("config"))
    media = fetch_missing_media(hub_url, key)   # download any new cover image files
    state.last_pull_cursor = resp["cursor"]
    state.save(update_fields=["last_pull_cursor"])
    return {"applied": applied, "media": media, "cursor": resp["cursor"]}


def sync_now(hub_url, key):
    """One full exchange: push local transactions, then pull master-data changes."""
    push = push_outbox(hub_url, key)
    pull = pull_master(hub_url, key)
    SyncState.objects.filter(singleton=True).update(last_push_at=timezone.now())
    return {"push": push, "pull": pull}
