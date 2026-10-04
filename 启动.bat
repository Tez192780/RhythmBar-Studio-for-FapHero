@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   没有找到 Python。请先安装 Python 3.10 或更高版本，
  echo   安装时务必勾选 "Add python.exe to PATH"。
  echo   下载：https://www.python.org/downloads/windows/
  echo.
  pause
  exit /b 1
)

python -c "import PySide6" >nul 2>nul
if errorlevel 1 (
  echo   首次运行，正在安装依赖 PySide6（约 100MB，只需一次）...
  python -X utf8 -m pip install -r requirements.txt
  if errorlevel 1 (
    echo   依赖安装失败，请检查网络后重试，或手动执行：python -m pip install -r requirements.txt
    pause
    exit /b 1
  )
)

rem 无控制台窗口启动（报错信息会写到 %TEMP%\rhythmbar.log）
where pythonw >nul 2>nul
if errorlevel 1 (
  start "" /min python -X utf8 "%~dp0main.py" %*
) else (
  start "" pythonw -X utf8 "%~dp0main.py" %*
)
exit /b 0
