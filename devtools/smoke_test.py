r"""冒烟自检：不打开真实窗口，验证 PySide6 / numpy / ffmpeg 与渲染、导出链路是否可用。

用法（项目根目录）：
    .\.venv\Scripts\python.exe devtools\smoke_test.py

它做四件事：
  1. 导入 PySide6 / numpy，打印版本；
  2. 用 demo 工程离屏渲染一帧 PNG；
  3. 走 exporter 的 ffmpeg 管道导出 10 帧 MOV(qtrle)，再用 ffprobe 读回结果；
  4. 构造一次 MainWindow（off-screen），确认整套 UI 能实例化并干净关闭。

任何一步失败都会打印原因并以非 0 退出码结束，方便以后接 CI。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 必须在 import PySide6 之前设置：无显示器也能跑
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")

failures: list[str] = []


def step(name: str, fn):
    print(f"[..] {name}", flush=True)
    try:
        out = fn()
    except Exception:
        failures.append(name)
        print(f"[FAIL] {name}")
        traceback.print_exc()
        return None
    print(f"[ OK ] {name}" + (f" -> {out}" if out else ""), flush=True)
    return out


def check_imports():
    import numpy
    import PySide6

    from PySide6 import QtCore

    return f"PySide6 {PySide6.__version__} / numpy {numpy.__version__} / Qt {QtCore.qVersion()}"


def check_ffmpeg():
    from rbar.audio import find_ffmpeg, find_ffprobe

    ff, fp = find_ffmpeg(), find_ffprobe()
    if not ff:
        raise RuntimeError("找不到 ffmpeg（PATH 或设置里）")
    if not fp:
        raise RuntimeError("找不到 ffprobe")
    out = subprocess.run([ff, "-version"], capture_output=True, text=True).stdout.splitlines()
    return f"{ff} | {out[0][:60] if out else ''}"


def check_render(outdir: str):
    from PySide6.QtWidgets import QApplication

    from rbar.demo import make_demo_project
    from rbar.exporter import render_frame_image

    app = QApplication.instance() or QApplication(sys.argv[:1])
    p = make_demo_project()
    img = render_frame_image(p, 4000.0, p.render.width, p.render.height, 2)
    path = os.path.join(outdir, "frame.png")
    if not img.save(path):
        raise RuntimeError(f"写不出 PNG: {path}")
    alpha_nonzero = any(
        img.pixelColor(x, y).alpha() > 0
        for x in range(0, img.width(), 37)
        for y in range(0, img.height(), 7)
    )
    if not alpha_nonzero:
        raise RuntimeError("渲染结果全透明，判定点/音符没画上去")
    del app
    return f"{path} ({img.width()}x{img.height()}, 含非透明像素)"


def check_export(outdir: str):
    from PySide6.QtWidgets import QApplication

    from rbar.audio import find_ffmpeg, find_ffprobe
    from rbar.demo import make_demo_project
    from rbar.exporter import export_video

    app = QApplication.instance() or QApplication(sys.argv[:1])
    p = make_demo_project()
    p.export.fmt = "mov_qtrle"
    p.export.width, p.export.height = 320, 20
    p.export.fps = 10
    p.export.supersample = 1
    p.export.range_mode = "custom"
    p.export.t_start, p.export.t_end = 0.0, 1000.0
    out = os.path.join(outdir, "smoke.mov")
    res = export_video(p, out, ffmpeg=find_ffmpeg())
    if not os.path.isfile(out) or os.path.getsize(out) < 100:
        raise RuntimeError("导出的 MOV 不存在或过小")
    probe = subprocess.run(
        [find_ffprobe(), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,width,height,nb_frames,pix_fmt",
         "-of", "default=nw=1", out],
        capture_output=True, text=True,
    ).stdout.replace("\n", " ").strip()
    del app
    return f"{res.frames} 帧, {os.path.getsize(out) / 1024:.0f} KB | {probe}"


def check_mainwindow():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from rbar.settings import AppSettings
    from rbar.ui.mainwindow import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    win = MainWindow(AppSettings())
    win.show()
    QTimer.singleShot(400, app.quit)
    app.exec()
    win.hide()
    win.shutdown()
    del win
    return "MainWindow 构造 / 显示 / shutdown 正常"


def main() -> int:
    outdir = tempfile.mkdtemp(prefix="rbar_smoke_")
    print(f"工作目录: {ROOT}\n临时输出: {outdir}\n")
    step("导入 PySide6 / numpy", check_imports)
    step("定位 ffmpeg / ffprobe", check_ffmpeg)
    step("离屏渲染一帧 PNG", lambda: check_render(outdir))
    step("ffmpeg 管道导出 MOV(qtrle)", lambda: check_export(outdir))
    step("构造 MainWindow", check_mainwindow)
    print()
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {', '.join(failures)}")
        return 1
    print("结果: 全部通过")
    shutil.rmtree(outdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
