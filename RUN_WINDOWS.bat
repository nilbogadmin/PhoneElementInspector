@echo off
title Element Inspector
cd /d "%~dp0"

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo Python not found. Install Python 3.10+ from python.org and check "Add to PATH".
    pause
    exit /b 1
)

if not exist "venv" (
    echo Creating environment...
    python -m venv venv
)

call venv\Scripts\activate.bat
echo Installing / updating packages...
pip install -r requirements.txt -q

echo Starting...
python main.py
if %errorlevel% neq 0 pause
