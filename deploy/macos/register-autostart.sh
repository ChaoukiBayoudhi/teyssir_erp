#!/usr/bin/env bash
# ===========================================================
#  Auto-start helpers for macOS.
#  Prefer Install-BackendService.sh for the API. This script:
#    - ensures the backend LaunchAgent is installed
#    - on tills, schedules sync every N seconds
#
#     bash deploy/macos/register-autostart.sh hub
#     bash deploy/macos/register-autostart.sh till 300
#     bash deploy/macos/register-autostart.sh --remove
# ===========================================================
set -euo pipefail

ROLE="${1:-till}"
INTERVAL="${2:-300}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LA="$HOME/Library/LaunchAgents"
SYNC_PLIST="$LA/com.teyssir.sync.plist"
uid="$(id -u)"
mkdir -p "$LA"

if [ "$ROLE" = "--remove" ]; then
  bash "$ROOT/deploy/macos/Install-BackendService.sh" --remove || true
  if [ -f "$SYNC_PLIST" ]; then
    launchctl bootout "gui/$uid/com.teyssir.sync" 2>/dev/null || true
    launchctl unload "$SYNC_PLIST" 2>/dev/null || true
    rm -f "$SYNC_PLIST"
  fi
  echo "Removed Teyssir launch agents."
  exit 0
fi

# Backend = LaunchAgent com.teyssir.backend (waitress, KeepAlive)
bash "$ROOT/deploy/macos/Install-BackendService.sh"

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
  <key>StandardOutPath</key><string>$ROOT/logs/teyssir-sync.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/teyssir-sync.log</string>
</dict></plist>
EOF
  mkdir -p "$ROOT/logs"
  launchctl bootout "gui/$uid/com.teyssir.sync" 2>/dev/null || true
  launchctl unload "$SYNC_PLIST" 2>/dev/null || true
  if ! launchctl bootstrap "gui/$uid" "$SYNC_PLIST" 2>/dev/null; then
    launchctl load -w "$SYNC_PLIST"
  fi
  echo "Loaded 'com.teyssir.sync' (every ${INTERVAL}s)."
fi

echo "Manage with: launchctl list | grep teyssir"
