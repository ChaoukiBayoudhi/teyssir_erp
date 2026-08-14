# Teyssir — Windows deployment kit

Scripts to install and run Teyssir on the client's Windows PCs (1 Hub + up to 3 tills).

👉 **Full step-by-step guide:** [`docs/INSTALL-WINDOWS.md`](../../docs/INSTALL-WINDOWS.md)

| File | Purpose |
|------|---------|
| `install.ps1` | One-shot installer (idempotent): Python, venv, Hub PostgreSQL or SQLite, seeds, Ollama, **Windows service**, **desktop shortcut**. |
| `Install-WindowsService.ps1` | NSSM service `TeyssirBackend` (waitress, auto-start, restart on failure, logs). |
| `Install-DesktopShortcut.ps1` | Desktop + Start Menu shortcut **Teyssir ERP** with branding icon. |
| `open-teyssir.ps1` | Wait for `/health/` then open the default browser. |
| `serve.py` | Service entry: migrate + waitress (not `runserver`). |
| `uninstall.ps1` | Remove service, shortcut, scheduled tasks (keeps data). |
| `Install-Postgres.ps1` | Silent PostgreSQL + `teyssir` database (hub only; SQLite fallback). |
| `Install-LocalLlm.ps1` | Silent Ollama install (never fails the ERP). |
| `start-teyssir.bat` | Manual windowed server **if** the service is not running. |
| `register-autostart.ps1` | Till sync schedule; logon fallback only if the service is missing. |
| `sync-now.bat` | Till → hub reconcile. |
| `sync-to-cloud.bat` | Hub → cloud hub (multi-store). |
| `.env.hub.example` / `.env.till.example` | Config templates. |

**Quick start**
```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\deploy\windows\install.ps1 -Role hub
.\deploy\windows\install.ps1 -Role till -Terminal C1 -HubUrl http://teyssir-hub.local:8000 -SyncKey <hub-key>
# Double-click  Teyssir ERP  on the Desktop
```

Everything here uses only free/open-source tools (Python, waitress, WhiteNoise, NSSM).
