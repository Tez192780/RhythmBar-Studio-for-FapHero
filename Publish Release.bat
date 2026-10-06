@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  Build the exe, zip it, and upload it to GitHub Releases
echo.
echo  Version = __version__ in rbar\__init__.py   (tag = v that version)
echo.
echo  Usage:
echo    Double-click                        build + zip + upload
echo    Publish Release.bat --no-build      zip the existing dist and upload
echo    Publish Release.bat --no-upload     only build + zip, check locally first
echo.
python -X utf8 devtools\release.py %*
echo.
echo  (this window stays open so you can read the result)
pause
