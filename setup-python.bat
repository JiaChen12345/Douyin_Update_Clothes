@echo off
setlocal
cd /d "%~dp0"
set "APP_PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%APP_PYTHON%" set "APP_PYTHON=python.exe"
"%APP_PYTHON%" -m pip install -r requirements-python.txt
"%APP_PYTHON%" -m playwright install chromium
"%APP_PYTHON%" douyin_automation.py --check
pause
endlocal

