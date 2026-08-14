# Installation & documentation QA (Phase 10)

Date: 2026-08-14 · Scope: Windows `install.ps1` + GitHub docs vs production Hub/Till.

This is the **final validation report** for the Windows installer and public guides.
A full silent run of `install.ps1` still requires a Windows PC (Administrator + winget).
Django migrate/seed paths were executed on this machine (macOS) for Hub PostgreSQL,
Hub SQLite fallback, and Till SQLite.

---

## What is correct

- **Roles:** Hub defaults to PostgreSQL (`TEYSSIR_DB=postgres`); tills are SQLite-only and never install PostgreSQL.
- **Fallback:** If PostgreSQL install, role creation, or `migrate` fails, the Hub switches to SQLite and continues.
- **Secrets:** Passwords and `SECRET_KEY` / `TEYSSIR_SYNC_KEY` are generated into `.env` (UTF-8, no BOM). Nothing is hardcoded in the repo.
- **Seeds:** `seed_rbac` and `seed_fiscal` are idempotent (`get_or_create` / `permissions.set`).
- **LLM:** Ollama is optional; failure never aborts the ERP. Default pull is the **text** model (`mistral`), not vision.
- **Idempotency (script):** Re-run reuses `.venv`, does not clobber secrets, skips admin if a superuser exists, reuses an already-reachable `teyssir` database, `CREATE ROLE/DATABASE` only if missing.
- **Start path:** `start-teyssir.bat` runs `migrate` then waitress on `:8000`. Health URL is documented.
- **Docs present:** `README.md`, `docs/INSTALL-WINDOWS.md`, `docs/ARCHITECTURE.md`, `docs/POSTGRESQL-SETUP.md`, `docs/LOCAL-AI.md`, `docs/PDF-CONVERSION.md`, `docs/AUDIT-REPORT-2026-07-28.md`, `docs/BOOK-OCR-ARCHITECTURE.md`.
- **Tests:** `python manage.py test` → **125 OK** (1 skipped).

---

## What was missing (found in this audit)

| Gap | Impact | Fix |
|-----|--------|-----|
| Python not installed → hard throw, no winget attempt | Hidden manual step | `install.ps1` now tries `Python.Python.3.12` via winget |
| `migrate` failure on Postgres aborted the whole install | Hub would not open | Catch + set `TEYSSIR_DB=sqlite` + retry |
| Second Hub run required unknown `postgres` superuser password | Duplicate-run looked broken | Login as app user `teyssir` first; skip create if reachable |
| `createsuperuser` always interactive | Second run blocked / extra users | Skip if a superuser exists; `-AdminUser` / `-AdminPassword` |
| Firewall port 8000 only in the guide | Tills could not reach Hub | Best-effort `New-NetFirewallRule` on Hub |
| Backup chapter only mentioned SQLite files | Wrong backup for production Hub | `pg_dump` documented |
| Vision OCR described as auto-installed | Surprise ~2 GB download missing | Docs: text model only; `-PullVision` for vision |
| README said **102** tests | Stale | **125** |
| Till `.env` example omitted `TEYSSIR_DB=sqlite` | Inconsistency | Added |

---

## Remaining risks (Windows shop PC)

| Risk | Mitigation |
|------|------------|
| This QA **did not** execute winget / EDB silent Postgres / OllamaSetup.exe on Windows | First Hub install should be watched once; SQLite fallback keeps the shop open |
| Microsoft Store `python` alias (exit 9009) | Script requires a real 3.11+ interpreter; guide tells the operator to disable the alias |
| Existing PostgreSQL with an unknown superuser password | App-user login works on re-run; first create still needs `POSTGRES_ADMIN_PASSWORD` |
| `frontend\dist` missing and Node install fails | API starts; UI missing — warning printed |
| Interactive admin prompt if no `-AdminUser` | Expected for a first human install; use flags for unattended |
| Installer does **not** start waitress itself | Prevents a blocking window inside the installer; `start-teyssir.bat` is the documented next step |
| Vision OCR still manual | Avoids a huge default download on a till PC |

---

## Simulated scenarios

### Scenario 1 — Hub (PostgreSQL)

On a machine with PostgreSQL 17: `TEYSSIR_ROLE=hub` `TEYSSIR_DB=postgres` → `migrate` + `seed_rbac` + `seed_fiscal` succeed (UTF-8, millime decimals, integer quantities). Windows silent install of the EDB package is **not** run here.

### Scenario 2 — Hub fallback (SQLite)

`TEYSSIR_ROLE=hub` `TEYSSIR_DB=sqlite` → same migrate/seed on `teyssir_hub.sqlite3`. This is the path `install.ps1` takes when Postgres is missing or `migrate` fails.

### Scenario 3 — Till (SQLite + sync env)

`TEYSSIR_ROLE=till` `TEYSSIR_DB=sqlite` `TEYSSIR_TERMINAL=C1` `TEYSSIR_HUB_URL` + `TEYSSIR_SYNC_KEY` → migrate/seed; no PostgreSQL. Re-running `install.ps1 -Role till -SyncKey …` updates `.env` without wiping `SECRET_KEY`.

---

## Operator checklist (fresh Windows PC)

1. Unzip or `git clone` into e.g. `C:\Teyssir\teyssir_erp`.
2. **Administrator** PowerShell in that folder.
3. `Set-ExecutionPolicy -Scope Process Bypass -Force`
4. `.\deploy\windows\install.ps1 -Role hub` — copy the printed **SYNC KEY**.
5. `deploy\windows\start-teyssir.bat` → <http://localhost:8000> and <http://localhost:8000/health/>.
6. Each till: `.\deploy\windows\install.ps1 -Role till -Terminal C1 -HubUrl http://<hub>:8000 -SyncKey <key>` then `start-teyssir.bat`.
7. Optional: `.\deploy\windows\register-autostart.ps1 -Role hub` (or `-Role till -SyncMinutes 5`).

No other hidden steps are required for POS + sync. OCR vision, Tesseract language packs, and cloud-hub URLs remain optional.
