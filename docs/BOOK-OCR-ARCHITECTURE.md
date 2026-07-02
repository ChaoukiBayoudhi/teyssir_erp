# Camera Book Registration & OCR — Architecture Decisions

Integrated into the existing Teyssir ERP (Django + DRF + React PWA, offline-first federated nodes).
Guiding principle: **ISBN-first, OCR-fallback, provider-pluggable, offline-tolerant.**

## 1. Identification strategy (the core insight)
Modern books carry an **EAN-13 barcode that *is* the ISBN-13**. Scanning it is faster and far more
accurate than OCR-ing a cover. The PWA already scans barcodes (`BarcodeDetector` / `@zxing`). So:

1. **Barcode/ISBN** → primary identifier (when present).
2. **Free metadata enrichment** by ISBN → rich structured data (title, authors, publisher, year,
   pages, subjects, cover). Providers: **OpenLibrary** (no key) and **Google Books** (no key for
   basic) — both free, good multilingual coverage.
3. **OCR fallback** only when there is no ISBN / no enrichment (offline or unknown book): read the
   cover text and best-effort extract fields.

This makes the common case (a barcoded book) a one-scan, high-accuracy, **free** operation.

## 2. OCR engine — analysis & decision
| Option | AR/FR/EN | Offline | Free | Verdict |
|---|---|---|---|---|
| **Tesseract** (OSS) | `ara+fra+eng` packs; moderate AR, good FR/EN | ✅ | ✅ | **default** (offline) — text → ISBN regex |
| **Vision LLM via Ollama** (qwen2.5vl / llama3.2-vision / minicpm-v) | best for mixed-language *structured* extraction | ✅ | ✅ | **implemented** — `OCR_PROVIDER=vision`, free+offline, no key |
| Cloud Vision (Google/Azure/AWS) or hosted Vision LLM (Claude/GPT-4o) | highest incl. AR | ❌ | ❌ (paid) | optional plug, same interface |

**Decision — Strategy pattern (`OcrProvider`)**: ship a **`TesseractOcrProvider`** (free/offline text
OCR; the ISBN it finds drives enrichment), degrading to a **`ManualOcrProvider`** no-op if Tesseract
isn't installed. **`VisionLlmOcrProvider`** (implemented) calls a **local Ollama vision model** — free,
offline, no API key — and returns a *structured* multilingual `BookDraft` directly (title, authors,
translators, publisher, languages, year, pages, ISBN, subject), so it doesn't depend on the ISBN being
present/registered. A hosted Vision LLM (paid) is the same interface with a different transport — no
schema/API change. Selected by `TEYSSIR_OCR_PROVIDER`; model via `TEYSSIR_VISION_MODEL`. Providers
return `(raw_text, BookDraft)` and degrade gracefully (never crash a scan).

## 3. Metadata-provider abstraction
`BookMetadataProvider.enrich(isbn) -> BookData | None`. Implementations `OpenLibraryProvider`,
`GoogleBooksProvider`, tried in order (`TEYSSIR_METADATA_PROVIDERS`); first hit wins, fields merged.
New providers (BNF, WorldCat, a local distributor feed) plug in with **no schema change** because the
full payload is also kept in `Book.raw_metadata` (JSON) for future enrichment.

## 4. Data model (normalized, additive, backward-compatible)
Reuse `catalog.Product` (already `is_book`, `isbn`, `name_fr/ar`, `sale_price`). Add:
- **`catalog.Book`** (OneToOne→Product): `isbn10/isbn13, subtitle, publisher, series, edition,
  languages (JSON), pub_year, pages, dimensions, cover_type, subject, keywords (JSON), description,
  source_provider, ocr_confidence, raw_metadata (JSON)`.
- **`catalog.Contributor`** (`name`, unique) + **`catalog.BookContributor`** (book, contributor,
  `role` ∈ AUTHOR/TRANSLATOR/EDITOR/ILLUSTRATOR, order) — normalized many-to-many for multiple
  authors/translators, no redundancy.
- **`catalog.ProductImage`** (FK→Product): `image (ImageField)`, `kind` ∈ COVER/BACK/PAGE/OTHER,
  `is_primary`, `order`, `ocr_text`, `created_at`. Stores the **originals**.

Existing tables are untouched → fully backward-compatible.

## 5. Image storage — compare & decide
| Option | Scalable | Offline | Infra | Verdict |
|---|---|---|---|---|
| DB BLOB | bloats DB, slow backups | ✅ | none | ✗ |
| **Local FS (`MEDIA_ROOT`)** | good per-node | ✅ | none | **default** |
| Object store (MinIO/S3) | excellent | ❌ (online) | adds service | future |
| Cloud (S3/GCS) | excellent | ❌ | lock-in, paid | future |

**Decision — Django `ImageField` over a pluggable storage backend** (`STORAGES`). Default = local
filesystem per node (offline-first). The DB stores only a **path/key string**, so moving to
**MinIO/S3** is a **settings/env change, zero migration** — **implemented**: set `TEYSSIR_S3_BUCKET`
(+ `TEYSSIR_S3_ENDPOINT` for MinIO) and media goes to object storage (needs django-storages+boto3,
installed only at the cloud tier; MinIO is free/self-hosted). OCR reads are **storage-agnostic**
(`bookscan.jobs.local_image_paths` streams remote files to a temp copy since S3 has no `.path`).
Store→hub image consolidation is the media-replication step already shipped (`fetch_missing_media`).

## 6. Async OCR — implemented
A scan is a **`ScanJob`** (local-only model) processed by a pluggable executor (`SCAN_EXECUTOR`):
- **`inline`** (default) — runs synchronously; the POST already returns the draft. Deterministic for
  tests, fine for fast OCR (Tesseract).
- **`thread`** — runs in a background daemon thread; the POST returns **`202 {job_id, status:"pending"}`**
  and the client **polls** `GET /catalog/books/scan/<job_id>` until `done`/`failed`. This keeps a slow
  engine (a vision LLM ~tens of seconds) from blocking the request.

`enqueue_scan(job_id)` (catalog/bookscan/jobs.py) is the seam: add a **Celery/Django-Q** backend later
without touching the HTTP API (the client already polls). Implemented with the **stdlib only** (no new
dep). A failed scan records `FAILED` + the error — a job is never lost. Verified live: with `thread`,
the scan POST returns in **~0.02 s** and the vision result lands ~60 s later via polling.

## 7. Workflow & API
```
Camera (PWA) → capture photo(s) + try BarcodeDetector
   → POST /api/v1/catalog/books/scan  (multipart: images[] + optional isbn)
       → store images (draft) → enrich(isbn) + OCR → return DRAFT fields + confidence + image refs
   → user reviews / corrects
   → POST /api/v1/catalog/books  (reviewed JSON + image ids) → create Product + Book + Contributors
Image mgmt: /api/v1/catalog/products/<id>/images/  (list/add/delete, set primary, reorder)
```

## 8. UX
Minimal clicks: scan → (auto-fill) → review → save. Show **OCR/source confidence** per field, image
preview/crop/rotate (client-side canvas), and validate before save. Bilingual AR/FR + RTL like the
rest of the PWA.

## Status / phasing
- **Phase A (this milestone):** models + migration, provider abstractions (`OcrProvider`,
  `BookMetadataProvider` + OpenLibrary), `scan_book` + create services, scan/create/image API,
  ImageField storage, tests (mocked providers).
- **Phase B:** PWA Book-Creation camera page (capture, barcode, review, crop).
- **Phase C (later):** Tesseract/Vision-LLM provider, async via Celery, hub media replication, MinIO.
