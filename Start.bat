@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem ---------------------------------------------------------------- 选解释器
rem 优先用项目内的 .venv（跟 requirements.txt 一起建的隔离环境）；
rem 没有 .venv 时才退回 PATH 上的 python（给只下载了源码的用户）。
set "PYEXE="
if exist ".venv\Scripts\python.exe" set "PYEXE=.venv\Scripts\python.exe"
if not defined PYEXE (
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
  set "PYEXE=python"
)

rem ---------------------------------------------------------------- 检查依赖
"%PYEXE%" -c "import PySide6, numpy" >nul 2>nul
if errorlevel 1 (
  echo   未检测到依赖，正在用 %PYEXE% 安装 PySide6 / numpy（约 100MB，只需一次）...
  "%PYEXE%" -X utf8 -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo   依赖安装失败。可以这样排查：
    echo     1. 当前使用的解释器：%PYEXE%
    echo     2. 手动执行：%PYEXE% -m pip install -r requirements.txt
    echo     3. 如果网络慢，可加国内镜像：
    echo        %PYEXE% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    echo.
    pause
    exit /b 1
  )
)

rem ---------------------------------------------------------------- 启动
rem 优先无控制台窗口启动（报错信息会写到 %TEMP%\rhythmbar.log）
set "PYWEXE="
if exist ".venv\Scripts\pythonw.exe" (
  set "PYWEXE=.venv\Scripts\pythonw.exe"
) else (
  where pythonw >nul 2>nul
  if errorlevel 1 (set "PYWEXE=%PYEXE%") else (set "PYWEXE=pythonw")
)
start "" "%PYWEXE%" -X utf8 "%~dp0main.py" %*
exit /b 0
