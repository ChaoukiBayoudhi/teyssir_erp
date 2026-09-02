<#
    Teyssir - Windows installer
    ----------------------------
    Prefer an *elevated* PowerShell (Run as Administrator) inside the project folder
    so PostgreSQL, the firewall rule, and optional autostart can be configured:

        Set-ExecutionPolicy -Scope Process Bypass -Force
        .\deploy\windows\install.ps1 -Role hub
        .\deploy\windows\install.ps1 -Role till -Terminal C1 -HubUrl http://teyssir-hub.local:8000 -SyncKey <hub-key>

    Safe to run twice (idempotent): existing .venv, .env, database, and admin are reused.
    Opt-in wipe then reinstall:

        .\deploy\windows\install.ps1 -Role hub -FreshInstall

    Hub: PostgreSQL when possible (SQLite fallback -- never abort).
    Till: SQLite only (PostgreSQL is never installed).
    Local Ollama is optional; a failure there never aborts the ERP install.
    Registers Windows service TeyssirBackend (NSSM + waitress, auto-start) and a Desktop shortcut.
#>
[CmdletBinding()]
param(
    [ValidateSet("hub", "till")] [string]$Role = "till",
    [string]$Terminal = "C1",
    [string]$StoreCode = "",
    [string]$HubUrl = "http://teyssir-hub.local:8000",
    [string]$SyncKey = "",
    [string]$Printer = "",
    [switch]$DiscoverPrinter,
    [switch]$SkipBuild,
    [switch]$SkipLlm,
    [string]$LlmModel = "mistral",
    # Phase 15.7: vision model (qwen2.5vl:3b) is pulled by default with Ollama.
    [switch]$SkipVision,
    [string]$VisionModel = "qwen2.5vl:3b",
    [switch]$SkipPostgres,
    [string]$PostgresSuperPassword = "",
    [string]$AdminUser = "",
    [string]$AdminPassword = "",
    [switch]$SkipAdmin,
    [switch]$RegisterAutostart,
    [switch]$SkipAutostart,
    [switch]$SkipFirewall,
    [switch]$SkipService,
    [switch]$SkipShortcut,
    # Wipe DB / .env / service via Clean-PreviousInstall.ps1, then continue install
    [switch]$FreshInstall,
    # With -FreshInstall: keep .venv (default on -FreshInstall is to remove it)
    [switch]$KeepVenv
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root
Write-Host "==== Teyssir installer  (role: $Role) ====" -ForegroundColor Green
Write-Host "Project: $Root"

# --- fresh install wipe (opt-in; shared spine for install_all / setup_app / caisse) -
if ($FreshInstall) {
    $cleanScript = Join-Path $PSScriptRoot "Clean-PreviousInstall.ps1"
    if (-not (Test-Path $cleanScript)) {
        Write-Host "ERROR -- Clean-PreviousInstall.ps1 missing; cannot run -FreshInstall." -ForegroundColor Red
        exit 3
    }
    $removeVenv = -not $KeepVenv
    Write-Host "==== FreshInstall: wiping previous Teyssir install (DB, .env, service) ====" -ForegroundColor Yellow
    $cleanArgs = @{
        FreshInstall = $true
        Role         = $Role
        Terminal     = $Terminal
        RemoveVenv   = $removeVenv
    }
    if ($PostgresSuperPassword) {
        $cleanArgs["PostgresSuperPassword"] = $PostgresSuperPassword
    }
    try {
        & $cleanScript @cleanArgs
        $cleanExit = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
        if ($cleanExit -ne 0) {
            Write-Host ("Clean-PreviousInstall failed (exit {0}). Install aborted." -f $cleanExit) -ForegroundColor Red
            exit $cleanExit
        }
    }
    catch {
        $cleanMsg = $_.Exception.Message
        try {
            $more = ($global:Error | Select-Object -First 20 | ForEach-Object { "$_" }) -join "`n"
            if ($more) { $cleanMsg = $cleanMsg + "`n" + $more }
        }
        catch { }
        Write-Host ("Clean-PreviousInstall failed: {0}" -f $cleanMsg) -ForegroundColor Red
        exit 3
    }
    Write-Host "FreshInstall: wipe done -- continuing with normal install." -ForegroundColor Green
}

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
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $lines = @()
    if (Test-Path $Path) {
        # Explicit UTF-8 (no BOM): PS 5.1 / .NET Framework ReadAllLines() can use ANSI.
        $lines = [System.IO.File]::ReadAllLines($Path, $utf8NoBom)
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
        $utf8NoBom)
}

function Get-DotEnvValue([string]$Path, [string]$Key) {
    if (-not (Test-Path $Path)) { return $null }
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $line = [System.IO.File]::ReadAllLines($Path, $utf8NoBom) |
        Where-Object { $_ -match ("^\s*" + [regex]::Escape($Key) + "=") } |
        Select-Object -First 1
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
    Write-Warning "Python 3.11+ not found -- attempting silent install (winget)."
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
    Write-Warning "Node.js/npm not found and frontend\dist is missing -- attempting winget OpenJS.NodeJS.LTS."
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
    # Continue on native stderr so Django/pip noise does not abort under -ErrorAction Stop.
    # Capture stdout/stderr into a variable so they NEVER enter this function's success
    # output stream. Otherwise `$code = Invoke-Py ...` becomes an Object[] of log lines
    # plus the exit int, and `if ($code -ne 0)` is truthy even when Python exited 0
    # (classic PS false failure when logging goes to stderr / migrate writes stdout).
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $output = $null
    try {
        Write-Host ("  > python " + ($PyArgs -join " ")) -ForegroundColor DarkGray
        $output = & $script:VenvPython @PyArgs 2>&1
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $prevEap
    }
    if ($null -eq $code) { $code = 1 }
    $exitCode = 1
    try { $exitCode = [int]$code } catch { $exitCode = 1 }

    $lines = @()
    if ($null -ne $output) {
        $lines = @($output | ForEach-Object { "$_" })
    }
    foreach ($line in $lines) {
        Write-Host $line
    }
    $script:LastPyFull = ($lines -join "`n")
    if ($lines.Count -gt 8) {
        $script:LastPyTail = @($lines[-8..-1])
    }
    else {
        $script:LastPyTail = @($lines)
    }

    if ($exitCode -ne 0) {
        Write-Host ("  ! python exited $exitCode -- " + ($PyArgs -join " ")) -ForegroundColor Red
    }
    # Emit ONLY the numeric exit code on the success stream.
    return $exitCode
}

function Invoke-NativePy {
    # Run any python.exe with Continue + 2>&1 capture (venv create, pip, etc.).
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [Parameter(Mandatory = $true)][string[]]$PyArgs,
        [string]$Label = "python"
    )
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $output = $null
    $code = 1
    try {
        Write-Host ("  > {0} {1}" -f $Label, ($PyArgs -join " ")) -ForegroundColor DarkGray
        $output = & $Exe @PyArgs 2>&1
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $prevEap
    }
    if ($null -eq $code) { $code = 1 }
    $exitCode = 1
    try { $exitCode = [int]$code } catch { $exitCode = 1 }
    $lines = @()
    if ($null -ne $output) {
        $lines = @($output | ForEach-Object { "$_" })
    }
    foreach ($line in $lines) {
        Write-Host $line
    }
    $script:LastPyFull = ($lines -join "`n")
    if ($lines.Count -gt 8) {
        $script:LastPyTail = @($lines[-8..-1])
    }
    else {
        $script:LastPyTail = @($lines)
    }
    if ($exitCode -ne 0) {
        Write-Host ("  ! {0} exited {1} -- {2}" -f $Label, $exitCode, ($PyArgs -join " ")) -ForegroundColor Red
    }
    return $exitCode
}

function Set-DbBackend([string]$Backend) {
    Set-DotEnvValue $envPath "TEYSSIR_DB" $Backend
    # Process env wins over dotenv (load_dotenv does not override). Keep them aligned.
    $env:TEYSSIR_DB = $Backend
}

function Remove-HubSqliteFiles {
    foreach ($sqliteName in @("teyssir_hub.sqlite3", "db.sqlite3")) {
        $sqlitePath = Join-Path $Root $sqliteName
        foreach ($p in @($sqlitePath, ($sqlitePath + "-wal"), ($sqlitePath + "-shm"))) {
            if (Test-Path -LiteralPath $p) {
                try {
                    Remove-Item -LiteralPath $p -Force -ErrorAction Stop
                    Write-Host ("  Removed stale SQLite file: " + $p) -ForegroundColor Yellow
                }
                catch {
                    Write-Warning ("Could not remove $p : " + $_.Exception.Message)
                }
            }
        }
    }
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
    $venvCode = Invoke-NativePy -Exe $script:SystemPython -PyArgs @("-m", "venv", ".venv") -Label "python"
    if ($venvCode -ne 0) {
        $hint = ""
        if ($script:LastPyFull) { $hint = "`n" + $script:LastPyFull }
        throw ("python -m venv failed." + $hint)
    }
}
$script:VenvPython = (Resolve-Path ".venv\Scripts\python.exe").Path
Write-Host "Installing Python dependencies ..."
$pipUp = Invoke-NativePy -Exe $script:VenvPython -PyArgs @("-m", "pip", "install", "--upgrade", "pip") -Label "pip"
if ($pipUp -ne 0) {
    $hint = ""
    if ($script:LastPyFull) { $hint = "`n" + $script:LastPyFull }
    throw ("pip upgrade failed." + $hint)
}
$pipReq = Invoke-NativePy -Exe $script:VenvPython -PyArgs @("-m", "pip", "install", "-r", "requirements.txt") -Label "pip"
if ($pipReq -ne 0) {
    $hint = ""
    if ($script:LastPyFull) { $hint = "`n" + $script:LastPyFull }
    throw ("pip install -r requirements.txt failed. Check the messages above (network / Microsoft C++ Build Tools)." + $hint)
}

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
        Write-Warning "No -SyncKey given. A random key was generated -- this till cannot sync until TEYSSIR_SYNC_KEY matches the Hub. Re-run with -SyncKey <hub-key> (updates .env) or edit .env by hand."
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

# 4a) Receipt printer (client LAN -- never assume a fixed shop IP) ------------
$resolvedPrinter = $Printer
if ($DiscoverPrinter -and -not $resolvedPrinter) {
    Write-Host "Scanning local /24 for ESC/POS on TCP 9100 ..."
    try {
        $discOut = & $script:VenvPython (Join-Path $Root "deploy\discover_printer.py") 2>&1
        $line = ($discOut | Where-Object { $_ -match '^(tcp:|dummy$)' } | Select-Object -Last 1)
        if ($line) { $resolvedPrinter = [string]$line.ToString().Trim() }
    }
    catch {
        Write-Warning ("Printer discover skipped: " + $_.Exception.Message)
    }
    if (-not $resolvedPrinter) { $resolvedPrinter = "dummy" }
    if ($resolvedPrinter -eq "dummy") {
        Write-Warning "No printer found -- TEYSSIR_PRINTER=dummy (set -Printer tcp:IP:9100 later)."
    }
    else {
        Write-Host ("Discovered printer: " + $resolvedPrinter) -ForegroundColor Green
    }
}
if ($resolvedPrinter) {
    Set-DotEnvValue $envPath "TEYSSIR_PRINTER" $resolvedPrinter
    Write-Host ("TEYSSIR_PRINTER=" + $resolvedPrinter + " written to .env")
}
elseif (Test-Path $envPath) {
    $existingPrinter = Get-DotEnvValue $envPath "TEYSSIR_PRINTER"
    if ($existingPrinter) {
        $resolvedPrinter = $existingPrinter.Trim()
        Write-Host ("Using existing TEYSSIR_PRINTER=" + $resolvedPrinter + " from .env")
    }
    else {
        Write-Host "Receipt printer: not set (dummy until you pass -Printer tcp:IP:9100 or -DiscoverPrinter)."
    }
}

# 4b) PostgreSQL (HUB only) -- optional, never fails the ERP install ----------
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
        & $pgScript -DatabaseName teyssir -User teyssir -Password $pgPassForDb -SuperPassword $admin
    }
    catch {
        Write-Warning ("PostgreSQL setup skipped: " + $_.Exception.Message)
    }
    if ($global:TeyssirPostgresReady) {
        Set-DbBackend "postgres"
        Set-DotEnvValue $envPath "POSTGRES_DB" "teyssir"
        Set-DotEnvValue $envPath "POSTGRES_USER" "teyssir"
        Set-DotEnvValue $envPath "POSTGRES_PASSWORD" $pgPassForDb
        Set-DotEnvValue $envPath "POSTGRES_HOST" "127.0.0.1"
        Set-DotEnvValue $envPath "POSTGRES_PORT" "5432"
        Write-Host "  Hub database: PostgreSQL (teyssir)." -ForegroundColor Green
    }
    else {
        Set-DbBackend "sqlite"
        Write-Warning "PostgreSQL not ready -- hub will use SQLite (teyssir_hub.sqlite3). See docs/POSTGRESQL-SETUP.md"
    }
}
elseif ($Role -eq "hub" -and $SkipPostgres) {
    Set-DbBackend "sqlite"
    Write-Host "Hub: -SkipPostgres -- using SQLite."
}
elseif ($Role -eq "till") {
    Set-DbBackend "sqlite"
    Write-Host "Till node: SQLite (offline). PostgreSQL is not installed on tills."
}

# 4c) Hub firewall (port 8000) -- best-effort --------------------------------
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
    $steps = @(
        @{ Name = "migrate --noinput"; Args = @("manage.py", "migrate", "--noinput") },
        @{ Name = "seed_rbac"; Args = @("manage.py", "seed_rbac") },
        @{ Name = "seed_fiscal"; Args = @("manage.py", "seed_fiscal") }
    )
    foreach ($step in $steps) {
        $raw = Invoke-Py -PyArgs ([string[]]$step.Args)
        # Honor numeric exit only (LASTEXITCODE). Never treat $? or captured log text as failure.
        if ($raw -is [System.Array]) {
            $code = [int](@($raw)[-1])
        }
        else {
            try { $code = [int]$raw } catch { $code = 1 }
        }
        if ($code -ne 0) {
            $tailBits = @()
            if ($script:LastPyTail -and $script:LastPyTail.Count -gt 0) {
                $tailBits = @($script:LastPyTail | Select-Object -Last 5)
            }
            $tailMsg = ""
            if ($tailBits.Count -gt 0) {
                $tailMsg = " Last lines: " + ($tailBits -join " | ")
            }
            Write-Warning ("Database step failed: manage.py " + $step.Name + " (exit " + $code + ")." + $tailMsg)
            return $false
        }
    }
    $csRaw = Invoke-Py -PyArgs @("manage.py", "collectstatic", "--noinput")
    if ($csRaw -is [System.Array]) { $csCode = [int](@($csRaw)[-1]) }
    else { try { $csCode = [int]$csRaw } catch { $csCode = 1 } }
    if ($csCode -ne 0) {
        Write-Warning ("collectstatic exited {0} (non-fatal). Last lines: {1}" -f $csCode, (($script:LastPyTail | Select-Object -Last 5) -join " | "))
    }
    # Soft django check -- surfaces settings/DB issues without aborting install.
    $chkRaw = Invoke-Py -PyArgs @("manage.py", "check")
    if ($chkRaw -is [System.Array]) { $chkCode = [int](@($chkRaw)[-1]) }
    else { try { $chkCode = [int]$chkRaw } catch { $chkCode = 1 } }
    if ($chkCode -ne 0) {
        Write-Warning ("manage.py check exited {0} (non-fatal). Last lines: {1}" -f $chkCode, (($script:LastPyTail | Select-Object -Last 5) -join " | "))
    }
    return $true
}

$dbBackendNow = (Get-DotEnvValue $envPath "TEYSSIR_DB")
if (-not $dbBackendNow) { $dbBackendNow = $env:TEYSSIR_DB }
if (-not (Invoke-DjangoSetup)) {
    $pyDump = ""
    if ($script:LastPyFull) {
        $pyDump = "`n---- python output ----`n" + $script:LastPyFull + "`n---- end ----"
    }
    $hint = "Re-run the failing step manually for full traceback: .\.venv\Scripts\python.exe manage.py migrate --noinput"
    if ($Role -eq "hub" -and ($dbBackendNow -match "^(postgres|postgresql|pg)$")) {
        Write-Warning "PostgreSQL migrate/seed failed -- falling back to SQLite so the shop can still open."
        Set-DbBackend "sqlite"
        $global:TeyssirPostgresReady = $false
        Remove-HubSqliteFiles
        if (-not (Invoke-DjangoSetup)) {
            if ($script:LastPyFull) {
                $pyDump = "`n---- python output ----`n" + $script:LastPyFull + "`n---- end ----"
            }
            throw ("Database setup failed on SQLite as well. " + $hint + $pyDump)
        }
    }
    elseif ($Role -eq "hub") {
        # Already on SQLite (Postgres soft-fail earlier) -- wipe stale DB and retry once.
        Write-Warning "SQLite migrate/seed failed -- removing hub SQLite files and retrying once."
        Remove-HubSqliteFiles
        Set-DbBackend "sqlite"
        if (-not (Invoke-DjangoSetup)) {
            if ($script:LastPyFull) {
                $pyDump = "`n---- python output ----`n" + $script:LastPyFull + "`n---- end ----"
            }
            throw ("Database setup failed (migrate / seed_rbac / seed_fiscal). " + $hint + $pyDump)
        }
    }
    else {
        throw ("Database setup failed (migrate / seed_rbac / seed_fiscal). " + $hint + $pyDump)
    }
}

# 5b) Tesseract OCR -- optional, never fails the ERP install -----------------
$global:TeyssirTesseractReady = $false
$tessCandidates = @(
    "C:\Program Files\Tesseract-OCR\tesseract.exe",
    "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"
)
$tessCmd = $tessCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $tessCmd) {
    Write-Host "Installing Tesseract OCR (eng/fra/ara) if possible ..."
    try {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            winget install --id UB-Mannheim.TesseractOCR -e --accept-package-agreements --accept-source-agreements --disable-interactivity --silent 2>&1 | Out-Host
        }
        elseif (Get-Command choco -ErrorAction SilentlyContinue) {
            choco install tesseract -y --no-progress 2>&1 | Out-Host
        }
        else {
            Write-Warning "winget/choco unavailable -- install Tesseract manually (UB Mannheim) with eng+fra+ara."
        }
    }
    catch {
        Write-Warning ("Tesseract install skipped: " + $_.Exception.Message)
    }
    $tessCmd = $tessCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
$global:TeyssirTesseractLangsOk = $false
if ($tessCmd) {
    $global:TeyssirTesseractReady = $true
    Write-Host ("Tesseract found: " + $tessCmd) -ForegroundColor Green
    # Post-verify eng+fra+ara (soft-fail if packs missing)
    try {
        $langRaw = & $tessCmd --list-langs 2>&1 | Out-String
        $have = @()
        foreach ($line in ($langRaw -split "`r?`n")) {
            $t = $line.Trim().ToLowerInvariant()
            if ($t -and $t -notmatch "list of available|available languages") { $have += $t }
        }
        $need = @("eng", "fra", "ara")
        $missingLangs = @($need | Where-Object { $have -notcontains $_ })
        if ($missingLangs.Count -eq 0) {
            $global:TeyssirTesseractLangsOk = $true
            Write-Host "  Tesseract languages eng+fra+ara present." -ForegroundColor Green
        }
        else {
            Write-Warning ("Tesseract missing language packs: {0}. Re-run UB Mannheim installer and tick eng, fra, ara. Soft-fail -- OCR continues with installed packs." -f ($missingLangs -join ","))
        }
    }
    catch {
        Write-Warning ("Could not list Tesseract languages: " + $_.Exception.Message)
    }
}
else {
    Write-Warning "Tesseract not found -- book OCR will use manual/vision fallback. See docs/INSTALL-WINDOWS.md (OCR Troubleshooting)."
}
if (Test-Path $envPath) {
    Set-DotEnvValue $envPath "TEYSSIR_OCR_PROVIDER" "tesseract"
    Set-DotEnvValue $envPath "TEYSSIR_SCAN_EXECUTOR" "thread"
    if ($tessCmd) {
        Set-DotEnvValue $envPath "TEYSSIR_TESSERACT_CMD" $tessCmd
        Set-DotEnvValue $envPath "TESSERACT_CMD" $tessCmd
    }
}

# 6) Local LLM (Ollama) -- optional, never fails the ERP install -------------
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
    # Keep day-to-day OCR on Tesseract; Vision is gated fallback (needs model on disk).
    Set-DotEnvValue $envPath "TEYSSIR_VISION_MODEL" $VisionModel
    if (-not $SkipVision) {
        Set-DotEnvValue $envPath "TEYSSIR_OCR_VISION_FALLBACK" "true"
    }
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
# UTF-8 no BOM -- avoids Python syntax issues on some Windows code pages.
[System.IO.File]::WriteAllText($probeFile, $probe, (New-Object System.Text.UTF8Encoding($false)))
$probeCode = Invoke-Py -PyArgs @($probeFile)
$probeOut = ""
if ($script:LastPyFull) {
    $probeOut = ($script:LastPyFull -split "`r?`n" | Where-Object { $_ -match '^[01]$' } | Select-Object -Last 1)
}
if ($probeCode -ne 0) {
    Write-Warning ("Admin probe failed (exit {0}) -- will try createsuperuser. Output: {1}" -f $probeCode, (($script:LastPyTail | Select-Object -Last 5) -join " | "))
}
elseif ($probeOut -match "1") {
    $hasAdmin = $true
}

if ($SkipAdmin) {
    Write-Host "Skipping administrator creation (-SkipAdmin)."
}
elseif ($hasAdmin) {
    Write-Host "An administrator already exists -- skipping createsuperuser (re-run safe)." -ForegroundColor Green
}
else {
    $userName = $AdminUser
    if (-not $userName) { $userName = $env:TEYSSIR_ADMIN_USER }
    $userPass = $AdminPassword
    if (-not $userPass) { $userPass = $env:TEYSSIR_ADMIN_PASSWORD }
    if ($userName -and $userPass) {
        Write-Host "Creating administrator '$userName' (non-interactive) ..."
        $env:DJANGO_SUPERUSER_PASSWORD = $userPass
        $suCode = Invoke-Py -PyArgs @("manage.py", "createsuperuser", "--noinput", "--username", $userName, "--email", "owner@localhost")
        Remove-Item Env:DJANGO_SUPERUSER_PASSWORD -ErrorAction SilentlyContinue
        if ($suCode -ne 0) {
            Write-Warning ("Non-interactive createsuperuser failed (exit {0}). You can run: .\.venv\Scripts\python.exe manage.py createsuperuser" -f $suCode)
            if ($script:LastPyFull) {
                Write-Warning ("createsuperuser output:`n" + $script:LastPyFull)
            }
        }
    }
    else {
        Write-Host ""
        Write-Host "Create the first administrator account (owner):" -ForegroundColor Green
        Write-Host "  (Tip: set -AdminUser / -AdminPassword or TEYSSIR_ADMIN_USER / TEYSSIR_ADMIN_PASSWORD to skip the prompt.)"
        # Interactive: do not redirect streams (prompts need a real console).
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $script:VenvPython manage.py createsuperuser
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Interactive createsuperuser did not complete. You can run: .\.venv\Scripts\python.exe manage.py createsuperuser"
            }
        }
        catch {
            Write-Warning ("createsuperuser skipped: " + $_.Exception.Message)
        }
        finally {
            $ErrorActionPreference = $prevEap
        }
    }
}

# Scheduled tasks (till sync + logon fallback if service missing). Reversible via uninstall.ps1.
# Hub boot uses NSSM TeyssirBackend (below) -- do not also force a logon server task.
if ($SkipAutostart) {
    Write-Host "Skipping scheduled-task autostart (-SkipAutostart). Service still installs unless -SkipService."
}
elseif ($RegisterAutostart -or $Role -eq "till") {
    $reg = Join-Path $PSScriptRoot "register-autostart.ps1"
    try {
        & $reg -Role $Role
    }
    catch {
        Write-Warning ("Autostart registration skipped: " + $_.Exception.Message)
    }
}

$global:TeyssirServiceReady = $false
if (-not $SkipService) {
    Write-Host "Registering Windows service TeyssirBackend ..."
    $svcScript = Join-Path $PSScriptRoot "Install-WindowsService.ps1"
    try {
        if ($resolvedPrinter) {
            & $svcScript -Printer $resolvedPrinter
        }
        else {
            & $svcScript
        }
    }
    catch {
        Write-Warning ("Windows service skipped: " + $_.Exception.Message)
    }
}
else {
    Write-Host "Skipping Windows service (-SkipService)."
}

$global:TeyssirShortcutReady = $false
if (-not $SkipShortcut) {
    Write-Host "Creating desktop shortcut ..."
    $scScript = Join-Path $PSScriptRoot "Install-DesktopShortcut.ps1"
    try {
        & $scScript
    }
    catch {
        Write-Warning ("Desktop shortcut skipped: " + $_.Exception.Message)
    }
}
else {
    Write-Host "Skipping desktop shortcut (-SkipShortcut)."
}

Write-Host ""
Write-Host "==== Installation complete ====" -ForegroundColor Green
if ($global:TeyssirServiceReady) {
    Write-Host "Backend:              Windows service TeyssirBackend (automatic at boot, no terminal)"
    Write-Host "Open Teyssir:         double-click the Desktop icon  'Teyssir ERP'"
}
else {
    Write-Host "Start Teyssir with:  deploy\windows\start-teyssir.bat"
    Write-Host "Then open:           http://localhost:8000"
}
Write-Host "Health check:        http://localhost:8000/health/"
if ($resolvedPrinter) {
    Write-Host ("Printer:             TEYSSIR_PRINTER=" + $resolvedPrinter + "  (Menu -> Diagnostics to verify)")
}
if ($global:TeyssirTesseractReady) {
    if ($global:TeyssirTesseractLangsOk) {
        Write-Host "OCR (Tesseract):     ready (eng+fra+ara) -- see Menu -> Diagnostics"
    }
    else {
        Write-Host "OCR (Tesseract):     binary OK -- verify eng+fra+ara packs (Menu -> Diagnostics / docs/INSTALL-WINDOWS.md)"
    }
}
else {
    Write-Host "OCR (Tesseract):     not found (manual entry still works). docs/INSTALL-WINDOWS.md"
}
if ($global:TeyssirLlmReady) {
    $modelNote = $LlmModel
    if ($global:TeyssirLlmModelReady) { $modelNote = "$LlmModel (downloaded)" }
    Write-Host ("Local AI:            Ollama ready - " + $modelNote)
    if ($global:TeyssirVisionModelReady) {
        Write-Host ("  Vision (bookscan):  " + $VisionModel + " ready -- gated fallback. See docs/LOCAL-AI.md")
    }
    elseif ($SkipVision) {
        Write-Host ("  Vision (bookscan):  skipped (-SkipVision). Pull later: ollama pull " + $VisionModel)
    }
    else {
        Write-Host ("  Vision (bookscan):  not on disk yet -- retry Install-LocalLlm.ps1 or ollama pull " + $VisionModel)
    }
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
        Write-Host "Hub database:        SQLite fallback  (teyssir_hub.sqlite3 -- see docs/POSTGRESQL-SETUP.md)"
    }
    Write-Host "Next:                Desktop icon 'Teyssir ERP'  -  uninstall: deploy\windows\uninstall.ps1"
}
else {
    Write-Host "Till database:       SQLite  (offline)"
    Write-Host ("Till terminal:       " + $Terminal)
    Write-Host ("Hub URL:             " + (Get-DotEnvValue $envPath "TEYSSIR_HUB_URL"))
    Write-Host "Next:                Desktop icon 'Teyssir ERP'  -  uninstall: deploy\windows\uninstall.ps1"
}
