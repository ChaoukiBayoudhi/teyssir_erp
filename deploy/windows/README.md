# Teyssir — Windows deployment kit

Scripts to install and run Teyssir on the client's Windows PCs (1 Hub + up to 3 tills).

👉 **Full step-by-step guide:** [`docs/INSTALL-WINDOWS.md`](../../docs/INSTALL-WINDOWS.md)

| File | Purpose |
|------|---------|
| **`install_all.ps1`** | **Preferred full entry:** log file, hub auto-elevate, host deps (winget), LLM, then `install.ps1`. |
| **`setup_app.ps1`** | **App-layer bootstrap (Phase 3):** git pull/clone if needed → LLM if missing → `install.ps1` → validate (`django check`, migrate, `frontend\dist`, `/health/`). Run after deps or alone when Python is present. |
| **`setup_caisse.ps1`** | **Per-caisse entry (Phase 4):** `-Terminal C1\|C2\|C3` → `setup_app.ps1 -Role till …`. Optional StoreCode / HubUrl / SyncKey / Printer / `-DiscoverPrinter`. Post-checks: hub `/health/`, Discover-Printer path, POS launch. |
| `setup_caisse_C1.ps1` / `_C2` / `_C3` | Thin ID wrappers → `setup_caisse.ps1 -Terminal Cx` (same optional flags). |
| `Install-HostDependencies.ps1` | Idempotent host deps: Python ≥3.11, optional Git, Node LTS if needed, Tesseract + eng/fra/ara verify. No Redis. |
| `install.ps1` | Full app installer (idempotent): venv, Hub PostgreSQL or SQLite, seeds, Ollama, **Windows service**, **desktop shortcut**. |
| `Discover-Printer.ps1` | LAN scan for ESC/POS on TCP 9100 → `tcp:IP:9100` or `dummy` (never hardcodes a shop IP). |
| `Install-WindowsService.ps1` | NSSM service `TeyssirBackend` (waitress, auto-start, restart on failure, logs). |
| `Install-DesktopShortcut.ps1` | Desktop + Start Menu shortcut **Teyssir ERP** with branding `.ico` (Phase 5). |
| `open-teyssir.vbs` | Silent launcher (no console flash) → `open-teyssir.ps1`. |
| `open-teyssir.ps1` | Wait for `/health/` then open the default browser. |
| `serve.py` | Service entry: migrate + waitress (not `runserver`). |
| `uninstall.ps1` | Remove service, shortcut, scheduled tasks (keeps data). |
| `Install-Postgres.ps1` | Silent PostgreSQL + `teyssir` database (hub only; SQLite fallback). |
| `Install-LocalLlm.ps1` | Silent Ollama install + text/vision pulls (never fails the ERP). |
| `start-teyssir.bat` | Manual windowed server **if** the service is not running. |
| `register-autostart.ps1` | Till sync schedule; logon fallback only if the service is missing. |
| `sync-now.bat` | Till → hub reconcile. |
| `sync-to-cloud.bat` | Hub → cloud hub (multi-store). |
| `.env.hub.example` / `.env.till.example` | Config templates. |

**Quick start**
```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
# Preferred full path (logs under %LOCALAPPDATA%\Teyssir\logs, deps, then full install):
.\deploy\windows\install_all.ps1 -Role hub
.\deploy\windows\install_all.ps1 -Role till -Terminal C1 -HubUrl http://teyssir-hub.local:8000 -SyncKey <hub-key>
# App layer only (after deps, or when host tools already installed):
.\deploy\windows\setup_app.ps1 -Role hub
.\deploy\windows\setup_app.ps1 -Role till -Terminal C1 -HubUrl http://teyssir-hub.local:8000 -SyncKey <hub-key>
# Per-caisse (Phase 4) — prefer these on till PCs:
.\deploy\windows\setup_caisse_C1.ps1 -HubUrl http://teyssir-hub.local:8000 -SyncKey <hub-key> -DiscoverPrinter
.\deploy\windows\setup_caisse.ps1 -Terminal C2 -HubUrl http://teyssir-hub.local:8000 -SyncKey <hub-key> -StoreCode S1
# Checks only (hub /health/, printer + POS paths):
.\deploy\windows\setup_caisse_C1.ps1 -ValidateOnly -HubUrl http://teyssir-hub.local:8000
# Ticket printer (optional): -DiscoverPrinter  or  .\deploy\windows\Discover-Printer.ps1
# Double-click  Teyssir ERP  on the Desktop  (or .\deploy\windows\open-teyssir.ps1)
```

**Notes**
- Till installs do **not** force UAC elevate (`-NoElevate` soft path). Hub auto-elevates unless `-NoElevate`.
- **Chain:** `install_all.ps1` (host) → `setup_app.ps1` (app) → `install.ps1` (shared spine). Per-caisse: `setup_caisse_Cx.ps1` → `setup_caisse.ps1` → `setup_app.ps1` → `install.ps1`.
- **Env fallbacks (caisse):** `TEYSSIR_TERMINAL`, `TEYSSIR_STORE_CODE`, `TEYSSIR_HUB_URL`, `TEYSSIR_SYNC_KEY`, `TEYSSIR_PRINTER` when matching params are empty.
- **Local AI (default):** `install_all.ps1` / `setup_app.ps1` detect Ollama → install/pull **mistral** + **qwen2.5vl:3b** when missing. Soft-fail on disk/network. Opt out: `-SkipLlm` / `-SkipVision`.
- **libzbar** (pyzbar ISBN): no winget package on Windows — bundle `libzbar-64.dll` or use client BarcodeDetector + digit-OCR fallback (see `docs/INSTALL-WINDOWS.md`).
- **Discover-Printer** kept — never hardcodes a shop ticket-printer IP (no fake Aclas IP).
- **No Redis** in this kit.

Everything here uses only free/open-source tools (Python, waitress, WhiteNoise, NSSM).
