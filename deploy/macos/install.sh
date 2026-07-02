#!/usr/bin/env bash
# ============================================================
#  Teyssir — macOS installer (Apple Silicon / M1 and Intel)
#  Usage (from the project root):
#     bash deploy/macos/install.sh --role hub
#     bash deploy/macos/install.sh --role till --terminal C1 \
#          --hub-url http://teyssir-hub.local:8000 --sync-key <hub-key>
# ============================================================
set -euo pipefail

ROLE="till"; TERMINAL="C1"; STORE=""; HUB_URL="http://teyssir-hub.local:8000"
SYNC_KEY=""; SKIP_BUILD=0
while [ $# -gt 0 ]; do
  case "$1" in
    --role) ROLE="$2"; shift 2;;
    --terminal) TERMINAL="$2"; shift 2;;
    --store) STORE="$2"; shift 2;;
    --hub-url) HUB_URL="$2"; shift 2;;
    --sync-key) SYNC_KEY="$2"; shift 2;;
    --skip-build) SKIP_BUILD=1; shift;;
    *) echo "Unknown option: $1"; exit 1;;
  esac
done

cd "$(cd "$(dirname "$0")/../.." && pwd)"
echo "==== Teyssir installer (role: $ROLE) ===="
echo "Project: $(pwd)"

rand_key() { LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c "$1"; }

# 1) Python 3.12+ -----------------------------------------------------------
PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  echo "Python 3 not found. Install it with:  brew install python@3.12"
  echo "(Homebrew: https://brew.sh  — on M1 it lives in /opt/homebrew)"
  exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)'; then
  echo "Python 3.12+ required (found $($PY --version)). Try:  brew install python@3.12"
  exit 1
fi
echo "Python: $($PY --version)"

# 2) venv + dependencies ----------------------------------------------------
[ -x ".venv/bin/python" ] || { echo "Creating .venv ..."; "$PY" -m venv .venv; }
echo "Installing Python dependencies ..."
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements.txt

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
if [ ! -f ".env" ]; then
  SECRET="$(rand_key 50)"
  [ -n "$SYNC_KEY" ] || SYNC_KEY="$(rand_key 40)"
  PCNAME="$(scutil --get LocalHostName 2>/dev/null || hostname -s)"
  if [ "$ROLE" = "hub" ]; then
    cat > .env <<EOF
TEYSSIR_ROLE=hub
TEYSSIR_STORE_CODE=$STORE
TEYSSIR_DB=sqlite
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
  echo ".env already exists — left unchanged."
fi

# 5) database + static ------------------------------------------------------
echo "Setting up the database ..."
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py collectstatic --noinput >/dev/null

# 6) first administrator ----------------------------------------------------
echo ""
echo "Create the first administrator account (owner):"
.venv/bin/python manage.py createsuperuser

echo ""
echo "==== Installation complete ===="
echo "Start Teyssir with:  bash deploy/macos/start-teyssir.sh"
echo "Then open:           http://localhost:8000"
