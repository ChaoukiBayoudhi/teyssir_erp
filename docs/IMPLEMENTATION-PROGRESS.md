# Teyssir ERP — Implementation Progress & Plan

Living status of the build. Backend tests: **61 passing**. PWA: **10 screens**. Git: standalone repo,
one commit per milestone.

## Done (this and prior phases)
| Area | Status |
|---|---|
| POS (cart, barcode, checkout, offline queue, ESC/POS receipt) | ✅ |
| Inventory (append-only ledger, weighted-avg cost, stock-take) | ✅ |
| Purchasing (supplier, receive goods, **PO → receive → supplier-invoice UI**) | ✅ |
| Returns / credit notes (AVOIR series) | ✅ |
| Customers + credit accounts (ledger, statement, payments) + **UI** | ✅ |
| Cash sessions (open → X → Z variance) + **UI** | ✅ |
| Quotations / reservations + **convert-to-sale UI** | ✅ |
| Federated sync (sales, returns, quotes, reservations, stock-take, customers, **cash, identity**) | ✅ |
| Identity: UUID-pk users replicated hub→till (offline login) | ✅ |
| Double-entry GL: chart of accounts, journals (sales/receipts/payments/**VAT**), trial balance, P&L, balance sheet, **monthly VAT declaration** | ✅ |
| **Camera book registration + OCR** (ISBN-first, pluggable providers, images) backend + **camera UI** + **hub→till federation incl. covers** | ✅ |
| **Tesseract offline OCR active** (ara+fra+eng) — photo → OCR finds ISBN → OpenLibrary enrichment | ✅ verified live |
| **Vision-LLM OCR provider** (free/offline via Ollama, default `qwen2.5vl:3b`) — direct structured multilingual extraction | ✅ **verified live** (title/author/publisher/lang/ISBN) |
| **Async OCR** (`SCAN_EXECUTOR=thread`) — scan returns `202 {job_id}` instantly, PWA polls; stdlib only, Celery-swappable seam | ✅ **verified live** (POST ~0.02s, result via poll) |
| Reporting/Dashboards + **Financials UI** (P&L, balance sheet, VAT, trial balance) | ✅ |
| PWA: Login · POS · Dashboard · Financials · StockTake · CashSession · Receiving · Customers · BookCreate · Quotation; FR/AR + RTL; nav consolidated into a Menu | ✅ |

## Remaining
### Phase 6 — Multi-store cloud hub (in progress)
**Done — store identity + globally-unique numbering (the keystone):** `TEYSSIR_STORE_CODE` (empty =
single-store, unchanged). When set, document numbers become `S1C1-YYYYMM-XXXX` so they never collide
across stores once consolidated. `Invoice.store_code` is stamped at issue (clean cross-store roll-up
without parsing the number); `GET /me` reports `store_code` + `role`. Backward-compatible (existing
`C1-YYYYMM-XXXX` series and all tests unchanged). Verified live (`/me` → `store_code:"S1"`).

**Done — cloud-hub sync peer (increment 2):** `CLOUD_HUB_URL`/`CLOUD_SYNC_KEY`. When set, a store
hub's `apply_push` re-enqueues applied transactions into its own outbox (idempotent by entry id);
`sync_to_cloud()` (+ `manage.py sync_to_cloud`) forwards them to the cloud hub via the *same*
idempotent push — the mechanism is recursive (till→store-hub→cloud-hub). Empty = standalone store,
no forwarding. Tested: re-enqueue, retry-idempotent, standalone no-op, mocked end-to-end drain.

**Done — consolidation API (increment 3):** `GET /reports/consolidated?from=&to=` rolls up sales by
`Invoice.store_code` (per-store lines + chain-wide grand total); `GET /reports/sales` gains an
optional `?store=` slice. Verified live. Tested: disaggregation math + store filter.

**Done — object storage + consolidated UI (increment 4):** `STORAGES` is env-driven — set
`TEYSSIR_S3_BUCKET` (+ endpoint for MinIO) to flip media to S3/MinIO at the cloud tier, zero schema
change (default stays local FS). OCR is storage-agnostic (`local_image_paths` streams remote files
to a temp copy). New PWA **Multi-magasins** screen (Dashboard → Consolidated) shows per-store
roll-up + chain total; FR/AR. Verified live.

**Optional remaining:** cloud-authored chain-wide master data (catalog/prices) if a chain wants
central control; deploy MinIO + docker-compose for the cloud hub; per-store `X-Sync-Key` rotation.
Phase 6 core is complete — a store runs standalone and federates to a cloud hub additively.

#### Original design plan
Today: one local hub per store (`teyssir-hub.local`) + offline tills syncing to it. Phase 6 federates
**multiple stores** under a cloud hub for consolidated reporting/inventory.

**Design (reuses the existing sync primitives):**
- **Store hub stays the local source of truth**; add a **cloud hub** (managed Postgres) as a *second*
  sync peer. The local hub becomes a *node* relative to the cloud hub — the same outbox/push +
  master-pull mechanism (§4.4) already generalizes (it's peer-to-hub, recursively).
- **Store identity:** add a `store` dimension (e.g. terminal codes already namespace per till; add a
  `store_code` so the cloud hub disaggregates by store). Numbering series become
  `{store}{terminal}-YYYYMM-XXXX` (the `_TYPE_CODE` seam already exists in `allocate_document_number`).
- **Master data direction:** catalog/prices can be **cloud-authored** (chain-wide) and pulled to store
  hubs, or store-local with cloud overrides — config per chain. The single-writer invariant holds at
  whatever tier owns the data.
- **Images:** flip `STORAGES` to **MinIO/S3** at the cloud tier (zero schema change — `ImageField`
  stores a key); store hubs keep local copies for offline. Media replication = an object-store sync.
- **Consolidation:** the cloud hub runs `post_all_to_gl` per store and a cross-store roll-up
  (group GL/sales by `store_code`); the Financials/Dashboard APIs gain an optional `store` filter.
- **Transport/security:** TLS to the cloud, per-store `X-Sync-Key` (rotate to mTLS/JWT), conflict
  rules unchanged (per-terminal series, append-only stock ledger, hub-authoritative master data).
- **Rollout:** a store works fully standalone (today); enabling the cloud peer is additive and
  backward-compatible. No big-bang migration.

## Architectural notes added this phase
- `docs/BOOK-OCR-ARCHITECTURE.md` — ISBN-first, OCR-fallback, pluggable metadata/OCR providers,
  image-storage trade-offs (chose `ImageField` over pluggable storage).
- GL: VAT déductible posts the VAT portion only (goods booked by the receipt) to avoid double-count.
- UX: consolidated the POS toolbar into a single Menu (was 6+ buttons).
