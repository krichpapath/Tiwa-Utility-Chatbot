@echo off
rem Tiwa 24/7 — restarts on crash. Put a shortcut to this in shell:startup.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. Run setup.ps1 first.
  exit /b 1
)
:loop
".venv\Scripts\python.exe" -X utf8 bot.py
if not errorlevel 1 exit /b 0
timeout /t 5 /nobreak
goto loop
