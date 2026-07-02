<#
    Register Teyssir to start automatically, and (on tills) to sync on a schedule.
    Run in an elevated PowerShell:

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

# Auto-start the Teyssir server when the user logs in.
$action = New-ScheduledTaskAction -Execute $start
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "Teyssir Server" -Action $action -Trigger $trigger `
    -RunLevel Highest -Force | Out-Null
Write-Host "Registered scheduled task 'Teyssir Server' (auto-start at logon)." -ForegroundColor Green

# On tills, reconcile with the hub every few minutes.
if ($Role -eq "till") {
    $sAction = New-ScheduledTaskAction -Execute $sync
    $sTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes $SyncMinutes)
    Register-ScheduledTask -TaskName "Teyssir Sync" -Action $sAction -Trigger $sTrigger -Force | Out-Null
    Write-Host "Registered scheduled task 'Teyssir Sync' (every $SyncMinutes minutes)." -ForegroundColor Green
}

Write-Host "Done. Manage these under Windows 'Task Scheduler'."
