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
    state.last_pull_cursor = resp["cursor"]
    state.save(update_fields=["last_pull_cursor"])
    return {"applied": applied, "cursor": resp["cursor"]}


def sync_now(hub_url, key):
    """One full exchange: push local transactions, then pull master-data changes."""
    push = push_outbox(hub_url, key)
    pull = pull_master(hub_url, key)
    SyncState.objects.filter(singleton=True).update(last_push_at=timezone.now())
    return {"push": push, "pull": pull}
