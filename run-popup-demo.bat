@echo off
setlocal
cd /d "%~dp0"
set "POPUP_DEMO_PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%POPUP_DEMO_PYTHON%" set "POPUP_DEMO_PYTHON=python.exe"
"%POPUP_DEMO_PYTHON%" "popup_guard_demo.py"
if errorlevel 1 pause
endlocal
