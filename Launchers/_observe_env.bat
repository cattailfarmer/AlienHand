@echo off
setlocal EnableExtensions

set "ALIENHAND_FRAMES=%~1"
if "%ALIENHAND_FRAMES%"=="" set "ALIENHAND_FRAMES=2400"

set "ALIENHAND_INTERVAL=%~2"
if "%ALIENHAND_INTERVAL%"=="" set "ALIENHAND_INTERVAL=0.25"

set "ALIENHAND_MODE=%~3"
if "%ALIENHAND_MODE%"=="" set "ALIENHAND_MODE=light"

set "ALIENHAND_CAPTURE_FLAGS=--no-frame-images"
set "ALIENHAND_RAW_FLAGS="

if /I "%ALIENHAND_MODE%"=="light" goto :done
if /I "%ALIENHAND_MODE%"=="visual" (
    set "ALIENHAND_CAPTURE_FLAGS=--track-objects"
    goto :done
)
if /I "%ALIENHAND_MODE%"=="raw" (
    set "ALIENHAND_RAW_FLAGS=--raw-mouse-motion"
    goto :done
)
if /I "%ALIENHAND_MODE%"=="raw-visual" (
    set "ALIENHAND_CAPTURE_FLAGS=--track-objects"
    set "ALIENHAND_RAW_FLAGS=--raw-mouse-motion"
    goto :done
)

echo Unknown AlienHand observation mode: %ALIENHAND_MODE%
echo.
echo Usage:
echo   launcher.bat [frames] [interval] [light^|visual^|raw^|raw-visual]
echo.
echo Examples:
echo   launcher.bat
echo   launcher.bat 2400 0.25 light
echo   launcher.bat 600 0.1 visual
exit /b 2

:done
endlocal & (
    set "ALIENHAND_FRAMES=%ALIENHAND_FRAMES%"
    set "ALIENHAND_INTERVAL=%ALIENHAND_INTERVAL%"
    set "ALIENHAND_MODE=%ALIENHAND_MODE%"
    set "ALIENHAND_CAPTURE_FLAGS=%ALIENHAND_CAPTURE_FLAGS%"
    set "ALIENHAND_RAW_FLAGS=%ALIENHAND_RAW_FLAGS%"
)
exit /b 0
