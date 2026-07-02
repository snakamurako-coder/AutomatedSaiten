@echo off
chcp 65001 >nul
title AutomatedSaiten PC

cd /d "%~dp0"

where py >nul 2>&1
if %errorlevel%==0 (
    py -3 main.py
) else (
    python main.py
)

if errorlevel 1 (
    echo.
    echo Failed to start. If packages are missing, run:
    echo   py -3 -m pip install -r requirements.txt
    echo.
    pause
)
