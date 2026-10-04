@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 安装 / 更新依赖...
python -X utf8 -m pip install -r requirements.txt
echo.
echo 检查 ffmpeg...
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo   [注意] 没在 PATH 里找到 ffmpeg。程序里可以在「设置」标签手动指定 ffmpeg.exe 路径。
  echo   下载：https://www.gyan.dev/ffmpeg/builds/  （解压后把 bin 目录加进 PATH）
) else (
  echo   ffmpeg 已就绪。
)
echo.
pause
