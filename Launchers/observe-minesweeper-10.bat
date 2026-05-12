@echo off
setlocal EnableExtensions

call "%~dp0_observe_env.bat" %1 %2 %3
if errorlevel 1 exit /b %errorlevel%

set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"

set "PY_DIR=%ROOT%\AlienHand\python"
set "APP_ID=ReflectionIT.Minesweeper10_h3qw2m3pefnrp!App"

if not exist "%PY_DIR%\alienhand_ai\cli.py" (
    echo AlienHand Python toolkit was not found at:
    echo   "%PY_DIR%"
    exit /b 1
)

pushd "%PY_DIR%" || exit /b 1
python -m alienhand_ai.cli record-process --output runs --name auto --frames %ALIENHAND_FRAMES% --interval %ALIENHAND_INTERVAL% --title-contains "Minesweeper" --watch-title-only --observe-input %ALIENHAND_CAPTURE_FLAGS% %ALIENHAND_RAW_FLAGS% --terminate-on-finish -- powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process explorer.exe 'shell:AppsFolder\%APP_ID%'; Start-Sleep -Seconds 86400"
set "EXITCODE=%ERRORLEVEL%"
popd

echo.
echo AlienHand Minesweeper 10 observation finished with exit code %EXITCODE%.
pause
exit /b %EXITCODE%
