@echo off
setlocal
cd /d "%~dp0"
title RescuePlate - Local Website
where python >nul 2>nul
if errorlevel 1 (
    echo Python is not installed or not available in PATH.
    echo Please install Python 3.10 or later, then try again.
    pause
    exit /b 1
)
python --version
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: Failed to install required Python packages.
    pause
    exit /b 1
)
echo.
python launch.py
if errorlevel 1 (
    echo.
    echo ERROR: Could not start RescuePlate. Please send the error above.
)
echo.
pause
