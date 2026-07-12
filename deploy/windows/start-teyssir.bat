@echo off
REM ===========================================================
REM  Start the Teyssir server (hub or till, per the .env file).
REM  Keep this window OPEN while the shop is using Teyssir.
REM ===========================================================
setlocal
cd /d "%~dp0\..\.."

REM Port the app is served on. Change here if 8000 is already in use.
set PORT=8000

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
echo    Teyssir is running.
echo    On THIS PC open:      http://localhost:%PORT%
echo    From a till PC use:   http://%COMPUTERNAME%:%PORT%   (or this PC's IP)
echo.
echo    Leave this window open. Close it to STOP Teyssir.
echo ==============================================================
echo.
".venv\Scripts\waitress-serve.exe" --listen=0.0.0.0:%PORT% teyssir.wsgi:application
pause
