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

**Next:** (2) cloud-hub sync peer — the store hub pushes its outbox to a cloud hub (reuses §4.4
push/pull recursively); (3) consolidation API — group GL/sales by `store_code`, add an optional
`store` filter to Financials/Dashboard; (4) flip `STORAGES` to MinIO/S3 at the cloud tier.

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
