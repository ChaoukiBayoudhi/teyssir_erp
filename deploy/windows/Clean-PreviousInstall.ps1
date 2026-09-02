<#
    Teyssir — remove a previous Windows installation before a clean re-install.

    Destructive: database, .env, optional .venv. Service, tasks, and shortcuts are removed.
    Does NOT delete the project folder, media\, or manual backups.

    Requires an explicit opt-in flag (never runs by accident):

        .\deploy\windows\Clean-PreviousInstall.ps1 -FreshInstall -Role hub
        .\deploy\windows\Clean-PreviousInstall.ps1 -ConfirmWipeData -Role till -Terminal C1

    Prefer the integrated path (any common entrypoint forwards -FreshInstall):

        .\deploy\windows\install_all.ps1 -Role hub -FreshInstall
        .\deploy\windows\setup_app.ps1 -Role hub -FreshInstall
        .\deploy\windows\setup_caisse_C1.ps1 -FreshInstall -HubUrl … -SyncKey …

    Logs: %LOCALAPPDATA%\Teyssir\logs\clean_previous_<timestamp>.log
#>
[CmdletBinding()]
param(
    [ValidateSet("hub", "till")] [string]$Role = "till",
    [string]$Terminal = "C1",
    [switch]$FreshInstall,
    [switch]$ConfirmWipeData,
    [switch]$RemoveVenv,
    [string]$ServiceName = "TeyssirBackend",
    [string]$PostgresSuperPassword = ""
)

$ErrorActionPreference = "Continue"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root

function Get-DotEnvValue([string]$Path, [string]$Key) {
    if (-not (Test-Path $Path)) { return $null }
    $line = [System.IO.File]::ReadAllLines($Path) |
        Where-Object { $_ -match ("^\s*" + [regex]::Escape($Key) + "=") } |
        Select-Object -First 1
    if (-not $line) { return $null }
    return $line.Substring($line.IndexOf("=") + 1).Trim()
}

function Initialize-CleanLog {
    $logDir = Join-Path $env:LOCALAPPDATA "Teyssir\logs"
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $logPath = Join-Path $logDir ("clean_previous_{0}.log" -f $stamp)
    $script:CleanLogPath = $logPath
    return $logPath
}

function Write-CleanLog([string]$Message, [string]$Color = "Gray") {
    Write-Host $Message -ForegroundColor $Color
    if ($script:CleanLogPath) {
        try {
            Add-Content -Path $script:CleanLogPath `
                -Value ("[{0}] {1}" -f (Get-Date -Format "o"), $Message) `
                -ErrorAction SilentlyContinue
        }
        catch { }
    }
}

function Remove-ItemIfExists([string]$Path, [string]$Label) {
    if (-not $Path) { return }
    if (Test-Path $Path) {
        try {
            Remove-Item -LiteralPath $Path -Force -Recurse -ErrorAction Stop
            Write-CleanLog ("  Supprimé : {0} ({1})" -f $Label, $Path) "Yellow"
        }
        catch {
            Write-CleanLog ("  Échec suppression {0} ({1}) : {2}" -f $Label, $Path, $_.Exception.Message) "Red"
        }
    }
    else {
        Write-CleanLog ("  Absent (OK) : {0}" -f $Label) "DarkGray"
    }
}

# --- safety gate -------------------------------------------------------------
if (-not $FreshInstall -and -not $ConfirmWipeData) {
    Write-Host @"

ERREUR — opération annulée.

Cette commande efface la base de données et le fichier .env.
Relancez avec l'un de ces indicateurs explicites :

  -FreshInstall          (recommandé via install_all.ps1)
  -ConfirmWipeData       (appel direct à Clean-PreviousInstall.ps1)

Exemple :
  .\deploy\windows\install_all.ps1 -Role hub -FreshInstall

"@ -ForegroundColor Red
    exit 2
}

$logPath = Initialize-CleanLog
Write-CleanLog "==== Teyssir Clean-PreviousInstall (role: $Role) ====" "Yellow"
Write-CleanLog ("Project: {0}" -f $Root)
Write-CleanLog ("Log:     {0}" -f $logPath)

$venvWarnLine = if ($RemoveVenv) {
    "║    • L'environnement virtuel Python (.venv)`n"
}
else {
    ""
}

Write-CleanLog (@"

╔══════════════════════════════════════════════════════════════════════════════╗
║  ATTENTION — INSTALLATION PROPRE (effacement des données Teyssir)            ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Cette opération va SUPPRIMER :                                              ║
║    • Le service Windows TeyssirBackend et les tâches planifiées              ║
║    • Les raccourcis Bureau / Menu Démarrer « Teyssir ERP »                   ║
║    • La base de données (PostgreSQL « teyssir » ou fichiers SQLite)          ║
║    • Le fichier .env (copie de sauvegarde .env.bak.<horodatage> créée)       ║
$venvWarnLine║  CONSERVÉ (non supprimé) :                                                   ║
║    • Dossier du projet, code source, frontend\dist                           ║
║    • Dossier media\ (images livres, fichiers uploadés)                       ║
║    • Sauvegardes manuelles (.sql, copies SQLite) ailleurs sur le disque      ║
║                                                                              ║
║  Un autre dossier Teyssir ailleurs sur le PC n'est PAS touché.               ║
╚══════════════════════════════════════════════════════════════════════════════╝

"@) "Red"

$envPath = Join-Path $Root ".env"
$envRole = $Role
$envTerminal = $Terminal
$envDbBackend = if ($Role -eq "hub") { "postgres" } else { "sqlite" }

if (Test-Path $envPath) {
    $readRole = Get-DotEnvValue $envPath "TEYSSIR_ROLE"
    if ($readRole) { $envRole = $readRole.Trim().ToLowerInvariant() }
    $readTerm = Get-DotEnvValue $envPath "TEYSSIR_TERMINAL"
    if ($readTerm) { $envTerminal = $readTerm.Trim() }
    $readDb = Get-DotEnvValue $envPath "TEYSSIR_DB"
    if ($readDb) { $envDbBackend = $readDb.Trim().ToLowerInvariant() }
    Write-CleanLog ("Configuration lue depuis .env : role=$envRole db=$envDbBackend terminal=$envTerminal")
}
else {
    Write-CleanLog ".env absent — nettoyage des emplacements par défaut uniquement." "DarkGray"
}

# --- 1) service, tasks, shortcuts (reuse uninstall.ps1) ----------------------
Write-CleanLog "Étape 1/4 — service, tâches planifiées, raccourcis ..." "Cyan"
$uninstallScript = Join-Path $PSScriptRoot "uninstall.ps1"
if (Test-Path $uninstallScript) {
    try {
        & $uninstallScript -ServiceName $ServiceName 2>&1 | ForEach-Object { Write-CleanLog $_ }
    }
    catch {
        Write-CleanLog ("uninstall.ps1 avertissement : {0}" -f $_.Exception.Message) "Yellow"
    }
}
else {
    Write-CleanLog "uninstall.ps1 introuvable — étape ignorée." "Yellow"
}

# --- 2) database wipe --------------------------------------------------------
Write-CleanLog "Étape 2/4 — base de données ..." "Cyan"

function Get-SqliteCandidates {
    param([string]$ForRole, [string]$ForTerminal)
    $names = New-Object System.Collections.Generic.List[string]
    if ($ForRole -eq "hub") {
        $names.Add("teyssir_hub.sqlite3") | Out-Null
        $names.Add("db.sqlite3") | Out-Null
    }
    else {
        $names.Add(("teyssir_{0}.sqlite3" -f $ForTerminal)) | Out-Null
        $names.Add("db.sqlite3") | Out-Null
    }
    if (Test-Path $envPath) {
        $custom = Get-DotEnvValue $envPath "TEYSSIR_SQLITE_NAME"
        if ($custom) { $names.Add($custom.Trim()) | Out-Null }
    }
    return @($names | Select-Object -Unique)
}

$pgResetOk = $false
if ($envRole -eq "hub" -and $envDbBackend -match "^(postgres|postgresql|pg)$") {
    $pgDb = Get-DotEnvValue $envPath "POSTGRES_DB"
    if (-not $pgDb) { $pgDb = "teyssir" }
    $pgUser = Get-DotEnvValue $envPath "POSTGRES_USER"
    if (-not $pgUser) { $pgUser = "teyssir" }
    $pgPass = Get-DotEnvValue $envPath "POSTGRES_PASSWORD"
    $superPass = $PostgresSuperPassword
    if (-not $superPass) { $superPass = $env:POSTGRES_ADMIN_PASSWORD }

    $pgScript = Join-Path $PSScriptRoot "Install-Postgres.ps1"
    if (-not $superPass) {
        Write-CleanLog "  PostgreSQL : mot de passe superuser absent (POSTGRES_ADMIN_PASSWORD ou -PostgresSuperPassword)." "Yellow"
        Write-CleanLog "  Tentative de DROP via rôle applicatif ou fichiers SQLite de repli ..." "Yellow"
    }
    elseif (Test-Path $pgScript) {
        Write-CleanLog ("  PostgreSQL : DROP + recréation base « {0} » ..." -f $pgDb) "Yellow"
        try {
            & $pgScript -Db $pgDb -User $pgUser -Password $pgPass -SuperPassword $superPass -ResetDatabase
            if ($global:TeyssirPostgresReady) {
                $pgResetOk = $true
                Write-CleanLog "  PostgreSQL : base réinitialisée (vide, prête pour migrate)." "Green"
            }
            else {
                Write-CleanLog "  PostgreSQL : reset incomplet — vérifiez le journal [PG]." "Yellow"
            }
        }
        catch {
            Write-CleanLog ("  PostgreSQL reset échec : {0}" -f $_.Exception.Message) "Yellow"
        }
    }
}

foreach ($sqliteName in (Get-SqliteCandidates -ForRole $envRole -ForTerminal $envTerminal)) {
    $sqlitePath = $sqliteName
    if (-not [System.IO.Path]::IsPathRooted($sqlitePath)) {
        $sqlitePath = Join-Path $Root $sqliteName
    }
    Remove-ItemIfExists $sqlitePath ("SQLite " + $sqliteName)
    # SQLite WAL/SHM sidecars
    Remove-ItemIfExists ($sqlitePath + "-wal") ("SQLite WAL " + $sqliteName)
    Remove-ItemIfExists ($sqlitePath + "-shm") ("SQLite SHM " + $sqliteName)
}

if ($envRole -eq "hub" -and -not $pgResetOk) {
    foreach ($sqliteName in @("teyssir_hub.sqlite3", "db.sqlite3")) {
        $sqlitePath = Join-Path $Root $sqliteName
        Remove-ItemIfExists $sqlitePath ("SQLite hub fallback " + $sqliteName)
        Remove-ItemIfExists ($sqlitePath + "-wal") "SQLite WAL"
        Remove-ItemIfExists ($sqlitePath + "-shm") "SQLite SHM"
    }
}

# --- 3) .env backup + remove -------------------------------------------------
Write-CleanLog "Étape 3/4 — fichier .env ..." "Cyan"
if (Test-Path $envPath) {
    $bakStamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $bakPath = Join-Path $Root (".env.bak." + $bakStamp)
    try {
        Copy-Item -LiteralPath $envPath -Destination $bakPath -Force
        Write-CleanLog ("  Sauvegarde .env → {0}" -f $bakPath) "Green"
        Remove-Item -LiteralPath $envPath -Force
        Write-CleanLog "  .env supprimé (sera régénéré par install.ps1)." "Yellow"
    }
    catch {
        Write-CleanLog ("  Échec backup/suppression .env : {0}" -f $_.Exception.Message) "Red"
    }
}
else {
    Write-CleanLog "  .env déjà absent (OK)." "DarkGray"
}

# --- 4) optional venv --------------------------------------------------------
Write-CleanLog "Étape 4/4 — environnement Python ..." "Cyan"
if ($RemoveVenv) {
    Remove-ItemIfExists (Join-Path $Root ".venv") ".venv"
    Remove-ItemIfExists (Join-Path $Root "venv") "venv"
}
else {
    Write-CleanLog "  .venv conservé (ajoutez -RemoveVenv pour réinstaller pip from scratch)." "DarkGray"
}

Write-CleanLog ""
Write-CleanLog "==== Nettoyage terminé ====" "Green"
Write-CleanLog "Lancez maintenant l'installation :"
Write-CleanLog ("  .\deploy\windows\install_all.ps1 -Role {0}{1}" -f $Role, $(if ($Role -eq "till") { " -Terminal $Terminal" } else { "" })) "Cyan"
Write-CleanLog ("Journal : {0}" -f $logPath)
exit 0
