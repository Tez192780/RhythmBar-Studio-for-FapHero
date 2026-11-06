#!/bin/bash
# 打包成 macOS .app（PyInstaller BUNDLE，已排除 QML/Quick/Pdf/WebEngine 等无用 Qt 模块）
# 用法：终端里执行 ./build_app.sh（需要联网装 PyInstaller，只需一次）
# 产物：dist/RhythmBarStudio.app 和 dist/RhythmBar-Studio-v{版本}-macOS-{架构}.zip

set -euo pipefail
cd "$(dirname "$0")"

echo "打包成 macOS .app（PyInstaller BUNDLE，已排除 QML/Quick/Pdf/WebEngine 等无用 Qt 模块）"
echo

# ---- 0. 准备 Python 虚拟环境（不依赖 uv/pyproject，直接用 requirements.txt）----
PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "  没有找到 $PYTHON_BIN。请先安装 Python 3.10+（或设置 PYTHON_BIN 指向 python）。"
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  "$PYTHON_BIN" -m venv .venv
fi

# ---- 1. 安装依赖（PySide6 / numpy）----
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

# ---- 2. 安装 PyInstaller（Python 3.14 需要 >= 6.16）----
.venv/bin/python -m pip install "pyinstaller>=6.16"

# ---- 3. 生成图标：assets/icon_256.png -> build/mac_icon.icns ----
# .app 只认 .icns；用系统自带的 sips/iconutil 转换，不用装任何东西。
mkdir -p build/icon.iconset
sips -z 16 16   assets/icon_256.png --out build/icon.iconset/icon_16x16.png     >/dev/null
sips -z 32 32   assets/icon_256.png --out build/icon.iconset/icon_16x16@2x.png  >/dev/null
sips -z 32 32   assets/icon_256.png --out build/icon.iconset/icon_32x32.png     >/dev/null
sips -z 64 64   assets/icon_256.png --out build/icon.iconset/icon_32x32@2x.png  >/dev/null
sips -z 128 128 assets/icon_256.png --out build/icon.iconset/icon_128x128.png   >/dev/null
sips -z 256 256 assets/icon_256.png --out build/icon.iconset/icon_256x256.png   >/dev/null
sips -z 512 512 assets/icon_256.png --out build/icon.iconset/icon_256x256@2x.png >/dev/null
sips -z 512 512 assets/icon_256.png --out build/icon.iconset/icon_512x512.png   >/dev/null
sips -z 1024 1024 assets/icon_256.png --out build/icon.iconset/icon_512x512@2x.png >/dev/null
iconutil -c icns build/icon.iconset -o build/mac_icon.icns

# ---- 4. 打包 ----
.venv/bin/pyinstaller --noconfirm --clean package_mac.spec

# ---- 5. ad-hoc 代码签名（免 Apple 开发者账号）----
# 签过名的包被 Gatekeeper 拦的时候用「右键 → 打开」就能过；不签则可能报“已损坏”。
codesign --force --deep --sign - dist/RhythmBarStudio.app

# ---- 6. 打 zip（ditto 保留 .app 里的符号链接和属性，比 zip 稳）----
VER="$(.venv/bin/python -c "from rbar import __version__; print(__version__)")"
ARCH="$(uname -m)"
ZIP="RhythmBar-Studio-v${VER}-macOS-${ARCH}.zip"
rm -f "dist/${ZIP}"
ditto -c -k --keepParent dist/RhythmBarStudio.app "dist/${ZIP}"

echo
echo "完成：dist/${ZIP}  （解压出 RhythmBarStudio.app 拖进「应用程序」即可）"
echo
echo "注意："
echo "  * 只包含当前机器架构（${ARCH}）；要给 Intel Mac 用，需要在 Intel 机器上再跑一次。"
echo "  * ffmpeg 不打包。App 会自动找 Homebrew 装的 ffmpeg（/opt/homebrew/bin 或 /usr/local/bin），"
echo "    没装的话先 brew install ffmpeg，或在 App「设置」标签里手动指定路径。"
echo "  * 对方下载解压后如果提示无法打开：右键 App →「打开」，或执行"
echo "      xattr -cr /Applications/RhythmBarStudio.app"
