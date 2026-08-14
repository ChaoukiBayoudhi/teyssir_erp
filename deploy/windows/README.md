# Teyssir — Windows deployment kit

Scripts to install and run Teyssir on the client's Windows PCs (1 Hub + up to 3 tills).

👉 **Full step-by-step guide:** [`docs/INSTALL-WINDOWS.md`](../../docs/INSTALL-WINDOWS.md)

| File | Purpose |
|------|---------|
| `install.ps1` | One-shot installer: Python venv, deps, build, `.env` (random secrets), DB, admin user, optional Ollama. |
| `Install-Postgres.ps1` | Silent PostgreSQL install + `teyssir` database (hub only; SQLite fallback). |
| `Install-LocalLlm.ps1` | Silent Ollama install, API check, model pull (called by `install.ps1`; never fails the ERP). |
| `start-teyssir.bat` | Start the server (hub or till, per `.env`) on `http://localhost:8000`. |
| `register-autostart.ps1` | Auto-start at logon + scheduled till→hub sync (Task Scheduler). |
| `sync-now.bat` | Till → hub reconcile (manual or scheduled). |
| `sync-to-cloud.bat` | Hub → cloud hub forwarding (multi-store only). |
| `.env.hub.example` / `.env.till.example` | Config templates (copy to `.env` in the project root). |

**Quick start**
```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
# Hub PC:
.\deploy\windows\install.ps1 -Role hub
# Each till (use the SYNC KEY printed by the hub install):
.\deploy\windows\install.ps1 -Role till -Terminal C1 -HubUrl http://teyssir-hub.local:8000 -SyncKey <hub-key>
# Then on each PC:
.\deploy\windows\start-teyssir.bat
```

Everything here uses only free/open-source tools (Python, waitress, WhiteNoise).
