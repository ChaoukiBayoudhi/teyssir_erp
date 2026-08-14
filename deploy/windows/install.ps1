<#
    Teyssir - Windows installer
    ----------------------------
    Run from an *elevated* PowerShell (Run as Administrator) inside the project folder:

        Set-ExecutionPolicy -Scope Process Bypass -Force
        .\deploy\windows\install.ps1 -Role hub
        .\deploy\windows\install.ps1 -Role till -Terminal C1 -HubUrl http://teyssir-hub.local:8000 -SyncKey <hub-key>

    It creates the Python environment, installs dependencies, builds the app (if Node is
    present and not already built), writes a .env with random secrets, sets up the database,
    and creates the first administrator. Hub installs PostgreSQL when possible (SQLite fallback).
    Local Ollama (optional AI) is installed when possible; a failure there never aborts the ERP install.
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
    [switch]$SkipPostgres,
    [string]$PostgresSuperPassword = ""
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
function Set-DotEnvValue([string]$Path, [string]$Key, [string]$Value) {
    $lines = @()
    if (Test-Path $Path) {
        $lines = [System.IO.File]::ReadAllLines($Path)
    }
    $found = $false
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($line in $lines) {
        if ($line -match ("^\s*" + [regex]::Escape($Key) + "=")) {
            $out.Add("$Key=$Value") | Out-Null
            $found = $true
        }
        else { $out.Add($line) | Out-Null }
    }
    if (-not $found) { $out.Add("$Key=$Value") | Out-Null }
    [System.IO.File]::WriteAllText(
        $Path,
        (($out -join "`n") + "`n"),
        (New-Object System.Text.UTF8Encoding($false)))
}
$pgPass = $null
if (-not (Test-Path ".env")) {
    $secret = New-Key 50
    if (-not $SyncKey) { $SyncKey = New-Key 40 }
    $pcName = [System.Net.Dns]::GetHostName()
    $pgPass = New-Key 28
    # Built as an array of lines (no here-strings: PowerShell 5.1 mis-parses here-strings in
    # files with Unix line endings, which is what a GitHub ZIP download contains).
    if ($Role -eq "hub") {
        $envLines = @(
            "TEYSSIR_ROLE=hub",
            "TEYSSIR_STORE_CODE=$StoreCode",
            "TEYSSIR_DB=postgres",
            "POSTGRES_DB=teyssir",
            "POSTGRES_USER=teyssir",
            "POSTGRES_PASSWORD=$pgPass",
            "POSTGRES_HOST=127.0.0.1",
            "POSTGRES_PORT=5432",
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
            "TEYSSIR_DB=sqlite",
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

# 4b) PostgreSQL (HUB only) — optional, never fails the ERP install ----------
$global:TeyssirPostgresReady = $false
$envPath = Join-Path $Root ".env"
if ($Role -eq "hub" -and -not $SkipPostgres) {
    Write-Host "Setting up PostgreSQL for the hub ..."
    $pgScript = Join-Path $PSScriptRoot "Install-Postgres.ps1"
    $pgPassForDb = $pgPass
    if (-not $pgPassForDb -and (Test-Path $envPath)) {
        $line = (Get-Content $envPath) | Where-Object { $_ -match '^\s*POSTGRES_PASSWORD=' } | Select-Object -First 1
        if ($line) { $pgPassForDb = $line.Split("=", 2)[1] }
    }
    if (-not $pgPassForDb) { $pgPassForDb = New-Key 28 }
    $admin = $PostgresSuperPassword
    if (-not $admin) { $admin = $env:POSTGRES_ADMIN_PASSWORD }
    try {
        & $pgScript -Db teyssir -User teyssir -Password $pgPassForDb -SuperPassword $admin
    }
    catch {
        Write-Warning ("PostgreSQL setup skipped: " + $_.Exception.Message)
    }
    if ($global:TeyssirPostgresReady) {
        Set-DotEnvValue $envPath "TEYSSIR_DB" "postgres"
        Set-DotEnvValue $envPath "POSTGRES_DB" "teyssir"
        Set-DotEnvValue $envPath "POSTGRES_USER" "teyssir"
        Set-DotEnvValue $envPath "POSTGRES_PASSWORD" $pgPassForDb
        Set-DotEnvValue $envPath "POSTGRES_HOST" "127.0.0.1"
        Set-DotEnvValue $envPath "POSTGRES_PORT" "5432"
        Write-Host "  Hub database: PostgreSQL (teyssir)." -ForegroundColor Green
    }
    else {
        Set-DotEnvValue $envPath "TEYSSIR_DB" "sqlite"
        Write-Warning "PostgreSQL not ready — hub will use SQLite (teyssir_hub.sqlite3). See docs/POSTGRESQL-SETUP.md"
    }
}
elseif ($Role -eq "till") {
    Write-Host "Till node: SQLite (offline). PostgreSQL is not installed on tills."
}

# 5) Database + static ------------------------------------------------------
Write-Host "Setting up the database ..."
& .\.venv\Scripts\python.exe manage.py migrate --noinput
& .\.venv\Scripts\python.exe manage.py seed_rbac
& .\.venv\Scripts\python.exe manage.py seed_fiscal
& .\.venv\Scripts\python.exe manage.py collectstatic --noinput | Out-Null

# 6) Local LLM (Ollama) — optional, never fails the ERP install -------------
$global:TeyssirLlmReady = $false
if (-not $SkipLlm) {
    Write-Host "Setting up local LLM (Ollama) ..."
    $llmScript = Join-Path $PSScriptRoot "Install-LocalLlm.ps1"
    try {
        & $llmScript -Model $LlmModel
    }
    catch {
        Write-Warning ("Local LLM setup skipped: " + $_.Exception.Message)
    }
}
else {
    Write-Host "Skipping local LLM (-SkipLlm)."
}

$envPath = Join-Path $Root ".env"
if (Test-Path $envPath) {
    $useLlm = if ($global:TeyssirLlmReady) { "true" } else { "false" }
    Set-DotEnvValue $envPath "USE_LLM" $useLlm
    Set-DotEnvValue $envPath "LLM_PROVIDER" "ollama"
    Set-DotEnvValue $envPath "LLM_MODEL" $LlmModel
    Set-DotEnvValue $envPath "TEYSSIR_OLLAMA_URL" "http://127.0.0.1:11434"
}

# 7) First administrator ----------------------------------------------------
Write-Host ""
Write-Host "Create the first administrator account (owner):" -ForegroundColor Green
& .\.venv\Scripts\python.exe manage.py createsuperuser

Write-Host ""
Write-Host "==== Installation complete ====" -ForegroundColor Green
Write-Host "Start Teyssir with:  deploy\windows\start-teyssir.bat"
Write-Host "Then open:           http://localhost:8000"
if ($global:TeyssirLlmReady) {
    $modelNote = $LlmModel
    if ($global:TeyssirLlmModelReady) { $modelNote = "$LlmModel (downloaded)" }
    Write-Host ("Local AI:            Ollama ready · " + $modelNote)
}
else {
    Write-Host "Local AI:            not active (ERP works without it). See docs/LOCAL-AI.md"
}
if ($Role -eq "hub") {
    if ($global:TeyssirPostgresReady) {
        Write-Host "Hub database:        PostgreSQL  (teyssir @ 127.0.0.1:5432)"
    }
    else {
        Write-Host "Hub database:        SQLite fallback  (see docs/POSTGRESQL-SETUP.md)"
    }
}
else {
    Write-Host "Till database:       SQLite  (offline)"
}
