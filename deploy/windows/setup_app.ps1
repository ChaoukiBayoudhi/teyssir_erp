<#
    Teyssir -- application-layer bootstrap (Phase 3)
    ------------------------------------------------
    Run after host deps (install_all.ps1 / Install-HostDependencies.ps1), or alone
    when Python/Node are already present. Idempotent; never deletes shop data.

        Set-ExecutionPolicy -Scope Process Bypass -Force
        .\deploy\windows\setup_app.ps1 -Role hub
        .\deploy\windows\setup_app.ps1 -Role hub -FreshInstall
        .\deploy\windows\setup_app.ps1 -Role till -Terminal C1 `
            -HubUrl http://teyssir-hub.local:8000 -SyncKey <hub-key>
        .\deploy\windows\setup_app.ps1 -Role till -DiscoverPrinter

    Steps:
      1) Resolve project root (safe if launched from deploy\windows)
      2) Git: pull if .git present; clone only when manage.py is missing
      3) Local LLM if missing (Install-LocalLlm.ps1) unless -SkipLlm
      4) Hand off to install.ps1 (venv, .env, migrate/seed, frontend, service)
      5) Validate: django check, migrate --check, frontend\dist, /health/ if up

    Preferred full entry remains install_all.ps1 (deps + LLM + install.ps1).
    This script is the focused "app layer" entry. Does not install Redis.
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
    # Forwarded to install.ps1. Rebuild is the default; this flag still wins over -SkipBuild.
    [switch]$ForceFrontendBuild,
    [switch]$SkipLlm,
    [string]$LlmModel = "mistral",
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
    # Git
    [string]$RepoUrl = "https://github.com/ChaoukiBayoudhi/teyssir_erp.git",
    [string]$CloneTarget = "",
    [switch]$SkipPull,
    # Validation only after install (skip install.ps1 hand-off)
    [switch]$ValidateOnly,
    # Wipe DB / .env / service then reinstall (forwarded to install.ps1)
    [switch]$FreshInstall,
    [switch]$KeepVenv
)

$ErrorActionPreference = "Stop"

function Write-Setup([string]$Message, [string]$Color = "Gray") {
    Write-Host $Message -ForegroundColor $Color
}

function Invoke-SetupPy {
    # PS 5.1: Python INFO on stderr is NativeCommandError when EAP is Stop.
    # Success = LASTEXITCODE only (same as install.ps1 Invoke-Py).
    param(
        [Parameter(Mandatory = $true)][string]$VenvPy,
        [Parameter(Mandatory = $true)][string[]]$PyArgs
    )
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $output = $null
    $code = 1
    try {
        $output = & $VenvPy @PyArgs 2>&1
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $prevEap
    }
    if ($null -eq $code) { $code = 1 }
    try { $code = [int]$code } catch { $code = 1 }
    $lines = @()
    if ($null -ne $output) {
        $lines = @($output | ForEach-Object { $_.ToString() })
    }
    return @{ Code = $code; Lines = $lines }
}

# See install_all.ps1 -- NativeCommandError.Message is often only the first stderr line.
function Get-TeyssirErrorText {
    param($ErrorRecord)
    if ($null -eq $ErrorRecord) { return "(no error record)" }
    $parts = New-Object System.Collections.Generic.List[string]
    try {
        $asString = ($ErrorRecord | Out-String).Trim()
        if ($asString) { [void]$parts.Add($asString) }
    }
    catch { }
    try {
        if ($ErrorRecord.Exception) {
            $ex = $ErrorRecord.Exception.ToString()
            if ($ex -and ($parts -notcontains $ex)) { [void]$parts.Add($ex) }
        }
    }
    catch { }
    try {
        if ($ErrorRecord.ScriptStackTrace) {
            [void]$parts.Add(("PS ScriptStackTrace:`n{0}" -f $ErrorRecord.ScriptStackTrace))
        }
    }
    catch { }
    try {
        $native = @()
        foreach ($e in @($global:Error | Select-Object -First 50)) {
            $native += ("{0}" -f $e)
        }
        if ($native.Count -gt 0) {
            [void]$parts.Add(("Recent error stream ({0} records):`n{1}" -f $native.Count, ($native -join "`n")))
        }
    }
    catch { }
    $text = ($parts -join "`n---`n").Trim()
    if (-not $text) {
        try { $text = [string]$ErrorRecord.Exception.Message } catch { $text = "$ErrorRecord" }
    }
    if ($text.Length -gt 16000) {
        $text = $text.Substring(0, 16000) + "`n... (truncated at 16000 chars)"
    }
    return $text
}

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-ProjectRoot {
    # Script lives in deploy\windows -- walk up until manage.py, else PSScriptRoot\..\..
    $start = $PSScriptRoot
    $cand = $start
    for ($i = 0; $i -lt 6; $i++) {
        if (Test-Path (Join-Path $cand "manage.py")) {
            return (Resolve-Path $cand).Path
        }
        $parent = Split-Path -Parent $cand
        if (-not $parent -or $parent -eq $cand) { break }
        $cand = $parent
    }
    $fallback = Join-Path $PSScriptRoot "..\.."
    return (Resolve-Path $fallback).Path
}

function Test-OllamaApiQuick {
    param([string]$Url = "http://127.0.0.1:11434", [int]$TimeoutSec = 2)
    try {
        $r = Invoke-WebRequest -Uri ($Url.TrimEnd("/") + "/api/tags") `
            -UseBasicParsing -TimeoutSec $TimeoutSec -ErrorAction Stop
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300)
    }
    catch { return $false }
}

function Test-LlmStackPresent {
    param([string]$Model, [string]$Vision, [switch]$SkipVisionCheck)
    if (-not (Test-OllamaApiQuick)) { return $false }
    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollama) {
        $candidates = @(
            (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"),
            (Join-Path $env:ProgramFiles "Ollama\ollama.exe")
        )
        $found = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $found) { return $false }
        $ollamaExe = $found
    }
    else {
        $ollamaExe = $ollama.Source
    }
    try {
        $list = & $ollamaExe list 2>&1 | Out-String
        if (-not $list) { return $false }
        $esc = [regex]::Escape($Model)
        if (-not ($list -match ("(?im)^\s*" + $esc + "(\s|:|$)"))) { return $false }
        if (-not $SkipVisionCheck -and $Vision) {
            $vesc = [regex]::Escape($Vision)
            if (-not ($list -match ("(?im)^\s*" + $vesc + "(\s|:|$)"))) { return $false }
        }
        return $true
    }
    catch { return $false }
}

function Ensure-GitCheckout {
    param(
        [string]$Root,
        [string]$Url,
        [string]$Target,
        [switch]$NoPull
    )
    $gitDir = Join-Path $Root ".git"
    $manage = Join-Path $Root "manage.py"

    if ((Test-Path $gitDir) -and (Test-Path $manage)) {
        Write-Setup ("Git checkout OK: {0}" -f $Root) "Green"
        if ($NoPull) {
            Write-Setup "Skipping git pull (-SkipPull)."
            return $Root
        }
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            Write-Setup "git not on PATH -- skip pull (app files already present)." "Yellow"
            return $Root
        }
        Write-Setup "Pulling latest (git pull --ff-only) ..." "Cyan"
        Push-Location $Root
        try {
            $branch = (& git rev-parse --abbrev-ref HEAD 2>$null)
            if ($LASTEXITCODE -ne 0) { $branch = "?" }
            & git pull --ff-only 2>&1 | ForEach-Object { Write-Setup ("  {0}" -f $_) }
            if ($LASTEXITCODE -ne 0) {
                Write-Setup ("git pull --ff-only soft-failed (branch {0}). Local changes preserved; continuing." -f $branch) "Yellow"
            }
            else {
                Write-Setup ("git pull OK (branch {0})." -f $branch) "Green"
            }
        }
        catch {
            Write-Setup ("git pull skipped: {0}" -f $_.Exception.Message) "Yellow"
        }
        finally { Pop-Location }
        return $Root
    }

    if ((Test-Path $manage) -and -not (Test-Path $gitDir)) {
        Write-Setup "Project present without .git (ZIP layout) -- skip clone/pull." "Yellow"
        return $Root
    }

    # Missing manage.py: clone into CloneTarget or parent of current Root
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "Project sources missing (no manage.py) and git is unavailable. Clone https://github.com/ChaoukiBayoudhi/teyssir_erp.git or extract the ZIP, then re-run setup_app.ps1 from the project folder."
    }
    $dest = $Target
    if (-not $dest) {
        $parent = Split-Path -Parent $Root
        if (-not $parent) { $parent = $env:USERPROFILE }
        $dest = Join-Path $parent "teyssir_erp"
    }
    if ((Test-Path (Join-Path $dest "manage.py"))) {
        Write-Setup ("Using existing clone at {0}" -f $dest) "Green"
        return (Resolve-Path $dest).Path
    }
    if (Test-Path $dest) {
        throw ("Clone target exists but is not a Teyssir tree: {0}. Pass -CloneTarget or remove it." -f $dest)
    }
    Write-Setup ("Cloning {0} -> {1} ..." -f $Url, $dest) "Cyan"
    & git clone $Url $dest
    if ($LASTEXITCODE -ne 0) { throw "git clone failed." }
    return (Resolve-Path $dest).Path
}

function Invoke-AppValidation {
    param([string]$Root)
    Write-Setup ""
    Write-Setup "==== Application validation ====" "Cyan"
    $ok = $true
    $venvPy = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPy)) {
        Write-Setup "FAIL: .venv\Scripts\python.exe missing." "Red"
        return $false
    }

    Write-Setup "django check ..."
    $check = Invoke-SetupPy -VenvPy $venvPy -PyArgs @("manage.py", "check")
    foreach ($line in $check.Lines) { Write-Setup ("  {0}" -f $line) }
    if ($check.Code -ne 0) {
        Write-Setup ("FAIL: manage.py check (exit {0})" -f $check.Code) "Red"
        $ok = $false
    }
    else {
        Write-Setup "  django check OK" "Green"
    }

    Write-Setup "migrate --check ..."
    $mig = Invoke-SetupPy -VenvPy $venvPy -PyArgs @("manage.py", "migrate", "--check")
    foreach ($line in $mig.Lines) { Write-Setup ("  {0}" -f $line) }
    if ($mig.Code -ne 0) {
        Write-Setup ("WARN: migrate --check exit {0} (pending migrations? re-run setup_app / install.ps1)." -f $mig.Code) "Yellow"
        $ok = $false
    }
    else {
        Write-Setup "  migrations applied" "Green"
    }

    $dist = Join-Path $Root "frontend\dist\index.html"
    if (Test-Path $dist) {
        Write-Setup "frontend\dist present" "Green"
    }
    else {
        Write-Setup "WARN: frontend\dist\index.html missing -- UI will be empty until npm build or SkipBuild copy." "Yellow"
        $ok = $false
    }

    $healthUrl = "http://127.0.0.1:8000/health/"
    $healthOk = $false
    try {
        $r = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300) {
            $healthOk = $true
            Write-Setup ("Health OK: {0}" -f $healthUrl) "Green"
        }
    }
    catch { }

    if (-not $healthOk) {
        $svc = Get-Service -Name "TeyssirBackend" -ErrorAction SilentlyContinue
        if ($svc -and $svc.Status -eq "Running") {
            Write-Setup "TeyssirBackend is Running but /health/ did not answer yet -- wait a few seconds, then open-teyssir.ps1 or http://localhost:8000/health/" "Yellow"
        }
        elseif ($svc) {
            Write-Setup ("TeyssirBackend status={0}. Start-Service TeyssirBackend, then check {1}" -f $svc.Status, $healthUrl) "Yellow"
        }
        else {
            Write-Setup ("Backend not reachable at {0}. After install, use Desktop 'Teyssir ERP', Start-Service TeyssirBackend, or deploy\windows\start-teyssir.bat -- then open-teyssir.ps1 / {0}" -f $healthUrl) "Yellow"
        }
    }

    if ($ok -and $healthOk) {
        Write-Setup "==== Validation passed ====" "Green"
    }
    elseif ($ok) {
        Write-Setup "==== App files OK -- start the service to complete health check ====" "Yellow"
    }
    else {
        Write-Setup "==== Validation finished with warnings/failures (see above) ====" "Yellow"
    }
    return $ok
}

# --- main -------------------------------------------------------------------
$Root = Get-ProjectRoot
Write-Setup "==== Teyssir setup_app (role: $Role) ====" "Green"
Write-Setup ("Resolved root: {0}" -f $Root)
if (-not (Test-IsAdmin)) {
    Write-Setup "Not Administrator -- hub PostgreSQL / firewall / service may soft-skip (same as install.ps1)." "Yellow"
}

$Root = Ensure-GitCheckout -Root $Root -Url $RepoUrl -Target $CloneTarget -NoPull:$SkipPull
Set-Location $Root

# Re-bind script dir helpers to the (possibly new) tree
$WindowsDeploy = Join-Path $Root "deploy\windows"
$installScript = Join-Path $WindowsDeploy "install.ps1"
$llmScript = Join-Path $WindowsDeploy "Install-LocalLlm.ps1"

if (-not (Test-Path $installScript)) {
    throw ("install.ps1 not found at {0}. Incomplete checkout?" -f $installScript)
}

if ($ValidateOnly) {
    $null = Invoke-AppValidation -Root $Root
    exit 0
}

# --- LLM if missing (user: prefer call Install-LocalLlm when stack incomplete) -
if ($SkipLlm) {
    Write-Setup "Local LLM skipped (-SkipLlm)." "Yellow"
}
else {
    $visionSkip = [bool]$SkipVision
    if (Test-LlmStackPresent -Model $LlmModel -Vision $VisionModel -SkipVisionCheck:$visionSkip) {
        Write-Setup ("Local LLM already present (Ollama + {0}{1})." -f $LlmModel, $(
                if ($SkipVision) { "" } else { " + $VisionModel" }
            )) "Green"
    }
    else {
        Write-Setup "Local LLM missing or incomplete -- calling Install-LocalLlm.ps1 ..." "Cyan"
        try {
            if ($SkipVision) {
                & $llmScript -Model $LlmModel -VisionModel $VisionModel -SkipVision
            }
            else {
                & $llmScript -Model $LlmModel -VisionModel $VisionModel
            }
        }
        catch {
            Write-Setup ("Install-LocalLlm soft-fail: {0}. Prefer install_all.ps1 first for host+LLM, or retry later." -f $_.Exception.Message) "Yellow"
        }
    }
}

# --- hand off to install.ps1 (venv, .env, migrate, seed, frontend, service) --
$forward = @{}
foreach ($key in @(
        "Role", "Terminal", "StoreCode", "HubUrl", "SyncKey", "Printer",
        "DiscoverPrinter", "SkipBuild", "ForceFrontendBuild", "SkipLlm", "LlmModel", "SkipVision",
        "VisionModel", "SkipPostgres", "PostgresSuperPassword", "AdminUser",
        "AdminPassword", "SkipAdmin", "RegisterAutostart", "SkipAutostart",
        "SkipFirewall", "SkipService", "SkipShortcut", "FreshInstall", "KeepVenv"
    )) {
    if ($PSBoundParameters.ContainsKey($key)) {
        $forward[$key] = $PSBoundParameters[$key]
    }
}
if (-not $forward.ContainsKey("Role")) { $forward["Role"] = $Role }
if (-not $forward.ContainsKey("Terminal") -and $Terminal) { $forward["Terminal"] = $Terminal }
if (-not $forward.ContainsKey("HubUrl") -and $HubUrl) { $forward["HubUrl"] = $HubUrl }
if (-not $forward.ContainsKey("LlmModel")) { $forward["LlmModel"] = $LlmModel }
if (-not $forward.ContainsKey("VisionModel")) { $forward["VisionModel"] = $VisionModel }

Write-Setup "Handing off to install.ps1 (app: venv, deps, .env, DB, seed, frontend, optional DiscoverPrinter) ..." "Cyan"
$exitCode = 0
try {
    & $installScript @forward
    $exitCode = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
}
catch {
    $detail = Get-TeyssirErrorText $_
    Write-Setup "install.ps1 failed -- full error follows:" "Red"
    foreach ($line in ($detail -split "`r?`n")) {
        Write-Setup $line "Red"
    }
    $exitCode = 1
}

if ($exitCode -eq 0) {
    $null = Invoke-AppValidation -Root $Root
}

Write-Setup ""
Write-Setup ("==== setup_app finished (exit {0}) ====" -f $exitCode) $(if ($exitCode -eq 0) { "Green" } else { "Red" })
Write-Setup "Chain: install_all.ps1 (host deps) -> setup_app.ps1 (this) -> install.ps1. No Redis."
Write-Setup "Printer: -DiscoverPrinter / -Printer tcp:IP:9100 / Discover-Printer.ps1"
exit $exitCode
