@echo off
setlocal
title CV Tailor
cd /d "%~dp0"
set URL=http://127.0.0.1:8765/

rem Already running? Just open the page.
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing %URL%api/state -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 (
  start "" "%URL%"
  exit /b 0
)

rem First run: create the environment and install what the app needs.
if not exist ".venv\Scripts\python.exe" (
  echo Setting up CV Tailor for the first time. This takes a minute...
  python -m venv .venv || goto :fail
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt || goto :fail
)

rem Open the browser once the server answers, then keep the server in this window.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for ($i=0; $i -lt 40; $i++) { try { Invoke-WebRequest -UseBasicParsing %URL%api/state -TimeoutSec 1 | Out-Null; Start-Process '%URL%'; break } catch { Start-Sleep -Milliseconds 500 } }"
echo CV Tailor is running at %URL%
echo Keep this window open while you use it. Close it to stop the app.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8765
exit /b %errorlevel%

:fail
echo.
echo Setup failed. Check that Python 3.12 is installed and on PATH, then run start.bat again.
pause
exit /b 1
