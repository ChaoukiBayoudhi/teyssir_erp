<#
    Teyssir -- host dependency installer / verifier (Windows).
    Idempotent: skips packages already present and usable.
    Soft-fails optional tools; never installs Redis.
    Called by install_all.ps1 (and safe to run alone).

        .\deploy\windows\Install-HostDependencies.ps1
        .\deploy\windows\Install-HostDependencies.ps1 -SkipNode -SkipGit
#>
[CmdletBinding()]
param(
    [switch]$SkipNode,
    [switch]$SkipGit,
    [switch]$SkipTesseract,
    [switch]$SkipPython,
    # When frontend\dist already exists, Node is optional.
    [switch]$ForceNode
)

$ErrorActionPreference = "Continue"
$script:Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Write-Dep {
    param(
        [string]$Message,
        [ValidateSet("Info", "Ok", "Warn", "Fail")] [string]$Level = "Info"
    )
    $color = switch ($Level) {
        "Ok" { "Green" }
        "Warn" { "Yellow" }
        "Fail" { "Red" }
        default { "Gray" }
    }
    $line = "  [deps] $Message"
    Write-Host $line -ForegroundColor $color
    if ($global:TeyssirInstallLogPath) {
        try {
            Add-Content -Path $global:TeyssirInstallLogPath -Value ("[{0}] {1}" -f (Get-Date -Format "o"), $line) -ErrorAction SilentlyContinue
        }
        catch { }
    }
}

function Refresh-Path {
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Test-Winget {
    return [bool](Get-Command winget -ErrorAction SilentlyContinue)
}

function Invoke-WingetInstall {
    param(
        [Parameter(Mandatory = $true)] [string]$Id,
        [ValidateSet("machine", "user", "")] [string]$Scope = ""
    )
    if (-not (Test-Winget)) {
        Write-Dep "winget unavailable -- cannot install $Id" "Warn"
        return $false
    }
    $wingetArgs = @(
        "install", "--id", $Id, "-e",
        "--accept-package-agreements", "--accept-source-agreements",
        "--disable-interactivity", "--silent"
    )
    if ($Scope) { $wingetArgs += @("--scope", $Scope) }
    try {
        Write-Dep ("winget install {0}{1} ..." -f $Id, $(if ($Scope) { " (scope=$Scope)" } else { "" }))
        & winget @wingetArgs 2>&1 | Out-Host
        Refresh-Path
        return $true
    }
    catch {
        Write-Dep ("winget install $Id skipped: " + $_.Exception.Message) "Warn"
        return $false
    }
}

function Test-RealPython {
    param([string]$Exe)
    if (-not $Exe) { return $false }
    try {
        & $Exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
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
            "$env:ProgramFiles\Python313\python.exe",
            "$env:LocalAppData\Programs\Python\Python311\python.exe",
            "$env:ProgramFiles\Python311\python.exe"
        )) {
        if ((Test-Path $p) -and (Test-RealPython $p)) { return $p }
    }
    return $null
}

function Install-PythonHost {
    if ($SkipPython) {
        Write-Dep "Python skipped (-SkipPython)."
        return $null
    }
    $exe = Get-PythonExe
    if ($exe) {
        $ver = (& $exe --version) 2>&1
        Write-Dep ("Python OK: {0} ({1})" -f $ver, $exe) "Ok"
        $global:TeyssirPythonReady = $true
        $global:TeyssirPythonExe = $exe
        return $exe
    }
    Write-Dep "Python 3.11+ missing -- installing Python.Python.3.12 via winget ..." "Warn"
    Invoke-WingetInstall -Id "Python.Python.3.12" -Scope "machine" | Out-Null
    Refresh-Path
    $exe = Get-PythonExe
    if (-not $exe) {
        Invoke-WingetInstall -Id "Python.Python.3.12" -Scope "user" | Out-Null
        Refresh-Path
        $exe = Get-PythonExe
    }
    if ($exe) {
        $ver = (& $exe --version) 2>&1
        Write-Dep ("Python installed: {0}" -f $ver) "Ok"
        $global:TeyssirPythonReady = $true
        $global:TeyssirPythonExe = $exe
        return $exe
    }
    Write-Dep "Python 3.11+ still missing. Install from https://www.python.org/downloads/windows/ (tick Add to PATH)." "Fail"
    $global:TeyssirPythonReady = $false
    return $null
}

function Install-GitHost {
    if ($SkipGit) {
        Write-Dep "Git skipped (-SkipGit). ZIP installs do not need Git."
        return
    }
    Refresh-Path
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        $ver = (& git --version) 2>&1
        Write-Dep ("Git OK: {0}" -f $ver) "Ok"
        $global:TeyssirGitReady = $true
        return
    }
    Write-Dep "Git not found -- optional install via winget (ZIP path still works without it)." "Warn"
    Invoke-WingetInstall -Id "Git.Git" | Out-Null
    Refresh-Path
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        Write-Dep ("Git installed: " + ((& git --version) 2>&1)) "Ok"
        $global:TeyssirGitReady = $true
    }
    else {
        Write-Dep "Git still missing -- OK for ZIP deployments; clone via GitHub Desktop or install later." "Warn"
        $global:TeyssirGitReady = $false
    }
}

function Install-NodeHost {
    if ($SkipNode) {
        Write-Dep "Node.js skipped (-SkipNode)."
        return
    }
    $distOk = Test-Path (Join-Path $script:Root "frontend\dist\index.html")
    Refresh-Path
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if ($npm) {
        Write-Dep ("Node/npm OK: " + ((& npm --version) 2>&1)) "Ok"
        $global:TeyssirNodeReady = $true
        return
    }
    if ($distOk -and -not $ForceNode) {
        Write-Dep "Node.js not found -- skipped (frontend\dist already present)." "Ok"
        $global:TeyssirNodeReady = $false
        return
    }
    Write-Dep "Node.js LTS missing and frontend\dist absent -- installing OpenJS.NodeJS.LTS ..." "Warn"
    Invoke-WingetInstall -Id "OpenJS.NodeJS.LTS" | Out-Null
    Refresh-Path
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if ($npm) {
        Write-Dep ("Node/npm installed: " + ((& npm --version) 2>&1)) "Ok"
        $global:TeyssirNodeReady = $true
    }
    else {
        Write-Dep "Node.js still missing -- copy frontend\dist from a build PC, or install Node LTS manually." "Warn"
        $global:TeyssirNodeReady = $false
    }
}

function Get-TesseractExe {
    Refresh-Path
    $cmd = Get-Command tesseract -ErrorAction SilentlyContinue
    if ($cmd -and (Test-Path $cmd.Source)) { return $cmd.Source }
    foreach ($p in @(
            "C:\Program Files\Tesseract-OCR\tesseract.exe",
            "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"
        )) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Test-TesseractLangs {
    param([Parameter(Mandatory = $true)] [string]$Exe)
    $needed = @("eng", "fra", "ara")
    try {
        $raw = & $Exe --list-langs 2>&1 | Out-String
        $installed = @()
        foreach ($line in ($raw -split "`r?`n")) {
            $t = $line.Trim()
            if ($t -and $t -notmatch "list of available|Available languages" -and $t -notmatch "^\s*$") {
                $installed += $t.ToLowerInvariant()
            }
        }
        $missing = @($needed | Where-Object { $installed -notcontains $_ })
        return @{
            Ok      = ($missing.Count -eq 0)
            Missing = $missing
            All     = $installed
        }
    }
    catch {
        return @{ Ok = $false; Missing = $needed; All = @() }
    }
}

function Install-TesseractHost {
    if ($SkipTesseract) {
        Write-Dep "Tesseract skipped (-SkipTesseract)."
        return
    }
    $exe = Get-TesseractExe
    if (-not $exe) {
        Write-Dep "Tesseract missing -- installing UB-Mannheim.TesseractOCR (eng/fra/ara) ..." "Warn"
        if (Test-Winget) {
            Invoke-WingetInstall -Id "UB-Mannheim.TesseractOCR" | Out-Null
        }
        elseif (Get-Command choco -ErrorAction SilentlyContinue) {
            try {
                choco install tesseract -y --no-progress 2>&1 | Out-Host
                Refresh-Path
            }
            catch {
                Write-Dep ("choco tesseract skipped: " + $_.Exception.Message) "Warn"
            }
        }
        else {
            Write-Dep "winget/choco unavailable -- install Tesseract (UB Mannheim) with eng+fra+ara manually." "Warn"
        }
        $exe = Get-TesseractExe
    }
    if (-not $exe) {
        Write-Dep "Tesseract not found -- book OCR uses manual/vision fallback." "Warn"
        $global:TeyssirTesseractReady = $false
        $global:TeyssirTesseractCmd = $null
        $global:TeyssirTesseractLangsOk = $false
        return
    }
    Write-Dep ("Tesseract OK: " + $exe) "Ok"
    $global:TeyssirTesseractCmd = $exe
    $global:TeyssirTesseractReady = $true
    $langs = Test-TesseractLangs -Exe $exe
    if ($langs.Ok) {
        Write-Dep "Tesseract languages eng+fra+ara present." "Ok"
        $global:TeyssirTesseractLangsOk = $true
    }
    else {
        $miss = ($langs.Missing -join ",")
        Write-Dep ("Tesseract missing language packs: {0}. Re-run UB Mannheim installer and tick eng, fra, ara. Soft-fail -- OCR continues with installed packs." -f $miss) "Warn"
        $global:TeyssirTesseractLangsOk = $false
    }
}

function Write-LibzbarNote {
    Write-Dep "libzbar (pyzbar): Windows has no official winget package. Bundle libzbar-64.dll on PATH / next to Python, or rely on client BarcodeDetector + digit-OCR fallback. See docs/INSTALL-WINDOWS.md (Phase 2 documents gap; DLL bundling deferred)." "Warn"
    $global:TeyssirLibzbarNote = $true
}

# --- run --------------------------------------------------------------------
Write-Host "==== Teyssir host dependencies ====" -ForegroundColor Cyan
$global:TeyssirPythonReady = $false
$global:TeyssirGitReady = $false
$global:TeyssirNodeReady = $false
$global:TeyssirTesseractReady = $false
$global:TeyssirTesseractLangsOk = $false
$global:TeyssirPythonExe = $null
$global:TeyssirTesseractCmd = $null

Install-GitHost
Install-PythonHost | Out-Null
Install-NodeHost
Install-TesseractHost
Write-LibzbarNote

Write-Host ""
Write-Dep ("Summary -- Python:{0} Git:{1} Node:{2} Tesseract:{3} Langs(eng+fra+ara):{4}" -f `
        $(if ($global:TeyssirPythonReady) { "OK" } else { "MISSING" }), `
        $(if ($global:TeyssirGitReady) { "OK" } else { "optional/miss" }), `
        $(if ($global:TeyssirNodeReady) { "OK" } else { "optional/miss" }), `
        $(if ($global:TeyssirTesseractReady) { "OK" } else { "optional/miss" }), `
        $(if ($global:TeyssirTesseractLangsOk) { "OK" } else { "check" })) "Info"
Write-Host "==== Host dependencies done (Redis intentionally not installed) ====" -ForegroundColor Cyan
