<#
    Teyssir — Windows installer
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
    [switch]$SkipBuild
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
function New-Key([int]$n) {
    -join ((48..57) + (65..90) + (97..122) | Get-Random -Count $n | ForEach-Object { [char]$_ })
}
if (-not (Test-Path ".env")) {
    $secret = New-Key 50
    if (-not $SyncKey) { $SyncKey = New-Key 40 }
    $pcName = [System.Net.Dns]::GetHostName()
    if ($Role -eq "hub") {
        @"
TEYSSIR_ROLE=hub
TEYSSIR_STORE_CODE=$StoreCode
TEYSSIR_DB=sqlite
TEYSSIR_SYNC_KEY=$SyncKey
DEBUG=0
SECRET_KEY=$secret
TEYSSIR_ALLOWED_HOSTS=localhost,127.0.0.1,$pcName,teyssir-hub.local
TEYSSIR_CSRF_TRUSTED_ORIGINS=http://$pcName:8000,http://teyssir-hub.local:8000
"@ | Set-Content -Encoding UTF8 ".env"
    }
    else {
        @"
TEYSSIR_ROLE=till
TEYSSIR_TERMINAL=$Terminal
TEYSSIR_STORE_CODE=$StoreCode
TEYSSIR_HUB_URL=$HubUrl
TEYSSIR_SYNC_KEY=$SyncKey
DEBUG=0
SECRET_KEY=$secret
TEYSSIR_ALLOWED_HOSTS=localhost,127.0.0.1
"@ | Set-Content -Encoding UTF8 ".env"
    }
    Write-Host ""
    Write-Host "  .env created." -ForegroundColor Green
    Write-Host "  SHARED SYNC KEY = $SyncKey" -ForegroundColor Yellow
    Write-Host "  ^ Use this SAME key on the hub and on every till." -ForegroundColor Yellow
    Write-Host ""
}
else {
    Write-Host ".env already exists — left unchanged."
}

# 5) Database + static ------------------------------------------------------
Write-Host "Setting up the database ..."
& .\.venv\Scripts\python.exe manage.py migrate --noinput
& .\.venv\Scripts\python.exe manage.py collectstatic --noinput | Out-Null

# 6) First administrator ----------------------------------------------------
Write-Host ""
Write-Host "Create the first administrator account (owner):" -ForegroundColor Green
& .\.venv\Scripts\python.exe manage.py createsuperuser

Write-Host ""
Write-Host "==== Installation complete ====" -ForegroundColor Green
Write-Host "Start Teyssir with:  deploy\windows\start-teyssir.bat"
Write-Host "Then open:           http://localhost:8000"
