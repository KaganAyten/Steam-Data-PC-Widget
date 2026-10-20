@echo off
cd /d "%~dp0"
set "PYW="
for /f "delims=" %%i in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%i"
if not defined PYW for /f "delims=" %%i in ('where pyw 2^>nul') do if not defined PYW set "PYW=%%i"
if not defined PYW (
    echo Python was not found. Run install.bat first.
    pause
    exit /b 1
)
start "" "%PYW%" "%~dp0steam_widget.py"
