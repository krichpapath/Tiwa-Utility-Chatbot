@echo off
rem Tiwa 24/7 — restarts on crash. Put a shortcut to this in shell:startup.
cd /d "%~dp0"
:loop
py -X utf8 bot.py
timeout /t 5 /nobreak
goto loop
