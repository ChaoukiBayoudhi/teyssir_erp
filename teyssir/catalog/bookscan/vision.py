"""Dual-image Vision LLM book analysis (Phase 15.4).

One Ollama ``/api/generate`` call with front (+ optional back) cover images,
structured JSON, ISBN checksum sanitize, never invent ``barcode_*``.
Default model: ``qwen2.5vl:3b`` (CPU-friendly). Images downscaled to
``VISION_IMAGE_MAX_EDGE`` (1280) before base64.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
import urllib.request
from typing import Callable

from django.conf import settings

from .draft import BookDraft
from .isbn import to_isbn13
from .price import extract_price_dt

logger = logging.getLogger("teyssir.vision")

DEFAULT_VISION_MODEL = "qwen2.5vl:3b"

# Dual-cover prompt: require language_detected + 2–4 sentence description.
VISION_PROMPT = (
    "You are analyzing a book from one or two cover photos (front, then optional back/verso). "
    "Covers may be Arabic, French, English, or mixed. "
    "Reply with a SINGLE JSON object only, no prose. Keys (empty string / empty list if unknown): "
    "title, subtitle, authors (list of strings), translators (list), publisher, series, edition, "
    "languages (ISO list e.g. [\"ar\",\"fr\"]), "
    "language_detected (REQUIRED: one of \"ar\", \"fr\", \"en\", "
    "\"mixed:ar+fr\", \"mixed:ar+en\", \"mixed:fr+en\", or \"mixed:ar+fr+en\"), "
    "pub_year (int), pages (int), "
    "isbn13 (optional; ONLY digits clearly printed — NEVER invent or guess check digits), "
    "subject, "
    "description (REQUIRED: 2 to 4 factual sentences about the book from the covers, "
    "in the book's primary language; never leave empty when a title is visible; "
    "do not invent long marketing blurbs unrelated to the cover), "
    "price (optional string in TND if a price sticker is visible). "
    "Preserve original Arabic script. "
    "NEVER invent barcode_raw, barcode_symbology, barcode_kind, or any product barcode. "
    "CRITICAL: Never invent or guess an ISBN. Set isbn13 to \"\" unless the digits "
    "are clearly printed on the cover/verso."
)

_VISION_SCALAR_KEYS = {
    "title",
    "subtitle",
    "publisher",
    "series",
    "edition",
    "subject",
    "description",
    "isbn13",
    "price",
    "language_detected",
}


def image_to_jpeg_bytes(image_path: str, max_edge: int | None = None) -> bytes:
    """JPEG-encode a downscaled cover (phone photos are multi-MB otherwise)."""
    from PIL import Image

    from .ocr import _downscale_max_edge

    edge = max_edge
    if edge is None:
        edge = int(getattr(settings, "VISION_IMAGE_MAX_EDGE", 1280) or 1280)
    with Image.open(image_path) as im:
        im = im.convert("RGB")
        im = _downscale_max_edge(im, edge)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue()


def image_to_b64(image_path: str, max_edge: int | None = None) -> str:
    """JPEG-encode a downscaled cover for Ollama (phone photos are multi-MB otherwise)."""
    return base64.b64encode(image_to_jpeg_bytes(image_path, max_edge=max_edge)).decode()


def sanitize_vision_isbn(draft: BookDraft | None) -> BookDraft | None:
    """Keep ISBN only when bookland 978/979 checksum validates — never invent identity."""
    if not draft or not draft.isbn13:
        return draft
    valid = to_isbn13(draft.isbn13)
    if valid:
        draft.isbn13 = valid
        draft.raw = {**(draft.raw or {}), "isbn_detected": True, "isbn_from_vision": True}
        draft.raw.pop("isbn_not_detected", None)
        draft.raw.pop("vision_isbn_rejected", None)
    else:
        draft.raw = {
            **(draft.raw or {}),
            "rejected_isbn": draft.isbn13,
            "vision_isbn_rejected": True,
            "isbn_not_detected": True,
        }
        draft.isbn13 = ""
    return draft


def strip_vision_barcodes(draft: BookDraft) -> BookDraft:
    """Decoder-only hard rule: Vision must never invent barcode_* fields."""
    draft.barcode_raw = ""
    draft.barcode_symbology = ""
    draft.barcode_kind = ""
    if draft.raw:
        for key in ("barcode_raw", "barcode_symbology", "barcode_kind"):
            draft.raw.pop(key, None)
    return draft


def draft_from_vision_json(raw: str) -> BookDraft:
    """Parse the model's JSON reply into a BookDraft (tolerant of stray prose)."""
    draft = BookDraft(source="vision", confidence=0.85, raw={"ocr_available": True})
    try:
        start, end = raw.index("{"), raw.rindex("}") + 1
        data = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        draft.raw = {"vision_reply": raw, "isbn_not_detected": True}
        return strip_vision_barcodes(draft)

    # Ignore any invented barcode keys from the model
    data = {k: v for k, v in data.items() if not str(k).startswith("barcode")}

    draft.raw = {**data, "ocr_available": True}
    for key in _VISION_SCALAR_KEYS:
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            setattr(draft, key, val.strip())
    if isinstance(data.get("authors"), list):
        draft.authors = [str(a).strip() for a in data["authors"] if str(a).strip()]
    if isinstance(data.get("translators"), list):
        draft.translators = [str(a).strip() for a in data["translators"] if str(a).strip()]
    if isinstance(data.get("languages"), list):
        draft.languages = [str(a).strip() for a in data["languages"] if str(a).strip()]

    ld = (data.get("language_detected") or "").strip()
    if ld:
        draft.language_detected = ld
        draft.raw["language_detected"] = ld
    elif draft.languages:
        from .language import format_language_detected

        draft.language_detected = format_language_detected(draft.languages)
        draft.raw["language_detected"] = draft.language_detected

    for num in ("pub_year", "pages"):
        try:
            value = int(data.get(num))
            if value > 0:
                setattr(draft, num, value)
        except (TypeError, ValueError):
            pass

    if draft.price:
        draft.price = extract_price_dt(draft.price) or draft.price

    if draft.isbn13:
        valid = to_isbn13(draft.isbn13)
        if valid:
            draft.isbn13 = valid
            draft.raw["isbn_detected"] = True
            draft.raw["isbn_from_vision"] = True
        else:
            draft.raw["rejected_isbn"] = draft.isbn13
            draft.raw["vision_isbn_rejected"] = True
            draft.isbn13 = ""
            draft.raw["isbn_not_detected"] = True
    else:
        draft.raw["isbn_not_detected"] = True

    # Description required when title visible — mark missing for callers
    desc = (draft.description or "").strip()
    if not desc and draft.title:
        draft.raw["vision_description_missing"] = True
    elif desc:
        # Soft check: prefer 2–4 sentences (period/Arabic full stop)
        sentences = [s for s in re.split(r"[.!?؟۔]+", desc) if s.strip()]
        if len(sentences) < 2:
            draft.raw["vision_description_short"] = True
        draft.raw["vision_description"] = True

    return strip_vision_barcodes(draft)


def ollama_generate(
    images_b64: list[str],
    *,
    timeout: float | None = None,
    model: str | None = None,
    prompt: str | None = None,
    transport: Callable[[list[str]], str] | None = None,
) -> str:
    """POST one generate call with one or more images. ``transport`` injects for tests."""
    if transport is not None:
        return transport(images_b64)

    timeout = timeout if timeout is not None else float(getattr(settings, "VISION_TIMEOUT", 45) or 45)
    model = model or getattr(settings, "VISION_MODEL", None) or DEFAULT_VISION_MODEL
    body = json.dumps({
        "model": model,
        "prompt": prompt or VISION_PROMPT,
        "images": list(images_b64),
        "stream": False,
        "format": "json",
    }).encode()
    url = f"{getattr(settings, 'OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')}/api/generate"
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp).get("response", "")


def analyze_covers(
    front_path: str,
    back_path: str | None = None,
    *,
    timeout: float | None = None,
    transport: Callable[[list[str]], str] | None = None,
    max_edge: int | None = None,
    use_cache: bool | None = None,
) -> tuple[str, BookDraft]:
    """One dual-image Vision call → (raw JSON text, sanitized BookDraft).

    Both images (when present) are downscaled and sent in a single Ollama request.
    Results are keyed by content-hash of those JPEGs (P15-T3 local FS cache).
    """
    from . import vision_cache

    edge = max_edge
    if edge is None:
        edge = int(getattr(settings, "VISION_IMAGE_MAX_EDGE", 1280) or 1280)
    # Dual covers: cap edge so CPU Ollama finishes (1280×2 often exceeds soft timeout)
    dual_cap = int(getattr(settings, "VISION_DUAL_MAX_EDGE", 896) or 896)
    model = getattr(settings, "VISION_MODEL", None) or DEFAULT_VISION_MODEL

    jpeg_blobs: list[bytes] = []
    dual = False
    # Shop CPU: keep vision JPEGs modest even for front-only (1280 starves timeout)
    fallback_cap = int(getattr(settings, "VISION_DUAL_MAX_EDGE", 896) or 896)
    edge = min(int(edge), fallback_cap)
    if back_path:
        try:
            jpeg_blobs.append(image_to_jpeg_bytes(front_path, max_edge=edge))
            jpeg_blobs.append(image_to_jpeg_bytes(back_path, max_edge=edge))
            dual = True
        except Exception as exc:
            logger.info("vision back image skipped: %s", exc)
            jpeg_blobs = [image_to_jpeg_bytes(front_path, max_edge=edge)]
            dual = False
    else:
        jpeg_blobs = [image_to_jpeg_bytes(front_path, max_edge=edge)]

    images = [base64.b64encode(b).decode() for b in jpeg_blobs]
    cache_on = vision_cache.cache_enabled() if use_cache is None else bool(use_cache)
    # Injected transports are for unit tests — skip disk cache unless explicitly on.
    if transport is not None and use_cache is not True:
        cache_on = False

    cache_key = ""
    if cache_on:
        cache_key = vision_cache.content_hash(jpeg_blobs, model=model, max_edge=int(edge))
        hit = vision_cache.get(cache_key)
        if hit:
            draft = BookDraft(**{
                k: v for k, v in (hit["draft"] or {}).items()
                if k in BookDraft.__dataclass_fields__
            })
            # Nested list/dict fields may arrive as plain JSON — keep raw intact.
            if not isinstance(draft.raw, dict):
                draft.raw = {}
            draft.raw = {
                **(draft.raw or {}),
                "vision_downscaled": True,
                "vision_dual_image": dual,
                "vision_image_count": len(images),
                "cover_role": "front+back" if dual else "front",
                "vision_cache_hit": True,
                "vision_cache_key": cache_key[:16],
            }
            if not draft.source:
                draft.source = "vision"
            return hit["raw"], draft

    raw = ollama_generate(images, timeout=timeout, transport=transport, model=model)
    draft = draft_from_vision_json(raw)
    draft = sanitize_vision_isbn(draft)
    draft = strip_vision_barcodes(draft)
    draft.raw = {
        **(draft.raw or {}),
        "vision_downscaled": True,
        "vision_dual_image": dual,
        "vision_image_count": len(images),
        "cover_role": "front+back" if dual else "front",
        "vision_cache_hit": False,
    }
    if cache_key:
        draft.raw["vision_cache_key"] = cache_key[:16]
        try:
            vision_cache.put(cache_key, raw=raw, draft=draft.as_dict(), model=model)
        except Exception as exc:
            logger.info("vision cache store skipped: %s", exc)
    if not draft.source:
        draft.source = "vision"
    return raw, draft
