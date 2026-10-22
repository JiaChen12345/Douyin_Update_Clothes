@echo off
setlocal
cd /d "%~dp0"
set "CODEX_NODE=C:\Users\14158\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
if exist "%CODEX_NODE%" goto run
where node.exe >nul 2>nul
if errorlevel 1 goto missing
set "CODEX_NODE=node.exe"
:run
if not exist "node_modules\playwright-core" goto dependencies
if "%~1"=="--check" goto checkok
"%CODEX_NODE%" "src\index.js" --login
goto done
:checkok
echo SETUP_LOGIN_OK
goto end
:dependencies
echo Dependencies are missing. Run npm install in this folder.
goto done
:missing
echo Node.js 20 or newer was not found.
:done
pause
:end
endlocal
