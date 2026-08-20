#!/usr/bin/env bash
# ===========================================================
#  Wait for the local backend, then open the PWA in the default browser.
# ===========================================================
set -euo pipefail
PORT="${TEYSSIR_PORT:-8000}"
HEALTH="http://127.0.0.1:${PORT}/health/"
APP="http://localhost:${PORT}/"

for _ in $(seq 1 40); do
  if curl -sf "$HEALTH" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

open "$APP"
