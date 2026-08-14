# Teyssir ERP — Deep Audit & Hardening Report

**Date:** 2026-07-28 (Phase 4 update 2026-07-30)
**Scope:** Offline-first retail ERP (Tunisia) — multi-agent static audit + corrective implementation
**Test suite:** **110 passing** (was 75)
**Status:** Critical fiscal / sync / POS integrity fixed; PDF→Word async shipped; Phase 4–5 OCR autofill + integer qty validated

---

## 1. Agent team & method

| Agent | Responsibility | Outcome |
|---|---|---|
| Code Auditor | Static analysis of Django modules | 4 CRITICAL + ~12 HIGH findings |
| Backend↔Frontend Integration | Full API contract validation | ACCOUNT-without-customer, RBAC gap, offline poison queue |
| Financial Integrity | Money / TVA / GL / timbre | Receipt TVA drift, header discount dead, returns absent from GL |
| Test & Validation | Coverage gaps + regression | +22 tests; suite green |
| Refactor & Optimization | Targeted fixes only | Money millime VO, sale finalize, sync stock guard |
| AI Enhancement | Deferred (OCR already live) | No change this pass — OCR/Vision-LLM already verified |

---

## 2. System map (verified)

```
Till (SQLite WAL) ── SyncOutbox UUID ──► Store Hub (Postgres) ──► Cloud Hub (optional)
     │                                        │
     ├─ POS finalize (local)                  ├─ GL post_all_to_gl
     ├─ ESC/POS print                         ├─ Master pull (catalog)
     └─ Offline PWA queue                     └─ VAT declaration
```

| Module | Purpose | Verdict |
|---|---|---|
| `core` | Money (millime), WAL, UUID models | Hardened — integer-millime arithmetic |
| `accounts` | UUID users + RBAC capabilities | Checkout now gated by `create_sale` |
| `catalog` | Products, TaxRate 7/13/19/0, books/OCR | TVA per product OK; category present |
| `inventory` | Append-only stock ledger | Sound; WAC under negative stock still a risk |
| `billing` | Gapless `C1-YYYYMM-XXXX`, timbre snapshot | Sound |
| `sales` | finalize + returns + cash X/Z | Discounts + return validation fixed |
| `ledger` | Double-entry GL + VAT declaration | Returns now posted; unpaid sales skipped |
| `sync` | Outbox push / master pull | Stock cache no longer clobbered |
| `printing` | ESC/POS receipt | TVA now uses `line_tax` (matches books) |
| `api` + PWA | DRF + React POS | Discounts, ACCOUNT+customer, poison queue fixed |

---

## 3. Issues detected (severity)

### CRITICAL (fixed)

| ID | Issue | Fix |
|---|---|---|
| C1 | `CheckoutView` only required `IsAuthenticated` — any role could finalize fiscal sales | Gated with `capability("create_sale")` |
| C2 | Returns not posted to GL → TVA declaration overstated after refunds | `post_return_to_gl` + included in `post_all_to_gl` |
| C3 | Master pull overwrote till `qty_on_hand` / `cost_avg` | Snapshot + restore local stock caches on apply |
| C4 | Returns accepted arbitrary products/qty/prices | Bound to original sale lines; over-return rejected |

### HIGH (fixed)

| ID | Issue | Fix |
|---|---|---|
| H1 | Header `Sale.discount` never applied | Proportional HT allocation **before** TVA |
| H2 | Line discount unbounded (could go negative HT) | `DiscountError` if discount > line HT |
| H3 | Receipt TVA recomputed without HALF_UP → 0.01 DT drift at 19% | Use `money.line_tax` |
| H4 | POS ACCOUNT without customer still finalized | Serializer + UI require customer |
| H5 | Offline flush requeued validation errors forever | Dead-letter quarantine for non-offline errors |
| H6 | Quote convert skipped thermal print | `_print_receipt` after convert |
| H7 | Unpaid sales poisoned GL batch | Batch skips sales where tender ≠ total |

### MEDIUM / LOW (documented, partial)

| ID | Issue | Status |
|---|---|---|
| M1 | `manager_pin` never verified | Remaining — wire for void/discount overrides |
| M2 | `Invoice.immutable` flag only | Remaining — add save guard |
| M3 | Till can author catalog without upward sync | Remaining — hub-only master data |
| M4 | Shared `X-Sync-Key` | Remaining — Phase-6 hardening |
| M5 | GET financial reports mutate GL | Remaining — prefer explicit `gl_post` |
| L1 | Books default tax 0% if omitted | Remaining — default to 7% for books |

---

## 4. Price system (millimes)

**Policy (ARCHITECTURE §7.2, confirmed):** store millime-exact amounts; display 2 dp.

**Implementation after this pass:**

- DB columns remain `Decimal(14,3)` (= millime scale: `1.000` DT = 1000 millimes) for migration compatibility.
- **All new arithmetic** goes through integer millimes: `to_millimes` / `from_millimes` / `add_money` / `sub_money` / `line_tax`.
- POS preview uses the same millime rounding so UI ≈ server.
- No `float` money path; float inputs coerce via `str` then quantize HALF_UP.

This satisfies “exact millime arithmetic / no rounding drift” without a breaking IntegerField migration across every money column.

---

## 5. VAT / discount / sale flow

**Order of operations (now enforced):**

1. Line HT = qty × unit_price − line_discount
2. Header discount allocated proportionally on HT
3. TVA per line on adjusted HT (`line_tax`, rates 0/7/13/19)
4. Timbre fiscal snapshotted from config
5. Total TTC = HT + TVA + timbre
6. Payment recorded; ACCOUNT charges customer ledger
7. Stock movements append-only
8. Invoice number allocated gaplessly
9. ESC/POS receipt printed (best-effort)
10. SyncOutbox enqueue (UUID idempotent)

---

## 6. Bill printing (ESC/POS)

Already present; hardened this pass:

- Store name + matricule fiscal
- Invoice number, terminal
- Lines (qty × unit, line discount if any)
- TVA breakdown per rate (millime-correct)
- Timbre + TOTAL TTC
- Payment method
- Cut + cash-drawer kick
- Works offline (local Django node)
- Quote→sale now also prints

---

## 7. Product enhancements

| Feature | Status |
|---|---|
| TVA per product (`TaxRate` FK) | ✅ existing + used at checkout |
| Category assignment | ✅ existing |
| Line % discount (POS → absolute HT) | ✅ **new UI + backend** |
| Global % discount per sale | ✅ **new UI + backend** |
| Discount before VAT | ✅ enforced |

---

## 8. Test results

```
Ran 97 tests in ~12.5s
OK
```

New coverage includes: millime round-trip, TVA 13%/19% drift case, line+header discount, discount bounds, return over-qty, return GL reversal, unpaid-sale GL skip, master-pull stock preservation, ACCOUNT customer required, `create_sale` RBAC denial, receipt tax == booked tax.

---

## 9. Before / after

| Area | Before | After |
|---|---|---|
| Tests | 75 | **102** |
| Checkout RBAC | Any authenticated user | `create_sale` only |
| Header discount | Dead field | Applied pre-VAT |
| Receipt @ 19% | Could print 0.48 vs booked 0.49 | Matches books |
| Returns → GL | Missing | Reversing journals |
| Master pull stock | Clobbered | Preserved |
| Offline queue | Poison loop | Dead-letter |
| ACCOUNT sales | No customer OK | Rejected |
| PDF→Word | Sync pdf2docx (blocks Hub) | Async job + fast text path |

---

## 10. Remaining risks

1. **DGI sign-off** on monthly-reset numbering format still required (product/legal, not code).
2. **manager_pin** unused — cashiers can apply any discount via API.
3. **WAC with negative on-hand** after oversell can distort COGS.
4. **Till-authored products** without hub push → catalog split-brain.
5. **GET /reports/*** still side-effect post to GL — race under concurrent accountants.
6. Integer DB columns (optional future migration) if auditor demands physical INTEGER millimes.

---

## 11. Recommended next increments

1. Enforce `manager_pin` for discount > N% and voids.
2. Default book tax_rate to 7% on create.
3. UNIQUE constraint on `JournalEntry(ref_type, ref_id)`.
4. Returns UI in the PWA.
5. Property-based random-cart rounding tests (Hypothesis).
6. Per-terminal sync credentials.

---

## 12. PDF → Word conversion optimization (2026-07-28 phase 2)

### Root cause (confirmed)

| Rank | Bottleneck | Evidence |
|---|---|---|
| 1 | Sync HTTP blocked on pdf2docx CPU layout | 10-page mixed PDF ≈ **5 s** on request thread → waitress/POS lag on Windows Hub |
| 2 | Default `clip_image_res_ratio=4.0` + stream-table parse | Heavy pixmap / table work |
| 3 | Double I/O (`upload.read` → `%TEMP%` write → re-read → buffer docx) | Worse under Windows Defender |
| 4 | No OCR involved | pdf2docx `ocr=0` by default — not the lag source |

### Architecture change

```
POST /tools/pdf-to-docx
  ├─ chunks() → MEDIA_ROOT/convert/<job>/in.pdf
  ├─ tiny (≤2MB, ≤5p) → sync 200 FileResponse (compat)
  └─ else → ConvertJob + enqueue (thread on Windows) → 202 {job_id}
GET  /tools/pdf-to-docx/<id>      → pending|running|done|failed
GET  /tools/pdf-to-docx/<id>/download → FileResponse (no full RAM buffer)
```

Engines:

* **fast** — text-dense (PyMuPDF → python-docx)
* **layout** — pdf2docx with `stream=`, `clip_image_res_ratio=2.0`, `parse_stream_table=False`, MP if ≥8 pages
* **auto** — pick by chars/images density

Temp files under `MEDIA_ROOT/tmp` and `MEDIA_ROOT/convert` (not system `%TEMP%`).

### Benchmarks (`tools/bench_pdfconvert.py`)

| Case | Legacy pdf2docx | Tuned layout | Fast / Auto | Gain |
|---|---:|---:|---:|---:|
| 2-page text | 0.38 s | 0.34 s | **0.06 s** | **6.3×** |
| 10-page mixed | 4.95 s | 5.33 s | **0.13 s** | **38.6×** |
| 50-page text | 6.89 s | 7.55 s | **0.78 s** | **8.8×** |

UX: PWA polls with queued / processing / ready states — Hub POS no longer freezes during large converts.

### Remaining limitations

* Fast path trades layout fidelity (columns/tables as plain paragraphs). Use **Fidèle** mode when layout matters.
* Scanned image-only PDFs still produce poor editable text (pdf2docx limitation; OCR is a separate bookscan pipeline).
* Exclude `media\tmp` + `media\convert` from Defender realtime scan on Windows Hub.

---

## 13. Phase 4 — Real-world functional validation (2026-07-30)

**Agents:** OCR & Camera · POS Integrity · Pricing · Printing & Receipt · Browser Automation
**Test suite:** **106 passing**

### 13.1 Negative balance bug (Customers “Règlement”)

| Item | Detail |
|---|---|
| **Symptom** | Client `xyz` showed **Solde -2222.000 DT** after a single Règlement |
| **Root cause** | `post_payment` accepted any positive amount with **no cap vs outstanding balance** — paying 2222 on a 0 balance created a credit (negative solde). UI prefixed `-` on PAYMENT rows; stored amount was +2222 |
| **Fix** | `AccountAmountError`; amounts must be `> 0`; default reject payment `> balance` (`allow_overpay` opt-in); FE validates + disables pay when solde ≤ 0 |
| **Evidence** | API `400` on overpay/negative; browser alert *« Le règlement (80.000 DT max) dépasse le solde dû »*; `xyz` repaired to 0.000 |
| **Screenshots** | `docs/phase4-screenshots/phase4-clients-repaired.png`, `phase4-overpay-blocked.png` |

### 13.2 OCR / camera pipeline

| Check | Result |
|---|---|
| Capture → POST `/catalog/books/scan` → job poll | Wired (inline executor returns `done`) |
| Silent empty OCR | **Fixed** — Tesseract unavailable stamps `raw.ocr_available=false`; empty text stamps `ocr_empty` + confidence 0; FE shows `ocrUnavailable` / `ocrWeak`; `pollScanJob` throws on `failed` |
| Manual fallback | Form remains editable; price input millime step `0.001` |
| Camera on POS | Present (barcode path); book OCR via **Nouveau livre** |

### 13.3 Integer / millime pricing

| Layer | Status |
|---|---|
| DB `Decimal(14,3)` + `to_millimes` / `from_millimes` | Enforced in money VO |
| `require_non_negative_money` on product/book create | Added |
| FE price inputs | `min: 0`, `step: 0.001` |
| Display | Storage 3 dp; POS/receipt display 2 dp HALF_UP |

### 13.4 POS totals & printing

| Check | Result |
|---|---|
| Discount > line / header | Still rejected (`DiscountError`); `sale.total < 0` guarded |
| Browser POS | Cahier + 50% remise → total **1.67 DT** (never negative) |
| Checkout response | `sale_id`, `printed` (bool), `receipt_url` |
| ESC/POS | Dummy device prints at checkout (`printed: true`) |
| Text receipt | `GET /api/v1/pos/sales/<id>/receipt` — preview/download; FE opens blob after pay |
| PDF A4 facture | Not in scope this phase (thermal + text ticket only) |

**Screenshots:** `docs/phase4-screenshots/phase4-pos-empty.png`, `phase4-pos-discount.png`

### 13.5 Remaining Phase 4 notes

* Physical camera + real ISBN cover not exercised in headless browser (no webcam); pipeline + soft-fail verified via API.
* Existing negative-balance rows elsewhere should be reviewed; `xyz` cleaned in local DB.
* Optional: manager PIN for large discounts (still listed in §11).

---

## 14. Phase 5 — Camera auto-fill + integer quantities (2026-07-30)

**Test suite:** **110 passing**

### 14.1 ISBN-first camera → metadata → form

| Step | Implementation |
|---|---|
| Barcode (browser) | `BarcodeDetector` EAN-13 → ISBN 978/979 |
| OCR digits | Center crop + grayscale/contrast + digit whitelist; retry full image |
| ISBN normalize | `bookscan/isbn.py` — ISBN-10/13 check digits, ISBN-10→13 |
| Metadata | Open Library **first**, Google Books **fallback** (`TEYSSIR_METADATA_PROVIDERS`) |
| Form fill | Title, authors, ISBN, publisher, subject… + **Catégorie** / **TVA 7%** defaults |
| Failure UX | Alert: *« ISBN non détecté »* + *« Veuillez saisir manuellement ou reprendre la photo »* |
| Loading | LinearProgress + status: détection ISBN / OCR / enregistrement |

**Verified live:** blank image → `isbn_not_detected`; `isbn=9782070612758` → Open Library *Le Petit Prince* / Saint-Exupéry.

### 14.2 Integer quantities (no more 1.000)

| Layer | Change |
|---|---|
| Models | `IntegerField` for all qty columns (Product, StockMovement, SaleLine, PO lines, …) |
| Helpers | `teyssir/core/qty.py` — `to_qty` rejects fractions; `format_qty` for API |
| API | Serializers use `IntegerField`; catalog rows return `"88"` not `"88.000"` |
| FE | `fmtQty()` + `step={1}` on qty inputs (POS, Catalog, Receiving, StockTake, …) |

**Screenshots:** `docs/phase5-screenshots/` — POS Qté=`1`, Catalog stock chips integers, ISBN failure banner.

### 14.3 Remaining notes

* Webcam cover photos vary by lighting; digit OCR + metadata is the reliable path when barcode/ISBN is readable.
* Suggested sale price still optional (providers rarely return TND prices).

---

## 15. Phase 6 — Smart Camera & OCR Enhancements (2026-07-30)

**Test suite:** **115 passing**

### 15.1 Camera UX

| Fix | Behavior |
|---|---|
| Auto-stop after capture | `stopCamera()` stops all `MediaStream` tracks, clears `video.srcObject`, hides video UI |
| Guided capture | Stepper: **Recto → Verso → Analyse** with FR/AR instructions |
| Reopen cleanly | Camera restarted only on explicit “Caméra” / “Caméra (verso)” |

### 15.2 ISBN vs non-ISBN

| Path | Behavior |
|---|---|
| ISBN present | Unchanged Phase 5 pipeline: barcode → OCR digits → Open Library / Google Books → form |
| No ISBN | Front-cover text OCR → title/author candidates → optional `enrich_by_title` search → `manual_assist` + prefilled title for correction |
| Image kinds | Scan API stores image[0]=`COVER`, image[1]=`BACK` |

### 15.3 Front / back intelligence + price

| Cover | Focus |
|---|---|
| Front | Large-text OCR (title, author, language) |
| Back | Digit pass for ISBN + Tunisian price regex (`15 DT`, `12.500`) → millimes |
| Merge | `_merge_cover_drafts`: title/author from front; ISBN/price from back |

Price extraction requires a **currency/label cue** or **millime-style** decimals (avoids false positives from ISBN digits).

### 15.4 Multilingual OCR (AR / FR / EN)

* Script heuristic `detect_script_langs` → Tesseract langs (`ara` / `fra+eng` / mixed)
* Form inputs use `dir="auto"` for RTL Arabic
* Live check: Arabic cover → title `كتاب الفقه`, author `سيد سابق`, `languages=["ar"]`, UTF-8 intact

### 15.5 Browser / API validation

| Scenario | Result |
|---|---|
| ISBN front+back | *Le Petit Prince* + `9782070612758` + price `15.000` (Open Library + OCR price) |
| No ISBN | Title OCR prefill + `isbn_not_detected` + `manual_assist` |
| Arabic | Correct Arabic text, no encoding corruption |
| ISBN-only still works | Metadata enrichment unchanged |

**Screenshots:** `docs/phase6-screenshots/` — Recto/Verso stepper, Arabic assist form, ISBN+price fill, OCR samples.

### 15.6 Remaining notes

* Real webcam lighting still limits OCR; barcode + ISBN metadata remains the most reliable path when present.
* Title-search APIs need network; offline path still returns OCR title for manual correction.

---

## 16. Phase 7 — Furniture Product Support (2026-08-14)

**Test suite:** **120 passing** (1 skipped)

Non-book items (knapsacks, pencil cases, school supplies) are identified by a **unique reference**, not ISBN. The book OCR/ISBN pipeline is unchanged.

### 16.1 Reference-based identification

| Field | Rule |
|---|---|
| `product_type` | `book` \| `furniture` (kept in sync with `is_book`) |
| `reference` | Unique when non-empty; numeric (`1001`) or alphanumeric (`SAC-001`) |
| `sku` | Equals the reference for furniture (POS/barcode lookup still works) |
| `color`, `brand` | Furniture attributes |
| `sale_price` | Millimes (3 dp TND) |
| `qty_on_hand` | Integer pieces |
| Barcode | Optional on furniture; never runs ISBN OCR |

Books keep ISBN as the key (`reference` left blank). Existing supplies: `reference` backfilled from `sku`.

### 16.2 UI adaptation

* **Nouveau article** toggle: Fournitures / Livres.
* Furniture form: required **référence**, nom, couleur, marque, prix DT, quantité entière; optional barcode camera (scan only — auto-stop, no ISBN).
* Book form: ISBN, titre, auteur + link to existing **Caméra OCR**.
* Duplicate reference → HTTP 409; missing furniture reference → 400.

### 16.3 POS behavior

* Exact lookup on **barcode, SKU, or reference** (not only 6+ digit EAN).
* Name search still works.
* Adding the same article increments **integer** quantity (`Math.trunc`).

### 16.4 Validation

| Scenario | Result |
|---|---|
| Create knapsack `1001` / Sac à dos / Blue / HP | Saved `product_type=furniture`, price `45.000`, qty `3` |
| POS lookup `1001` | Instant hit |
| Duplicate `1001` | 409 |
| Search name “Sac” | Found |

**Screenshots:** `docs/phase7-screenshots/` (form + POS reference/qty). Live Chrome automation was unavailable on this host; scenarios were executed against the running API (`127.0.0.1:8000`).

---

*Generated by the Teyssir multi-agent audit pass — updated through Phase 7.*
