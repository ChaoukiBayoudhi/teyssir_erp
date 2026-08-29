# Teyssir — macOS deployment kit (Apple Silicon / M1 & Intel)

Scripts to install and run Teyssir on macOS.

👉 **Full step-by-step guide:** [`docs/INSTALL-MACOS.md`](../../docs/INSTALL-MACOS.md)

| File | Purpose |
|------|---------|
| `install.sh` | One-shot installer: venv, deps, `.env`, migrate + seeds, LaunchAgent, Desktop app. |
| `Install-BackendService.sh` | LaunchAgent `com.teyssir.backend` (waitress, RunAtLoad, KeepAlive, logs/). |
| `Install-DesktopApp.sh` | **Teyssir ERP.app** on Desktop + `~/Applications` with branding `.icns`. |
| `open-teyssir.sh` | Wait for `/health/` then `open http://localhost:8000`. |
| `serve.py` | Service entry: migrate + waitress. |
| `uninstall.sh` | Remove LaunchAgents + shortcuts (keeps data). |
| `start-teyssir.sh` | Manual Terminal server **if** the agent is not running. |
| `register-autostart.sh` | Ensures backend agent + till sync schedule. |
| `sync-now.sh` / `sync-to-cloud.sh` | Sync helpers. |
| `.env.hub.example` / `.env.till.example` | Config templates. |

**Quick start**
```bash
bash deploy/macos/install.sh --role hub
# Double-click  Teyssir ERP  on the Desktop
```

Verified live on a MacBook Pro M1. Uses only free/open-source tools (Python, waitress, WhiteNoise, launchd).

### Book scan speed (`TEYSSIR_BOOKSCAN_ACCURACY`)

Shop default: **accuracy off** (fast Tess path; Vision only for weak drafts).
`Install-BackendService.sh` does not set this env var.

If a LaunchAgent was configured with `TEYSSIR_BOOKSCAN_ACCURACY=1` for testing, turn it
off for day-to-day Nouveau livre:

```bash
# Set to 0 (or delete the key — unset means off)
/usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:TEYSSIR_BOOKSCAN_ACCURACY 0" \
  ~/Library/LaunchAgents/com.teyssir.backend.plist
launchctl kickstart -k "gui/$(id -u)/com.teyssir.backend"
```
