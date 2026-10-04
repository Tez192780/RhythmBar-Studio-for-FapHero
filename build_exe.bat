@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 打包成单文件 exe（体积约 150MB，首次需要联网安装 pyinstaller）
echo.
python -X utf8 -m pip install pyinstaller
python -X utf8 -m PyInstaller --noconfirm --windowed --clean ^
  --name "节奏条工作室" ^
  --icon "assets\icon.ico" ^
  --add-data "assets;assets" ^
  --collect-all PySide6 ^
  main.py
echo.
echo 完成后在 dist\节奏条工作室\ 里。ffmpeg.exe 需要另外准备（放同目录或加进 PATH）。
pause
