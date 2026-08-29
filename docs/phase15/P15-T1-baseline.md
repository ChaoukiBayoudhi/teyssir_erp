# Phase 15 P15-T1 — Regression fixtures E/F + blur baseline

**Date:** 2026-08-29  
**Branch:** `feature/pdf-conversion-async-optimization`  
**Harness:** `python manage.py bookscan_regression --json`  
**Corpus:** `books_photos/` (symlink) + `fixtures/bookscan/images/` (B_blur)

## Fixtures added

| Id | Photos | Notes |
|----|--------|-------|
| `E_french` | Math CNP (same as D) | French-forward title/lang expects |
| `F_mixed` | Premier (same as B) | Mixed FR+AR `languages_mode=includes_all` |
| `B_blur` | Synthetic blur of Premier front/back | `fixtures/bookscan/images/B_blur_premier_{front,back}.jpg` |

Existing A–D unchanged.

## Vision OFF matrix

Source: `fixtures/bookscan/baseline/p15_t1_vision_off.json`

| Fixture | Result | ms | Notes |
|---------|--------|-----|-------|
| A_beauty | PASS | ~105s | title garbage allowed; honesty OK |
| B_blur | PASS | ~51s | empty title/price; honesty OK |
| B_premier | FAIL | ~83s | **price** want 17.000 got 5.000 |
| C_history_cnp | FAIL | ~60s | **price** want 4.900 got 24.900 |
| D_math_cnp | PASS | ~65s | title Mathématiques; price 4.200 |
| E_french | PASS | ~115s | title Mathématiques; lang fr |
| F_mixed | FAIL | ~59s | **price** want 17.000 got 5.000 (same as B) |

**Summary Vision OFF:** 4 pass / 3 fail / 0 skip (~539 s)

Price misses on B/F/C are **baseline for P15-T2** — do not rewrite OCR/merge in T1.

## Vision ON (optional sample)

Source: `fixtures/bookscan/baseline/p15_t1_vision_on_EF_blur.json`  
Subset: `B_blur`, `E_french`, `F_mixed` only (Ollama up with `qwen2.5vl:3b`).

| Fixture | Result | ms | Notes |
|---------|--------|-----|-------|
| B_blur | PASS | ~70s | Vision gate did not fire (`vision_fallback` null); empty title |
| E_french | PASS | ~124s | Same Tess path as OFF (title Mathématiques) |
| F_mixed | FAIL | ~87s | **price** want 17.000 got 5.000; Vision not used |

**Summary Vision ON (subset):** 2 pass / 1 fail / 0 skip (~281 s)

Compact matrix: `fixtures/bookscan/baseline/p15_t1_matrix.json`.
