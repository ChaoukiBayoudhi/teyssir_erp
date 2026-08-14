"""Local LLM client (Ollama). Optional — the ERP must run when Ollama is absent."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from django.conf import settings


def llm_enabled() -> bool:
    return bool(getattr(settings, "USE_LLM", False))


def llm_provider() -> str:
    return (getattr(settings, "LLM_PROVIDER", None) or "ollama").strip().lower()


def llm_model() -> str:
    return (getattr(settings, "LLM_MODEL", None) or "mistral").strip()


def ollama_url() -> str:
    return (getattr(settings, "OLLAMA_URL", None) or "http://127.0.0.1:11434").rstrip("/")


def ollama_reachable(timeout=2) -> bool:
    try:
        req = urllib.request.Request(f"{ollama_url()}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def generate(prompt: str, *, model: str | None = None, timeout: int | None = None) -> str:
    """Send a prompt to the local Ollama generate API. Returns '' if disabled or unreachable."""
    if not llm_enabled() or llm_provider() != "ollama":
        return ""
    payload = json.dumps({
        "model": model or llm_model(),
        "prompt": prompt,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"{ollama_url()}/api/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout or 60) as resp:
            data = json.load(resp)
            return (data.get("response") or "").strip()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return ""


def status(*, ping=False) -> dict:
    """Config snapshot for /health. ``ping`` hits Ollama (avoid on every liveness probe)."""
    out = {
        "enabled": llm_enabled(),
        "provider": llm_provider(),
        "model": llm_model(),
        "url": ollama_url(),
    }
    if ping:
        out["reachable"] = ollama_reachable()
    return out
