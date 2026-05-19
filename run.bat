@echo off
title AI Agent Desktop
cd /d "%~dp0"

REM Check if Python is available
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Python not found. Please install Python 3.11 or later.
    pause
    exit /b 1
)

REM Mark that we were launched via .bat (used by in-app restart logic if needed)
set WIDGET_LAUNCH_MODE=run

REM Restart loop: exit code 75 means "restart me"
:run
python run.py
set EXITCODE=%ERRORLEVEL%
if "%EXITCODE%"=="75" (
    echo.
    echo [run.bat] Restart requested... relaunching.
    echo.
    goto run
)

exit /b %EXITCODE%
