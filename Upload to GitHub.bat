@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  Commit and push the current changes to GitHub
echo.
echo  Usage:
echo    Double-click                     auto-generated commit message
echo    Upload to GitHub.bat your text    use your text as the commit message
echo.
python -X utf8 devtools\git_push.py %*
echo.
echo  (this window stays open so you can read the result)
pause
