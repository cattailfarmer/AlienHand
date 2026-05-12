@echo off
setlocal EnableExtensions

call "%~dp0_observe_env.bat" %1 %2 %3
if errorlevel 1 exit /b %errorlevel%

set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"

set "PY_DIR=%ROOT%\AlienHand\python"
set "EXE=%ROOT%\build\Space Invaders\Release\SpaceInvaders.exe"
if not exist "%EXE%" set "EXE=%ROOT%\Space Invaders\build\Release\SpaceInvaders.exe"

if not exist "%PY_DIR%\alienhand_ai\cli.py" (
    echo AlienHand Python toolkit was not found at:
    echo   "%PY_DIR%"
    exit /b 1
)

if not exist "%EXE%" (
    echo Space Invaders executable was not found.
    echo Checked:
    echo   "%ROOT%\build\Space Invaders\Release\SpaceInvaders.exe"
    echo   "%ROOT%\Space Invaders\build\Release\SpaceInvaders.exe"
    exit /b 1
)

pushd "%PY_DIR%" || exit /b 1
python -m alienhand_ai.cli record-process --output runs --name auto --frames %ALIENHAND_FRAMES% --interval %ALIENHAND_INTERVAL% --title-contains "AlienHand Space Invaders" --observe-input %ALIENHAND_CAPTURE_FLAGS% %ALIENHAND_RAW_FLAGS% -- "%EXE%"
set "EXITCODE=%ERRORLEVEL%"
popd

echo.
echo AlienHand Space Invaders observation finished with exit code %EXITCODE%.
pause
exit /b %EXITCODE%
