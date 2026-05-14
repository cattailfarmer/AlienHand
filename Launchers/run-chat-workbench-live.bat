@echo off
setlocal EnableExtensions

set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"

set "PY_DIR=%ROOT%\AlienHand\python"
set "THELOUNGE_DIR=%ROOT%\AlienHand\thelounge"
set "RUN_NAME=%~1"
if "%RUN_NAME%"=="" set "RUN_NAME=chat-workbench-live"

set "RUN_DIR=%PY_DIR%\runs\%RUN_NAME%"
set "READY_FILE=%RUN_DIR%\workbench-live-ready.json"
set "STOP_FILE=%RUN_DIR%\stop"

if not exist "%PY_DIR%\alienhand_ai\cli.py" (
    echo AlienHand Python toolkit was not found at:
    echo   "%PY_DIR%"
    exit /b 1
)

if not exist "%THELOUNGE_DIR%\dist\server\index.js" (
    echo The Lounge build output was not found at:
    echo   "%THELOUNGE_DIR%\dist\server\index.js"
    echo.
    echo Build The Lounge first:
    echo   cd /d "%THELOUNGE_DIR%"
    echo   yarn build
    exit /b 1
)

if exist "%READY_FILE%" del "%READY_FILE%"
if exist "%STOP_FILE%" del "%STOP_FILE%"
if not exist "%RUN_DIR%" mkdir "%RUN_DIR%"

echo Starting AlienHand live chat workbench...
echo Run name: %RUN_NAME%
echo.

start "AlienHand Chat Workbench Live" /D "%PY_DIR%" cmd /c python -m alienhand_ai.cli chat-workbench-live --output runs --name "%RUN_NAME%" --ready-file "runs\%RUN_NAME%\workbench-live-ready.json" --stop-file "runs\%RUN_NAME%\stop"

echo Waiting for workbench ready file...
for /L %%I in (1,1,120) do (
    if exist "%READY_FILE%" goto :ready
    timeout /t 1 /nobreak >nul
)

echo Timed out waiting for:
echo   "%READY_FILE%"
echo.
echo If the live process window shows an error, fix that first.
exit /b 1

:ready
for /f "usebackq delims=" %%U in (`powershell -NoProfile -Command "(Get-Content -Raw '%READY_FILE%' | ConvertFrom-Json).thelounge_base_url"`) do set "THELOUNGE_URL=%%U"

echo.
echo AlienHand workbench is ready:
echo   %THELOUNGE_URL%
echo.
echo Ready file:
echo   "%READY_FILE%"
echo.
start "" "%THELOUNGE_URL%"

echo Press any key here to stop the live workbench.
pause >nul

type nul > "%STOP_FILE%"
echo Stop requested:
echo   "%STOP_FILE%"
echo.
echo The live process window should close after shutdown completes.
pause
exit /b 0
