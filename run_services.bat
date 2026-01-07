@echo off
setlocal ENABLEDELAYEDEXPANSION

REM Usage: run_services.bat [OPENAI_API_KEY]
REM Defaults: uses existing OPENAI_API_KEY
REM Launches: transcribe (6001), agent-main (6002), and widget

REM Set PYTHONPATH to project root
set "PYTHONPATH=%~dp0;"

set TRANSCRIBE_PORT=6001
set AGENT_PORT=6002

set SCRIPT_DIR=%~dp0
set TRANSCRIBE_DIR=%SCRIPT_DIR%transcribe
set AGENT_DIR=%SCRIPT_DIR%agent-main
set WIDGET_DIR=%SCRIPT_DIR%widget

echo.
echo ======================================
echo   Starting AI Agent Services
echo ======================================
echo.
echo [1/3] Starting Transcribe Service on port %TRANSCRIBE_PORT%...
start "Transcribe Service" powershell -NoExit -Command "Set-Location '%TRANSCRIBE_DIR%'; $env:PORT='%TRANSCRIBE_PORT%'; Write-Host 'Transcribe Service - Port %TRANSCRIBE_PORT%' -ForegroundColor Cyan; uvicorn app:app --host 0.0.0.0 --port %TRANSCRIBE_PORT%"

echo [2/3] Starting Agent Service on port %AGENT_PORT%...
timeout /t 2 /nobreak >nul
start "Agent Service" powershell -NoExit -Command "Set-Location '%AGENT_DIR%'; Write-Host 'Agent Service - Port %AGENT_PORT%' -ForegroundColor Green; python app.py --mode service --port %AGENT_PORT%"

echo [3/3] Starting Widget...
timeout /t 3 /nobreak >nul
start "Widget" powershell -NoExit -Command "Set-Location '%WIDGET_DIR%'; $env:TRANSCRIBE_URL='http://127.0.0.1:%TRANSCRIBE_PORT%/upload'; $env:AGENT_URL='http://127.0.0.1:%AGENT_PORT%'; Write-Host 'Widget - Transcribe: %TRANSCRIBE_PORT% / Agent: %AGENT_PORT%' -ForegroundColor Magenta; python widget.py"

echo.
echo ======================================
echo   All Services Launched!
echo ======================================
echo.
echo Services:
echo   - Transcribe Service: http://localhost:%TRANSCRIBE_PORT%
echo   - Agent Service:      http://localhost:%AGENT_PORT%
echo   - Widget:             Desktop application
echo.
echo Press any key to close this window...
pause >nul
endlocal
