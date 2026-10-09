@echo off
REM One-time setup for Windows. Double-click this file.
cd /d "%~dp0"
echo Creating virtual environment...
python -m venv venv || (echo Python not found. Install it from python.org and tick "Add Python to PATH". & pause & exit /b 1)
call venv\Scripts\activate.bat
echo Installing packages...
python -m pip install --upgrade pip
pip install -r requirements.txt || (echo Package install failed. & pause & exit /b 1)
echo Installing browser...
python -m playwright install chromium
if not exist .env copy .env.example .env
echo.
echo Setup finished.
echo NEXT: open ".env" in Notepad and add your Gemini key and Telegram details,
echo       put your CV in documents\cv.pdf, then double-click run.bat
pause
