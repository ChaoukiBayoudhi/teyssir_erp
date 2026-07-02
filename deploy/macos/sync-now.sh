#!/usr/bin/env bash
# TILL -> HUB sync (push local sales, pull master data). Safe to run anytime;
# sales are always saved locally first. Schedule via register-autostart.sh.
set -euo pipefail
cd "$(cd "$(dirname "$0")/../.." && pwd)"
.venv/bin/python manage.py sync_now || echo "[WARN] Sync incomplete (hub offline?). Will retry."
