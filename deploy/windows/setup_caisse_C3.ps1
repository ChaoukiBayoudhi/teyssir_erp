<#
    Teyssir — caisse C3 wrapper (Phase 4)
    Thin ID shim → setup_caisse.ps1 -Terminal C3 (idempotent).

        .\deploy\windows\setup_caisse_C3.ps1 -HubUrl http://teyssir-hub.local:8000 -SyncKey <key>
        .\deploy\windows\setup_caisse_C3.ps1 -StoreCode S1 -DiscoverPrinter
        .\deploy\windows\setup_caisse_C3.ps1 -ValidateOnly
#>
[CmdletBinding()]
param(
    [string]$StoreCode = "",
    [string]$HubUrl = "",
    [string]$SyncKey = "",
    [string]$Printer = "",
    [switch]$DiscoverPrinter,
    [switch]$SkipBuild,
    [switch]$SkipLlm,
    [string]$LlmModel = "mistral",
    [switch]$SkipVision,
    [string]$VisionModel = "qwen2.5vl:3b",
    [switch]$SkipAdmin,
    [string]$AdminUser = "",
    [string]$AdminPassword = "",
    [switch]$RegisterAutostart,
    [switch]$SkipAutostart,
    [switch]$SkipFirewall,
    [switch]$SkipService,
    [switch]$SkipShortcut,
    [switch]$SkipPull,
    [string]$RepoUrl = "https://github.com/ChaoukiBayoudhi/teyssir_erp.git",
    [string]$CloneTarget = "",
    [switch]$ValidateOnly,
    [switch]$SkipChecks,
    [switch]$OpenPos,
    [switch]$FreshInstall,
    [switch]$KeepVenv
)

$ErrorActionPreference = "Stop"
$target = Join-Path $PSScriptRoot "setup_caisse.ps1"
if (-not (Test-Path $target)) { throw "setup_caisse.ps1 missing next to this wrapper." }

$forward = @{ Terminal = "C3" }
foreach ($key in $PSBoundParameters.Keys) {
    $forward[$key] = $PSBoundParameters[$key]
}
& $target @forward
exit $LASTEXITCODE
