<#
    Scan the local /24 for an ESC/POS printer on TCP 9100.
    Prints tcp:HOST:9100 or dummy (soft-fail). See deploy/discover_printer.py.

        .\deploy\windows\Discover-Printer.ps1
#>
$ErrorActionPreference = "Continue"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $py = $cmd.Source } else { throw "Python not found. Run install.ps1 first." }
}
& $py (Join-Path $Root "deploy\discover_printer.py") @args
exit $LASTEXITCODE
