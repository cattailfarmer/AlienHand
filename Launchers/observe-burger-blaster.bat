@echo off
setlocal EnableExtensions

call "%~dp0_observe_env.bat" %1 %2 %3
if errorlevel 1 exit /b %errorlevel%

set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"

set "PY_DIR=%ROOT%\AlienHand\python"
set "EXE=%ROOT%\build\BurgerBlaster\Release\BurgerBlaster.exe"
if not exist "%EXE%" set "EXE=%ROOT%\BurgerBlaster\build\Release\BurgerBlaster.exe"

if not exist "%PY_DIR%\alienhand_ai\cli.py" (
    echo AlienHand Python toolkit was not found at:
    echo   "%PY_DIR%"
    exit /b 1
)

if not exist "%EXE%" (
    echo Burger Blaster executable was not found.
    echo Checked:
    echo   "%ROOT%\build\BurgerBlaster\Release\BurgerBlaster.exe"
    echo   "%ROOT%\BurgerBlaster\build\Release\BurgerBlaster.exe"
    exit /b 1
)

pushd "%PY_DIR%" || exit /b 1
python -m alienhand_ai.cli record-process --output runs --name auto --frames %ALIENHAND_FRAMES% --interval %ALIENHAND_INTERVAL% --title-contains "Burger Blaster" --observe-input %ALIENHAND_CAPTURE_FLAGS% %ALIENHAND_RAW_FLAGS% -- "%EXE%"
set "EXITCODE=%ERRORLEVEL%"
popd

echo.
echo AlienHand Burger Blaster observation finished with exit code %EXITCODE%.
pause
exit /b %EXITCODE%
