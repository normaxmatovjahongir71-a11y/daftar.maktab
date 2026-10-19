@echo off
title Raqamli Daftar - Avtomatik Ishga Tushirish
cd /d "%~dp0"

echo ========================================================
echo    RAQAMLI DAFTAR - TIZIMNI ISHGA TUSHIRISH
echo ========================================================

:: 1. Cloudflare tunnelni orqa fonda yoqish
echo [*] Cloudflare tunnel yoqilmoqda...
start /b "" cloudflared.exe tunnel --url http://127.0.0.1:5000 > cloudflared.log 2>&1

:: 3 soniya kutish
timeout /t 3 /nobreak >nul

:: 2. Bot va Web serverni yoqish
echo [*] Telegram bot va Sayt ishga tushmoqda...
python bot.py

pause
