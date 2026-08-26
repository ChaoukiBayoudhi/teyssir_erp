"""Build Django DATABASES from role + env (hub → PostgreSQL, till → SQLite)."""
from __future__ import annotations

from pathlib import Path


def _env(environ: dict, key: str, default: str = "") -> str:
    return str(environ.get(key, default) or default)


def database_config(*, role: str, backend: str, base_dir, terminal: str = "C1",
                    environ: dict | None = None) -> dict:
    """Return the `default` DATABASES entry. ``backend`` is postgres|sqlite."""
    environ = environ or {}
    role = (role or "till").lower()
    backend = (backend or ("postgres" if role == "hub" else "sqlite")).lower()
    if backend in ("postgres", "postgresql", "pg"):
        conn_max_age = 60
        try:
            conn_max_age = int(_env(environ, "POSTGRES_CONN_MAX_AGE", "60") or "60")
        except ValueError:
            conn_max_age = 60
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _env(environ, "POSTGRES_DB", "teyssir") or "teyssir",
            "USER": _env(environ, "POSTGRES_USER", "teyssir") or "teyssir",
            "PASSWORD": _env(environ, "POSTGRES_PASSWORD", ""),
            "HOST": _env(environ, "POSTGRES_HOST", "127.0.0.1") or "127.0.0.1",
            "PORT": _env(environ, "POSTGRES_PORT", "5432") or "5432",
            "CONN_MAX_AGE": conn_max_age,
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": {
                "client_encoding": "UTF8",
                "connect_timeout": 10,
            },
        }
    default_name = "teyssir_hub.sqlite3" if role == "hub" else f"teyssir_{terminal}.sqlite3"
    name = _env(environ, "TEYSSIR_SQLITE_NAME", default_name) or default_name
    path = Path(name)
    if not path.is_absolute():
        path = Path(base_dir) / name
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": path,
        "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"},
    }
