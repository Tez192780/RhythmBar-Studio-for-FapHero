@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 调试模式：控制台会显示报错信息，关掉这个窗口即退出程序。
echo.
python -X utf8 main.py %*
pause
