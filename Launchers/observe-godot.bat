@echo off
setlocal EnableExtensions

call "%~dp0_observe_env.bat" %1 %2 %3
if errorlevel 1 exit /b %errorlevel%

set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"

set "PY_DIR=%ROOT%\AlienHand\python"
set "GODOT_EXE=C:\Project\Codex_Projects\LLMOS-Compiler\bin\godot.windows.editor.dev.x86_64.exe"

if not exist "%PY_DIR%\alienhand_ai\cli.py" (
    echo AlienHand Python toolkit was not found at:
    echo   "%PY_DIR%"
    exit /b 1
)

if not exist "%GODOT_EXE%" (
    echo Godot executable was not found at:
    echo   "%GODOT_EXE%"
    echo Build or update the Godot fork first, then run this launcher again.
    exit /b 1
)

pushd "%PY_DIR%" || exit /b 1
python -m alienhand_ai.cli record-process --output runs --name auto --frames %ALIENHAND_FRAMES% --interval %ALIENHAND_INTERVAL% --title-contains "Godot" --watch-title-only --observe-input %ALIENHAND_CAPTURE_FLAGS% %ALIENHAND_RAW_FLAGS% -- "%GODOT_EXE%"
set "EXITCODE=%ERRORLEVEL%"
popd

echo.
echo AlienHand Godot observation finished with exit code %EXITCODE%.
pause
exit /b %EXITCODE%
