# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（macOS：.app 包）。

在 package.spec（Windows 版）基础上改出来的 Mac 版本，两者并存、互不影响：
  * 产物从 onedir 文件夹变成 .app（EXE + COLLECT + BUNDLE）；
  * 排除列表与 Windows 版一致（QML/Quick/Pdf/WebEngine 等在 Mac 上同样用不到）；
  * 不用 UPX：UPX 压缩的 .so / framework 在 macOS 上容易崩，还会破坏代码签名，
    而体积收益很小，Mac 版直接放弃 UPX；
  * 可执行文件和 .app 用 ASCII 名「RhythmBarStudio」——中文名在 zip / codesign /
    更新器资产匹配里都容易出问题，Finder 里显示的名字交给 CFBundleDisplayName
    设成「节奏条工作室」；
  * 图标用 build/mac_icon.icns（build_app.sh 用系统自带 sips/iconutil 从
    assets/icon_256.png 生成；没有的话也能正常打包，只是没有图标）；
  * 代码签名在 build_app.sh 里做 ad-hoc 签名（免 Apple 开发者账号）。
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(SPEC))
sys.path.insert(0, ROOT)

from rbar import __version__  # noqa: E402  版本号跟着代码走（rbar/__init__.py）

EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuick3D",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtTest",
    "PySide6.QtSql", "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtWebSockets",
    "PySide6.QtWebChannel",
]

ICON = os.path.join(ROOT, "build", "mac_icon.icns")
if not os.path.isfile(ICON):
    ICON = None                     # 图标缺失不挡打包，只少个图标

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[(os.path.join(ROOT, "assets"), "assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RhythmBarStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,           # 需要 PyObjC；命令行参数目前用不到
    target_arch=None,
    codesign_identity=None,         # 签名由 build_app.sh 收尾时做
    entitlements_file=None,
    icon=None,                      # macOS 的图标在 BUNDLE 里设置
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="RhythmBarStudio",
)
app = BUNDLE(
    coll,
    name="RhythmBarStudio.app",
    icon=ICON,
    bundle_identifier="studio.rhythmbar.app",
    info_plist={
        "CFBundleDisplayName": "节奏条工作室",
        "CFBundleName": "RhythmBarStudio",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "NSHighResolutionCapable": True,
        # Qt 6.8+ 最低 macOS 12；需要支持更老系统时改这里（dyld 会以真实要求为准）
        "LSMinimumSystemVersion": "12.0",
        "LSApplicationCategoryType": "public.app-category.music",
    },
)
