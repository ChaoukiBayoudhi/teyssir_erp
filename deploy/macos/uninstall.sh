#!/usr/bin/env bash
# ===========================================================
#  Remove Teyssir LaunchAgents, desktop/app shortcuts.
#  Does NOT delete the project folder, database, or .env.
#
#     bash deploy/macos/uninstall.sh
# ===========================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
echo "==== Teyssir uninstall (LaunchAgent + shortcuts) ===="

bash "$ROOT/deploy/macos/Install-BackendService.sh" --remove || true

# Till sync agent (from register-autostart.sh)
SYNC_PLIST="$HOME/Library/LaunchAgents/com.teyssir.sync.plist"
if [ -f "$SYNC_PLIST" ]; then
  uid="$(id -u)"
  launchctl bootout "gui/$uid/com.teyssir.sync" 2>/dev/null || true
  launchctl unload "$SYNC_PLIST" 2>/dev/null || true
  rm -f "$SYNC_PLIST"
  echo "Removed com.teyssir.sync"
fi

# Legacy register-autostart --remove path
bash "$ROOT/deploy/macos/register-autostart.sh" --remove 2>/dev/null || true

rm -rf "$HOME/Desktop/Teyssir ERP.app"
rm -rf "$HOME/Applications/Teyssir ERP.app"
echo "Removed Desktop / Applications shortcuts (if present)."
echo ""
echo "Done. Project files and the database were left in place."
echo "Delete the project folder yourself after backing up data if you want a full wipe."
