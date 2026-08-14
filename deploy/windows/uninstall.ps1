<#
    Remove the Teyssir Windows service, desktop shortcut, and scheduled tasks.
    Does NOT delete the project folder, database, or .env.

        .\deploy\windows\uninstall.ps1
#>
[CmdletBinding()]
param(
    [string]$ServiceName = "TeyssirBackend"
)

$ErrorActionPreference = "Continue"
Write-Host "==== Teyssir uninstall (service + shortcuts) ====" -ForegroundColor Yellow

function Get-NssmExe {
    $cmd = Get-Command nssm -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @(
            (Join-Path $env:ProgramData "Teyssir\nssm\nssm.exe"),
            (Join-Path $PSScriptRoot "nssm\nssm.exe")
        )) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

$nssm = Get-NssmExe
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc) {
    Write-Host "Stopping $ServiceName ..."
    if ($nssm) { & $nssm stop $ServiceName | Out-Null } else { Stop-Service $ServiceName -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
    if ($nssm) {
        & $nssm remove $ServiceName confirm | Out-Host
    }
    else {
        sc.exe delete $ServiceName | Out-Host
    }
}
else {
    Write-Host "Service $ServiceName is not installed."
}

foreach ($task in @("Teyssir Server", "Teyssir Sync")) {
    try {
        Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "Removed scheduled task '$task'."
    }
    catch { }
}

$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = Join-Path $desktop "Teyssir ERP.lnk"
if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Host "Removed $lnk" }
$start = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Teyssir ERP.lnk"
if (Test-Path $start) { Remove-Item $start -Force; Write-Host "Removed $start" }

Write-Host ""
Write-Host "Done. Project files and the database were left in place."
Write-Host "Delete the project folder yourself after backing up data if you want a full wipe."
