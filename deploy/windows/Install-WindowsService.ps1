<#
    Register Teyssir as a Windows service (NSSM + waitress).
    Called by install.ps1. Never throws: the ERP install continues without the service.

        .\deploy\windows\Install-WindowsService.ps1
        .\deploy\windows\uninstall.ps1   # removes the service
#>
[CmdletBinding()]
param(
    [string]$ServiceName = "TeyssirBackend",
    [string]$Port = "8000",
    [string]$Printer = ""
)

$ErrorActionPreference = "Continue"
$global:TeyssirServiceReady = $false
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Write-Svc([string]$Message, [string]$Color = "Gray") {
    Write-Host "  [SVC] $Message" -ForegroundColor $Color
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

function Get-NssmExe {
    Refresh-Path
    $cmd = Get-Command nssm -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        (Join-Path $env:ProgramData "Teyssir\nssm\nssm.exe"),
        (Join-Path $PSScriptRoot "nssm\nssm.exe"),
        "C:\Program Files\nssm\nssm.exe",
        "C:\ProgramData\chocolatey\lib\NSSM\tools\nssm.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Install-Nssm {
    $exe = Get-NssmExe
    if ($exe) {
        Write-Svc ("NSSM: " + $exe) "Green"
        return $exe
    }
    Write-Svc "NSSM not found — installing ..." "Yellow"
    try {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            winget install --id NSSM.NSSM -e --accept-package-agreements --accept-source-agreements --disable-interactivity --silent | Out-Host
            Refresh-Path
            $exe = Get-NssmExe
            if ($exe) { return $exe }
        }
    }
    catch {
        Write-Svc ("winget NSSM skipped: " + $_.Exception.Message) "Yellow"
    }
    try {
        $zip = Join-Path $env:TEMP "nssm-2.24.zip"
        $dest = Join-Path $env:ProgramData "Teyssir\nssm"
        Write-Svc "Downloading nssm-2.24.zip ..."
        Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile $zip -UseBasicParsing
        $extract = Join-Path $env:TEMP "nssm-extract"
        if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
        Expand-Archive -Path $zip -DestinationPath $extract -Force
        $found = Get-ChildItem -Path $extract -Recurse -Filter nssm.exe | Where-Object { $_.DirectoryName -match "win64" } | Select-Object -First 1
        if (-not $found) {
            $found = Get-ChildItem -Path $extract -Recurse -Filter nssm.exe | Select-Object -First 1
        }
        if (-not $found) { throw "nssm.exe missing from zip" }
        New-Item -ItemType Directory -Force -Path $dest | Out-Null
        Copy-Item $found.FullName (Join-Path $dest "nssm.exe") -Force
        return (Join-Path $dest "nssm.exe")
    }
    catch {
        Write-Svc ("NSSM download failed: " + $_.Exception.Message) "Yellow"
        return $null
    }
}

function Get-ListenerPid([int]$TcpPort) {
    try {
        $c = Get-NetTCPConnection -LocalPort $TcpPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($c) { return [int]$c.OwningProcess }
    }
    catch { }
    return $null
}

try {
    if (-not (Test-IsAdmin)) {
        Write-Svc "Administrator rights required to install a Windows service. Re-run install.ps1 elevated, or start with deploy\windows\start-teyssir.bat." "Yellow"
        return
    }

    $python = Join-Path $Root ".venv\Scripts\python.exe"
    $serve = Join-Path $Root "deploy\windows\serve.py"
    if (-not (Test-Path $python) -or -not (Test-Path $serve)) {
        Write-Svc "Python venv or serve.py missing — skip service." "Yellow"
        return
    }

    $nssm = Install-Nssm
    if (-not $nssm) {
        Write-Svc "NSSM unavailable. Backend can still run via start-teyssir.bat." "Yellow"
        return
    }

    # Avoid two servers: drop the old logon scheduled task.
    try {
        Unregister-ScheduledTask -TaskName "Teyssir Server" -Confirm:$false -ErrorAction SilentlyContinue
    }
    catch { }

    $logDir = Join-Path $Root "logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $stdout = Join-Path $logDir "teyssir-backend-stdout.log"
    $stderr = Join-Path $logDir "teyssir-backend-stderr.log"

    try {
        icacls $Root /grant "NT AUTHORITY\SYSTEM:(OI)(CI)M" /T /C /Q | Out-Null
    }
    catch {
        Write-Svc ("Could not grant SYSTEM modify on project folder: " + $_.Exception.Message) "Yellow"
    }

    $existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $existing) {
        Write-Svc "Creating service $ServiceName ..."
        & $nssm install $ServiceName $python $serve | Out-Host
        if ($LASTEXITCODE -ne 0 -and -not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) {
            throw "nssm install failed (exit $LASTEXITCODE)"
        }
    }
    else {
        Write-Svc "Service $ServiceName already exists — updating paths (no duplicate)." "Green"
        if ($existing.Status -eq "Running") {
            & $nssm stop $ServiceName | Out-Null
            Start-Sleep -Seconds 2
        }
    }

    & $nssm set $ServiceName Application $python | Out-Null
    & $nssm set $ServiceName AppDirectory $Root | Out-Null
    & $nssm set $ServiceName AppParameters "`"$serve`"" | Out-Null
    & $nssm set $ServiceName DisplayName "Teyssir Backend" | Out-Null
    & $nssm set $ServiceName Description "Teyssir ERP API and PWA (waitress). Starts automatically at boot." | Out-Null
    & $nssm set $ServiceName Start SERVICE_DELAYED_AUTO_START | Out-Null
    & $nssm set $ServiceName AppStdout $stdout | Out-Null
    & $nssm set $ServiceName AppStderr $stderr | Out-Null
    & $nssm set $ServiceName AppRotateFiles 1 | Out-Null
    & $nssm set $ServiceName AppRotateBytes 10485760 | Out-Null
    & $nssm set $ServiceName AppExit Default Restart | Out-Null
    & $nssm set $ServiceName AppRestartDelay 5000 | Out-Null
    & $nssm set $ServiceName AppThrottle 5000 | Out-Null
    $tessEnv = "C:\Program Files\Tesseract-OCR\tesseract.exe"
    if (-not (Test-Path $tessEnv)) {
        $tessEnv = "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"
    }
    if (-not (Test-Path $tessEnv)) { $tessEnv = "tesseract" }

    # TEYSSIR_PRINTER: CLI > .env > dummy (client LAN; never hardcode a shop IP)
    $printerTarget = $Printer
    if (-not $printerTarget) {
        $envFile = Join-Path $Root ".env"
        if (Test-Path $envFile) {
            $pline = [System.IO.File]::ReadAllLines($envFile) |
                Where-Object { $_ -match '^\s*TEYSSIR_PRINTER=' } |
                Select-Object -First 1
            if ($pline) {
                $printerTarget = $pline.Substring($pline.IndexOf("=") + 1).Trim()
            }
        }
    }
    if (-not $printerTarget) { $printerTarget = "dummy" }

    $envExtra = @(
        "PORT=$Port",
        "PYTHONUNBUFFERED=1",
        "TEYSSIR_SCAN_EXECUTOR=thread",
        "TEYSSIR_TESSERACT_CMD=$tessEnv",
        "TESSERACT_CMD=$tessEnv",
        "TEYSSIR_PRINTER=$printerTarget",
        "PATH=C:\Program Files\Tesseract-OCR;C:\Program Files (x86)\Tesseract-OCR;%SystemRoot%\system32;%SystemRoot%;%SystemRoot%\System32\Wbem"
    ) -join "`n"
    & $nssm set $ServiceName AppEnvironmentExtra $envExtra | Out-Null
    Write-Svc ("TEYSSIR_PRINTER=$printerTarget") "Gray"

    sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/5000/restart/5000 | Out-Null
    sc.exe config $ServiceName start= delayed-auto | Out-Null

    $listenPid = Get-ListenerPid ([int]$Port)
    $svcPid = 0
    try {
        $cim = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
        if ($cim) { $svcPid = [int]$cim.ProcessId }
    }
    catch { }
    if ($listenPid -and $svcPid -ne 0 -and $listenPid -ne $svcPid) {
        Write-Svc "Port $Port is already in use (PID $listenPid). Stop start-teyssir.bat / the other process, then: nssm start $ServiceName" "Yellow"
        return
    }
    if ($listenPid -and $svcPid -eq 0) {
        Write-Svc "Port $Port is already in use (PID $listenPid). Close that window, then re-run this script." "Yellow"
        return
    }

    Write-Svc "Starting $ServiceName ..."
    Start-Service $ServiceName -ErrorAction SilentlyContinue
    if ((Get-Service $ServiceName).Status -ne "Running") {
        & $nssm start $ServiceName | Out-Host
    }

    $ok = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 1
        $st = (Get-Service $ServiceName -ErrorAction SilentlyContinue).Status
        if ($st -eq "Running") {
            try {
                $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health/" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
                if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300) { $ok = $true; break }
            }
            catch { }
        }
    }

    if ($ok) {
        Write-Svc "Service $ServiceName is RUNNING (delayed auto-start + restart on failure). Logs: $logDir" "Green"
        $global:TeyssirServiceReady = $true
    }
    else {
        $st = (Get-Service $ServiceName -ErrorAction SilentlyContinue).Status
        Write-Svc ("Service status=$st — check $stderr or start-teyssir.bat as fallback.") "Yellow"
        if ($st -eq "Running") { $global:TeyssirServiceReady = $true }
    }
}
catch {
    Write-Svc ("Service setup skipped: " + $_.Exception.Message) "Yellow"
}
