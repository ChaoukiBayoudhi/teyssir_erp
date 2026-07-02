#!/usr/bin/env bash
# ===========================================================
#  Start the Teyssir server (hub or till, per the .env file).
#  Keep this Terminal window OPEN while the shop uses Teyssir.
# ===========================================================
set -euo pipefail
cd "$(cd "$(dirname "$0")/../.." && pwd)"

PORT="${TEYSSIR_PORT:-8000}"

if [ ! -x ".venv/bin/waitress-serve" ]; then
  echo "[ERROR] Teyssir is not installed yet."
  echo "Run:  bash deploy/macos/install.sh"
  exit 1
fi

echo "Applying database updates ..."
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py collectstatic --noinput >/dev/null 2>&1 || true

echo ""
echo "=============================================================="
echo "   Teyssir is running."
echo "   On THIS Mac open:   http://localhost:$PORT"
echo "   From another PC:    http://$(scutil --get LocalHostName 2>/dev/null || hostname -s).local:$PORT"
echo ""
echo "   Press Ctrl+C to STOP Teyssir."
echo "=============================================================="
echo ""
exec .venv/bin/waitress-serve --listen=0.0.0.0:"$PORT" teyssir.wsgi:application
