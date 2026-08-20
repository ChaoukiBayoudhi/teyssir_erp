#!/usr/bin/env bash
# ===========================================================
#  Start Teyssir in this Terminal IF the LaunchAgent is not already running.
# ===========================================================
set -euo pipefail
cd "$(cd "$(dirname "$0")/../.." && pwd)"

PORT="${TEYSSIR_PORT:-8000}"

if curl -sf "http://127.0.0.1:$PORT/health/" >/dev/null 2>&1; then
  echo "Teyssir is already running on port $PORT."
  echo "Opening http://localhost:$PORT"
  exec bash "$(dirname "$0")/open-teyssir.sh"
fi

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
echo "   Teyssir is running in this Terminal."
echo "   On THIS Mac open:   http://localhost:$PORT"
echo "   Prefer the LaunchAgent: bash deploy/macos/Install-BackendService.sh"
echo "   Press Ctrl+C to STOP Teyssir."
echo "=============================================================="
echo ""
exec .venv/bin/waitress-serve --listen=0.0.0.0:"$PORT" teyssir.wsgi:application
