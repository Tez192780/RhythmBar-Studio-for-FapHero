@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem 与 Start.bat 一致：优先用项目内 .venv，没有才用 PATH 上的 python
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

echo 当前解释器：%PYEXE%
echo 安装 / 更新依赖...
rem --timeout/--retries：网络不通时快速失败，避免卡住几分钟不动
"%PYEXE%" -m pip install --disable-pip-version-check --progress-bar off --timeout 20 --retries 2 -r requirements.txt
echo.
echo 检查 ffmpeg...
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo   [注意] 没在 PATH 里找到 ffmpeg。程序里可以在「设置」标签手动指定 ffmpeg.exe 路径。
  echo   下载：https://www.gyan.dev/ffmpeg/builds/ （解压后把 bin 目录加进 PATH）
) else (
  echo   ffmpeg 已就绪。
)
echo.
pause
