@echo off
setlocal
set "APP_MODE=local"
cd /d "%~dp0"

set "APP_URL=http://127.0.0.1:8765/"
set "VENV_PYTHON=.venv\Scripts\python.exe"

rem Reuse an already running local instance instead of starting a second worker.
powershell.exe -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>&1
if not errorlevel 1 (
  echo Classmates Explorer is already running. Opening the browser...
  start "" "%APP_URL%"
  exit /b 0
)

if exist "%VENV_PYTHON%" goto dependencies

echo Creating the Python virtual environment...
py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
if not errorlevel 1 (
  py -3.12 -m venv .venv
  if errorlevel 1 goto setup_failed
  goto dependencies
)

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
if errorlevel 1 goto python_missing
python -m venv .venv
if errorlevel 1 goto setup_failed

:dependencies
"%VENV_PYTHON%" -c "import fastapi, uvicorn, httpx, sqlalchemy, jinja2, pydantic_settings" >nul 2>&1
if not errorlevel 1 goto environment

echo Installing dependencies...
"%VENV_PYTHON%" -m pip install -e .
if errorlevel 1 goto setup_failed

:environment
if not exist ".env" (
  copy /Y ".env.example" ".env" >nul
  echo Created .env from .env.example.
)

echo Starting Classmates Explorer at %APP_URL%
echo Close this window or press Ctrl+C to stop the service.
start "" powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process '%APP_URL%'"
"%VENV_PYTHON%" -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --workers 1
set "SERVER_EXIT=%ERRORLEVEL%"
echo.
echo The service stopped with exit code %SERVER_EXIT%.
pause
exit /b %SERVER_EXIT%

:python_missing
echo Python 3.12 or newer was not found. Install it and run this file again.
pause
exit /b 1

:setup_failed
echo Setup failed. Review the message above, then run this file again.
pause
exit /b 1
