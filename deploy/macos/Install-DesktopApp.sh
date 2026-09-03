#!/usr/bin/env bash
# ===========================================================
#  Create "Teyssir ERP.app" on the Desktop (and ~/Applications)
#  with the branding icon. Double-click opens the default browser.
#
#     bash deploy/macos/Install-DesktopApp.sh
# ===========================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OPEN_SH="$ROOT/deploy/macos/open-teyssir.sh"
ICNS="$ROOT/assets/branding/teyssir.icns"
APP_NAME="Teyssir ERP.app"
DESKTOP="${HOME}/Desktop"
APPS_USER="${HOME}/Applications"

if [ ! -f "$OPEN_SH" ]; then
  echo "[ERROR] open-teyssir.sh missing"
  exit 1
fi
chmod +x "$OPEN_SH"

make_app() {
  local dest="$1"
  local app="$dest/$APP_NAME"
  rm -rf "$app"
  mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
  cat > "$app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Teyssir ERP</string>
  <key>CFBundleDisplayName</key><string>Teyssir ERP</string>
  <key>CFBundleIdentifier</key><string>com.teyssir.erp.open</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>teyssir-open</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF
  cat > "$app/Contents/MacOS/teyssir-open" <<EOF
#!/bin/bash
exec /bin/bash "$OPEN_SH"
EOF
  chmod +x "$app/Contents/MacOS/teyssir-open"
  if [ -f "$ICNS" ]; then
    cp "$ICNS" "$app/Contents/Resources/AppIcon.icns"
  fi
  # Refresh Finder icon cache for this bundle
  touch "$app"
  echo "Created: $app"
}

mkdir -p "$DESKTOP"
make_app "$DESKTOP"

mkdir -p "$APPS_USER"
make_app "$APPS_USER"

# Also drop a simple .webloc as a fallback (no icon control, but works)
cat > "$DESKTOP/Teyssir ERP.url" <<'EOF' 2>/dev/null || true
EOF
rm -f "$DESKTOP/Teyssir ERP.url" 2>/dev/null || true

echo "Double-click « Teyssir ERP » on the Desktop to open http://localhost:8000"
