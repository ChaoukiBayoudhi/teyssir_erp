"""Local FS content-hash cache for dual-image Vision LLM results (P15-T3).

Keys are SHA-256 of the downscaled JPEG bytes actually sent to Ollama (front +
optional back) plus model name and max_edge — identical visual inputs hit even
when filenames differ. Entries expire by TTL and the directory is capped by
entry count (oldest mtime evicted first).
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from django.conf import settings

logger = logging.getLogger("teyssir.vision")

DEFAULT_TTL_SECONDS = 7 * 24 * 3600  # 7 days
DEFAULT_MAX_ENTRIES = 200


def cache_enabled() -> bool:
    return bool(getattr(settings, "VISION_CACHE_ENABLED", True))


def cache_dir() -> Path:
    configured = getattr(settings, "VISION_CACHE_DIR", None)
    if configured:
        path = Path(configured)
    else:
        base = Path(getattr(settings, "BASE_DIR", Path.cwd()))
        path = base / "media" / "vision_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ttl_seconds() -> int:
    try:
        return max(0, int(getattr(settings, "VISION_CACHE_TTL_SECONDS", DEFAULT_TTL_SECONDS) or 0))
    except (TypeError, ValueError):
        return DEFAULT_TTL_SECONDS


def max_entries() -> int:
    try:
        return max(1, int(getattr(settings, "VISION_CACHE_MAX_ENTRIES", DEFAULT_MAX_ENTRIES) or DEFAULT_MAX_ENTRIES))
    except (TypeError, ValueError):
        return DEFAULT_MAX_ENTRIES


def content_hash(
    image_jpeg_bytes: list[bytes],
    *,
    model: str,
    max_edge: int,
) -> str:
    """Stable key from the exact JPEG payloads Vision would send."""
    h = hashlib.sha256()
    h.update(b"v1\0")
    h.update(model.encode("utf-8", errors="replace"))
    h.update(b"\0")
    h.update(str(int(max_edge)).encode())
    h.update(b"\0")
    for blob in image_jpeg_bytes:
        h.update(str(len(blob)).encode())
        h.update(b"\0")
        h.update(blob)
        h.update(b"\0")
    return h.hexdigest()


def _entry_path(key: str) -> Path:
    return cache_dir() / f"{key}.json"


def get(key: str) -> dict[str, Any] | None:
    """Return cached payload ``{raw, draft}`` or None if missing/expired."""
    if not cache_enabled() or not key:
        return None
    path = _entry_path(key)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.info("vision cache read failed: %s", exc)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None

    created = data.get("created_at")
    ttl = ttl_seconds()
    if ttl > 0 and created is not None:
        try:
            age = time.time() - float(created)
            if age > ttl:
                path.unlink(missing_ok=True)
                return None
        except (TypeError, ValueError, OSError):
            path.unlink(missing_ok=True)
            return None

    raw = data.get("raw")
    draft = data.get("draft")
    if not isinstance(raw, str) or not isinstance(draft, dict):
        return None
    return {"raw": raw, "draft": draft, "key": key}


def put(key: str, *, raw: str, draft: dict[str, Any], model: str = "") -> None:
    """Persist a Vision result; enforce max entry count after write."""
    if not cache_enabled() or not key:
        return
    path = _entry_path(key)
    payload = {
        "created_at": time.time(),
        "model": model,
        "raw": raw if isinstance(raw, str) else "",
        "draft": draft if isinstance(draft, dict) else {},
    }
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.info("vision cache write failed: %s", exc)
        return
    _enforce_size_cap()


def _enforce_size_cap() -> None:
    """Keep at most ``VISION_CACHE_MAX_ENTRIES`` files (oldest mtime first)."""
    root = cache_dir()
    try:
        files = sorted(
            (p for p in root.glob("*.json") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
    except OSError:
        return
    cap = max_entries()
    overflow = len(files) - cap
    if overflow <= 0:
        return
    for stale in files[:overflow]:
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            pass


def clear() -> int:
    """Delete all cache entries. Returns number removed (tests / ops)."""
    root = cache_dir()
    n = 0
    try:
        for path in root.glob("*.json"):
            try:
                path.unlink(missing_ok=True)
                n += 1
            except OSError:
                pass
    except OSError:
        pass
    return n
