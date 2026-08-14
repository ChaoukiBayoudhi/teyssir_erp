<#
    Wait until the local backend answers, then open the PWA in the default browser.
    Used by the "Teyssir ERP" desktop shortcut.
#>
$ErrorActionPreference = "Continue"
$Port = "8000"
$Health = "http://127.0.0.1:$Port/health/"
$App = "http://localhost:$Port/"

for ($i = 0; $i -lt 40; $i++) {
    try {
        $r = Invoke-WebRequest -Uri $Health -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300) { break }
    }
    catch {
        Start-Sleep -Seconds 1
    }
}

Start-Process $App
