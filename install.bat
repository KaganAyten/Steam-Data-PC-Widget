@echo off
setlocal
cd /d "%~dp0"
title Steam Data PC Widget - install

set "PYW="
for /f "delims=" %%i in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%i"
if not defined PYW for /f "delims=" %%i in ('where pyw 2^>nul') do if not defined PYW set "PYW=%%i"

if not defined PYW (
    echo Python was not found. Installing Python 3.13 with winget...
    winget install -e --id Python.Python.3.13 --scope user --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo.
        echo Automatic install failed. Install Python from https://www.python.org/downloads/
        echo and tick "Add python.exe to PATH", then run install.bat again.
        pause
        exit /b 1
    )
    echo.
    echo Python is installed. Close this window and run install.bat AGAIN.
    pause
    exit /b 0
)

echo Python: %PYW%

if not exist "%~dp0config.json" (
    copy "%~dp0config.example.json" "%~dp0config.json" >nul
    echo Created config.json - add your API keys to it.
    start "" notepad "%~dp0config.json"
)

set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\SteamStatsWidget.vbs"
> "%STARTUP%" echo CreateObject("WScript.Shell").Run """%PYW%"" ""%~dp0steam_widget.py""", 0, False
echo Added to Windows startup.

start "" "%PYW%" "%~dp0steam_widget.py"
echo Widget started. You can close this window.
timeout /t 5 >nul
