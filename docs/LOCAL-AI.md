# Local AI (Ollama) — Teyssir

Teyssir can use a **local large language model** for book OCR (vision) and future AI helpers.
Nothing is sent to the cloud: inference runs on the shop PC via [Ollama](https://ollama.com).

The Windows installer (`deploy/windows/install.ps1`) tries to install Ollama, start it, and
download a default text model. **If that step fails, Teyssir still installs and runs**
(POS, stock, books, PDF→Word). AI is optional.

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
