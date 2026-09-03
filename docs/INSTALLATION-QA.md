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
- **LLM:** Ollama is optional; failure never aborts the ERP. Default pulls are the **text**
  model (`mistral`) **and** the bookscan **vision** model (`qwen2.5vl:3b`); use `-SkipVision`
  / `--skip-vision` to omit the ~2 GB vision download.
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
| Vision OCR described as missing by default | Operators miss gated bookscan fallback | Phase 15.7: auto-pull `qwen2.5vl:3b`; `-SkipVision` documented |
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
| Vision OCR still large (~2 GB) | Default auto-pull on Hub; use `-SkipVision` on thin tills |

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

0. **Already have Teyssir?** See [INSTALL-WINDOWS.md §4.0](INSTALL-WINDOWS.md#40-mise-à-jour--installation-propre-hub-déjà-installé): in-place upgrade (`setup_app.ps1`) vs clean wipe (`install_all.ps1 -FreshInstall` / `Clean-PreviousInstall.ps1`).
1. Unzip or `git clone` into e.g. `C:\Teyssir\teyssir_erp`.
2. **Administrator** PowerShell in that folder.
3. `Set-ExecutionPolicy -Scope Process Bypass -Force`
4. Prefer hub: `.\deploy\windows\install_all.ps1 -Role hub` (or legacy `install.ps1 -Role hub`) — copy the printed **SYNC KEY**.
5. Desktop **Teyssir ERP** shortcut (or `start-teyssir.bat`) → <http://localhost:8000> and <http://localhost:8000/health/>.
6. Each till: prefer `.\deploy\windows\setup_caisse_C1.ps1 -HubUrl http://<hub>:8000 -SyncKey <key> -DiscoverPrinter` (or `install_all.ps1 -Role till …`).
7. Optional: `.\deploy\windows\register-autostart.ps1 -Role hub` (or `-Role till -SyncMinutes 5`). Use `-SkipAutostart` at install if you want service-only (no scheduled tasks).

No other hidden steps are required for POS + sync. Vision weights are pulled with Ollama by
default (opt out `-SkipVision`). Tesseract language packs and cloud-hub URLs remain optional.

---

## Win11 dry-run checklist (Phase 7)

> **Honest scope:** validated on **macOS** (Django suite + PowerShell structure). Items below marked
> **Win11 required** were **not** executed on this host (no winget / NSSM / Windows service).

### A — macOS / CI already verified (2026-08-30)

| Check | Result |
|-------|--------|
| `python manage.py test` | **217 OK**, 3 skipped |
| Book OCR honesty fixtures (`BookScanRegressionFixtureTests`) | **3 OK**, live photo scan skipped (needs `TEYSSIR_BOOKSCAN_REGRESSION=1`) |
| Install script brace balance (`install_all` → `setup_app` → `setup_caisse` / C1–C3 → `install.ps1`) | Balanced |
| Flag consistency: `-DiscoverPrinter`, `-SkipAutostart`, `-SkipShortcut`, LLM (`Install-LocalLlm.ps1`) | Present and forwarded through chain |
| `Discover-Printer.ps1` still in kit | Present; referenced by `setup_caisse` / `install_all` |
| `Clean-PreviousInstall.ps1` + `install_all.ps1 -FreshInstall` | Present (feature tip); docs §4.0 |
| `uninstall.ps1` leaves project DB / `.env` / media | Documented + script comments confirm |

### B — Win11 shop dry-run (do on a real Windows 11 PC)

Tick each box on Hub and at least one till (`C1`).

**Install hub**

- [ ] Admin PowerShell in project root; `Set-ExecutionPolicy -Scope Process Bypass -Force`
- [ ] `.\deploy\windows\install_all.ps1 -Role hub` completes (or soft-falls to SQLite with clear `[PG]` warning)
- [ ] Printed **SYNC KEY** noted; `.env` UTF-8 **without BOM**
- [ ] `ollama list` shows text model (`mistral`) and vision (`qwen2.5vl:3b`) unless `-SkipLlm` / `-SkipVision`
- [ ] Service `TeyssirBackend` exists, Delayed Auto-start; **no** second process listening on **8000** (no duplicate Task Scheduler “Teyssir Server” + service)
- [ ] Desktop shortcut **Teyssir ERP** opens default browser to `http://localhost:8000` **without** a persistent console window
- [ ] `http://localhost:8000/health/` → `ok`; POS UI loads (login + caisse screen)

**Install till C1**

- [ ] `.\deploy\windows\setup_caisse_C1.ps1 -HubUrl http://<hub>:8000 -SyncKey <key> -DiscoverPrinter`
- [ ] `.env` has `TEYSSIR_ROLE=till`, `TEYSSIR_TERMINAL=C1`, hub URL + sync key
- [ ] Printer: `TEYSSIR_PRINTER` is `tcp:IP:9100` after discover, or intentional `dummy` (never a hardcoded fake shop IP)
- [ ] Receipt: finalize a cash sale → ESC/POS attempt or Diagnostics printer TCP check
- [ ] Autostart: reboot till → service up, `/health/` ok, **one** listener on 8000; sync task present unless `-SkipAutostart`

**Upgrade from existing install (Win11 required)**

- [ ] In-place: `git checkout` RC/feature → `setup_app.ps1 -Role hub` → rebuild `frontend\dist` if UI stale → PWA hard-refresh
- [ ] Clean wipe: backup §12 → `install_all.ps1 -Role hub -FreshInstall` (Hub Postgres: `POSTGRES_ADMIN_PASSWORD`) → note **new** SYNC KEY → each till `-FreshInstall` with new key
- [ ] Verify old `.env.bak.*` exists after wipe; `media\` still present unless manually deleted

**Uninstall reverse (data kept)**

- [ ] `.\deploy\windows\uninstall.ps1` removes service, desktop shortcut, scheduled tasks
- [ ] Project folder, SQLite/Postgres data, `media\`, and `.env` **still present**
- [ ] Re-run `install_all.ps1` / `setup_caisse_C1.ps1` recovers without wiping secrets

### C — POS / OCR / printing / DB — verified vs Win11

| Area | Verified on macOS | Must verify on Win11 |
|------|-------------------|----------------------|
| POS checkout API + RBAC | Django tests | Live browser POS + offline queue flush |
| Receipt reprint (`?print=1` DUPLICATA) | Tests with `TEYSSIR_PRINTER=dummy` | Real ESC/POS or LAN printer after `-DiscoverPrinter` |
| Book OCR honesty (619≠ISBN, conf caps) | Fixture unit tests | Live `bookscan_regression --honesty-only` + camera Nouveau livre |
| Vision / Ollama path | Mocked in suite | Models on disk; Diagnostics LLM green |
| DB migrate / seed | Suite + local SQLite | Hub Postgres path + till SQLite + sync once |
| Desktop shortcut / NSSM / winget | Script structure only | Full install_all + reboot smoke |

Phase 8 (docs) polished operator-facing prose in [INSTALL-WINDOWS.md](INSTALL-WINDOWS.md) (chemin rapide hub/till, `install_all` / `setup_app`, caisse Cx + `-DiscoverPrinter`, LLM `-SkipLlm`/`-SkipVision`, shortcut sans console, `-SkipAutostart` + unregister, troubleshooting port 8000 / PG→SQLite / Tesseract / OCR / printer / PWA hard-refresh) and points here as the acceptance gate — **no merge to main in Phase 8**; Phase 9 (squash/merge) needs explicit confirmation.

---

## Controlled release rollback (pre–squash merge)

| Artifact | Value |
|----------|--------|
| Stable tag | `v0.9.0-pre-windows-kit` → `master` @ `8c8ce0a` |
| RC tag (frozen) | **`v1.0.0-windows-rc2`** → `dab47b9` — **behind tip**; install fixes landed after freeze |
| Feature tip (preferred) | `feature/pdf-conversion-async-optimization` @ `c82921c`+ |
| Ready tag | `v1.0.0-windows-ready` — **do not create** until Win11 dry-run PASS |
| Feature branch | Keep `feature/pdf-conversion-async-optimization` (do not delete) |
| Squash to `master` | **Blocked** until § B checklist is green on a real Win11 host |

Client revert and policy: [INSTALL-WINDOWS.md §17](INSTALL-WINDOWS.md#17-controlled-release--rollback-windows-kit).

---

## Operator Win11 dry-run runbook

> **Host gate:** run on a **native Windows 11** PC (or Hyper-V / Parallels / VMware guest
> with real Windows 11). **Wine is not acceptable.**

### 0 — Prepare tree

```powershell
# Prefer the feature tip (post-rc2 install + product fixes).
# Do NOT use bare clone of master / GitHub "Code ▸ Download ZIP" (incomplete kit).
git fetch --tags origin
git checkout feature/pdf-conversion-async-optimization
# Frozen RC only if you must reproduce dab47b9: git checkout v1.0.0-windows-rc2
cd <project-root>   # folder that contains manage.py AND deploy\windows\install_all.ps1
Set-ExecutionPolicy -Scope Process Bypass -Force
```

### 1 — Hub (Admin PowerShell)

```powershell
.\deploy\windows\install_all.ps1 -Role hub
# Optional focused re-run after deps are present:
# .\deploy\windows\setup_app.ps1 -Role hub

# Note the printed SYNC KEY, then:
# - Desktop "Teyssir ERP" shortcut → browser opens http://localhost:8000 (no console)
# - Invoke-WebRequest http://localhost:8000/health/  → expect ok
# - ollama list → mistral + qwen2.5vl:3b (unless -SkipLlm / -SkipVision)
# - Get-Service TeyssirBackend ; confirm ONE listener on TCP 8000
```

### 2 — One caisse (C1)

```powershell
.\deploy\windows\setup_caisse_C1.ps1 `
  -HubUrl http://<HUB-IP-OR-HOSTNAME>:8000 `
  -SyncKey '<SYNC_KEY_FROM_HUB>' `
  -DiscoverPrinter

# Validate:
# - .env: TEYSSIR_ROLE=till, TEYSSIR_TERMINAL=C1, hub URL + sync key
# - TEYSSIR_PRINTER=tcp:IP:9100 after discover, or intentional dummy
# - Finalize one cash sale → real receipt if printer found (else document hardware block)
# - Reboot → service/autostart + /health/ + single :8000 listener
```

### 3 — Tick § B checklist above

Copy results (PASS/FAIL + notes) back to the release engineer. On FAIL: fix on the
feature branch, push, re-run from §0 — still **no squash**.

### macOS verified vs Win11 required (this release gate)

| Verified on macOS (dev host) | Must run on Win11 |
|------------------------------|-------------------|
| Feature branch + tags pushed; RC docs present | Full `install_all.ps1 -Role hub` (winget / EDB Postgres / NSSM) |
| Script tree + flag forwarding (structure) | Hub DB `teyssir` create + migrate; `/health/` + POS UI |
| Django test suite (see § A) | Ollama models on disk; camera + Tesseract + Vision path |
| PowerShell sources parse on paper | `setup_caisse_C1.ps1 -DiscoverPrinter` + real ESC/POS :9100 if hardware |
| — | Desktop shortcut without console; autostart; PATH/permission smoke |
| — | `uninstall.ps1` keeps data; re-install recovers secrets |

**Status 2026-09-03:** dry-run still **BLOCKED** — no native Win11 / Parallels / VMware / SSH
Windows host on the release engineer Mac. Do not claim PASS; do not squash. Client installs
should use the **feature tip**, not `master` and not frozen rc2 alone.
