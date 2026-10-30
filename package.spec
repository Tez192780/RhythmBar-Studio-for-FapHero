# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir）。

为什么不用 `--collect-all PySide6`：
PySide6 的 hook 会连 QML / Quick / VirtualKeyboard / Pdf / 软件 OpenGL
一起带进来，包会从 ~125MB 涨到 260MB+，而本工具只用 QtCore/QtGui/
QtWidgets/QtMultimedia（QAudioSink），这些模块一个都不需要。

ucrtbase.dll 是 Windows 10/11 自带的 UCRT，不重复打包；部分安全软件会
拦截在用户目录创建同名系统 DLL，去掉它也能正常启动。
"""

import os

ROOT = os.path.dirname(os.path.abspath(SPEC))

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

# 这些是 hook 顺手带进来的 Qt DLL / 插件，本工具用不到。
_DROP_BIN_NAMES = {
    "ucrtbase.dll",
    "qt6quick.dll", "qt6quickwidgets.dll", "qt6quick3d.dll", "qt6qml.dll",
    "qt6qmlmeta.dll", "qt6qmlmodels.dll", "qt6qmlworkerscript.dll",
    "qt6virtualkeyboard.dll", "qt6pdf.dll", "qt6pdfwidgets.dll",
    "opengl32sw.dll", "qpdf.dll", "qt6opengl.dll", "qt6openglwidgets.dll",
}


def _keep_binary(item) -> bool:
    dest = str(item[0]).replace("/", "\\").lower()
    name = os.path.basename(dest)
    if name in _DROP_BIN_NAMES:
        return False
    if "\\qml\\" in "\\" + dest or "virtualkeyboard" in dest:
        return False
    return True


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
a.binaries = [b for b in a.binaries if _keep_binary(b)]
a.datas = [d for d in a.datas
           if "\\qml\\" not in "\\" + str(d[0]).replace("/", "\\").lower()
           and "virtualkeyboard" not in str(d[0]).lower()]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="节奏条工作室",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "assets", "icon.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="节奏条工作室",
)
