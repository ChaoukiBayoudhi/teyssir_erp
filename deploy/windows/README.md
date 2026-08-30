# Teyssir — Windows deployment kit

Scripts to install and run Teyssir on the client's Windows PCs (1 Hub + up to 3 tills).

👉 **Full step-by-step guide:** [`docs/INSTALL-WINDOWS.md`](../../docs/INSTALL-WINDOWS.md)

| File | Purpose |
|------|---------|
| **`install_all.ps1`** | **Preferred entry:** log file, hub auto-elevate, host deps (winget), then `install.ps1`. |
| `Install-HostDependencies.ps1` | Idempotent host deps: Python ≥3.11, optional Git, Node LTS if needed, Tesseract + eng/fra/ara verify. No Redis. |
| `install.ps1` | Full app installer (idempotent): venv, Hub PostgreSQL or SQLite, seeds, Ollama, **Windows service**, **desktop shortcut**. |
| `Discover-Printer.ps1` | LAN scan for ESC/POS on TCP 9100 → `tcp:IP:9100` or `dummy` (never hardcodes a shop IP). |
| `Install-WindowsService.ps1` | NSSM service `TeyssirBackend` (waitress, auto-start, restart on failure, logs). |
| `Install-DesktopShortcut.ps1` | Desktop + Start Menu shortcut **Teyssir ERP** with branding icon. |
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
# Preferred (logs under %LOCALAPPDATA%\Teyssir\logs, deps, then full install):
.\deploy\windows\install_all.ps1 -Role hub
.\deploy\windows\install_all.ps1 -Role till -Terminal C1 -HubUrl http://teyssir-hub.local:8000 -SyncKey <hub-key>
# Ticket printer (optional): -DiscoverPrinter  or  .\deploy\windows\Discover-Printer.ps1
# Double-click  Teyssir ERP  on the Desktop
```

**Notes**
- Till installs do **not** force UAC elevate (`-NoElevate` soft path). Hub auto-elevates unless `-NoElevate`.
- **Local AI (default):** `install_all.ps1` detects Ollama → installs via winget if missing → pulls **mistral** + **qwen2.5vl:3b** (skips if already present). Soft-fail on disk/network. Opt out: `-SkipLlm` / `-SkipVision`.
- **libzbar** (pyzbar ISBN): no winget package on Windows — bundle `libzbar-64.dll` or use client BarcodeDetector + digit-OCR fallback (see `docs/INSTALL-WINDOWS.md`).
- **Discover-Printer** kept — never hardcodes a shop ticket-printer IP.

Everything here uses only free/open-source tools (Python, waitress, WhiteNoise, NSSM).
