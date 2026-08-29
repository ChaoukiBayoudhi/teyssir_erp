#!/bin/bash
# LaunchAgent entry for Teyssir backend.
# Homebrew's venv ``python`` is a posix_spawn stub into Python.app; under launchd
# Launch Constraints that nested spawn exits 78 (EX_CONFIG). Exec Python.app directly.
#
# If :PORT is already healthy (manual ``serve.py`` / another worktree), hold without
# re-binding so KeepAlive does not thrash against Address-already-in-use.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
VENV="$ROOT/.venv"
VENV_PY="$VENV/bin/python"
SERVE="$ROOT/deploy/macos/serve.py"
export VIRTUAL_ENV="$VENV"
export PATH="$VENV/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# libzbar for pyzbar bookscan barcodes (LaunchAgent PATH alone is not enough)
export DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib:/usr/local/lib:${DYLD_FALLBACK_LIBRARY_PATH:-}"
export PYTHONUNBUFFERED=1
export __PYVENV_LAUNCHER__="$VENV_PY"

PORT="${TEYSSIR_PORT:-${PORT:-8000}}"
PORT="$(echo "$PORT" | tr -d '[:space:]')"
PORT="${PORT:-8000}"

# Manual serve.py already up → hold the agent slot without fighting the bind.
if command -v curl >/dev/null 2>&1; then
  if curl -sf "http://127.0.0.1:${PORT}/health/" >/dev/null 2>&1; then
    echo "launch-backend: :${PORT} already healthy — holding (no duplicate bind)" >&2
    while curl -sf "http://127.0.0.1:${PORT}/health/" >/dev/null 2>&1; do
      sleep 30
    done
    echo "launch-backend: health lost on :${PORT} — starting serve.py" >&2
  fi
fi

PYAPP=""
# Stable opt path first, then Cellar glob
for cand in \
  /opt/homebrew/Frameworks/Python.framework/Versions/Current/Resources/Python.app/Contents/MacOS/Python \
  /opt/homebrew/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python \
  /opt/homebrew/Cellar/python@3.14/*/Frameworks/Python.framework/Versions/*/Resources/Python.app/Contents/MacOS/Python
do
  if [ -x "$cand" ]; then PYAPP="$cand"; break; fi
done

if [ -z "$PYAPP" ]; then
  echo "launch-backend: Python.app not found under Homebrew" >&2
  exit 78
fi
exec "$PYAPP" "$SERVE"
