@echo off
title AI Agent Desktop
cd /d "%~dp0"

REM Check if Python is available
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Python not found. Please install Python 3.10 or later.
    pause
    exit /b 1
)

REM Run the application
python run.py
