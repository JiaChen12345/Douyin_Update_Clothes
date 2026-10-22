@echo off
setlocal
cd /d "%~dp0"
set "DIFY_BRIDGE_PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%DIFY_BRIDGE_PYTHON%" set "DIFY_BRIDGE_PYTHON=python.exe"
if not exist ".env.dify.local" "%DIFY_BRIDGE_PYTHON%" "run_dify_bridge.py" --init
if errorlevel 1 goto failed
"%DIFY_BRIDGE_PYTHON%" "run_dify_bridge.py" %*
:failed
if errorlevel 1 pause
endlocal
