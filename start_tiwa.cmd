@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Tiwa's Python environment is missing. Run setup.ps1 first.
  pause
  exit /b 1
)
if /i "%~1"=="dashboard" (
  ".venv\Scripts\python.exe" -u -X utf8 dashboard.py --open
) else (
  ".venv\Scripts\python.exe" -u -X utf8 bot.py
)
set "tiwa_exit=%errorlevel%"
if not "%tiwa_exit%"=="0" (
  echo.
  echo Tiwa exited with code %tiwa_exit%. Keep the complete error above for diagnosis.
  pause
)
exit /b %tiwa_exit%
