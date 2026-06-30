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
HUB_URL = os.environ.get("TEYSSIR_HUB_URL", "")
SYNC_KEY = os.environ.get("TEYSSIR_SYNC_KEY", "")            # shared secret for /api/v1/sync/*

# --- Core --------------------------------------------------------------------
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-change-me")
DEBUG = os.environ.get("DEBUG", "1") == "1"
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "teyssir-hub.local"]

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
            "OPTIONS": {"timeout": 20},
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
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

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
