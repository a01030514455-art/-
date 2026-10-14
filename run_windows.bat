@echo off
setlocal
cd /d "%~dp0"
set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Links"

echo ============================================
echo   Blog Reels Maker - setup and start
echo ============================================

rem ---- 1. FFmpeg (free) ----
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [1/3] FFmpeg not found. Installing it with winget ...
  winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo.
  echo [!] FFmpeg is still not available.
  echo     1. Close this window and run run_windows.bat again.
  echo     2. If it still fails, restart the PC and try again.
  echo.
  pause
  exit /b 1
)
echo [1/3] FFmpeg OK

rem ---- 2. Python ----
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
if not exist ".venv\Scripts\python.exe" (
  echo [2/3] Creating Python environment - first run only ...
  %PY% -m venv .venv
)
echo [2/3] Installing packages - first run takes 1-2 minutes ...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo.
  echo [!] Package install failed. Check the internet connection and run again.
  pause
  exit /b 1
)

rem ---- 3. Start (uses Microsoft Edge already on the PC, no browser download) ----
echo [3/3] Starting app at http://127.0.0.1:8765  - keep this window OPEN while using the app.
".venv\Scripts\python.exe" -m reels_app
pause
