@echo off
REM Starts the AI agents in autonomous mode. Leave this window open (minimise it).
cd /d "%~dp0"
call venv\Scripts\activate.bat
title AI Course Agents - running
python main.py auto
pause
