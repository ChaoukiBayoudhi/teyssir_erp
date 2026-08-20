"""Waitress entrypoint for the Teyssir macOS LaunchAgent.

Runs migrate (idempotent) then serves the PWA+API on 0.0.0.0:PORT.
Do not use ``manage.py runserver`` in production.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "teyssir.settings")
    os.environ.setdefault("PYTHONUNBUFFERED", "1")

    import django

    django.setup()
    from django.core.management import call_command

    call_command("migrate", interactive=False, verbosity=1)

    from waitress import serve

    from teyssir.wsgi import application

    port = (
        os.environ.get("PORT")
        or os.environ.get("TEYSSIR_PORT")
        or "8000"
    ).strip() or "8000"
    listen = f"0.0.0.0:{port}"
    print(f"Teyssir waitress listening on {listen}", flush=True)
    serve(application, listen=listen, ident="teyssir")


if __name__ == "__main__":
    main()
