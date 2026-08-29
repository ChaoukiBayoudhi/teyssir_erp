# Teyssir — macOS deployment kit (Apple Silicon / M1 & Intel)

Scripts to install and run Teyssir on macOS.

👉 **Full step-by-step guide:** [`docs/INSTALL-MACOS.md`](../../docs/INSTALL-MACOS.md)

| File | Purpose |
|------|---------|
| `install.sh` | One-shot installer: venv, deps, build, `.env` (random secrets), DB, admin user. |
| `start-teyssir.sh` | Start the server (hub or till, per `.env`) on `http://localhost:8000`. |
| `register-autostart.sh` | Auto-start at login + scheduled sync via `launchctl` (LaunchAgents). |
| `sync-now.sh` | Till → hub reconcile. |
| `sync-to-cloud.sh` | Hub → cloud hub forwarding (multi-store only). |
| `.env.hub.example` / `.env.till.example` | Config templates (copy to `.env` in the project root). |

**Quick start**
```bash
# Hub Mac:
bash deploy/macos/install.sh --role hub          # note the printed SYNC KEY
# Each till (use the SYNC KEY from the hub install):
bash deploy/macos/install.sh --role till --terminal C1 \
     --hub-url http://teyssir-hub.local:8000 --sync-key <hub-key>
# Then on each Mac:
bash deploy/macos/start-teyssir.sh               # open http://localhost:8000
```

<<<<<<< HEAD
Verified live on a MacBook Pro M1. Uses only free/open-source tools (Python, waitress, WhiteNoise).
=======
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
>>>>>>> f204f96 (fix: barcode-only books without ISBN and faster scan path)
