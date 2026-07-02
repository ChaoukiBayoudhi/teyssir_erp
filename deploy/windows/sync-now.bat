@echo off
REM ===========================================================
REM  TILL -> HUB sync (push local sales, pull master data).
REM  Run manually, or every few minutes via Windows Task Scheduler.
REM  (Sales are ALWAYS saved locally first; this only reconciles.)
REM ===========================================================
setlocal
cd /d "%~dp0\..\.."
".venv\Scripts\python.exe" manage.py sync_now
if errorlevel 1 (
  echo [WARN] Sync did not complete (hub offline?). Will retry next time.
)
