# Phase 15 — Book analysis report (P15-T3)

**Date:** 2026-08-29  
**Branch:** `feature/pdf-conversion-async-optimization`  
**Tip at report:** includes distant-cover fix `2255957`  
**Scope:** Nouveau livre / bookscan only (no POS/CRUD).

## Before (P15-T1 baseline — Vision OFF)

Source: `fixtures/bookscan/baseline/p15_t1_matrix.json` / `docs/phase15/P15-T1-baseline.md`

| Metric | Value |
|--------|-------|
| Pass / fail / skip | **4 / 3 / 0** |
| Total wall time | ~539 s |
| Price misses | B_premier & F_mixed got `5.000` (want `17.000`); C_history_cnp got `24.900` (want `4.900`) |
| Fixtures E/F/B_blur | Added; E/D pass; B_blur honesty pass (empty title/price) |

Vision ON subset (B_blur, E_french, F_mixed): **2 / 1 / 0** (~281 s) — F still price `5.000`; Vision often skipped when Tess path “strong enough”.

## After (distant-cover `2255957` + Vision cache)

| Item | Status |
|------|--------|
| Distant / weak cover rescue (`2255957`) | **Done** — force Vision on ultra-garbage / low conf / small fill; closer-shot UX |
| Vision content-hash cache (P15-T3) | **Done** — local FS under `media/vision_cache/`, TTL + max entries; repeat scan hits cache |
| Screenshots A–F | **Draft captures present** in `docs/phase15-screenshots/case_{A–F}_draft.png` (+ `cases_af_results.json`). Formal Playwright UI pack / polish owned by **Phase 15 QA agent** (some `ui_*.png` are login-only stubs). |
| P15-T2 price honesty | **Still pending** — B/F `17.000` vs `5.000`, C `4.900` vs `24.900` not closed on tip at report time |

### Live Cases A–F snapshot (Vision OFF API, draft screenshots)

From `docs/phase15-screenshots/cases_af_results.json` (~433 s total):

| Case | Overall | Notable |
|------|---------|---------|
| A Beauty | FAIL | Title garbage; no ISBN/price |
| B Blur | FAIL | Empty title/price; usable_draft PASS |
| C Math/History path | PARTIAL | Title + price `4.200` PASS (school banner) |
| D Arabic | FAIL | Price `24.900` (T2 target `4.900`) |
| E French | PARTIAL | Mathématiques + `4.200` |
| F Mixed | FAIL | Price `5.000` (T2 target `17.000`); empty title |

## Cache behaviour (done when)

- First Vision call for a cover pair stores JSON keyed by SHA-256 of downscaled JPEGs + model + max_edge.
- Identical bytes (even under a new filename) return `vision_cache_hit=true` without calling Ollama.
- Env: `TEYSSIR_VISION_CACHE` (default on), `TEYSSIR_VISION_CACHE_DIR`, `TEYSSIR_VISION_CACHE_TTL` (7d), `TEYSSIR_VISION_CACHE_MAX` (200).

## Still open for Phase 15

1. **P15-T2** price sticker honesty (Premier 17.000 / History CNP 4.900).
2. **QA** finish formal UI screenshots + pass/fail sign-off after T2.
3. Optional: subject→category suggestion (STEP 9 residue); accuracy-gated multi-pass only if measured scores still fail after T2.
