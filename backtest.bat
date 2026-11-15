@echo off
chcp 65001 >nul
cd /d "%~dp0"
python nds_bot.py --backtest
echo.
pause
