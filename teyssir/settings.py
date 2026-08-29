"""
Teyssir ERP settings — single file, env-driven, selecting a node profile.

  TEYSSIR_ROLE=hub   -> PostgreSQL (consolidated source of truth, sync master, reporting)
  TEYSSIR_ROLE=till  -> local SQLite (offline-capable POS), TEYSSIR_TERMINAL = C1/C2/C3

See docs/ARCHITECTURE.md §5 (stack), §7 (DB), §20 (deployment).
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Optional .env loading (python-dotenv); harmless if absent.
try:  # pragma: no cover
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except Exception:  # pragma: no cover
    pass

# --- Node identity -----------------------------------------------------------
ROLE = os.environ.get("TEYSSIR_ROLE", "till").lower()        # "hub" | "till"
TERMINAL = os.environ.get("TEYSSIR_TERMINAL", "C1")          # till series prefix
# Multi-store (Phase 6): a short code identifying THIS store. Empty = single-store (numbers stay
# C1-YYYYMM-XXXX). When set (e.g. "S1"), document numbers become S1C1-YYYYMM-XXXX so they are
# globally unique across stores once consolidated at a cloud hub. All tills in a store share it.
STORE_CODE = os.environ.get("TEYSSIR_STORE_CODE", "").strip()
HUB_URL = os.environ.get("TEYSSIR_HUB_URL", "")
SYNC_KEY = os.environ.get("TEYSSIR_SYNC_KEY", "")            # shared secret for /api/v1/sync/*
# Multi-store (Phase 6): when a store hub is federated under a cloud hub, set CLOUD_HUB_URL so the
# store hub forwards its transactions upward (reusing the same push mechanism, recursively). Empty
# = standalone store (no forwarding). CLOUD_SYNC_KEY defaults to SYNC_KEY if unset.
CLOUD_HUB_URL = os.environ.get("TEYSSIR_CLOUD_HUB_URL", "")
CLOUD_SYNC_KEY = os.environ.get("TEYSSIR_CLOUD_SYNC_KEY", "")

# --- Core --------------------------------------------------------------------
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-change-me")
DEBUG = os.environ.get("DEBUG", "1") == "1"
ALLOWED_HOSTS = [
    h.strip() for h in os.environ.get(
        "TEYSSIR_ALLOWED_HOSTS", "localhost,127.0.0.1,teyssir-hub.local"
    ).split(",") if h.strip()
]
# Trust the hub origin for admin POSTs when served over the LAN with DEBUG=0 (the PWA uses token
# auth, not CSRF). Provide scheme://host[:port], comma-separated, e.g. http://teyssir-hub.local:8000
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get("TEYSSIR_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]

# HTTPS hardening — ONLY when actually served behind TLS (e.g. a cloud hub). Enabling these on the
# shop LAN (plain HTTP) would make cookies never send and redirect-loop, so they are OFF by default.
HTTPS = os.environ.get("TEYSSIR_HTTPS", "0") == "1"
if HTTPS:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "teyssir.core",
    "teyssir.accounts",
    "teyssir.catalog",
    "teyssir.inventory",
    "teyssir.billing",
    "teyssir.sales",
    "teyssir.sync",
    "teyssir.api",
    "teyssir.printing",
    "teyssir.purchasing",
    "teyssir.reports",
    "teyssir.customers",
    "teyssir.quotations",
    "teyssir.ledger",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise serves the built PWA + Django static on one port (Windows deploy, no Nginx/Caddy).
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "teyssir.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "teyssir.wsgi.application"

# --- Database: hub=PostgreSQL, till=SQLite (spec §7) --------------------------
# Engine defaults from the role but can be overridden with TEYSSIR_DB (e.g. run a hub on
# SQLite for local/dev/CI without a PostgreSQL server).
DB_BACKEND = os.environ.get("TEYSSIR_DB", "postgres" if ROLE == "hub" else "sqlite").lower()
if DB_BACKEND == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "teyssir"),
            "USER": os.environ.get("POSTGRES_USER", "teyssir"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        }
    }
else:
    default_name = "teyssir_hub.sqlite3" if ROLE == "hub" else f"teyssir_{TERMINAL}.sqlite3"
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / os.environ.get("TEYSSIR_SQLITE_NAME", default_name),
            # transaction_mode=IMMEDIATE makes every atomic() take the write lock at BEGIN, so
            # concurrent writes (waitress serves with multiple threads) WAIT on busy_timeout instead
            # of failing with "database is locked" on a read->write upgrade. `timeout` = busy_timeout.
            "OPTIONS": {"timeout": 20, "transaction_mode": "IMMEDIATE"},
        }
    }

# --- Auth / security (spec §18) ----------------------------------------------
AUTH_USER_MODEL = "accounts.User"
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- i18n / l10n (AR + FR, spec §11) -----------------------------------------
LANGUAGE_CODE = "fr"
LANGUAGES = [("fr", "Français"), ("ar", "العربية")]
TIME_ZONE = "Africa/Tunis"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"          # collectstatic target (Django admin assets)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Serve the built React PWA (frontend/dist) at the site root via WhiteNoise, so ONE process serves
# both the app and the /api on a single port (Windows deploy). API/admin/media URLs fall through to
# Django when no static file matches. Only enabled once the frontend has been built.
_SPA_DIST = BASE_DIR / "frontend" / "dist"
if _SPA_DIST.is_dir():
    WHITENOISE_ROOT = _SPA_DIST
    WHITENOISE_INDEX_FILE = True

# Media (product/book images). ImageField stores a path/key; the storage backend is pluggable
# (local FS now; S3/MinIO at a cloud tier — no schema change). Spec docs/BOOK-OCR.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Phase 6: flip media to S3-compatible object storage (MinIO = free/self-hosted, or AWS S3) with a
# settings/env change only — set TEYSSIR_S3_BUCKET (+ endpoint for MinIO). Needs django-storages +
# boto3 (optional; installed only at the cloud tier). Default stays local filesystem.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
if os.environ.get("TEYSSIR_S3_BUCKET"):
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": os.environ["TEYSSIR_S3_BUCKET"],
            "endpoint_url": os.environ.get("TEYSSIR_S3_ENDPOINT") or None,   # MinIO: http://minio:9000
            "access_key": os.environ.get("TEYSSIR_S3_ACCESS_KEY", ""),
            "secret_key": os.environ.get("TEYSSIR_S3_SECRET_KEY", ""),
            "region_name": os.environ.get("TEYSSIR_S3_REGION", ""),
            "querystring_auth": False,
        },
    }

# Book registration / OCR providers (replaceable; docs/BOOK-OCR-ARCHITECTURE.md)
OCR_PROVIDER = os.environ.get("TEYSSIR_OCR_PROVIDER", "tesseract")          # tesseract|manual|vision
# Absolute Tesseract binary (LaunchAgent / NSSM often lack Homebrew / Program Files on PATH).
def _default_tesseract_cmd() -> str:
    import sys as _sys
    if _sys.platform == "darwin":
        for cand in ("/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"):
            if Path(cand).is_file():
                return cand
    elif _sys.platform == "win32":
        for cand in (
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ):
            if Path(cand).is_file():
                return cand
    return "tesseract"


TESSERACT_CMD = (
    os.environ.get("TEYSSIR_TESSERACT_CMD")
    or os.environ.get("TESSERACT_CMD")
    or _default_tesseract_cmd()
)
# Below this OCR mean-confidence (0–100), drafts are flagged for manual review.
OCR_CONFIDENCE_THRESHOLD = float(os.environ.get("TEYSSIR_OCR_CONFIDENCE_THRESHOLD", "45"))
# When Tesseract returns a weak/empty draft, try local Ollama vision (qwen2.5vl) once.
OCR_VISION_FALLBACK = os.environ.get("TEYSSIR_OCR_VISION_FALLBACK", "true").strip().lower() in (
    "1", "true", "yes", "on",
)
METADATA_PROVIDERS = [
    p for p in os.environ.get(
        "TEYSSIR_METADATA_PROVIDERS", "openlibrary,googlebooks"
    ).split(",") if p
]
# Vision-LLM OCR (OCR_PROVIDER=vision): free, offline, local — Ollama + a vision model.
OLLAMA_URL = os.environ.get("TEYSSIR_OLLAMA_URL", os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"))
VISION_MODEL = os.environ.get("TEYSSIR_VISION_MODEL", "qwen2.5vl:3b")
# Hard timeout for primary vision provider calls (OCR_PROVIDER=vision).
VISION_TIMEOUT = int(os.environ.get("TEYSSIR_VISION_TIMEOUT", "45"))
# Shorter timeout when vision is only a Tesseract fallback (skip if Ollama is slow/down).
VISION_FALLBACK_TIMEOUT = float(os.environ.get("TEYSSIR_VISION_FALLBACK_TIMEOUT", "28"))
# Max image edge (px) before base64→Ollama; phone photos are huge and starve the timeout budget.
VISION_IMAGE_MAX_EDGE = int(os.environ.get("TEYSSIR_VISION_IMAGE_MAX_EDGE", "1280"))
# Phase 15.6: optional accuracy mode for low-quality camera covers (extra title_band
# Tess variants + slightly longer Vision budget). Default off keeps Phase 2C fast path.
BOOKSCAN_ACCURACY = os.environ.get("TEYSSIR_BOOKSCAN_ACCURACY", "").strip().lower() in (
    "1", "true", "yes", "on",
)
# Optional text LLM (Ollama). ERP must run when this is false or Ollama is down.
USE_LLM = os.environ.get("USE_LLM", os.environ.get("TEYSSIR_USE_LLM", "false")).strip().lower() in (
    "1", "true", "yes", "on",
)
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", os.environ.get("TEYSSIR_LLM_PROVIDER", "ollama"))
LLM_MODEL = os.environ.get("LLM_MODEL", os.environ.get("TEYSSIR_LLM_MODEL", "mistral"))
# Scan execution: inline (sync, default) | thread (background, so slow OCR doesn't block the request)
SCAN_EXECUTOR = os.environ.get("TEYSSIR_SCAN_EXECUTOR", "inline")
# PDF→Word: thread by default on Windows Hub (long converts must not block waitress); inline elsewhere/tests.
import sys as _sys
_CONVERT_DEFAULT = "thread" if _sys.platform == "win32" else "inline"
CONVERT_EXECUTOR = os.environ.get("TEYSSIR_CONVERT_EXECUTOR", _CONVERT_DEFAULT)

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

# --- Money policy (spec §7.2) ------------------------------------------------
CURRENCY = "TND"
MONEY_STORE_DP = 3   # store millimes (lossless)
MONEY_DISPLAY_DP = 2  # display 2 decimals

# --- Store / fiscal identity (printed on receipts/factures, spec §13.5) ------
STORE_MATRICULE_FISCAL = os.environ.get("TEYSSIR_MATRICULE_FISCAL", "")
# Receipt printer target for this node: dummy | file:/path | tcp:host:port (spec §6)
# (read at send time from TEYSSIR_PRINTER env)

# --- Logging -----------------------------------------------------------------
# The node runs headless (waitress). Persist warnings/errors to a rotating file so a shop problem
# can be diagnosed after the fact, and echo to the console. Level via TEYSSIR_LOG_LEVEL.
_LOG_DIR = BASE_DIR / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_LOG_LEVEL = os.environ.get("TEYSSIR_LOG_LEVEL", "INFO").upper()
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "std": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "std"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(_LOG_DIR / "teyssir.log"),
            "maxBytes": 2_000_000, "backupCount": 5, "formatter": "std", "encoding": "utf-8",
        },
    },
    "root": {"handlers": ["console", "file"], "level": _LOG_LEVEL},
    "loggers": {
        # Always capture server 500s to the file, even if the root level is raised.
        "django.request": {"handlers": ["console", "file"], "level": "ERROR", "propagate": False},
    },
}
