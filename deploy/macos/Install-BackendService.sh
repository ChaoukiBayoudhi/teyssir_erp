#!/usr/bin/env bash
# ===========================================================
#  Register Teyssir as a macOS LaunchAgent (auto-start at login).
#  Equivalent of the Windows NSSM service "TeyssirBackend".
#
#     bash deploy/macos/Install-BackendService.sh
#     bash deploy/macos/Install-BackendService.sh --printer tcp:192.168.1.100:9100
#     bash deploy/macos/Install-BackendService.sh --remove
#  TEYSSIR_PRINTER is taken from --printer, else .env, else dummy.
#
#  Program is /bin/bash + launch-backend.sh (not .venv/bin/python): Homebrew's
#  python posix_spawn stub exits 78 EX_CONFIG under launchd Launch Constraints.
#  Plist lives in ~/Library/LaunchAgents (user-local; do not commit).
# ===========================================================
set -euo pipefail

REMOVE=0
PORT="${TEYSSIR_PORT:-8000}"
PRINTER_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --remove) REMOVE=1; shift;;
    --port) PORT="$2"; shift 2;;
    --printer) PRINTER_ARG="$2"; shift 2;;
    *) echo "Unknown option: $1"; exit 1;;
  esac
done

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LA="$HOME/Library/LaunchAgents"
LABEL="com.teyssir.backend"
PLIST="$LA/$LABEL.plist"
OLD_PLIST="$LA/com.teyssir.server.plist"
PYTHON="$ROOT/.venv/bin/python"
SERVE="$ROOT/deploy/macos/serve.py"
LAUNCH_SRC="$ROOT/deploy/macos/launch-backend.sh"
SUPPORT_DIR="$HOME/Library/Application Support/Teyssir"
LAUNCH="$SUPPORT_DIR/launch-backend.sh"
LOG_DIR="$HOME/Library/Logs/teyssir"
mkdir -p "$LA" "$LOG_DIR" "$SUPPORT_DIR"

# LaunchAgent cannot execute scripts under Documents/ (TCC → exit 126).
# Install a copy under ~/Library/Application Support with absolute Python.app.
PYAPP=""
for cand in \
  /opt/homebrew/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python \
  /opt/homebrew/Cellar/python@3.14/*/Frameworks/Python.framework/Versions/*/Resources/Python.app/Contents/MacOS/Python
do
  if [ -x "$cand" ]; then PYAPP="$cand"; break; fi
done
if [ -z "$PYAPP" ]; then
  echo "[ERROR] Homebrew Python.app not found"
  exit 1
fi
cat > "$LAUNCH" <<LAUNCH_EOF
#!/bin/bash
set -euo pipefail
ROOT="$ROOT"
cd "\$ROOT"
export VIRTUAL_ENV="\$ROOT/.venv"
export PATH="\$VIRTUAL_ENV/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHONUNBUFFERED=1
export __PYVENV_LAUNCHER__="\$VIRTUAL_ENV/bin/python"
exec "$PYAPP" "\$ROOT/deploy/macos/serve.py"
LAUNCH_EOF
chmod +x "$LAUNCH"

TESS_CMD="/opt/homebrew/bin/tesseract"
for cand in /opt/homebrew/bin/tesseract /usr/local/bin/tesseract; do
  if [ -x "$cand" ]; then TESS_CMD="$cand"; break; fi
done
# Prefer .env override when present
if [ -f "$ROOT/.env" ]; then
  env_cmd="$(grep -E '^(TEYSSIR_TESSERACT_CMD|TESSERACT_CMD)=' "$ROOT/.env" | head -1 | cut -d= -f2- || true)"
  if [ -n "${env_cmd:-}" ] && [ -x "$env_cmd" ]; then TESS_CMD="$env_cmd"; fi
fi

# Receipt printer: CLI > .env > dummy (never bake a developer LAN IP into the plist)
PRINTER="dummy"
if [ -n "$PRINTER_ARG" ]; then
  PRINTER="$PRINTER_ARG"
elif [ -f "$ROOT/.env" ]; then
  env_printer="$(grep -E '^TEYSSIR_PRINTER=' "$ROOT/.env" | head -1 | cut -d= -f2- | tr -d '\r' || true)"
  if [ -n "${env_printer:-}" ]; then PRINTER="$env_printer"; fi
fi

uid="$(id -u)"

unload_label() {
  local label="$1" plist="$2"
  if command -v launchctl >/dev/null 2>&1; then
    launchctl bootout "gui/$uid/$label" 2>/dev/null || true
    launchctl unload "$plist" 2>/dev/null || true
  fi
}

if [ "$REMOVE" -eq 1 ]; then
  unload_label "$LABEL" "$PLIST"
  unload_label "com.teyssir.server" "$OLD_PLIST"
  rm -f "$PLIST" "$OLD_PLIST"
  echo "Removed LaunchAgent $LABEL (and legacy com.teyssir.server)."
  exit 0
fi

if [ ! -x "$PYTHON" ] || [ ! -f "$SERVE" ]; then
  echo "[ERROR] Missing .venv or serve.py. Run: bash deploy/macos/install.sh first."
  exit 1
fi
if [ ! -x "$LAUNCH" ]; then
  echo "[ERROR] Failed to install $LAUNCH"
  exit 1
fi

# Drop legacy agent that called start-teyssir.sh (avoid two servers on :8000).
if [ -f "$OLD_PLIST" ]; then
  unload_label "com.teyssir.server" "$OLD_PLIST"
  rm -f "$OLD_PLIST"
  echo "Removed legacy LaunchAgent com.teyssir.server."
fi

# If something else already listens on the port, warn but still install the plist.
if command -v lsof >/dev/null 2>&1; then
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "WARNING: port $PORT is already in use. Stop the other process before expecting the agent to bind."
  fi
fi

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$LAUNCH</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$ROOT</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PORT</key>
    <string>$PORT</string>
    <key>TEYSSIR_PORT</key>
    <string>$PORT</string>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>DYLD_FALLBACK_LIBRARY_PATH</key>
    <string>/opt/homebrew/lib:/usr/local/lib</string>
    <key>HOME</key>
    <string>$HOME</string>
    <key>LANG</key>
    <string>en_US.UTF-8</string>
    <key>LC_ALL</key>
    <string>en_US.UTF-8</string>
    <key>TEYSSIR_SCAN_EXECUTOR</key>
    <string>thread</string>
    <key>TEYSSIR_TESSERACT_CMD</key>
    <string>$TESS_CMD</string>
    <key>TESSERACT_CMD</key>
    <string>$TESS_CMD</string>
    <key>TEYSSIR_PRINTER</key>
    <string>$PRINTER</string>
    <key>TEYSSIR_OCR_VISION_FALLBACK</key>
    <string>true</string>
    <key>TEYSSIR_BOOKSCAN_ACCURACY</key>
    <string>0</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>5</integer>
  <!-- Do NOT set ProcessType=Background: macOS App Nap marks it "inefficient" and SIGTERMs waitress. -->
  <key>StandardOutPath</key>
  <string>$LOG_DIR/teyssir-backend-stdout.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/teyssir-backend-stderr.log</string>
</dict>
</plist>
EOF

unload_label "$LABEL" "$PLIST"
if launchctl bootstrap "gui/$uid" "$PLIST" 2>/dev/null; then
  :
else
  # Older macOS / fallback
  launchctl load -w "$PLIST"
fi
launchctl enable "gui/$uid/$LABEL" 2>/dev/null || true
launchctl kickstart -k "gui/$uid/$LABEL" 2>/dev/null || true

ok=0
for i in $(seq 1 25); do
  if curl -sf "http://127.0.0.1:$PORT/health/" >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 1
done

echo "LaunchAgent $LABEL installed (RunAtLoad + KeepAlive)."
echo "Printer:     TEYSSIR_PRINTER=$PRINTER"
echo "Logs: $LOG_DIR/teyssir-backend-*.log"
if [ "$ok" -eq 1 ]; then
  echo "Health check: OK  http://127.0.0.1:$PORT/health/"
else
  echo "WARNING: /health/ not ready yet — check logs, or: launchctl print gui/$uid/$LABEL"
fi
echo "Status: launchctl list | grep teyssir"
