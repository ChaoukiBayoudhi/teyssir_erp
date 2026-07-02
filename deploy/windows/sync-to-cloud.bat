@echo off
REM ===========================================================
REM  HUB -> CLOUD HUB forwarding (multi-store only).
REM  No effect unless TEYSSIR_CLOUD_HUB_URL is set in .env.
REM  Schedule on the HUB PC (e.g. every 10 minutes).
REM ===========================================================
setlocal
cd /d "%~dp0\..\.."
".venv\Scripts\python.exe" manage.py sync_to_cloud
