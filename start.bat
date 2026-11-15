@echo off
chcp 65001 >nul
title NDS Bot
cd /d "%~dp0"
python nds_bot.py
echo.
echo Bot stopped.
pause
