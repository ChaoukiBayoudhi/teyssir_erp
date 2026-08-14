<#
    Teyssir — local LLM (Ollama) helper for Windows Hub/tills.
    Called by install.ps1. Never throws: the ERP must install even if AI setup fails.

    Usage:
        .\deploy\windows\Install-LocalLlm.ps1 -Model mistral
        .\deploy\windows\Install-LocalLlm.ps1 -SkipPull
#>
[CmdletBinding()]
param(
    [string]$Model = "mistral",
    [string]$OllamaUrl = "http://127.0.0.1:11434",
    [switch]$SkipPull,
    [switch]$PullVision,
    [string]$VisionModel = "qwen2.5vl:3b"
)

$ErrorActionPreference = "Continue"
$script:LlmReady = $false
$script:ModelReady = $false

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
    Write-Llm "Ollama not found — installing (Windows, silent) ..." "Yellow"
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

# --- main (install + start only; pull is a separate step in later revisions) ---
try {
    if (-not (Install-OllamaSilent)) {
        Write-Llm "Ollama is not available. Teyssir will run without local AI." "Yellow"
        return
    }
    $ver = & (Get-OllamaExe) --version 2>&1 | Select-Object -First 1
    Write-Llm "ollama --version: $ver" "Green"

    if (Start-OllamaService -and (Test-OllamaApi -TimeoutSec 5)) {
        $script:LlmReady = $true
        Write-Llm ("API ready at " + $OllamaUrl) "Green"
    }
    else {
        Write-Llm "Ollama API did not respond on $OllamaUrl — ERP continues without AI." "Yellow"
    }

    if ($script:LlmReady -and -not $SkipPull -and $Model) {
        Write-Llm ("Ensuring model '$Model' (ollama pull) ...") "Yellow"
        try {
            & (Get-OllamaExe) pull $Model
            if ($LASTEXITCODE -eq 0) {
                $script:ModelReady = $true
                Write-Llm "Model $Model is available." "Green"
            }
            else {
                Write-Llm ("ollama pull $Model exited " + $LASTEXITCODE + " — continuing without it.") "Yellow"
            }
        }
        catch {
            Write-Llm ("Model pull skipped: " + $_.Exception.Message) "Yellow"
        }
        if ($PullVision -and $VisionModel) {
            Write-Llm ("Optional vision model '$VisionModel' ...") "Yellow"
            try { & (Get-OllamaExe) pull $VisionModel } catch { Write-Llm $_.Exception.Message "Yellow" }
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
catch {
    Write-Llm ("LLM setup skipped: " + $_.Exception.Message) "Yellow"
}

# Exported for the caller (install.ps1)
$global:TeyssirLlmReady = [bool]$script:LlmReady
$global:TeyssirLlmModelReady = [bool]$script:ModelReady
$global:TeyssirLlmModel = $Model
