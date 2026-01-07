@echo off
REM Quick launcher - uses existing OPENAI_API_KEY from environment
REM If not set, it will prompt you

REM Set PYTHONPATH to project root
set "PYTHONPATH=%~dp0;" 

call run_services.bat
