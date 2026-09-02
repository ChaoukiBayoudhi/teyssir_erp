<#
    Teyssir -- preferred Windows entry (Phase 2)
    --------------------------------------------
    Logging, careful auto-elevate (hub), host deps via winget, then install.ps1.

        Set-ExecutionPolicy -Scope Process Bypass -Force
        .\deploy\windows\install_all.ps1 -Role hub
        .\deploy\windows\install_all.ps1 -Role till -Terminal C1 `
            -HubUrl http://teyssir-hub.local:8000 -SyncKey <hub-key>
        .\deploy\windows\install_all.ps1 -Role hub -FreshInstall
            # Forwards -FreshInstall to install.ps1 -> Clean-PreviousInstall.ps1
        .\deploy\windows\install_all.ps1 -Role till -DiscoverPrinter

    Till without admin: continues (soft path). Hub without admin: UAC re-launch
    unless -NoElevate. Does not install Redis. Does not hardcode a shop printer IP --
    use -DiscoverPrinter / -Printer tcp:IP:9100 / Discover-Printer.ps1.
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
    # Phase 2 host-dep controls
    [switch]$SkipGit,
    [switch]$SkipNode,
    [switch]$SkipTesseract,
    [switch]$DepsOnly,
    # Do not UAC-elevate (till soft path / already elevated / CI)
    [switch]$NoElevate,
    # Force elevate even for till (rare: machine-scope winget)
    [switch]$ForceElevate,
    # Drop previous install (DB, .env, service) then reinstall -- destructive; see Clean-PreviousInstall.ps1
    [switch]$FreshInstall,
    # With -FreshInstall: also remove .venv for a clean pip install (default on -FreshInstall)
    [switch]$KeepVenv
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-ForwardedArgList {
    $parts = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ("`"{0}`"" -f $PSCommandPath)
    )
    foreach ($key in $PSBoundParameters.Keys) {
        if ($key -eq "NoElevate") { continue }
        $val = $PSBoundParameters[$key]
        if ($val -is [switch]) {
            if ($val) { $parts += ("-{0}" -f $key) }
        }
        elseif ($null -ne $val -and "$val" -ne "") {
            $escaped = ("{0}" -f $val) -replace '"', '\"'
            $parts += ("-{0}" -f $key)
            $parts += ("`"{0}`"" -f $escaped)
        }
    }
    $parts += "-NoElevate"
    return ($parts -join " ")
}

function Initialize-InstallLog {
    $logDir = Join-Path $env:LOCALAPPDATA "Teyssir\logs"
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $logPath = Join-Path $logDir ("install_all_{0}.log" -f $stamp)
    $global:TeyssirInstallLogPath = $logPath
    return $logPath
}

function Write-InstallLog([string]$Message, [string]$Color = "Gray") {
    Write-Host $Message -ForegroundColor $Color
    if ($global:TeyssirInstallLogPath) {
        try {
            Add-Content -Path $global:TeyssirInstallLogPath `
                -Value ("[{0}] {1}" -f (Get-Date -Format "o"), $Message) `
                -ErrorAction SilentlyContinue
        }
        catch { }
    }
}

# NativeCommandError.Message is often only the FIRST stderr line (e.g. Python
# "Traceback (most recent call last):"). Dump Out-String + recent $Error so the
# full traceback reaches the log and console.
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
        # Python tracebacks often arrive as one ErrorRecord per stderr line; the
        # terminating record is only line 1. Reconstruct from the error stream.
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

# --- logging ----------------------------------------------------------------
$logPath = Initialize-InstallLog
$transcriptStarted = $false
try {
    Start-Transcript -Path $logPath -Append -ErrorAction Stop | Out-Null
    $transcriptStarted = $true
}
catch {
    Write-Host ("(Transcript unavailable -- file log only: {0})" -f $logPath) -ForegroundColor DarkGray
}

Write-InstallLog "==== Teyssir install_all (role: $Role) ====" "Green"
Write-InstallLog ("Project: {0}" -f $Root)
Write-InstallLog ("Log:     {0}" -f $logPath)

# --- admin / elevate --------------------------------------------------------
$isAdmin = Test-IsAdmin
$wantElevate = $false
if (-not $isAdmin -and -not $NoElevate) {
    if ($ForceElevate -or ($Role -eq "hub")) {
        $wantElevate = $true
    }
}

if ($wantElevate) {
    Write-InstallLog "Not elevated -- re-launching as Administrator (hub / -ForceElevate). Accept the UAC prompt." "Yellow"
    $argLine = Get-ForwardedArgList
    try {
        $p = Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $argLine -Wait -PassThru
        if ($transcriptStarted) { Stop-Transcript | Out-Null }
        exit $p.ExitCode
    }
    catch {
        Write-InstallLog ("Auto-elevate failed: {0}. Continuing without admin (PostgreSQL/firewall/service may soft-fail)." -f $_.Exception.Message) "Yellow"
    }
}
elseif (-not $isAdmin) {
    Write-InstallLog "Not running as Administrator (till soft path). PostgreSQL/firewall/service steps may be skipped -- OK for most tills." "Yellow"
}
else {
    Write-InstallLog "Running as Administrator." "Green"
}

# FreshInstall / KeepVenv are forwarded to install.ps1 (shared wipe spine).

# --- host dependencies (idempotent) ----------------------------------------
$depScript = Join-Path $PSScriptRoot "Install-HostDependencies.ps1"
$depArgs = @{}
if ($SkipGit) { $depArgs["SkipGit"] = $true }
if ($SkipNode -or $SkipBuild) { $depArgs["SkipNode"] = $true }
if ($SkipTesseract) { $depArgs["SkipTesseract"] = $true }
Write-InstallLog "Installing / verifying host dependencies ..." "Cyan"
try {
    & $depScript @depArgs
}
catch {
    Write-InstallLog ("Host dependencies warning: {0}" -f $_.Exception.Message) "Yellow"
}

# --- local LLM (Ollama + mistral + qwen2.5vl:3b) -- default on, soft-fail ----
# Explicit Phase 2 check: detect -> winget install -> pull models if missing.
# Reuses Install-LocalLlm.ps1; install.ps1 will re-run safely (idempotent).
if ($SkipLlm) {
    Write-InstallLog "Local LLM skipped (-SkipLlm). Shop runs without Ollama/Vision." "Yellow"
}
else {
    $llmScript = Join-Path $PSScriptRoot "Install-LocalLlm.ps1"
    Write-InstallLog ("Local LLM: ensure Ollama + text '{0}'{1} ..." -f $LlmModel, $(
            if ($SkipVision) { " (vision skipped)" } else { " + vision '$VisionModel'" }
        )) "Cyan"
    try {
        if ($SkipVision) {
            & $llmScript -Model $LlmModel -VisionModel $VisionModel -SkipVision
        }
        else {
            & $llmScript -Model $LlmModel -VisionModel $VisionModel
        }
        if ($global:TeyssirLlmReady) {
            Write-InstallLog ("Ollama API ready. Text model: {0}; Vision: {1}" -f `
                    $(if ($global:TeyssirLlmModelReady) { "$LlmModel (OK)" } else { "$LlmModel (missing/soft-fail)" }), `
                    $(if ($SkipVision) { "skipped" } elseif ($global:TeyssirVisionModelReady) { "$VisionModel (OK)" } else { "$VisionModel (missing/soft-fail)" })
            ) "Green"
        }
        else {
            Write-InstallLog "Ollama not ready after install attempt -- ERP continues without local AI (soft-fail)." "Yellow"
        }
    }
    catch {
        Write-InstallLog ("Local LLM setup warning (soft-fail): {0}" -f $_.Exception.Message) "Yellow"
    }
}

if ($DepsOnly) {
    Write-InstallLog "==== DepsOnly -- skipping install.ps1 ====" "Green"
    Write-InstallLog ("Log saved: {0}" -f $logPath)
    if ($transcriptStarted) { Stop-Transcript | Out-Null }
    exit 0
}

# --- full app install (existing spine) -------------------------------------
$installScript = Join-Path $PSScriptRoot "install.ps1"
$forward = @{}
foreach ($key in @(
        "Role", "Terminal", "StoreCode", "HubUrl", "SyncKey", "Printer",
        "DiscoverPrinter", "SkipBuild", "SkipLlm", "LlmModel", "SkipVision",
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

Write-InstallLog "Handing off to install.ps1 (venv, .env, Postgres soft-fail, LLM re-check, DiscoverPrinter, service) ..." "Cyan"
$exitCode = 0
try {
    & $installScript @forward
    $exitCode = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
}
catch {
    $detail = Get-TeyssirErrorText $_
    Write-InstallLog "install.ps1 failed -- full error follows:" "Red"
    foreach ($line in ($detail -split "`r?`n")) {
        Write-InstallLog $line "Red"
    }
    $exitCode = 1
}

Write-InstallLog ""
Write-InstallLog ("==== install_all finished (exit {0}) ====" -f $exitCode) $(if ($exitCode -eq 0) { "Green" } else { "Red" })
Write-InstallLog ("Log: {0}" -f $logPath)
Write-InstallLog "Printer: use -DiscoverPrinter, -Printer tcp:IP:9100, or .\deploy\windows\Discover-Printer.ps1 (no fake shop IP)."
Write-InstallLog "Local AI: Ollama + mistral (+ qwen2.5vl:3b unless -SkipVision); soft-fail if pull fails. -SkipLlm to opt out."
Write-InstallLog "libzbar: optional DLL -- see docs/INSTALL-WINDOWS.md (ISBN barcode soft-fail without it)."

if ($transcriptStarted) {
    try { Stop-Transcript | Out-Null } catch { }
}
exit $exitCode
