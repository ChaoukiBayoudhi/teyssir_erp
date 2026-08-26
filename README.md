<p align="center">
  <img src="assets/branding/logo.png" alt="Teyssir" width="420">
</p>

<h1 align="center">Teyssir ERP</h1>

<p align="center">
Offline-first retail platform for <b>Teyssir Library</b> (Tunisia) — POS, inventory, purchasing,
double-entry accounting &amp; VAT, camera book registration (OCR), federated multi-store sync.
<br>Django + DRF backend · React PWA (FR/AR) · 100&nbsp;% free/open-source tools.
</p>

<p align="center">
  🪟 <b><a href="docs/INSTALL-WINDOWS.md">Install on Windows</a></b>
  · 🍎 <b><a href="docs/INSTALL-MACOS.md">Install on macOS (M1)</a></b>
  · 🏗️ <a href="docs/ARCHITECTURE.md">Architecture</a>
  · 📊 <a href="docs/IMPLEMENTATION-PROGRESS.md">Progress</a>
  · 📖 <a href="docs/BOOK-OCR-ARCHITECTURE.md">Book&nbsp;OCR</a>
  · 🗄️ <a href="docs/POSTGRESQL-SETUP.md">PostgreSQL</a>
  · 🧪 <a href="docs/INSTALLATION-QA.md">Install&nbsp;QA</a>
</p>

---

## Capabilities

POS (offline queue, barcode, ESC/POS receipts) · inventory (append-only ledger, weighted-avg cost,
stock-take) · purchasing (supplier → PO → receive → invoice) · returns/credit-notes · customers &amp;
credit accounts · cash sessions (X/Z) · quotations → sales · **double-entry GL** (chart of accounts,
journals, trial balance, P&amp;L, balance sheet, **monthly VAT declaration**) · **camera book
registration + OCR** (ISBN-first; Tesseract &amp; a free offline Vision-LLM; async) · **federated
sync** (till → store-hub → cloud-hub) with **multi-store consolidation**. 75 automated tests.

## Deploy for the client (Windows)

One PC Hub + up to 3 tills, each serving the PWA + API on a single port (WhiteNoise + waitress).
Full guide: **[docs/INSTALL-WINDOWS.md](docs/INSTALL-WINDOWS.md)** · kit: [deploy/windows/](deploy/windows/).

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\deploy\windows\install.ps1 -Role hub          # note the printed SYNC KEY
.\deploy\windows\install.ps1 -Role till -Terminal C1 -HubUrl http://teyssir-hub.local:8000 -SyncKey <hub-key>
.\deploy\windows\start-teyssir.bat              # then open http://localhost:8000
```

Hub install (elevated PowerShell recommended) auto-detects/installs **Python 3.12** if missing, creates `.venv`, installs PostgreSQL when possible (SQLite fallback), and seeds RBAC/fiscal data. Tills never install PostgreSQL. The script is **safe to re-run**. Full QA notes: [docs/INSTALLATION-QA.md](docs/INSTALLATION-QA.md).

---

## Backend modules

- `teyssir/core` — `Money` helpers (store `Decimal(14,3)` millime, display 2 dp, `ROUND_HALF_UP`);
  sync-ready abstract base models; SQLite WAL/PRAGMA wiring; `/health` endpoint.
- `teyssir/accounts` — custom `User` + RBAC capability permissions and a `seed_rbac` command (§10).
- `teyssir/catalog` — `Product`, `Category`, `TaxRate`, `Barcode`.
- `teyssir/inventory` — append-only `StockMovement` ledger + `apply_movement` / `recompute_on_hand`.
- `teyssir/billing` — atomic per-terminal **+ per-month** `DocumentCounter` (`C1-YYYYMM-XXXX`),
  configurable `FiscalStampConfig`, and immutable `Invoice` with `timbre_amount_snapshot`.
- `teyssir/sales` — `Sale`/`SaleLine`/`Payment`/`CashSession` + `finalize_sale` (offline-capable).
- `teyssir/sync` — append-only `SyncOutbox` (idempotent-by-UUID) skeleton (§4.4).

## Run (a till node, offline-capable, SQLite)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                     # TEYSSIR_ROLE=till, TEYSSIR_TERMINAL=C1
python manage.py migrate
python manage.py seed_rbac               # create the 8 role groups
python manage.py seed_fiscal             # TVA 7/13/19/0 + timbre fiscal 1.000 DT
python manage.py test                    # money / numbering / stamp / stock-ledger tests
python manage.py runserver               # http://127.0.0.1:8000/health/
```

## Run the hub (PostgreSQL)

Set `TEYSSIR_ROLE=hub` and the `POSTGRES_*` vars, then `migrate`. Same codebase, different node
profile (spec §20). On Windows, `install.ps1 -Role hub` does this for you.

## Node roles

| Role | DB | Purpose |
|------|----|---------|
| `hub` ("Teyssir Hub", PC-1) | **PostgreSQL** (SQLite fallback) | source of truth, sync master, backups, reporting |
| `till` (C1/C2/C3) | **SQLite** | offline-capable POS; syncs to the hub |

## Database architecture

| Node | Engine | Why |
|------|--------|-----|
| Hub | PostgreSQL | Concurrent tills, UTF-8, durable central store |
| Till | SQLite | Works offline; no extra service on the cash PC |

Hub `.env`: `TEYSSIR_ROLE=hub`, `TEYSSIR_DB=postgres`, plus `POSTGRES_DB/USER/PASSWORD/HOST/PORT`.
Tills: `TEYSSIR_ROLE=till`, `TEYSSIR_DB=sqlite`. The driver `psycopg[binary]` is in `requirements.txt`.
Windows Hub: PostgreSQL is installed automatically — [docs/POSTGRESQL-SETUP.md](docs/POSTGRESQL-SETUP.md).
