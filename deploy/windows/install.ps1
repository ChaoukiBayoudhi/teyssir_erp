<#
    Teyssir - Windows installer
    ----------------------------
    Run from an *elevated* PowerShell (Run as Administrator) inside the project folder:

        Set-ExecutionPolicy -Scope Process Bypass -Force
        .\deploy\windows\install.ps1 -Role hub
        .\deploy\windows\install.ps1 -Role till -Terminal C1 -HubUrl http://teyssir-hub.local:8000 -SyncKey <hub-key>

    It creates the Python environment, installs dependencies, builds the app (if Node is
    present and not already built), writes a .env with random secrets, sets up the database,
    and creates the first administrator.
#>
[CmdletBinding()]
param(
    [ValidateSet("hub", "till")] [string]$Role = "till",
    [string]$Terminal = "C1",
    [string]$StoreCode = "",
    [string]$HubUrl = "http://teyssir-hub.local:8000",
    [string]$SyncKey = "",
    [switch]$SkipBuild,
    [switch]$SkipLlm,
    [string]$LlmModel = "mistral",
    # Phase 15.7: vision model (qwen2.5vl:3b) is pulled by default with Ollama.
    [switch]$SkipVision,
    [string]$VisionModel = "qwen2.5vl:3b"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root
Write-Host "==== Teyssir installer  (role: $Role) ====" -ForegroundColor Green
Write-Host "Project: $Root"

# 1) Python -----------------------------------------------------------------
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python not found. Install Python 3.12+ from https://www.python.org/downloads/windows/ and tick 'Add python.exe to PATH', then re-run."
}
Write-Host ("Python: " + ((python --version) 2>&1))

# 2) Virtual environment + dependencies -------------------------------------
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment (.venv) ..."
    python -m venv .venv
}
Write-Host "Installing Python dependencies ..."
& .\.venv\Scripts\python.exe -m pip install --upgrade pip | Out-Null
& .\.venv\Scripts\pip.exe install -r requirements.txt

# 3) Front-end build (only if not already built) ----------------------------
if (-not $SkipBuild -and -not (Test-Path "frontend\dist\index.html")) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Write-Host "Building the web app (npm) ..."
        Push-Location frontend
        npm ci
        npm run build
        Pop-Location
    }
    else {
        Write-Warning "Node.js/npm not found and frontend\dist is missing. Build once on a PC with Node (npm ci; npm run build) and copy the frontend\dist folder here."
    }
}

# 4) .env (created once, with random secrets) -------------------------------
# Pick n random chars WITH replacement (robust for any n; allows repeats for full entropy).
function New-Key([int]$n) {
    $chars = [char[]]((48..57) + (65..90) + (97..122))
    -join (1..$n | ForEach-Object { $chars | Get-Random })
}
if (-not (Test-Path ".env")) {
    $secret = New-Key 50
    if (-not $SyncKey) { $SyncKey = New-Key 40 }
    $pcName = [System.Net.Dns]::GetHostName()
    # Built as an array of lines (no here-strings: PowerShell 5.1 mis-parses here-strings in
    # files with Unix line endings, which is what a GitHub ZIP download contains).
    if ($Role -eq "hub") {
        $envLines = @(
            "TEYSSIR_ROLE=hub",
            "TEYSSIR_STORE_CODE=$StoreCode",
            "TEYSSIR_DB=sqlite",
            "TEYSSIR_SYNC_KEY=$SyncKey",
            "DEBUG=0",
            "SECRET_KEY=$secret",
            ("TEYSSIR_ALLOWED_HOSTS=localhost,127.0.0.1," + $pcName + ",teyssir-hub.local"),
            ("TEYSSIR_CSRF_TRUSTED_ORIGINS=http://" + $pcName + ":8000,http://teyssir-hub.local:8000")
        )
    }
    else {
        $envLines = @(
            "TEYSSIR_ROLE=till",
            "TEYSSIR_TERMINAL=$Terminal",
            "TEYSSIR_STORE_CODE=$StoreCode",
            "TEYSSIR_HUB_URL=$HubUrl",
            "TEYSSIR_SYNC_KEY=$SyncKey",
            "DEBUG=0",
            "SECRET_KEY=$secret",
            "TEYSSIR_ALLOWED_HOSTS=localhost,127.0.0.1"
        )
    }
    # Write WITHOUT a BOM: PowerShell 5.1 'Set-Content -Encoding UTF8' prepends a UTF-8 BOM,
    # which makes python-dotenv read the first key as '<BOM>TEYSSIR_ROLE' -> the role is lost
    # and a hub would silently start as a till. UTF8Encoding($false) = no BOM (all PS versions).
    [System.IO.File]::WriteAllText(
        (Join-Path (Get-Location).Path ".env"),
        (($envLines -join "`n") + "`n"),
        (New-Object System.Text.UTF8Encoding($false)))
    Write-Host ""
    Write-Host "  .env created." -ForegroundColor Green
    Write-Host "  SHARED SYNC KEY = $SyncKey" -ForegroundColor Yellow
    Write-Host "  ^ Use this SAME key on the hub and on every till." -ForegroundColor Yellow
    Write-Host ""
}
else {
    Write-Host ".env already exists - left unchanged."
}

# 5) Database + static ------------------------------------------------------
Write-Host "Setting up the database ..."
& .\.venv\Scripts\python.exe manage.py migrate --noinput
& .\.venv\Scripts\python.exe manage.py collectstatic --noinput | Out-Null

# 6) Local LLM (Ollama) — optional, never fails the ERP install -------------
$envPath = Join-Path $Root ".env"
$global:TeyssirLlmReady = $false
$global:TeyssirVisionModelReady = $false
if (-not $SkipLlm) {
    Write-Host "Setting up local LLM (Ollama + vision for bookscan) ..."
    $llmScript = Join-Path $PSScriptRoot "Install-LocalLlm.ps1"
    try {
        if ($SkipVision) {
            & $llmScript -Model $LlmModel -VisionModel $VisionModel -SkipVision
        }
        else {
            & $llmScript -Model $LlmModel -VisionModel $VisionModel
        }
        $global:TeyssirLlmReady = [bool]$global:TeyssirLlmReady
        $global:TeyssirVisionModelReady = [bool]$global:TeyssirVisionModelReady
    }
    catch {
        Write-Warning ("Local LLM setup skipped: " + $_.Exception.Message)
    }
    # Best-effort .env hints (do not clobber existing secrets)
    function Set-DotEnvValue([string]$Path, [string]$Key, [string]$Value) {
        if (-not (Test-Path $Path)) { return }
        $lines = Get-Content $Path
        $found = $false
        $out = foreach ($line in $lines) {
            if ($line -match ("^" + [regex]::Escape($Key) + "=")) {
                $found = $true
                "$Key=$Value"
            } else { $line }
        }
        if (-not $found) { $out += "$Key=$Value" }
        [System.IO.File]::WriteAllText(
            $Path,
            (($out -join "`n") + "`n"),
            (New-Object System.Text.UTF8Encoding($false)))
    }
    Set-DotEnvValue $envPath "TEYSSIR_VISION_MODEL" $VisionModel
    if (-not $SkipVision) {
        Set-DotEnvValue $envPath "TEYSSIR_OCR_VISION_FALLBACK" "true"
    }
    Set-DotEnvValue $envPath "USE_LLM" "false"
    Set-DotEnvValue $envPath "LLM_PROVIDER" "ollama"
    Set-DotEnvValue $envPath "LLM_MODEL" $LlmModel
    Set-DotEnvValue $envPath "TEYSSIR_OLLAMA_URL" "http://127.0.0.1:11434"
}
else {
    Write-Host "Skipping local LLM (-SkipLlm)."
}

# 7) First administrator ----------------------------------------------------
Write-Host ""
Write-Host "Create the first administrator account (owner):" -ForegroundColor Green
& .\.venv\Scripts\python.exe manage.py createsuperuser

Write-Host ""
Write-Host "==== Installation complete ====" -ForegroundColor Green
Write-Host "Start Teyssir with:  deploy\windows\start-teyssir.bat"
Write-Host "Then open:           http://localhost:8000"
if (-not $SkipLlm) {
    if ($SkipVision) {
        Write-Host ("Vision (bookscan):  skipped (-SkipVision). Pull later: ollama pull " + $VisionModel)
    }
    else {
        Write-Host ("Vision (bookscan):  " + $VisionModel + " — gated fallback. See docs/LOCAL-AI.md")
    }
}
