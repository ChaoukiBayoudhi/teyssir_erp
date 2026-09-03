<#
    Teyssir -- local LLM (Ollama) helper for Windows Hub/tills.
    Called by install_all.ps1 (explicit Phase 2) and install.ps1. Never throws:
    the ERP must install even if AI setup fails.

    Default: detect Ollama -> winget/silent install if missing -> ensure text model
    (mistral) and vision model (qwen2.5vl:3b). Idempotent: skips pulls when
    already present. Soft-fail on disk/network. -SkipVision / -SkipPull to opt out.

    Usage:
        .\deploy\windows\Install-LocalLlm.ps1 -Model mistral
        .\deploy\windows\Install-LocalLlm.ps1 -Model mistral -SkipVision
        .\deploy\windows\Install-LocalLlm.ps1 -SkipPull
#>
[CmdletBinding()]
param(
    [string]$Model = "mistral",
    [string]$OllamaUrl = "http://127.0.0.1:11434",
    [switch]$SkipPull,
    # Opt out of vision pull (default is auto-pull for bookscan).
    [switch]$SkipVision,
    # Kept for callers/docs that pass -PullVision explicitly (now the default).
    [switch]$PullVision,
    [string]$VisionModel = "qwen2.5vl:3b"
)

$ErrorActionPreference = "Continue"
$script:LlmReady = $false
$script:ModelReady = $false
$script:VisionReady = $false

function Write-Llm([string]$Message, [string]$Color = "Gray") {
    Write-Host "  [LLM] $Message" -ForegroundColor $Color
}

function Refresh-Path {
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Get-OllamaExe {
    Refresh-Path
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"),
        (Join-Path $env:ProgramFiles "Ollama\ollama.exe"),
        "C:\Program Files\Ollama\ollama.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Test-OllamaApi {
    param([int]$TimeoutSec = 3)
    try {
        $r = Invoke-WebRequest -Uri ($OllamaUrl.TrimEnd("/") + "/api/tags") `
            -UseBasicParsing -TimeoutSec $TimeoutSec -ErrorAction Stop
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300)
    }
    catch {
        return $false
    }
}

function Install-OllamaSilent {
    if (Get-OllamaExe) {
        Write-Llm ("Ollama already installed: " + (( & (Get-OllamaExe) --version) 2>&1 | Select-Object -First 1)) "Green"
        return $true
    }
    Write-Llm "Ollama not found -- installing (Windows, silent) ..." "Yellow"
    try {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Write-Llm "Trying winget Ollama.Ollama ..."
            winget install --id Ollama.Ollama -e --accept-package-agreements --accept-source-agreements --disable-interactivity | Out-Host
            Refresh-Path
            if (Get-OllamaExe) { return $true }
        }
    }
    catch {
        Write-Llm ("winget install skipped: " + $_.Exception.Message) "Yellow"
    }
    try {
        $setup = Join-Path $env:TEMP "OllamaSetup.exe"
        Write-Llm "Downloading OllamaSetup.exe ..."
        Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $setup -UseBasicParsing
        Write-Llm "Running silent installer ..."
        $p = Start-Process -FilePath $setup -ArgumentList "/VERYSILENT","/NORESTART","/SUPPRESSMSGBOXES" -Wait -PassThru
        if ($p.ExitCode -ne 0 -and $p.ExitCode -ne $null) {
            Write-Llm ("Installer exit code " + $p.ExitCode) "Yellow"
        }
        Refresh-Path
        Start-Sleep -Seconds 3
        return [bool](Get-OllamaExe)
    }
    catch {
        Write-Llm ("Ollama download/install failed: " + $_.Exception.Message) "Yellow"
        return $false
    }
}

function Start-OllamaService {
    $exe = Get-OllamaExe
    if (-not $exe) { return $false }
    if (Test-OllamaApi) { return $true }

    $svc = Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "ollama" }
    if ($svc) {
        try {
            if ($svc.Status -ne "Running") { Start-Service $svc.Name }
        }
        catch { Write-Llm ("Could not start Windows service: " + $_.Exception.Message) "Yellow" }
        Start-Sleep -Seconds 2
        if (Test-OllamaApi) { return $true }
    }

    Write-Llm "Starting ollama serve in the background ..."
    try {
        Start-Process -FilePath $exe -ArgumentList "serve" -WindowStyle Hidden
    }
    catch {
        Write-Llm ("ollama serve failed: " + $_.Exception.Message) "Yellow"
        return $false
    }
    for ($i = 0; $i -lt 30; $i++) {
        if (Test-OllamaApi) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Test-OllamaModelPresent {
    param([string]$Name)
    if (-not $Name) { return $false }
    $exe = Get-OllamaExe
    if (-not $exe) { return $false }
    try {
        $list = & $exe list 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0 -and -not $list) { return $false }
        # Match name or name:tag prefix (e.g. mistral, mistral:latest, qwen2.5vl:3b)
        $esc = [regex]::Escape($Name)
        return [bool]($list -match ("(?im)^\s*" + $esc + "(\s|:|$)"))
    }
    catch {
        return $false
    }
}

function Pull-OllamaModel {
    param([string]$Name, [string]$Kind = "model")
    if (-not $Name) { return $false }
    if (Test-OllamaModelPresent -Name $Name) {
        Write-Llm ("$Kind '$Name' already present -- skip pull.") "Green"
        return $true
    }
    Write-Llm ("$Kind '$Name' missing -- ollama pull (soft-fail on disk/network) ...") "Yellow"
    try {
        & (Get-OllamaExe) pull $Name
        if ($LASTEXITCODE -eq 0 -or (Test-OllamaModelPresent -Name $Name)) {
            Write-Llm ("$Kind $Name is available.") "Green"
            return $true
        }
        Write-Llm ("ollama pull $Name exited " + $LASTEXITCODE + " -- continuing without it (ERP still runs).") "Yellow"
        return $false
    }
    catch {
        Write-Llm ("$Kind pull skipped: " + $_.Exception.Message + " -- ERP continues without it.") "Yellow"
        return $false
    }
}

# --- main -------------------------------------------------------------------
try {
    if (-not (Install-OllamaSilent)) {
        Write-Llm "Ollama is not available. Teyssir will run without local AI." "Yellow"
    }
    else {
        $ver = & (Get-OllamaExe) --version 2>&1 | Select-Object -First 1
        Write-Llm "ollama --version: $ver" "Green"

        if (Start-OllamaService -and (Test-OllamaApi -TimeoutSec 5)) {
            $script:LlmReady = $true
            Write-Llm ("API ready at " + $OllamaUrl) "Green"
        }
        else {
            Write-Llm "Ollama API did not respond on $OllamaUrl -- ERP continues without AI." "Yellow"
        }

        if ($script:LlmReady -and -not $SkipPull) {
            if ($Model) {
                $script:ModelReady = Pull-OllamaModel -Name $Model -Kind "text model"
            }

            # Phase 15.7: auto-pull vision for bookscan gated fallback (CPU-friendly 3B).
            # -SkipVision opts out; -PullVision is a no-op synonym (pull is already default).
            if ($SkipVision) {
                Write-Llm "Skipping vision model (-SkipVision). Bookscan Vision fallback needs: ollama pull $VisionModel" "Gray"
            }
            elseif ($VisionModel) {
                $null = $PullVision  # accepted for backward-compatible callers
                $script:VisionReady = Pull-OllamaModel -Name $VisionModel -Kind "vision model"
                if (-not $script:VisionReady) {
                    Write-Llm "Vision pull failed -- bookscan keeps Tesseract; retry: Install-LocalLlm.ps1 (no -SkipVision)." "Yellow"
                }
            }

            if ($script:ModelReady) {
                try {
                    $body = @{ model = $Model; prompt = "Reply with the single word: pong"; stream = $false } | ConvertTo-Json
                    $gen = Invoke-RestMethod -Uri ($OllamaUrl.TrimEnd("/") + "/api/generate") `
                        -Method Post -Body $body -ContentType "application/json" -TimeoutSec 120
                    $snippet = [string]$gen.response
                    if ($snippet) {
                        Write-Llm ("Probe response: " + $snippet.Trim().Substring(0, [Math]::Min(80, $snippet.Trim().Length))) "Green"
                    }
                }
                catch {
                    Write-Llm ("Model probe skipped: " + $_.Exception.Message) "Yellow"
                }
            }
        }
    }
}
catch {
    Write-Llm ("LLM setup skipped: " + $_.Exception.Message) "Yellow"
}

# Exported for the caller (install_all.ps1 / install.ps1) -- always set
$global:TeyssirLlmReady = [bool]$script:LlmReady
$global:TeyssirLlmModelReady = [bool]$script:ModelReady
$global:TeyssirLlmModel = $Model
$global:TeyssirVisionModelReady = [bool]$script:VisionReady
$global:TeyssirVisionModel = $VisionModel
Write-Llm ("Summary -- Ollama API:{0} text({1}):{2} vision({3}):{4}" -f `
        $(if ($script:LlmReady) { "OK" } else { "no" }), `
        $Model, $(if ($script:ModelReady) { "OK" } else { "miss/skip" }), `
        $VisionModel, $(if ($SkipVision) { "skipped" } elseif ($script:VisionReady) { "OK" } else { "miss/skip" })) "Gray"