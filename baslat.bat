@echo off
chcp 65001 > nul
title Iade Takip Sistemi - Sunucu Baslatici
cd /d "%~dp0"

echo ===================================================
echo   IADE TAKIP SISTEMI BASLATILIYOR (Port: 5050)
echo ===================================================
echo.

for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5050 " ^| findstr "LISTENING"') do (
    echo Eski sunucu sureci kapatiliyor (PID: %%a)...
    taskkill /F /PID %%a > nul 2>&1
)

start "" http://127.0.0.1:5050
python app.py
pause

