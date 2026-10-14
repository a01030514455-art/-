@echo off
chcp 65001 >nul
cd /d %~dp0
where ffmpeg >nul 2>nul || (echo [필요] FFmpeg가 없습니다. 관리자 PowerShell에서: winget install --id Gyan.FFmpeg & pause & exit /b 1)
if not exist .venv (
  py -3 -m venv .venv || (echo [필요] Python 3.10 이상을 설치하세요: https://www.python.org/downloads/ & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install -q -r requirements.txt
rem 브라우저는 PC에 있는 Microsoft Edge를 그대로 사용합니다(추가 다운로드 없음).
python -m reels_app
pause
