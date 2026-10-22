@echo off
setlocal
cd /d "%~dp0"
set "APP_PYTHON=%~dp0..\.venv\Scripts\python.exe"
if exist "%APP_PYTHON%" goto run
where python.exe >nul 2>nul
if errorlevel 1 goto missing
set "APP_PYTHON=python.exe"
:run
if "%~1"=="--check" goto check
"%APP_PYTHON%" "douyin_gui_v49.py"
goto done
:check
"%APP_PYTHON%" "douyin_automation_dual_store_v49.py" --check
goto done
:missing
echo Python 3.11 or newer was not found.
:done
if errorlevel 1 pause
endlocal
