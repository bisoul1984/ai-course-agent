@echo off
REM Menu for the agents on Windows. Double-click this file.
cd /d "%~dp0"
call venv\Scripts\activate.bat
:menu
echo.
echo ===== AI Course Application Agents =====
echo  1. Check my setup
echo  2. Sign in to Google (once)
echo  3. START THE AGENTS (run 24/7 by themselves)
echo  4. Run one full cycle now, then stop
echo  5. Show the profile the agents will use
echo  6. Show tracked courses
echo  7. Answer the agents' questions here (if no Telegram)
echo  8. Export list to Excel (courses.csv)
echo  0. Exit
set /p choice=Choose: 
if "%choice%"=="1" python main.py check
if "%choice%"=="2" python main.py login
if "%choice%"=="3" python main.py auto
if "%choice%"=="4" python main.py run
if "%choice%"=="5" python main.py profile
if "%choice%"=="6" python main.py list
if "%choice%"=="7" python main.py answer
if "%choice%"=="8" python main.py export
if "%choice%"=="0" exit /b
goto menu
