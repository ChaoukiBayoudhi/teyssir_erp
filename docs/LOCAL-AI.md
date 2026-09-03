# Local AI (Ollama) — Teyssir

Teyssir can use a **local large language model** for book OCR (vision) and future AI helpers.
Nothing is sent to the cloud: inference runs on the shop PC via [Ollama](https://ollama.com).

**Phase 15.7:** Windows and macOS installers **auto-pull** the bookscan vision model
`qwen2.5vl:3b` when Ollama is available (opt out with `-SkipVision` / `--skip-vision`).
They also pull a default **text** model (`mistral`) for `check_llm` / future helpers.
**If Ollama setup fails, Teyssir still installs and runs** (POS, stock, books, PDF→Word).
AI is optional.

Day-to-day OCR stays **Tesseract**; Vision is a **gated fallback** for weak covers
(Arabic calligraphy, garbage Latin, phone / XTRIKE webcam photos with no barcode).

## Architecture

```
  PWA / POS  ──►  Django (waitress :8000)
                      │
                      ├─ OCR default: Tesseract (offline, no LLM)
                      ├─ OCR gated: Vision fallback ──► Ollama :11434
                      │         TEYSSIR_OCR_VISION_FALLBACK=true
                      │         TEYSSIR_VISION_MODEL=qwen2.5vl:3b
                      ├─ OCR optional primary: VisionLlmOcrProvider
                      │         TEYSSIR_OCR_PROVIDER=vision
                      └─ Text LLM (future modules / check_llm)
                            USE_LLM=true
                            LLM_PROVIDER=ollama
                            LLM_MODEL=mistral   (or llama3, gemma, …)
```

| Component | Port / command | Role |
|-----------|----------------|------|
| Ollama | `http://127.0.0.1:11434` | Local runtime (offline after models are pulled) |
| Text model | `LLM_MODEL` (default `mistral`) | General prompts, `manage.py check_llm` |
| Vision model | `TEYSSIR_VISION_MODEL` (default `qwen2.5vl:3b`) | Cover/ISBN structured OCR + auto description |

## Hardware: CPU vs GPU, cold start

| Machine | Expectation |
|---------|-------------|
| **CPU-only** (typical Hub / till) | `qwen2.5vl:3b` is chosen because it runs on CPU. First Vision call after idle (**cold start**) often takes **20–90 s** while Ollama loads weights into RAM. Later calls on a warm model are faster. |
| **GPU / Apple Silicon** | Ollama uses Metal / CUDA when present — same model tag, shorter latency. No env change required. |
| **Low RAM (&lt;8 GB)** | Prefer keeping Vision as gated fallback only; raise patience via `TEYSSIR_VISION_FALLBACK_TIMEOUT` / `TEYSSIR_SCAN_EXECUTOR=thread`. Skip pull on tills with `-SkipVision` if disk is tight (~2 GB for the vision weights). |

Always use `TEYSSIR_SCAN_EXECUTOR=thread` so a cold Vision call does not block waitress / POS.

## Offline & privacy

- After `ollama pull`, bookscan Vision needs **no internet**.
- Images stay on `127.0.0.1:11434` — never sent to a cloud LLM.
- Metadata enrichment (OpenLibrary / Google Books) is a **separate** online step when ISBN is known; disable by leaving the network off or clearing metadata providers — Vision/Tess still work offline.

## XTRIKE ME XPC01 / low-quality camera

Shop cameras (e.g. **XTRIKE ME XPC01**) produce soft, noisy, low-contrast covers:

- BookCreate captures the highest practical resolution and shows an aspect guide; tracks stop after each shot.
- Tess alone often fails on Arabic calligraphy or blur → Vision gated fallback (dual-image front+back) fills title / language / short description.
- Expect **editable drafts**, not perfect auto-save: operators should review ISBN checksum and price.
- Good lighting + fill the frame still help more than any model upgrade.

## Change the model

In `.env` (project root):

```
USE_LLM=true
LLM_PROVIDER=ollama
LLM_MODEL=llama3
TEYSSIR_OLLAMA_URL=http://127.0.0.1:11434
TEYSSIR_VISION_MODEL=qwen2.5vl:3b
TEYSSIR_OCR_VISION_FALLBACK=true
```

Then:

```powershell
ollama pull llama3
ollama pull qwen2.5vl:3b
.\.venv\Scripts\python.exe manage.py check_llm --ping --prompt "Say pong"
```

Other small text models that work offline: `mistral`, `llama3`, `gemma`, `qwen2.5:3b`.

For **camera OCR via LLM as primary** (instead of Tesseract):

```
TEYSSIR_OCR_PROVIDER=vision
TEYSSIR_VISION_MODEL=qwen2.5vl:3b
TEYSSIR_SCAN_EXECUTOR=thread
```

## Install commands

### Windows 11 (Hub)

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
# Full Hub install — Ollama + mistral + qwen2.5vl:3b (auto)
.\deploy\windows\install.ps1 -Role hub

# Vision only (re-run safe):
.\deploy\windows\Install-LocalLlm.ps1 -Model mistral

# Opt out of the ~2 GB vision download:
.\deploy\windows\install.ps1 -Role hub -SkipVision
.\deploy\windows\Install-LocalLlm.ps1 -Model mistral -SkipVision
```

### macOS

```bash
# Full install — brew Ollama if needed, then pull vision model
bash deploy/macos/install.sh --role hub

# Opt out:
bash deploy/macos/install.sh --role hub --skip-vision

# Manual pull / different tag:
ollama pull qwen2.5vl:3b
# TEYSSIR_VISION_MODEL=… or:
bash deploy/macos/install.sh --role hub --vision-model qwen2.5vl:3b
```

Confirm: `ollama list` shows `qwen2.5vl:3b`; `curl -s http://127.0.0.1:11434` → Ollama is running.

## Windows installer flags

```powershell
.\deploy\windows\install.ps1 -Role hub                  # text + vision auto-pull
.\deploy\windows\install.ps1 -Role hub -LlmModel llama3
.\deploy\windows\install.ps1 -Role hub -VisionModel qwen2.5vl:3b
.\deploy\windows\install.ps1 -Role hub -SkipVision      # Ollama text only
.\deploy\windows\install.ps1 -Role hub -SkipLlm         # no Ollama at all
```

## Vision fallback gate (Phase 2E / 15.4 dual-image)

Default OCR stays **Tesseract**. Local Ollama Vision runs as a gated fallback when the
Tess path is weak (Arabic calligraphy, garbage Latin misreads, phone / XTRIKE photos with
no barcode and no usable title). Strong barcode ISBN + usable title paths skip Vision
(metadata enriches).

**Phase 15.4:** one Ollama call sends **front + back** covers together (downscaled to 1280px),
returns structured JSON including required `language_detected` and a 2–4 sentence
`description` (auto-fills the BookCreate draft). Invalid ISBN checksums are dropped;
Vision never invents `barcode_*`.

| Env | Default | Role |
|-----|---------|------|
| `TEYSSIR_OCR_VISION_FALLBACK` | `true` | Enable/disable the gate |
| `TEYSSIR_VISION_FALLBACK_TIMEOUT` | `28` | Soft budget (s) for fallback calls |
| `TEYSSIR_VISION_TIMEOUT` | `45` | Hard cap (s) |
| `TEYSSIR_VISION_IMAGE_MAX_EDGE` | `1280` | Downscale before base64→Ollama |
| `TEYSSIR_VISION_MODEL` | `qwen2.5vl:3b` | CPU-friendly vision model |

Vision **never** invents an ISBN: only checksum-valid bookland 978/979 is kept.

### Win11 / local Ollama tips

1. Keep `TEYSSIR_OCR_PROVIDER=tesseract` for day-to-day speed; installers pull vision for gated fallback.
2. Ensure Ollama runs as the same user as the Teyssir service (or allow `127.0.0.1:11434`).
3. First Vision call loads the model (cold start); use `TEYSSIR_SCAN_EXECUTOR=thread`.
4. If Vision times out, Tess draft is kept — lower `TEYSSIR_VISION_FALLBACK_TIMEOUT` on weak PCs.

### macOS / local Ollama tips

1. `install.sh` tries `brew install ollama` then `ollama pull qwen2.5vl:3b` (or open the Ollama app).
2. Keep `TEYSSIR_OCR_PROVIDER=tesseract` in `.env` (Vision is fallback layer 2).
3. Confirm: `ollama list` + `curl -s http://127.0.0.1:11434`.

### Regression (Phase 2F)

Default regression runs **without** Vision:

```powershell
$env:TEYSSIR_OCR_VISION_FALLBACK = "false"
python manage.py bookscan_regression --json
```

To include Vision on weak Tess paths: `python manage.py bookscan_regression --vision`.

## Troubleshooting

### Model not loading / `ollama pull` hangs

- Need disk space (mistral ≈ 4 GB, `qwen2.5vl:3b` ≈ 2 GB).
- Re-run: `ollama pull mistral` / `ollama pull qwen2.5vl:3b`
- Confirm: `ollama list`

### Port 11434 in use / API not responding

- Browser: `http://127.0.0.1:11434` should return `Ollama is running`.
- Windows: check the Ollama tray icon, or `Get-NetTCPConnection -LocalPort 11434`.
- Start manually: `ollama serve`
- Change URL: `TEYSSIR_OLLAMA_URL=http://127.0.0.1:11434` (or the Hub host if Ollama runs only there).

### ERP works but AI does not

That is expected. Set `USE_LLM=false` or leave Ollama stopped. OCR falls back to Tesseract or
manual entry. `python manage.py check_llm --ping` prints reachability without breaking POS.

### Fresh-PC simulation

On a Windows Hub:

1. `install.ps1 -Role hub` — silent Ollama + text + vision pulls (or reuse existing).
2. `ollama --version` and `ollama list` show `mistral` and `qwen2.5vl:3b`.
3. Desktop **Teyssir ERP** / health — ERP `status=ok` even if LLM is down.
