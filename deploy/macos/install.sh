#!/usr/bin/env bash
# ============================================================
#  Teyssir — macOS installer (Apple Silicon / M1 and Intel)
#  Usage (from the project root):
#     bash deploy/macos/install.sh --role hub
#     bash deploy/macos/install.sh --role till --terminal C1 \
#          --hub-url http://teyssir-hub.local:8000 --sync-key <hub-key>
#     bash deploy/macos/install.sh --role till --printer tcp:192.168.1.100:9100 ...
#     bash deploy/macos/install.sh --role till --discover-printer ...
#
#  Safe to re-run. Registers LaunchAgent com.teyssir.backend + Desktop app.
# ============================================================
set -euo pipefail

ROLE="till"; TERMINAL="C1"; STORE=""; HUB_URL="http://teyssir-hub.local:8000"
SYNC_KEY=""; SKIP_BUILD=0; SKIP_SERVICE=0; SKIP_SHORTCUT=0; SKIP_ADMIN=0
ADMIN_USER=""; ADMIN_PASSWORD=""
PRINTER=""; DISCOVER_PRINTER=0
while [ $# -gt 0 ]; do
  case "$1" in
    --role) ROLE="$2"; shift 2;;
    --terminal) TERMINAL="$2"; shift 2;;
    --store) STORE="$2"; shift 2;;
    --hub-url) HUB_URL="$2"; shift 2;;
    --sync-key) SYNC_KEY="$2"; shift 2;;
    --printer) PRINTER="$2"; shift 2;;
    --discover-printer) DISCOVER_PRINTER=1; shift;;
    --skip-build) SKIP_BUILD=1; shift;;
    --skip-service) SKIP_SERVICE=1; shift;;
    --skip-shortcut) SKIP_SHORTCUT=1; shift;;
    --skip-admin) SKIP_ADMIN=1; shift;;
    --admin-user) ADMIN_USER="$2"; shift 2;;
    --admin-password) ADMIN_PASSWORD="$2"; shift 2;;
    *) echo "Unknown option: $1"; exit 1;;
  esac
done

cd "$(cd "$(dirname "$0")/../.." && pwd)"
ROOT="$(pwd)"
echo "==== Teyssir installer (role: $ROLE) ===="
echo "Project: $ROOT"

rand_key() { LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c "$1"; }

set_env_value() {
  # set_env_value KEY VALUE — upsert into .env (no BOM concerns on macOS)
  local key="$1" value="$2"
  if [ -f .env ] && grep -q "^${key}=" .env 2>/dev/null; then
    sed -i '' "s|^${key}=.*|${key}=${value}|" .env
  else
    echo "${key}=${value}" >> .env
  fi
}

# 1) Python 3.12+ -----------------------------------------------------------
PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  echo "Python 3 not found. Install it with:  brew install python@3.12"
  echo "(Homebrew: https://brew.sh  — on M1 it lives in /opt/homebrew)"
  exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)'; then
  echo "Python 3.11+ required (found $($PY --version)). Try:  brew install python@3.12"
  exit 1
fi
echo "Python: $($PY --version)"

# 2) venv + dependencies ----------------------------------------------------
[ -x ".venv/bin/python" ] || { echo "Creating .venv ..."; "$PY" -m venv .venv; }
echo "Installing Python dependencies ..."
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements.txt

# 2b) Tesseract OCR (optional — never abort) --------------------------------
TESS_CMD=""
for cand in /opt/homebrew/bin/tesseract /usr/local/bin/tesseract; do
  if [ -x "$cand" ]; then TESS_CMD="$cand"; break; fi
done
if [ -z "$TESS_CMD" ] && command -v brew >/dev/null 2>&1; then
  echo "Installing Tesseract (brew) ..."
  brew install tesseract tesseract-lang >/dev/null 2>&1 || echo "WARNING: brew tesseract skipped — OCR may need manual install."
  for cand in /opt/homebrew/bin/tesseract /usr/local/bin/tesseract; do
    if [ -x "$cand" ]; then TESS_CMD="$cand"; break; fi
  done
fi
if [ -n "$TESS_CMD" ]; then
  echo "Tesseract: $TESS_CMD"
else
  echo "WARNING: Tesseract not found — book OCR will fall back to manual/vision."
fi

# 3) Front-end build (only if not already built) ----------------------------
if [ "$SKIP_BUILD" -eq 0 ] && [ ! -f "frontend/dist/index.html" ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "Building the web app (npm) ..."
    ( cd frontend && npm ci && npm run build )
  else
    echo "WARNING: npm not found and frontend/dist is missing."
    echo "Build once on a Mac with Node (cd frontend && npm ci && npm run build) or:  brew install node"
  fi
fi

# 4) .env (created once, with random secrets) -------------------------------
SYNC_FROM_CLI=0
[ -n "$SYNC_KEY" ] && SYNC_FROM_CLI=1
if [ ! -f ".env" ]; then
  SECRET="$(rand_key 50)"
  [ -n "$SYNC_KEY" ] || SYNC_KEY="$(rand_key 40)"
  if [ "$ROLE" = "till" ] && [ "$SYNC_FROM_CLI" -eq 0 ]; then
    echo "WARNING: No --sync-key given. A random key was generated — this till cannot sync until TEYSSIR_SYNC_KEY matches the Hub."
  fi
  PCNAME="$(scutil --get LocalHostName 2>/dev/null || hostname -s)"
  if [ "$ROLE" = "hub" ]; then
    cat > .env <<EOF
TEYSSIR_ROLE=hub
TEYSSIR_STORE_CODE=$STORE
TEYSSIR_DB=sqlite
TEYSSIR_SCAN_EXECUTOR=thread
TEYSSIR_OCR_PROVIDER=tesseract
TEYSSIR_TESSERACT_CMD=${TESS_CMD:-/opt/homebrew/bin/tesseract}
TEYSSIR_SYNC_KEY=$SYNC_KEY
DEBUG=0
SECRET_KEY=$SECRET
TEYSSIR_ALLOWED_HOSTS=localhost,127.0.0.1,$PCNAME,$PCNAME.local,teyssir-hub.local
TEYSSIR_CSRF_TRUSTED_ORIGINS=http://$PCNAME.local:8000,http://teyssir-hub.local:8000
EOF
  else
    cat > .env <<EOF
TEYSSIR_ROLE=till
TEYSSIR_TERMINAL=$TERMINAL
TEYSSIR_STORE_CODE=$STORE
TEYSSIR_HUB_URL=$HUB_URL
TEYSSIR_SYNC_KEY=$SYNC_KEY
TEYSSIR_DB=sqlite
TEYSSIR_SCAN_EXECUTOR=thread
TEYSSIR_OCR_PROVIDER=tesseract
TEYSSIR_TESSERACT_CMD=${TESS_CMD:-/opt/homebrew/bin/tesseract}
DEBUG=0
SECRET_KEY=$SECRET
TEYSSIR_ALLOWED_HOSTS=localhost,127.0.0.1
EOF
  fi
  echo ""
  echo "  .env created."
  echo "  SHARED SYNC KEY = $SYNC_KEY"
  echo "  ^ Use this SAME key on the hub and on every till."
  echo ""
else
  echo ".env already exists — secrets left unchanged."
  if [ -n "$TESS_CMD" ]; then
    if grep -q '^TEYSSIR_TESSERACT_CMD=' .env 2>/dev/null; then
      sed -i '' "s|^TEYSSIR_TESSERACT_CMD=.*|TEYSSIR_TESSERACT_CMD=$TESS_CMD|" .env
    else
      echo "TEYSSIR_TESSERACT_CMD=$TESS_CMD" >> .env
    fi
  fi
  if [ "$ROLE" = "till" ]; then
    # Allow re-run to fix terminal / hub / sync key without wiping SECRET_KEY
    if [ -n "$TERMINAL" ]; then
      if grep -q '^TEYSSIR_TERMINAL=' .env; then
        sed -i '' "s|^TEYSSIR_TERMINAL=.*|TEYSSIR_TERMINAL=$TERMINAL|" .env
      else
        echo "TEYSSIR_TERMINAL=$TERMINAL" >> .env
      fi
    fi
    if [ -n "$HUB_URL" ]; then
      if grep -q '^TEYSSIR_HUB_URL=' .env; then
        sed -i '' "s|^TEYSSIR_HUB_URL=.*|TEYSSIR_HUB_URL=$HUB_URL|" .env
      else
        echo "TEYSSIR_HUB_URL=$HUB_URL" >> .env
      fi
    fi
    if [ "$SYNC_FROM_CLI" -eq 1 ]; then
      if grep -q '^TEYSSIR_SYNC_KEY=' .env; then
        sed -i '' "s|^TEYSSIR_SYNC_KEY=.*|TEYSSIR_SYNC_KEY=$SYNC_KEY|" .env
      else
        echo "TEYSSIR_SYNC_KEY=$SYNC_KEY" >> .env
      fi
    fi
  fi
fi

# 4b) Receipt printer (client LAN — never assume a fixed shop IP) ------------
if [ "$DISCOVER_PRINTER" -eq 1 ] && [ -z "$PRINTER" ]; then
  echo "Scanning local /24 for ESC/POS on TCP 9100 ..."
  disc_py=".venv/bin/python"
  [ -x "$disc_py" ] || disc_py="python3"
  PRINTER="$("$disc_py" deploy/discover_printer.py 2>/dev/null | tail -1 | tr -d '\r' || true)"
  [ -n "$PRINTER" ] || PRINTER="dummy"
  if [ "$PRINTER" = "dummy" ]; then
    echo "WARNING: no printer found — TEYSSIR_PRINTER=dummy (set --printer tcp:IP:9100 later)."
  else
    echo "Discovered printer: $PRINTER"
  fi
fi
if [ -n "$PRINTER" ]; then
  set_env_value TEYSSIR_PRINTER "$PRINTER"
  echo "TEYSSIR_PRINTER=$PRINTER written to .env"
elif [ -f .env ] && grep -q '^TEYSSIR_PRINTER=' .env 2>/dev/null; then
  PRINTER="$(grep -E '^TEYSSIR_PRINTER=' .env | head -1 | cut -d= -f2- | tr -d '\r')"
  echo "Using existing TEYSSIR_PRINTER=$PRINTER from .env"
else
  # First install without a flag: leave unset so the service defaults to dummy
  echo "Receipt printer: not set (dummy until you pass --printer tcp:IP:9100 or --discover-printer)."
fi

# 5) database + static ------------------------------------------------------
echo "Setting up the database ..."
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py seed_rbac
.venv/bin/python manage.py seed_fiscal
.venv/bin/python manage.py collectstatic --noinput >/dev/null

# 6) first administrator (idempotent) ---------------------------------------
HAS_ADMIN="$(.venv/bin/python - <<'PY'
import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "teyssir.settings")
django.setup()
from django.contrib.auth import get_user_model
print("1" if get_user_model().objects.filter(is_superuser=True).exists() else "0")
PY
)"
if [ "$SKIP_ADMIN" -eq 1 ]; then
  echo "Skipping administrator creation (--skip-admin)."
elif [ "$HAS_ADMIN" = "1" ]; then
  echo "An administrator already exists — skipping createsuperuser (re-run safe)."
elif [ -n "$ADMIN_USER" ] && [ -n "$ADMIN_PASSWORD" ]; then
  echo "Creating administrator '$ADMIN_USER' (non-interactive) ..."
  DJANGO_SUPERUSER_PASSWORD="$ADMIN_PASSWORD" \
    .venv/bin/python manage.py createsuperuser --noinput --username "$ADMIN_USER" --email "owner@localhost" \
    || echo "WARNING: createsuperuser failed — run: .venv/bin/python manage.py createsuperuser"
else
  echo ""
  echo "Create the first administrator account (owner):"
  echo "  Tip: --admin-user / --admin-password to skip the prompt."
  .venv/bin/python manage.py createsuperuser
fi

# 7) LaunchAgent backend ----------------------------------------------------
if [ "$SKIP_SERVICE" -eq 0 ]; then
  echo "Registering LaunchAgent com.teyssir.backend ..."
  # Install-BackendService reads TEYSSIR_PRINTER from .env (or --printer if passed).
  if [ "$ROLE" = "till" ]; then
    bash deploy/macos/register-autostart.sh till 300 || echo "WARNING: LaunchAgent / sync schedule skipped."
  else
    if [ -n "$PRINTER" ]; then
      bash deploy/macos/Install-BackendService.sh --printer "$PRINTER" || echo "WARNING: LaunchAgent skipped."
    else
      bash deploy/macos/Install-BackendService.sh || echo "WARNING: LaunchAgent skipped."
    fi
  fi
else
  echo "Skipping LaunchAgent (--skip-service)."
fi

# 8) Desktop app shortcut ---------------------------------------------------
if [ "$SKIP_SHORTCUT" -eq 0 ]; then
  echo "Creating Desktop app « Teyssir ERP » ..."
  bash deploy/macos/Install-DesktopApp.sh || echo "WARNING: Desktop app skipped."
else
  echo "Skipping desktop shortcut (--skip-shortcut)."
fi

echo ""
echo "==== Installation complete ===="
if [ "$SKIP_SERVICE" -eq 0 ]; then
  echo "Backend:     LaunchAgent com.teyssir.backend (starts at login, KeepAlive, no Terminal)"
  echo "Open Teyssir: double-click « Teyssir ERP » on the Desktop"
else
  echo "Start with:  bash deploy/macos/start-teyssir.sh"
  echo "Then open:   http://localhost:8000"
fi
echo "Health:      http://localhost:8000/health/"
if [ -n "${PRINTER:-}" ]; then
  echo "Printer:     TEYSSIR_PRINTER=$PRINTER  (Menu → Diagnostics to verify reachability)"
fi
echo "Uninstall:   bash deploy/macos/uninstall.sh"
