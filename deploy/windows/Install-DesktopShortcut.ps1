<#
    Create "Teyssir ERP" on the user Desktop (and Start Menu) with the branding icon.
    Opens the default browser at http://localhost:8000 after waiting for /health/.
    Prefers open-teyssir.vbs via wscript so double-click shows no console flash.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
$global:TeyssirShortcutReady = $false
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Write-Sc([string]$Message, [string]$Color = "Gray") {
    Write-Host "  [UI] $Message" -ForegroundColor $Color
}

try {
    $openPs1 = Join-Path $PSScriptRoot "open-teyssir.ps1"
    $openVbs = Join-Path $PSScriptRoot "open-teyssir.vbs"
    $ico = Join-Path $Root "assets\branding\teyssir.ico"
    if (-not (Test-Path $ico)) { $ico = Join-Path $Root "frontend\public\favicon.ico" }
    if (-not (Test-Path $openPs1)) { throw "open-teyssir.ps1 missing" }

    $desktop = [Environment]::GetFolderPath("Desktop")
    if (-not $desktop) { $desktop = Join-Path $env:USERPROFILE "Desktop" }
    $lnkPath = Join-Path $desktop "Teyssir ERP.lnk"

    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($lnkPath)
    if (Test-Path $openVbs) {
        $lnk.TargetPath = (Join-Path $env:SystemRoot "System32\wscript.exe")
        $lnk.Arguments = "//nologo `"$openVbs`""
        $lnk.WindowStyle = 1
    }
    else {
        $lnk.TargetPath = (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe")
        $lnk.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$openPs1`""
        $lnk.WindowStyle = 7
    }
    $lnk.WorkingDirectory = $Root
    $lnk.Description = "Teyssir ERP"
    if (Test-Path $ico) { $lnk.IconLocation = "$ico,0" }
    $lnk.Save()
    Write-Sc "Desktop shortcut: $lnkPath" "Green"

    $programs = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
    if (Test-Path $programs) {
        $startLnk = Join-Path $programs "Teyssir ERP.lnk"
        Copy-Item $lnkPath $startLnk -Force
        Write-Sc "Start Menu shortcut: $startLnk"
    }

    $global:TeyssirShortcutReady = $true
}
catch {
    Write-Sc ("Desktop shortcut skipped: " + $_.Exception.Message) "Yellow"
}
