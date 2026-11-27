@echo off
setlocal
cd /d "%~dp0"
set "task_python=%CD%\.venv\Scripts\python.exe"
if not exist "%task_python%" (
    echo Please create .venv and install config/requirements-sam.txt first. See docs/DEPLOYMENT.md.
    pause
    exit /b 1
)
"%task_python%" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8768/api/captures', timeout=2)" >nul 2>&1
if not errorlevel 1 (
    start "" "http://127.0.0.1:8768"
    exit /b 0
)
"%task_python%" app.py --port 8768 --open
if errorlevel 1 pause
