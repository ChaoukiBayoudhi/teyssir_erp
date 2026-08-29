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
| `launch-backend.sh` | LaunchAgent wrapper (Python.app; avoids EX_CONFIG). |
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

## LaunchAgent notes (macOS)

- Plist path is **user-local only**: `~/Library/LaunchAgents/com.teyssir.backend.plist` — do not commit it.
- Working tree for Phase 15 book OCR: run `bash deploy/macos/Install-BackendService.sh` from the **bookocr worktree** so `WorkingDirectory` and `launch-backend.sh` point there.
- The agent runs `/bin/bash deploy/macos/launch-backend.sh`, which execs Homebrew **Python.app** with the worktree venv. Calling `.venv/bin/python` directly from launchd often fails with **exit 78 `EX_CONFIG`** (Homebrew python is a `posix_spawn` stub blocked by Launch Constraints).
- LaunchAgent `StandardOutPath`/`StandardErrorPath` must **not** live under `Documents/` (TCC): use `~/Library/Logs/teyssir/`.
- Wrapper executed by launchd: `~/Library/Application Support/Teyssir/launch-backend.sh` (not the copy under Documents).
