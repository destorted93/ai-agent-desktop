@echo off
REM Clean launcher - runs all services in background, no visible windows
REM Closes everything when you close the widget
REM Note: Make sure OPENAI_API_KEY is set in your environment variables

REM Set launch mode for widget detection
set "WIDGET_LAUNCH_MODE=START_CLEAN"

start /B wscript.exe start_hidden.vbs
exit
