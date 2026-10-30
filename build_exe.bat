@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 打包成 exe 文件夹（PyInstaller onedir，已排除 QML/Quick/Pdf/WebEngine 等无用 Qt 模块）
echo.
python -X utf8 -m pip install pyinstaller
python -X utf8 -m PyInstaller --noconfirm --clean package.spec
echo.
echo 完成后在 dist\节奏条工作室\ 里。ffmpeg.exe 需要另外准备（放同目录或加进 PATH）。
pause
