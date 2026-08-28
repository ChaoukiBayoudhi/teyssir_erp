# Local AI (Ollama) — Teyssir

Teyssir can use a **local large language model** for book OCR (vision) and future AI helpers.
Nothing is sent to the cloud: inference runs on the shop PC via [Ollama](https://ollama.com).

The Windows installer (`deploy/windows/install.ps1`) tries to install Ollama, start it, and
download a default **text** model (`mistral`). It does **not** pull the large vision model
unless you run `Install-LocalLlm.ps1 -PullVision`. **If Ollama setup fails, Teyssir still
installs and runs** (POS, stock, books, PDF→Word). AI is optional.

## Architecture

```
  PWA / POS  ──►  Django (waitress :8000)
                      │
                      ├─ OCR default: Tesseract (offline, no LLM)
                      ├─ OCR optional: VisionLlmOcrProvider ──► Ollama :11434
                      │         TEYSSIR_OCR_PROVIDER=vision
                      │         TEYSSIR_VISION_MODEL=qwen2.5vl:3b
                      └─ Text LLM (future modules / check_llm)
                            USE_LLM=true
                            LLM_PROVIDER=ollama
                            LLM_MODEL=mistral   (or llama3, gemma, …)
```

| Component | Port / command | Role |
|-----------|----------------|------|
| Ollama | `http://127.0.0.1:11434` | Local runtime |
| Text model | `LLM_MODEL` (default `mistral`) | General prompts, `manage.py check_llm` |
| Vision model | `TEYSSIR_VISION_MODEL` | Cover/ISBN structured OCR (optional, larger download) |

## Change the model

In `.env` (project root):

```
USE_LLM=true
LLM_PROVIDER=ollama
LLM_MODEL=llama3
TEYSSIR_OLLAMA_URL=http://127.0.0.1:11434
```

Then:

```powershell
ollama pull llama3
.\.venv\Scripts\python.exe manage.py check_llm --ping --prompt "Say pong"
```

Other small models that work offline: `mistral`, `llama3`, `gemma`, `qwen2.5:3b`.

For **camera OCR via LLM** (instead of Tesseract):

```
TEYSSIR_OCR_PROVIDER=vision
TEYSSIR_VISION_MODEL=qwen2.5vl:3b
TEYSSIR_SCAN_EXECUTOR=thread
```

```powershell
.\deploy\windows\Install-LocalLlm.ps1 -Model mistral -PullVision
```

## Windows installer flags

```powershell
.\deploy\windows\install.ps1 -Role hub                  # default model: mistral
.\deploy\windows\install.ps1 -Role hub -LlmModel llama3
.\deploy\windows\install.ps1 -Role hub -SkipLlm          # no Ollama at all
```


## Vision fallback gate (Phase 2E / 15.4 dual-image)

Default OCR stays **Tesseract**. Local Ollama Vision runs as a gated fallback when the
Tess path is weak (Arabic calligraphy, garbage Latin misreads, phone photos with no barcode
and no usable title). Strong barcode ISBN + usable title paths skip Vision (metadata enriches).

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

1. Keep `TEYSSIR_OCR_PROVIDER=tesseract` for day-to-day speed; pull vision for gated fallback:
   `.\deploy\windows\Install-LocalLlm.ps1 -Model mistral -PullVision`
   (pulls `qwen2.5vl:3b` by default).
2. Ensure Ollama is running as the same user as the Teyssir service (or allow `127.0.0.1:11434`).
3. First Vision call loads the model (~tens of seconds); use `TEYSSIR_SCAN_EXECUTOR=thread`.
4. If Vision times out, Tess draft is kept — set a lower `TEYSSIR_VISION_FALLBACK_TIMEOUT` on weak PCs.

### macOS / local Ollama tips

1. `brew install ollama` then start the app / `ollama serve`.
2. Pull the vision model used by bookscan fallback:
   ```bash
   ollama pull qwen2.5vl:3b
   ```
3. Keep `TEYSSIR_OCR_PROVIDER=tesseract` in `.env` (Vision is fallback layer 2). Optional primary:
   `TEYSSIR_OCR_PROVIDER=vision` + `TEYSSIR_SCAN_EXECUTOR=thread`.
4. Confirm: `ollama list` shows `qwen2.5vl:3b`; `curl -s http://127.0.0.1:11434` → Ollama is running.

### Regression (Phase 2F)

Default regression runs **without** Vision:

```powershell
$env:TEYSSIR_OCR_VISION_FALLBACK = "false"
python manage.py bookscan_regression --json
```

To include Vision on weak Tess paths: `python manage.py bookscan_regression --vision`.

## Troubleshooting

### Model not loading / `ollama pull` hangs

- Need disk space (mistral ≈ 4 GB). Use a smaller tag if needed: `ollama pull qwen2.5:3b`.
- Re-run: `ollama pull mistral`
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

On a Windows Hub without Ollama:

1. `install.ps1 -Role hub` — silent Ollama setup or reuse an existing install.
2. `ollama --version` and `ollama list` show the pulled model.
3. `start-teyssir.bat` then `http://localhost:8000/health/` — ERP `status=ok` even if LLM is down.
