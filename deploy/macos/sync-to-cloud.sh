#!/usr/bin/env bash
# HUB -> CLOUD HUB forwarding (multi-store only). No effect unless
# TEYSSIR_CLOUD_HUB_URL is set in .env. Schedule on the HUB Mac.
set -euo pipefail
cd "$(cd "$(dirname "$0")/../.." && pwd)"
.venv/bin/python manage.py sync_to_cloud
