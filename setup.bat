@echo off
title AI Agent - Install Dependencies
cd /d "%~dp0"

echo ========================================
echo AI Agent Desktop - Installation
echo ========================================
echo.

REM Check Python
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Python not found. Please install Python 3.10 or later.
    pause
    exit /b 1
)

echo Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Installation failed.
    pause
    exit /b 1
)

echo.
echo ========================================
echo Installation complete!
echo 1. Run the agent:
echo    run.bat
echo ========================================
pause
