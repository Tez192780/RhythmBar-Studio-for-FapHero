@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  把当前改动提交并推送到 GitHub
echo.
echo  用法：
echo    直接双击           自动生成提交信息
echo    上传更新.bat 说明文字   用你给的说明当提交信息
echo.
python tools\git_push.py %*
echo.
echo  （本窗口不会自动关闭，方便你看结果）
pause
