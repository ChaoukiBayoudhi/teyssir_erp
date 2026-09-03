#!/usr/bin/env bash
# Scan the local /24 for an ESC/POS printer on TCP 9100.
# Prints tcp:HOST:9100 or dummy (soft-fail). See deploy/discover_printer.py.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python deploy/discover_printer.py "$@"
fi
exec python3 deploy/discover_printer.py "$@"
