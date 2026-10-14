@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Blog Reels Maker v3 - setup and start
echo ============================================

rem ---- 1. Python ----
set "PY=py -3"
py -3 --version >nul 2>nul
if errorlevel 1 set "PY=python"
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo.
  echo [!] Python not found. Install Python 3.10+ from https://www.python.org/downloads/
  echo     and CHECK "Add python.exe to PATH" on the first install screen.
  echo.
  pause
  exit /b 1
)
echo [1/2] Python OK

rem ---- 2. Packages (FFmpeg is included, nothing else to install) ----
if not exist ".venv\Scripts\python.exe" (
  echo [2/2] Creating Python environment - first run only ...
  %PY% -m venv .venv
)
echo [2/2] Installing packages - first run takes 1-3 minutes ...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo.
  echo [!] Package install failed. Check the internet connection and run again.
  pause
  exit /b 1
)

rem ---- Start (uses Microsoft Edge already on the PC) ----
echo.
echo Starting app at http://127.0.0.1:8765
echo Keep this window OPEN while using the app.
".venv\Scripts\python.exe" -m reels_app
pause
