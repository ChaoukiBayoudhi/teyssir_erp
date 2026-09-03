# Phase 15 QA — Bookscan Cases A–F

**Date:** 2026-08-29  
**Worktree:** `teyssir_erp_bookocr`  
**Tip:** `2255957` (Vision rescue + closer-shot UX)  
**Also in ancestry:** `f4db5d1` barcode-noise price rejection (T2)  
**Scope:** Nouveau livre / bookscan only (POS/CRUD untouched)  
**Backend:** `deploy/macos/serve.py` on `:8000` from this worktree (LaunchAgent `com.teyssir.erp.backend` intermittently fights the port)  
**Corpus:** `books_photos/` (+ blur fixtures)

## Method

1. Offline `scan_book` Cases A–F with `OCR_VISION_FALLBACK=true` (~529 s).  
2. `manage.py bookscan_regression --honesty-only` → **7/7 PASS** (~206 s).  
3. Playwright Nouveau livre: MENU → Nouveau livre → PHOTOS upload → Analyser.

## Results table (CLI draft fields — authoritative)

| Case | Corpus | Overall | ms | title | language_detected | description | isbn/barcode | price | banner |
|------|--------|---------|-----|-------|-------------------|-------------|--------------|-------|--------|
| **A** | Beauty F+B | **FAIL** | 61 244 | PARTIAL `ais, melbease \|` | PASS `en` | PASS (junk template) | **FAIL** empty | **FAIL** empty | PARTIAL `unknown` |
| **B** | Premier blur | **FAIL** | 78 633 | **FAIL** empty | PASS `mixed:ar+fr+en` | **FAIL** empty | PARTIAL empty OK | **FAIL** empty | PARTIAL `unknown` |
| **C** | Math CNP | **PARTIAL** | 112 885 | PASS `Mathématiques` | PASS `fr` | PARTIAL empty | PARTIAL barcode miss | PASS `4.200` | PASS `school_cnp` |
| **D** | History Arabic | **FAIL** | 90 261 | **FAIL** empty | PASS `mixed:fr+en` | PARTIAL empty | PARTIAL barcode miss | **FAIL** empty | **FAIL** not school |
| **E** | Math French | **PARTIAL** | 97 076 | PASS `Mathématiques` | PASS `fr` | PARTIAL empty | PARTIAL barcode miss | PASS `4.200` | PASS `school_cnp` |
| **F** | Premier FR+AR | **FAIL** | 88 649 | **FAIL** empty | PASS `mixed:ar+fr` | PARTIAL empty | **FAIL** empty | **FAIL** got `4.000` | PARTIAL `unknown` |

**Summary:** 0 PASS / 2 PARTIAL / 4 FAIL · **`vision_fallback` never set** despite Vision enabled.

## Honesty regression (`--honesty-only`)

| Fixture | Result | ms | notes |
|---------|--------|-----|-------|
| A_beauty | PASS | 51 013 | title garbage allowed |
| B_blur | PASS | 24 439 | |
| B_premier | PASS | 33 464 | price `17.000` |
| C_history_cnp | PASS | 25 783 | |
| D_math_cnp | PASS | 24 916 | title Mathématiques · `4.200` |
| E_french | PASS | 24 504 | same |
| F_mixed | PASS | 22 019 | price `17.000` |

→ Soft field expects allow empty; honesty (619≠ISBN, conf caps) holds. JSON: `regression_honesty.json`.

## Nouveau livre UI

| Case | Upload | Analyze form | Screenshots |
|------|--------|--------------|-------------|
| **C** | OK | **PASS** — Mathématiques, 4,200 DT, fr, school banner, Confiance 15% | `ui_case_C_{uploaded,result}.png` |
| **A** | OK | PARTIAL — weak Beauty OCR / thumb stacking | `ui_case_A_{uploaded,result}.png` |
| **D/E/F/B** | OK | Analyze hit **offline** (backend dropped mid-job) — form empty on result shots | `ui_case_{D,E,F,B}_{uploaded,result}.png` |
| Shell | OK | MENU → Nouveau livre | `ui_00_token_login.png`, `ui_02_nouveau_livre.png` |

## Screenshot index (`docs/phase15-screenshots/`)

- Draft cards: `case_A_draft.png` … `case_F_draft.png`
- JSON: `cases_af_results.json`, `ui_playwright_results.json`, `regression_honesty.json`
- Report: this file + `README.md`

## Gaps for implementers

1. **Vision not firing** on weak Beauty/Premier/History (`vision_fallback=null`, `source=tesseract`) — junk titles like `ais, melbease |` may still skip the Vision gate.  
2. **Barcode miss** on all live phone photos — no ISBN / no CNP 619 in drafts.  
3. **Case D Arabic** — no `التاريخ`, wrong lang mix, no `school_cnp` without title/barcode.  
4. **Case F price** — Tess noise `4.000` vs `17.000` (tighten sticker-band / T2 filter).  
5. **Description** — Vision 2–4 sentence path never appears.  
6. **UI / ops** — LaunchAgent + manual `serve.py` fight `:8000` → Playwright sees `offline` mid-analyze; reopen Nouveau livre between cases; avoid stacking thumbs.  
7. **Blur fixtures** — keep `B_blur_*.jpg` discoverable from `books_photos/` for regression needles.
