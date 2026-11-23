@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem 打包也要用同一个解释器：.venv 里已经装好了 PySide6 和 PyInstaller，
rem 用 PATH 上的 python 打包会找不到依赖（或打出版本不一致的包）。
set "PYEXE="
if exist ".venv\Scripts\python.exe" set "PYEXE=.venv\Scripts\python.exe"
if not defined PYEXE (
  where python >nul 2>nul
  if errorlevel 1 (
    echo   没有找到 Python。请先安装 Python 3.10+ 并勾选 Add to PATH。
    pause
    exit /b 1
  )
  set "PYEXE=python"
)

echo 使用解释器：%PYEXE%
echo 打包成 exe 文件夹（PyInstaller onedir，已排除 QML/Quick/Pdf/WebEngine 等无用 Qt 模块）
echo.
"%PYEXE%" -X utf8 -m pip install pyinstaller
"%PYEXE%" -X utf8 -m PyInstaller --noconfirm --clean package.spec
echo.
echo 完成后在 dist\节奏条工作室\ 里。ffmpeg.exe 需要另外准备（放同目录或加进 PATH）。
pause
