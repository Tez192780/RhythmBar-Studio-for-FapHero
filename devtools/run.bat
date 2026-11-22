@echo off
rem 开发用启动器：优先用项目内的 .venv，找不到再退回 PATH 里的 python。
rem 不进仓库（.gitignore 忽略了 devtools/），也不改动给用户的 Start.bat。
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (
  echo [venv] .venv\Scripts\python.exe
  .venv\Scripts\python.exe -X utf8 main.py %*
  goto :end
)
echo [警告] 没找到 .venv，退回 PATH 里的 python（可能没装 PySide6）
python -X utf8 main.py %*
:end
if errorlevel 1 pause
