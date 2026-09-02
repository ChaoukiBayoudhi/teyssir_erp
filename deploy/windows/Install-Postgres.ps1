<#
    Teyssir -- PostgreSQL helper for the Windows HUB only.
    Never throws: if setup fails, the caller falls back to SQLite.

    Usage:
        .\deploy\windows\Install-Postgres.ps1 -DatabaseName teyssir -User teyssir -Password <generated>

    Note: do NOT use a parameter named -Db (or Alias Db). With [CmdletBinding()] it
    conflicts with the common -Debug parameter on Windows PowerShell 5.1.
#>
[CmdletBinding()]
param(
    # Application database name (was -Db; renamed to avoid -Debug conflict).
    [Alias('Database')]
    [string]$DatabaseName = "teyssir",
    [string]$User = "teyssir",
    [string]$Password = "",
    [string]$Port = "5432",
    [string]$SuperUser = "postgres",
    [string]$SuperPassword = "",
    [string]$HostName = "127.0.0.1",
    # Drop and recreate the app database (used by Clean-PreviousInstall.ps1 -FreshInstall).
    [switch]$ResetDatabase
)

$ErrorActionPreference = "Continue"
$script:Ready = $false
$global:TeyssirPostgresReady = $false

function Write-Pg([string]$Message, [string]$Color = "Gray") {
    Write-Host "  [PG] $Message" -ForegroundColor $Color
}

function Refresh-Path {
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Get-PsqlExe {
    Refresh-Path
    $cmd = Get-Command psql -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($ver in @("17", "16", "15", "14")) {
        $p = "C:\Program Files\PostgreSQL\$ver\bin\psql.exe"
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Install-PostgresSilent {
    if (Get-PsqlExe) {
        Write-Pg ("psql found: " + (( & (Get-PsqlExe) --version) 2>&1 | Select-Object -First 1)) "Green"
        return $true
    }
    Write-Pg "PostgreSQL not found -- installing (Windows, silent) ..." "Yellow"
    if (-not $SuperPassword) {
        $alphabet = [char[]]((48..57) + (65..90) + (97..122))
        $SuperPassword = -join (1..24 | ForEach-Object { $alphabet | Get-Random })
        $script:GeneratedSuper = $SuperPassword
    }
    try {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Write-Pg "Trying winget PostgreSQL.PostgreSQL.16 ..."
            $override = "--mode unattended --unattendedmodeui none --superpassword `"$SuperPassword`" --serverport $Port --disable-components stackbuilder"
            winget install --id PostgreSQL.PostgreSQL.16 -e --accept-package-agreements --accept-source-agreements --disable-interactivity --override $override | Out-Host
            Refresh-Path
            Start-Sleep -Seconds 5
            if (Get-PsqlExe) { return $true }
        }
    }
    catch {
        Write-Pg ("winget install skipped: " + $_.Exception.Message) "Yellow"
    }
    Write-Pg "PostgreSQL installer did not complete. Hub can still use SQLite." "Yellow"
    return $false
}

function Start-PostgresService {
    $svc = Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -match "postgresql" }
    if ($svc) {
        try {
            if ($svc.Status -ne "Running") { Start-Service $svc.Name }
            Write-Pg ("Service " + $svc.Name + " is " + (Get-Service $svc.Name).Status)
            return $true
        }
        catch {
            Write-Pg ("Could not start service: " + $_.Exception.Message) "Yellow"
        }
    }
    return [bool](Get-PsqlExe)
}

function Invoke-PsqlAdmin([string]$Sql) {
    $psql = Get-PsqlExe
    if (-not $psql) { throw "psql not found" }
    $env:PGPASSWORD = $SuperPassword
    $out = & $psql -U $SuperUser -h $HostName -p $Port -d postgres -v ON_ERROR_STOP=1 -c $Sql 2>&1
    $code = $LASTEXITCODE
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    if ($code -ne 0) { throw ($out | Out-String) }
    return $out
}

function Test-AppLogin {
    $psql = Get-PsqlExe
    if (-not $psql -or -not $Password) { return $false }
    $env:PGPASSWORD = $Password
    & $psql -U $User -h $HostName -p $Port -d $DatabaseName -v ON_ERROR_STOP=1 -c "SELECT 1" 2>&1 | Out-Null
    $ok = ($LASTEXITCODE -eq 0)
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    return $ok
}

function Remove-TeyssirDatabase {
    $safeDb = $DatabaseName.Replace("'", "''")
    Write-Pg ("Terminating connections to database $DatabaseName ...") "Yellow"
    Invoke-PsqlAdmin @"
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = '$safeDb' AND pid <> pg_backend_pid();
"@
    Write-Pg ("Dropping database $DatabaseName ...") "Yellow"
    Invoke-PsqlAdmin "DROP DATABASE IF EXISTS $safeDb;"
    Write-Pg "Database $DatabaseName dropped." "Green"
}

function New-TeyssirDatabase {
    if (-not $Password) { throw "POSTGRES_PASSWORD is empty" }
    $safePass = $Password.Replace("'", "''")
    $safeUser = $User.Replace("'", "''")
    $safeDb = $DatabaseName.Replace("'", "''")
    Write-Pg "Creating role and database (UTF-8) ..."
    Invoke-PsqlAdmin @"
DO `$`$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$safeUser') THEN
    CREATE ROLE $safeUser LOGIN PASSWORD '$safePass';
  ELSE
    ALTER ROLE $safeUser WITH LOGIN PASSWORD '$safePass';
  END IF;
END
`$`$;
"@
    $exists = Invoke-PsqlAdmin "SELECT 1 FROM pg_database WHERE datname = '$safeDb';"
    if ($exists -notmatch "1") {
        Invoke-PsqlAdmin "CREATE DATABASE $safeDb OWNER $safeUser ENCODING 'UTF8' TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C';"
    }
    Invoke-PsqlAdmin "GRANT ALL PRIVILEGES ON DATABASE $safeDb TO $safeUser;"
    Write-Pg "Database $DatabaseName ready (owner $User)." "Green"
}

try {
    if ($ResetDatabase) {
        if (-not (Install-PostgresSilent)) { return }
        Start-PostgresService | Out-Null
        if ($script:GeneratedSuper -and -not $SuperPassword) { $SuperPassword = $script:GeneratedSuper }
        if (-not $SuperPassword) {
            $SuperPassword = $env:POSTGRES_ADMIN_PASSWORD
        }
        if (-not $SuperPassword) {
            Write-Pg "ResetDatabase: superuser password required (-SuperPassword or POSTGRES_ADMIN_PASSWORD)." "Yellow"
            return
        }
        Remove-TeyssirDatabase
        New-TeyssirDatabase
        if (Test-AppLogin) {
            $script:Ready = $true
        }
        else {
            Write-Pg "Database recreated but login as $User failed." "Yellow"
        }
    }
    elseif (Test-AppLogin) {
        Write-Pg "Existing database is reachable as $User -- skipping create (re-run safe)." "Green"
        $script:Ready = $true
    }
    else {
        if (-not (Install-PostgresSilent)) { return }
        Start-PostgresService | Out-Null
        if ($script:GeneratedSuper -and -not $SuperPassword) { $SuperPassword = $script:GeneratedSuper }
        if (-not $SuperPassword) {
            Write-Pg "No superuser password (pass -SuperPassword or POSTGRES_ADMIN_PASSWORD). Cannot create the app role." "Yellow"
            Write-Pg "If PostgreSQL is already installed, re-run with POSTGRES_ADMIN_PASSWORD set, or use -SkipPostgres for SQLite." "Yellow"
            return
        }
        New-TeyssirDatabase
        if (Test-AppLogin) {
            $script:Ready = $true
        }
        else {
            Write-Pg "Role/database created but login as $User failed -- hub will use SQLite." "Yellow"
        }
    }
}
catch {
    Write-Pg ("PostgreSQL setup skipped: " + $_.Exception.Message) "Yellow"
}

$global:TeyssirPostgresReady = [bool]$script:Ready
if ($script:GeneratedSuper) {
    $global:TeyssirPostgresSuperPassword = $script:GeneratedSuper
}
