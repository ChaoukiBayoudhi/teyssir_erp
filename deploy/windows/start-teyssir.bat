@echo off
REM Start Teyssir in this window IF the Windows service is not already running.
setlocal
cd /d "%~dp0\..\.."

set PORT=8000

sc query TeyssirBackend | findstr /I "RUNNING" >nul 2>&1
if not errorlevel 1 (
  echo Teyssir Backend service is already running.
  echo Opening http://localhost:%PORT%
  if exist "%~dp0open-teyssir.vbs" (
    start "" wscript //nologo "%~dp0open-teyssir.vbs"
  ) else (
    start "" powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0open-teyssir.ps1"
  )
  goto :eof
)

if not exist ".venv\Scripts\waitress-serve.exe" (
  echo [ERROR] Teyssir is not installed yet.
  echo Run:  powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1
  pause
  exit /b 1
)

echo Applying database updates...
".venv\Scripts\python.exe" manage.py migrate --noinput
if errorlevel 1 ( echo [ERROR] Database update failed. & pause & exit /b 1 )

".venv\Scripts\python.exe" manage.py collectstatic --noinput >nul 2>&1

echo.
echo ==============================================================
echo    Teyssir is running in this window.
echo    On THIS PC open:      http://localhost:%PORT%
echo    Prefer the Windows service: Install-WindowsService.ps1
echo    Leave this window open. Close it to STOP Teyssir.
echo ==============================================================
echo.
".venv\Scripts\waitress-serve.exe" --listen=0.0.0.0:%PORT% teyssir.wsgi:application
pause
