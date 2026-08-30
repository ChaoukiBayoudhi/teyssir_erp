"""Waitress entrypoint for the Teyssir macOS LaunchAgent.

Runs migrate (idempotent) then serves the PWA+API on 0.0.0.0:PORT.
Do not use ``manage.py runserver`` in production.

If ``PORT`` is already serving a healthy Teyssir instance (manual serve / other
worktree), exit 0 so LaunchAgent KeepAlive does not thrash on EADDRINUSE.
"""
from __future__ import annotations

import os
import socket
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _port() -> str:
    return (
        os.environ.get("PORT")
        or os.environ.get("TEYSSIR_PORT")
        or "8000"
    ).strip() or "8000"


def _health_ok(port: str, timeout: float = 1.5) -> bool:
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/health/", method="GET"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception:
        return False


def _port_in_use(port: str) -> bool:
    try:
        p = int(port)
    except ValueError:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", p)) == 0


def main() -> None:
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "teyssir.settings")
    os.environ.setdefault("PYTHONUNBUFFERED", "1")

    port = _port()
    if _port_in_use(port):
        if _health_ok(port):
            print(
                f"Teyssir already healthy on :{port} — skipping duplicate bind",
                flush=True,
            )
            return
        print(
            f"ERROR: port {port} is in use but /health/ failed. "
            "Stop the other process or pick TEYSSIR_PORT.",
            flush=True,
        )
        sys.exit(1)

    import django

    django.setup()
    from django.core.management import call_command

    call_command("migrate", interactive=False, verbosity=1)

    from waitress import serve

    from teyssir.wsgi import application

    listen = f"0.0.0.0:{port}"
    print(f"Teyssir waitress listening on {listen}", flush=True)
    serve(application, listen=listen, ident="teyssir")


if __name__ == "__main__":
    main()
