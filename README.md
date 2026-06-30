# Teyssir ERP

Retail management platform for **Teyssir Library** (Tunisia) — POS, inventory, purchasing,
accounting. Full architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

This repository currently contains the **Phase-0 skeleton** implementing the *correctness-critical*
core of the spec:

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

Set `TEYSSIR_ROLE=hub` and the `POSTGRES_*` vars, uncomment `psycopg[binary]` in
`requirements.txt`, then `migrate`. Same codebase, different node profile (spec §20).

## Node roles

| Role | DB | Purpose |
|------|----|---------|
| `hub` ("Teyssir Hub", PC-1) | PostgreSQL | source of truth, sync master, backups, reporting |
| `till` (C1/C2/C3) | SQLite | offline-capable POS; syncs to the hub |
