@echo off
title Voice Studio
cd /d "%~dp0"
set PY=python
if not "%VOICE_STUDIO_PY_RUN%"=="" set PY=%VOICE_STUDIO_PY_RUN%
powershell -NoProfile -Command "try{Invoke-WebRequest http://127.0.0.1:8765/api/state -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0}catch{exit 1}"
if %errorlevel%==0 goto openapp
echo Starting Voice Studio ... keep this window open while you record.
start "" /min cmd /c "ping -n 4 127.0.0.1 >nul & start chrome --app=http://localhost:8765 --window-size=1400,900"
%PY% server.py 8765
pause
exit /b
:openapp
start chrome --app=http://localhost:8765 --window-size=1400,900
