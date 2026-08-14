<#
    Teyssir - Windows installer
    ----------------------------
    Prefer an *elevated* PowerShell (Run as Administrator) inside the project folder
    so PostgreSQL, the firewall rule, and optional autostart can be configured:

        Set-ExecutionPolicy -Scope Process Bypass -Force
        .\deploy\windows\install.ps1 -Role hub
        .\deploy\windows\install.ps1 -Role till -Terminal C1 -HubUrl http://teyssir-hub.local:8000 -SyncKey <hub-key>

    Safe to run twice (idempotent): existing .venv, .env, database, and admin are reused.

    Hub: PostgreSQL when possible (SQLite fallback — never abort).
    Till: SQLite only (PostgreSQL is never installed).
    Local Ollama is optional; a failure there never aborts the ERP install.
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
    [string]$PostgresSuperPassword = "",
    [string]$AdminUser = "",
    [string]$AdminPassword = "",
    [switch]$SkipAdmin,
    [switch]$RegisterAutostart,
    [switch]$SkipFirewall
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root
Write-Host "==== Teyssir installer  (role: $Role) ====" -ForegroundColor Green
Write-Host "Project: $Root"

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Refresh-Path {
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

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

function Get-DotEnvValue([string]$Path, [string]$Key) {
    if (-not (Test-Path $Path)) { return $null }
    $line = [System.IO.File]::ReadAllLines($Path) | Where-Object { $_ -match ("^\s*" + [regex]::Escape($Key) + "=") } | Select-Object -First 1
    if (-not $line) { return $null }
    return $line.Substring($line.IndexOf("=") + 1)
}

function Test-RealPython {
    param([string]$Exe)
    if (-not $Exe) { return $false }
    try {
        $code = & $Exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        return ($LASTEXITCODE -eq 0)
    }
    catch { return $false }
}

function Get-PythonExe {
    Refresh-Path
    foreach ($name in @("python", "python3")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and (Test-RealPython $cmd.Source)) { return $cmd.Source }
    }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        try {
            $candidate = & $py.Source -3 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $candidate -and (Test-RealPython $candidate.Trim())) {
                return $candidate.Trim()
            }
        }
        catch { }
    }
    foreach ($p in @(
            "$env:LocalAppData\Programs\Python\Python312\python.exe",
            "$env:LocalAppData\Programs\Python\Python313\python.exe",
            "$env:ProgramFiles\Python312\python.exe",
            "$env:ProgramFiles\Python313\python.exe"
        )) {
        if ((Test-Path $p) -and (Test-RealPython $p)) { return $p }
    }
    return $null
}

function Install-PythonIfMissing {
    $exe = Get-PythonExe
    if ($exe) {
        Write-Host ("Python: " + ((& $exe --version) 2>&1))
        return $exe
    }
    Write-Warning "Python 3.11+ not found — attempting silent install (winget)."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Python not found and winget is unavailable. Install Python 3.12+ from https://www.python.org/downloads/windows/ (tick 'Add python.exe to PATH'), then re-run."
    }
    try {
        winget install --id Python.Python.3.12 -e --scope machine --accept-package-agreements --accept-source-agreements --disable-interactivity --silent | Out-Host
    }
    catch {
        Write-Warning ("winget Python (machine) skipped: " + $_.Exception.Message)
    }
    Refresh-Path
    $exe = Get-PythonExe
    if ($exe) { Write-Host ("Python: " + ((& $exe --version) 2>&1)); return $exe }
    try {
        winget install --id Python.Python.3.12 -e --scope user --accept-package-agreements --accept-source-agreements --disable-interactivity --silent | Out-Host
    }
    catch {
        Write-Warning ("winget Python (user) skipped: " + $_.Exception.Message)
    }
    Refresh-Path
    $exe = Get-PythonExe
    if ($exe) { Write-Host ("Python: " + ((& $exe --version) 2>&1)); return $exe }
    throw "Python 3.11+ is still missing. Install it from https://www.python.org/downloads/windows/ (tick 'Add python.exe to PATH'), close PowerShell, and re-run this script."
}

function Install-NodeIfNeeded {
    if ($SkipBuild) { return }
    if (Test-Path "frontend\dist\index.html") { return }
    if (Get-Command npm -ErrorAction SilentlyContinue) { return }
    Write-Warning "Node.js/npm not found and frontend\dist is missing — attempting winget OpenJS.NodeJS.LTS."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { return }
    try {
        winget install --id OpenJS.NodeJS.LTS -e --accept-package-agreements --accept-source-agreements --disable-interactivity --silent | Out-Host
        Refresh-Path
    }
    catch {
        Write-Warning ("Node.js install skipped: " + $_.Exception.Message)
    }
}

function Invoke-Py([string[]]$PyArgs) {
    & $script:VenvPython @PyArgs
    return $LASTEXITCODE
}

# --- elevation hint ----------------------------------------------------------
$script:IsAdmin = Test-IsAdmin
if (-not $script:IsAdmin) {
    Write-Warning "Not running as Administrator. Hub PostgreSQL, firewall, and autostart may be skipped. Re-run from an elevated PowerShell if those steps fail."
}

# 1) Python -----------------------------------------------------------------
$script:SystemPython = Install-PythonIfMissing

# 2) Virtual environment + dependencies -------------------------------------
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment (.venv) ..."
    & $script:SystemPython -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "python -m venv failed." }
}
$script:VenvPython = (Resolve-Path ".venv\Scripts\python.exe").Path
Write-Host "Installing Python dependencies ..."
& $script:VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
& $script:VenvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install -r requirements.txt failed. Check the messages above (network / Microsoft C++ Build Tools)." }

# 3) Front-end build (only if not already built) ----------------------------
Install-NodeIfNeeded
if (-not $SkipBuild -and -not (Test-Path "frontend\dist\index.html")) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Write-Host "Building the web app (npm) ..."
        Push-Location frontend
        npm ci
        if ($LASTEXITCODE -ne 0) { Pop-Location; throw "npm ci failed." }
        npm run build
        if ($LASTEXITCODE -ne 0) { Pop-Location; throw "npm run build failed." }
        Pop-Location
    }
    else {
        Write-Warning "Node.js/npm not found and frontend\dist is missing. Build once on a PC with Node (npm ci; npm run build) and copy the frontend\dist folder here. The API will still start; the UI will be missing."
    }
}

# 4) .env (created once, with random secrets) -------------------------------
$envPath = Join-Path $Root ".env"
$pgPass = $null
$syncKeyFromCaller = [bool]$SyncKey
if (-not (Test-Path $envPath)) {
    $secret = New-Key 50
    if (-not $SyncKey) { $SyncKey = New-Key 40 }
    if ($Role -eq "till" -and -not $syncKeyFromCaller) {
        Write-Warning "No -SyncKey given. A random key was generated — this till cannot sync until TEYSSIR_SYNC_KEY matches the Hub. Re-run with -SyncKey <hub-key> (updates .env) or edit .env by hand."
    }
    $pcName = [System.Net.Dns]::GetHostName()
    $pgPass = New-Key 28
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
    # Write WITHOUT a BOM: PowerShell 5.1 'Set-Content -Encoding UTF8' prepends a UTF-8 BOM.
    [System.IO.File]::WriteAllText(
        $envPath,
        (($envLines -join "`n") + "`n"),
        (New-Object System.Text.UTF8Encoding($false)))
    Write-Host ""
    Write-Host "  .env created." -ForegroundColor Green
    Write-Host "  SHARED SYNC KEY = $SyncKey" -ForegroundColor Yellow
    Write-Host "  ^ Use this SAME key on the hub and on every till." -ForegroundColor Yellow
    Write-Host ""
}
else {
    Write-Host ".env already exists - secrets left unchanged (DB/LLM/sync keys may be updated below)."
    $existingRole = Get-DotEnvValue $envPath "TEYSSIR_ROLE"
    if ($existingRole -and ($existingRole.Trim() -ne $Role)) {
        Write-Warning ("This folder's .env has TEYSSIR_ROLE=$existingRole but you passed -Role $Role. The existing .env wins. Use a separate folder per PC, or edit .env by hand.")
    }
}

if ($Role -eq "till") {
    if ($PSBoundParameters.ContainsKey("Terminal")) { Set-DotEnvValue $envPath "TEYSSIR_TERMINAL" $Terminal }
    if ($PSBoundParameters.ContainsKey("HubUrl")) { Set-DotEnvValue $envPath "TEYSSIR_HUB_URL" $HubUrl }
    if ($syncKeyFromCaller) { Set-DotEnvValue $envPath "TEYSSIR_SYNC_KEY" $SyncKey }
}

# 4b) PostgreSQL (HUB only) — optional, never fails the ERP install ----------
$global:TeyssirPostgresReady = $false
if ($Role -eq "hub" -and -not $SkipPostgres) {
    Write-Host "Setting up PostgreSQL for the hub ..."
    $pgScript = Join-Path $PSScriptRoot "Install-Postgres.ps1"
    $pgPassForDb = $pgPass
    if (-not $pgPassForDb) { $pgPassForDb = Get-DotEnvValue $envPath "POSTGRES_PASSWORD" }
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
elseif ($Role -eq "hub" -and $SkipPostgres) {
    Set-DotEnvValue $envPath "TEYSSIR_DB" "sqlite"
    Write-Host "Hub: -SkipPostgres — using SQLite."
}
elseif ($Role -eq "till") {
    Set-DotEnvValue $envPath "TEYSSIR_DB" "sqlite"
    Write-Host "Till node: SQLite (offline). PostgreSQL is not installed on tills."
}

# 4c) Hub firewall (port 8000) — best-effort --------------------------------
if ($Role -eq "hub" -and -not $SkipFirewall) {
    try {
        $existing = Get-NetFirewallRule -DisplayName "Teyssir 8000" -ErrorAction SilentlyContinue
        if (-not $existing) {
            New-NetFirewallRule -DisplayName "Teyssir 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -ErrorAction Stop | Out-Null
            Write-Host "  Firewall: inbound TCP 8000 allowed (Teyssir 8000)." -ForegroundColor Green
        }
        else {
            Write-Host "  Firewall rule 'Teyssir 8000' already present."
        }
    }
    catch {
        Write-Warning ("Could not add firewall rule for port 8000 (run as Administrator). Tills may not reach this hub. " + $_.Exception.Message)
    }
}

# 5) Database + static ------------------------------------------------------
Write-Host "Setting up the database ..."
function Invoke-DjangoSetup {
    $migrate = Invoke-Py @("manage.py", "migrate", "--noinput")
    if ($migrate -ne 0) { return $false }
    $rbac = Invoke-Py @("manage.py", "seed_rbac")
    if ($rbac -ne 0) { return $false }
    $fiscal = Invoke-Py @("manage.py", "seed_fiscal")
    if ($fiscal -ne 0) { return $false }
    Invoke-Py @("manage.py", "collectstatic", "--noinput") | Out-Null
    return $true
}

if (-not (Invoke-DjangoSetup)) {
    if ($Role -eq "hub" -and ((Get-DotEnvValue $envPath "TEYSSIR_DB") -eq "postgres")) {
        Write-Warning "PostgreSQL migrate/seed failed — falling back to SQLite so the shop can still open."
        Set-DotEnvValue $envPath "TEYSSIR_DB" "sqlite"
        $global:TeyssirPostgresReady = $false
        if (-not (Invoke-DjangoSetup)) {
            throw "Database setup failed on SQLite as well. See the messages above."
        }
    }
    else {
        throw "Database setup failed (migrate / seed_rbac / seed_fiscal). See the messages above."
    }
}

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

if (Test-Path $envPath) {
    $useLlm = if ($global:TeyssirLlmReady) { "true" } else { "false" }
    Set-DotEnvValue $envPath "USE_LLM" $useLlm
    Set-DotEnvValue $envPath "LLM_PROVIDER" "ollama"
    Set-DotEnvValue $envPath "LLM_MODEL" $LlmModel
    Set-DotEnvValue $envPath "TEYSSIR_OLLAMA_URL" "http://127.0.0.1:11434"
}

# 7) First administrator (idempotent) ---------------------------------------
$hasAdmin = $false
$probe = @'
import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "teyssir.settings")
django.setup()
from django.contrib.auth import get_user_model
print("1" if get_user_model().objects.filter(is_superuser=True).exists() else "0")
'@
$probeFile = Join-Path $env:TEMP "teyssir_admin_probe.py"
[System.IO.File]::WriteAllText($probeFile, $probe)
$probeOut = & $script:VenvPython $probeFile 2>$null
if ($probeOut -match "1") { $hasAdmin = $true }

if ($SkipAdmin) {
    Write-Host "Skipping administrator creation (-SkipAdmin)."
}
elseif ($hasAdmin) {
    Write-Host "An administrator already exists — skipping createsuperuser (re-run safe)." -ForegroundColor Green
}
else {
    $userName = $AdminUser
    if (-not $userName) { $userName = $env:TEYSSIR_ADMIN_USER }
    $userPass = $AdminPassword
    if (-not $userPass) { $userPass = $env:TEYSSIR_ADMIN_PASSWORD }
    if ($userName -and $userPass) {
        Write-Host "Creating administrator '$userName' (non-interactive) ..."
        $env:DJANGO_SUPERUSER_PASSWORD = $userPass
        & $script:VenvPython manage.py createsuperuser --noinput --username $userName --email "owner@localhost"
        Remove-Item Env:DJANGO_SUPERUSER_PASSWORD -ErrorAction SilentlyContinue
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Non-interactive createsuperuser failed. You can run: .\.venv\Scripts\python.exe manage.py createsuperuser"
        }
    }
    else {
        Write-Host ""
        Write-Host "Create the first administrator account (owner):" -ForegroundColor Green
        Write-Host "  (Tip: set -AdminUser / -AdminPassword or TEYSSIR_ADMIN_USER / TEYSSIR_ADMIN_PASSWORD to skip the prompt.)"
        & $script:VenvPython manage.py createsuperuser
    }
}

if ($RegisterAutostart) {
    $reg = Join-Path $PSScriptRoot "register-autostart.ps1"
    try {
        & $reg -Role $Role
    }
    catch {
        Write-Warning ("Autostart registration skipped: " + $_.Exception.Message)
    }
}

Write-Host ""
Write-Host "==== Installation complete ====" -ForegroundColor Green
Write-Host "Start Teyssir with:  deploy\windows\start-teyssir.bat"
Write-Host "Then open:           http://localhost:8000"
Write-Host "Health check:        http://localhost:8000/health/"
if ($global:TeyssirLlmReady) {
    $modelNote = $LlmModel
    if ($global:TeyssirLlmModelReady) { $modelNote = "$LlmModel (downloaded)" }
    Write-Host ("Local AI:            Ollama ready · " + $modelNote)
    Write-Host "  Vision OCR model is NOT pulled by default (large). See docs/LOCAL-AI.md (-PullVision)."
}
else {
    Write-Host "Local AI:            not active (ERP works without it). See docs/LOCAL-AI.md"
}
if ($Role -eq "hub") {
    if ($global:TeyssirPostgresReady) {
        Write-Host "Hub database:        PostgreSQL  (teyssir @ 127.0.0.1:5432)"
        Write-Host "Backup:              pg_dump -U teyssir teyssir  (see docs/POSTGRESQL-SETUP.md)"
    }
    else {
        Write-Host "Hub database:        SQLite fallback  (teyssir_hub.sqlite3 — see docs/POSTGRESQL-SETUP.md)"
    }
    Write-Host "Next (recommended):  .\deploy\windows\register-autostart.ps1 -Role hub"
}
else {
    Write-Host "Till database:       SQLite  (offline)"
    Write-Host ("Till terminal:       " + $Terminal)
    Write-Host ("Hub URL:             " + (Get-DotEnvValue $envPath "TEYSSIR_HUB_URL"))
    Write-Host "Next (recommended):  .\deploy\windows\register-autostart.ps1 -Role till -SyncMinutes 5"
}
