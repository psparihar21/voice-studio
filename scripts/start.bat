@echo off
rem Starts Voice Studio and opens it as an app window. Optional settings (set them before calling this file):
rem   VOICE_STUDIO_PYTHON  python to use (default: python)
rem   VOICE_STUDIO_DATA    folder with your takes and projects (default: %USERPROFILE%\VoiceStudio)
rem   VOICE_STUDIO_PORT    port (default: 8765)
title Voice Studio
setlocal
cd /d "%~dp0.."
if not defined VOICE_STUDIO_PYTHON set "VOICE_STUDIO_PYTHON=python"
if not defined VOICE_STUDIO_PORT set "VOICE_STUDIO_PORT=8765"
set "URL=http://localhost:%VOICE_STUDIO_PORT%"
set "BROWSER=msedge"
reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" >nul 2>&1 && set "BROWSER=chrome"
reg query "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" >nul 2>&1 && set "BROWSER=chrome"
powershell -NoProfile -Command "try{Invoke-WebRequest %URL%/api/info -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0}catch{exit 1}"
if %errorlevel%==0 goto openapp
echo Starting Voice Studio ... keep this window open while you record.
start "" /min cmd /c "ping -n 4 127.0.0.1 >nul & start %BROWSER% --app=%URL% --window-size=1400,900"
"%VOICE_STUDIO_PYTHON%" -m voice_studio serve --port %VOICE_STUDIO_PORT%
pause
exit /b
:openapp
start %BROWSER% --app=%URL% --window-size=1400,900
