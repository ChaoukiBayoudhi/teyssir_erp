# Teyssir — Windows deployment kit

Scripts to install and run Teyssir on the client's Windows PCs (1 Hub + up to 3 tills).

> **Source of truth:** this full kit lives on
> `feature/pdf-conversion-async-optimization` and tag `v1.0.0-windows-rc2`.  
> Default GitHub `master` / **Code ▸ Download ZIP** is **incomplete** (no
> `install_all.ps1` / `setup_caisse_C*.ps1`). See [`docs/INSTALL-WINDOWS.md`](../../docs/INSTALL-WINDOWS.md) §3.

👉 **Full step-by-step guide (operators):** [`docs/INSTALL-WINDOWS.md`](../../docs/INSTALL-WINDOWS.md)  
👉 **Acceptance gate (Win11 dry-run):** [`docs/INSTALLATION-QA.md`](../../docs/INSTALLATION-QA.md#win11-dry-run-checklist-phase-7)

| File | Purpose |
|------|---------|
| **`install_all.ps1`** | **Preferred full entry:** log file, hub auto-elevate, host deps (winget), LLM, then `install.ps1`. |
| **`setup_app.ps1`** | **App-layer bootstrap:** git pull/clone if needed → LLM if missing → `install.ps1` → validate (`django check`, migrate, `frontend\dist`, `/health/`). Run after deps or alone when Python is present. |
| **`setup_caisse.ps1`** | **Per-caisse entry:** `-Terminal C1\|C2\|C3` → `setup_app.ps1 -Role till …`. Optional StoreCode / HubUrl / SyncKey / Printer / `-DiscoverPrinter`. Post-checks: hub `/health/`, Discover-Printer path, POS launch. |
| `setup_caisse_C1.ps1` / `_C2` / `_C3` | Thin ID wrappers → `setup_caisse.ps1 -Terminal Cx` (same optional flags). Prefer these on till PCs. |
| `Install-HostDependencies.ps1` | Idempotent host deps: Python ≥3.11, optional Git, Node LTS if needed, Tesseract + eng/fra/ara verify. No Redis. |
| `install.ps1` | Full app installer (idempotent): venv, Hub PostgreSQL or SQLite soft-fail, seeds, Ollama, **Windows service**, **desktop shortcut**. |
| `Discover-Printer.ps1` | LAN scan for ESC/POS on TCP 9100 → `tcp:IP:9100` or `dummy` (never hardcodes a shop IP). |
| `Install-WindowsService.ps1` | NSSM service `TeyssirBackend` (waitress, auto-start, restart on failure, logs). |
| `Install-DesktopShortcut.ps1` | Desktop + Start Menu shortcut **Teyssir ERP** with branding `.ico` (no console). |
| `open-teyssir.vbs` | Silent launcher (no console flash) → `open-teyssir.ps1`. |
| `open-teyssir.ps1` | Wait for `/health/` then open the default browser. |
| `serve.py` | Service entry: migrate + waitress (not `runserver`). |
| `uninstall.ps1` | Remove service, shortcut, scheduled tasks (**keeps** project data / `.env` / media). |
| **`Clean-PreviousInstall.ps1`** | **Opt-in wipe** before reinstall: service, tasks, shortcuts, DB, `.env` backup+remove, optional `.venv`. Used by `-FreshInstall`. |
| `Install-Postgres.ps1` | Silent PostgreSQL + `teyssir` database (hub only; SQLite fallback). `-ResetDatabase` for Hub fresh wipe. |
| `Install-LocalLlm.ps1` | Silent Ollama install + text/vision pulls (never fails the ERP). Opt out: `-SkipLlm` / `-SkipVision` on callers. |
| `start-teyssir.bat` | Manual windowed server **if** the service is not running. Do not run alongside the service (port 8000). |
| `register-autostart.ps1` | Till sync (5 min); logon « Teyssir Server » only if service missing. Opt out at install: `-SkipAutostart`. |
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
# Per-caisse — prefer these on till PCs:
.\deploy\windows\setup_caisse_C1.ps1 -HubUrl http://teyssir-hub.local:8000 -SyncKey <hub-key> -DiscoverPrinter
.\deploy\windows\setup_caisse_C2.ps1 -HubUrl http://teyssir-hub.local:8000 -SyncKey <hub-key>
.\deploy\windows\setup_caisse_C3.ps1 -HubUrl http://teyssir-hub.local:8000 -SyncKey <hub-key> -StoreCode S1
# Checks only (hub /health/, printer + POS paths):
.\deploy\windows\setup_caisse_C1.ps1 -ValidateOnly -HubUrl http://teyssir-hub.local:8000
# Ticket printer (optional): -DiscoverPrinter  or  .\deploy\windows\Discover-Printer.ps1
# Double-click  Teyssir ERP  on the Desktop  (or .\deploy\windows\open-teyssir.ps1)
# Skip scheduled tasks only: add -SkipAutostart (service still installs unless -SkipService)
```

**Notes**
- Till installs do **not** force UAC elevate (`-NoElevate` soft path). Hub auto-elevates unless `-NoElevate`.
- **Chain:** `install_all.ps1` (host) → `setup_app.ps1` (app) → `install.ps1` (shared spine). Per-caisse: `setup_caisse_Cx.ps1` → `setup_caisse.ps1` → `setup_app.ps1` → `install.ps1`.
- **Env fallbacks (caisse):** `TEYSSIR_TERMINAL`, `TEYSSIR_STORE_CODE`, `TEYSSIR_HUB_URL`, `TEYSSIR_SYNC_KEY`, `TEYSSIR_PRINTER` when matching params are empty.
- **Local AI (default):** `install_all.ps1` / `setup_app.ps1` detect Ollama → install/pull **mistral** + **qwen2.5vl:3b** when missing. Soft-fail on disk/network. Opt out: `-SkipLlm` / `-SkipVision`.
- **libzbar** (pyzbar ISBN): no winget package on Windows — bundle `libzbar-64.dll` or use client BarcodeDetector + digit-OCR fallback (see `docs/INSTALL-WINDOWS.md`).
- **Discover-Printer** kept — never hardcodes a shop ticket-printer IP (no fake Aclas IP).
- **Unregister autostart:** `Unregister-ScheduledTask -TaskName "Teyssir Sync","Teyssir Server" -Confirm:$false` or full reverse via `uninstall.ps1`.
- **Upgrade from old install:** idempotent re-run keeps DB/`.env` — see [`docs/INSTALL-WINDOWS.md` §3bis](../../docs/INSTALL-WINDOWS.md#3bis-mise-à-jour-depuis-une-ancienne-installation-hub-déjà-en-place). For a **clean wipe**: `-FreshInstall` on `install_all` / `setup_app` / `install` / `setup_caisse*` (or `Clean-PreviousInstall.ps1 -FreshInstall`). Verify: `Select-String FreshInstall deploy\windows\*.ps1`.
- **`frontend\dist` is not in Git** — build once (`npm ci && npm run build` in `frontend\`) or copy from a PC that built it.
- **No Redis** in this kit.

Everything here uses only free/open-source tools (Python, waitress, WhiteNoise, NSSM).
