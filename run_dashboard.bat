@echo off
rem Starts the battery-sandbox dashboard. Double-click this file, or run it from a terminal.
rem On first run it creates the .venv and installs dependencies (a few minutes).

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3.13 -m venv .venv || python -m venv .venv
    if errorlevel 1 (
        echo Could not create a virtual environment. Install Python 3.10+ from python.org and try again.
        pause
        exit /b 1
    )
    echo Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -e ".[dev]"
    if errorlevel 1 (
        echo Dependency install failed; see the messages above.
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" dashboard\launch.py
pause
