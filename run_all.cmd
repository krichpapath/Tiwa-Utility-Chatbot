@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Missing virtual environment: .venv\Scripts\python.exe
    pause
    exit /b 1
)
if not exist ".env" (
    echo Missing .env. Configure Tiwa before starting.
    pause
    exit /b 1
)
echo Starting Tiwa bot and dashboard in separate windows.
echo Stop any existing instances before running this launcher again.
start "Tiwa Discord Bot" cmd /k .venv\Scripts\python.exe -X utf8 bot.py
start "Tiwa Dashboard" cmd /k .venv\Scripts\python.exe -X utf8 dashboard.py --open
endlocal
