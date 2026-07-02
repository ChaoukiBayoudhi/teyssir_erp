#!/usr/bin/env bash
# ===========================================================
#  Auto-start Teyssir at login (+ scheduled till->hub sync).
#  Usage:
#     bash deploy/macos/register-autostart.sh hub
#     bash deploy/macos/register-autostart.sh till 300     # sync every 300s
#  Undo:  bash deploy/macos/register-autostart.sh --remove
# ===========================================================
set -euo pipefail
ROLE="${1:-till}"
INTERVAL="${2:-300}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LA="$HOME/Library/LaunchAgents"
SERVER_PLIST="$LA/com.teyssir.server.plist"
SYNC_PLIST="$LA/com.teyssir.sync.plist"
mkdir -p "$LA"

if [ "$ROLE" = "--remove" ]; then
  for p in "$SERVER_PLIST" "$SYNC_PLIST"; do
    [ -f "$p" ] && { launchctl unload "$p" 2>/dev/null || true; rm -f "$p"; }
  done
  echo "Removed Teyssir launch agents."
  exit 0
fi

cat > "$SERVER_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.teyssir.server</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$ROOT/deploy/macos/start-teyssir.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$ROOT/teyssir-server.log</string>
  <key>StandardErrorPath</key><string>$ROOT/teyssir-server.log</string>
</dict></plist>
EOF
launchctl unload "$SERVER_PLIST" 2>/dev/null || true
launchctl load "$SERVER_PLIST"
echo "Loaded 'com.teyssir.server' (starts at login, auto-restarts)."

if [ "$ROLE" = "till" ]; then
  cat > "$SYNC_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.teyssir.sync</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$ROOT/deploy/macos/sync-now.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>StandardOutPath</key><string>$ROOT/teyssir-sync.log</string>
  <key>StandardErrorPath</key><string>$ROOT/teyssir-sync.log</string>
</dict></plist>
EOF
  launchctl unload "$SYNC_PLIST" 2>/dev/null || true
  launchctl load "$SYNC_PLIST"
  echo "Loaded 'com.teyssir.sync' (every ${INTERVAL}s)."
fi

echo "Manage with: launchctl list | grep teyssir"
