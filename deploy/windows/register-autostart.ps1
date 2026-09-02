<#
    Register till->hub sync (and a logon fallback if the Windows service is absent).

    Prefer NSSM service (Install-WindowsService.ps1) for the backend on hub/till.
    This script never creates 'Teyssir Server' when TeyssirBackend already exists
    (avoids two listeners on port 8000).

    Unregister (reversible):
        .\deploy\windows\uninstall.ps1
    Or selectively:
        Unregister-ScheduledTask -TaskName "Teyssir Sync" -Confirm:$false
        Unregister-ScheduledTask -TaskName "Teyssir Server" -Confirm:$false

        .\deploy\windows\register-autostart.ps1 -Role hub
        .\deploy\windows\register-autostart.ps1 -Role till -SyncMinutes 5
#>
param(
    [ValidateSet("hub", "till")] [string]$Role = "till",
    [int]$SyncMinutes = 5
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$start = Join-Path $Root "deploy\windows\start-teyssir.bat"
$sync = Join-Path $Root "deploy\windows\sync-now.bat"

$svc = Get-Service -Name "TeyssirBackend" -ErrorAction SilentlyContinue
if ($svc) {
    Write-Host "Windows service 'TeyssirBackend' is installed -- skipping scheduled task 'Teyssir Server' (no duplicate)." -ForegroundColor Green
}
else {
    $action = New-ScheduledTaskAction -Execute $start
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    Register-ScheduledTask -TaskName "Teyssir Server" -Action $action -Trigger $trigger `
        -RunLevel Highest -Force | Out-Null
    Write-Host "Registered scheduled task 'Teyssir Server' (logon fallback; prefer Install-WindowsService.ps1)." -ForegroundColor Green
}

if ($Role -eq "till") {
    $sAction = New-ScheduledTaskAction -Execute $sync
    $sTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes $SyncMinutes)
    Register-ScheduledTask -TaskName "Teyssir Sync" -Action $sAction -Trigger $sTrigger -Force | Out-Null
    Write-Host "Registered scheduled task 'Teyssir Sync' (every $SyncMinutes minutes)." -ForegroundColor Green
}

Write-Host "Done."
