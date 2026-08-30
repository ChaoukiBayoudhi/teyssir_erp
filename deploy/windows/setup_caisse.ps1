<#
    Teyssir — per-caisse (till) setup wrapper (Phase 4)
    ----------------------------------------------------
    Thin, idempotent entry for one till terminal. Always Role=till.
    Chains to setup_app.ps1 (→ install.ps1). Prefer the ID wrappers:

        .\deploy\windows\setup_caisse_C1.ps1 -HubUrl http://teyssir-hub.local:8000 -SyncKey <key>
        .\deploy\windows\setup_caisse.ps1 -Terminal C2 -HubUrl http://… -SyncKey <key> -DiscoverPrinter

    Optional env fallbacks (when param empty): TEYSSIR_TERMINAL, TEYSSIR_STORE_CODE,
    TEYSSIR_HUB_URL, TEYSSIR_SYNC_KEY, TEYSSIR_PRINTER.

    Printer: pass -Printer tcp:IP:9100, or -DiscoverPrinter (LAN /24 scan → tcp:… or dummy).
    Never hardcodes a shop / Aclas IP. No parallel installer — extends the existing kit.
#>
[CmdletBinding()]
param(
    [ValidateSet("C1", "C2", "C3")]
    [string]$Terminal = "",

    [string]$StoreCode = "",
    [string]$HubUrl = "",
    [string]$SyncKey = "",
    [string]$Printer = "",
    [switch]$DiscoverPrinter,

    # Forwarded to setup_app / install
    [switch]$SkipBuild,
    [switch]$SkipLlm,
    [string]$LlmModel = "mistral",
    [switch]$SkipVision,
    [string]$VisionModel = "qwen2.5vl:3b",
    [switch]$SkipAdmin,
    [string]$AdminUser = "",
    [string]$AdminPassword = "",
    [switch]$RegisterAutostart,
    [switch]$SkipFirewall,
    [switch]$SkipService,
    [switch]$SkipShortcut,
    [switch]$SkipPull,
    [string]$RepoUrl = "https://github.com/ChaoukiBayoudhi/teyssir_erp.git",
    [string]$CloneTarget = "",

    # Post-setup helpers (safe on non-Windows for documentation; real checks when online)
    [switch]$ValidateOnly,
    [switch]$SkipChecks,
    [switch]$OpenPos
)

$ErrorActionPreference = "Stop"

function Write-Caisse([string]$Message, [string]$Color = "Gray") {
    Write-Host $Message -ForegroundColor $Color
}

function Get-EnvOrEmpty([string]$Name) {
    $v = [Environment]::GetEnvironmentVariable($Name)
    if ($null -eq $v) { return "" }
    return $v.Trim()
}

function Test-HttpOk([string]$Url, [int]$TimeoutSec = 5) {
    if (-not $Url) { return $false }
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec -ErrorAction Stop
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300)
    }
    catch { return $false }
}

function Invoke-CaisseChecks {
    param(
        [string]$WindowsDeploy,
        [string]$Hub,
        [string]$Term,
        [switch]$LaunchPos
    )

    Write-Caisse ""
    Write-Caisse "==== Caisse checks (terminal $Term) ====" "Cyan"

    # 1) Hub API connectivity
    $hubBase = if ($Hub) { $Hub.TrimEnd("/") } else { "http://teyssir-hub.local:8000" }
    $hubHealth = "$hubBase/health/"
    Write-Caisse ("Hub health: {0}" -f $hubHealth)
    if (Test-HttpOk $hubHealth) {
        Write-Caisse "  Hub reachable." "Green"
    }
    else {
        Write-Caisse "  Hub not reachable yet (firewall, hosts, or hub service). Re-check after hub is up." "Yellow"
        Write-Caisse "  Manual: open the URL above from this PC's browser." "Yellow"
    }

    # 2) Local till health (if service already running)
    $localHealth = "http://127.0.0.1:8000/health/"
    if (Test-HttpOk $localHealth 3) {
        Write-Caisse ("Local till health OK: {0}" -f $localHealth) "Green"
    }
    else {
        Write-Caisse "Local /health/ not up yet — start TeyssirBackend or Desktop « Teyssir ERP », then re-check." "Yellow"
    }

    # 3) Printer discover helper (document + optional invoke path)
    $disc = Join-Path $WindowsDeploy "Discover-Printer.ps1"
    Write-Caisse "Printer: use -DiscoverPrinter on install, or run:"
    Write-Caisse ("  {0}" -f $disc)
    Write-Caisse "  (writes tcp:IP:9100 or dummy — never invents a shop Aclas IP)"
    if (Test-Path $disc) {
        Write-Caisse "  Discover-Printer.ps1 present." "Green"
    }
    else {
        Write-Caisse "  Discover-Printer.ps1 missing from kit." "Yellow"
    }

    # 4) POS UI launch path
    $openPs1 = Join-Path $WindowsDeploy "open-teyssir.ps1"
    $shortcutPs1 = Join-Path $WindowsDeploy "Install-DesktopShortcut.ps1"
    Write-Caisse "POS UI: Desktop shortcut « Teyssir ERP » (Install-DesktopShortcut.ps1) or:"
    Write-Caisse ("  {0}" -f $openPs1)
    if ($LaunchPos -and (Test-Path $openPs1)) {
        Write-Caisse "Launching POS (open-teyssir.ps1) ..." "Cyan"
        try {
            & $openPs1
        }
        catch {
            Write-Caisse ("open-teyssir soft-fail: {0}" -f $_.Exception.Message) "Yellow"
        }
    }
    elseif (-not (Test-Path $openPs1)) {
        Write-Caisse "  open-teyssir.ps1 missing." "Yellow"
    }
    if (Test-Path $shortcutPs1) {
        Write-Caisse "  Install-DesktopShortcut.ps1 present (Phase 5 — usually run by install.ps1)." "Green"
    }
}

# --- resolve terminal / optional env fallbacks --------------------------------
if (-not $Terminal) {
    $fromEnv = Get-EnvOrEmpty "TEYSSIR_TERMINAL"
    if ($fromEnv -match '^(?i)C[123]$') {
        $Terminal = $fromEnv.ToUpper()
    }
    else {
        throw "Pass -Terminal C1|C2|C3 (or set TEYSSIR_TERMINAL). Prefer setup_caisse_C1.ps1 / _C2 / _C3."
    }
}
$Terminal = $Terminal.ToUpper()

if (-not $StoreCode) { $StoreCode = Get-EnvOrEmpty "TEYSSIR_STORE_CODE" }
if (-not $HubUrl) {
    $HubUrl = Get-EnvOrEmpty "TEYSSIR_HUB_URL"
    if (-not $HubUrl) { $HubUrl = "http://teyssir-hub.local:8000" }
}
if (-not $SyncKey) { $SyncKey = Get-EnvOrEmpty "TEYSSIR_SYNC_KEY" }
if (-not $Printer) { $Printer = Get-EnvOrEmpty "TEYSSIR_PRINTER" }

$WindowsDeploy = $PSScriptRoot
$setupApp = Join-Path $WindowsDeploy "setup_app.ps1"
if (-not (Test-Path $setupApp)) {
    throw ("setup_app.ps1 not found at {0}" -f $setupApp)
}

Write-Caisse ("==== Teyssir setup_caisse (till / {0}) ====" -f $Terminal) "Green"
Write-Caisse ("HubUrl={0}  StoreCode={1}  DiscoverPrinter={2}" -f $HubUrl, $(
        if ($StoreCode) { $StoreCode } else { "(none)" }
    ), [bool]$DiscoverPrinter)

if ($ValidateOnly) {
    Invoke-CaisseChecks -WindowsDeploy $WindowsDeploy -Hub $HubUrl -Term $Terminal -LaunchPos:$OpenPos
    Write-Caisse "==== ValidateOnly done (no install) ====" "Cyan"
    exit 0
}

# --- hand off to setup_app.ps1 (Role=till, Terminal=Cx) ----------------------
$forward = @{
    Role     = "till"
    Terminal = $Terminal
    HubUrl   = $HubUrl
}
if ($StoreCode) { $forward["StoreCode"] = $StoreCode }
if ($SyncKey) { $forward["SyncKey"] = $SyncKey }
if ($Printer) { $forward["Printer"] = $Printer }
if ($DiscoverPrinter) { $forward["DiscoverPrinter"] = $true }

foreach ($key in @(
        "SkipBuild", "SkipLlm", "LlmModel", "SkipVision", "VisionModel",
        "SkipAdmin", "AdminUser", "AdminPassword", "RegisterAutostart",
        "SkipFirewall", "SkipService", "SkipShortcut", "SkipPull",
        "RepoUrl", "CloneTarget"
    )) {
    if ($PSBoundParameters.ContainsKey($key)) {
        $forward[$key] = $PSBoundParameters[$key]
    }
}
if (-not $forward.ContainsKey("LlmModel")) { $forward["LlmModel"] = $LlmModel }
if (-not $forward.ContainsKey("VisionModel")) { $forward["VisionModel"] = $VisionModel }

Write-Caisse "Chain: setup_caisse → setup_app.ps1 → install.ps1 (idempotent)." "Cyan"
$exitCode = 0
try {
    & $setupApp @forward
    $exitCode = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
}
catch {
    Write-Caisse ("setup_app.ps1 failed: {0}" -f $_.Exception.Message) "Red"
    $exitCode = 1
}

if (-not $SkipChecks) {
    Invoke-CaisseChecks -WindowsDeploy $WindowsDeploy -Hub $HubUrl -Term $Terminal -LaunchPos:($OpenPos -and $exitCode -eq 0)
}

Write-Caisse ""
Write-Caisse ("==== setup_caisse {0} finished (exit {1}) ====" -f $Terminal, $exitCode) $(
    if ($exitCode -eq 0) { "Green" } else { "Red" }
)
Write-Caisse "Printer: -DiscoverPrinter | -Printer tcp:IP:9100 | Discover-Printer.ps1 (no hardcoded Aclas IP)."
Write-Caisse "POS: Desktop « Teyssir ERP » or .\deploy\windows\open-teyssir.ps1"
exit $exitCode
