@echo off
REM Quick launcher - uses existing OPENAI_API_KEY from environment
REM If not set, it will prompt you

REM Set launch mode for widget detection
set "WIDGET_LAUNCH_MODE=START"

REM Set PYTHONPATH to project root
set "PYTHONPATH=%~dp0;" 

call run_services.bat
