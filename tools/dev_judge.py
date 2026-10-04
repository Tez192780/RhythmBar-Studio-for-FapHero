r"""开发用：验证判定菱形是实心的（回归 qc() 颜色缓存污染导致的空心问题）。

    cmd /c "python -X utf8 -u tools\dev_judge.py > build\judge.log 2>&1"
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402

from rbar.model import Note, Project  # noqa: E402
from rbar.render import BarRenderer  # noqa: E402

W, H = 1200, 90
BASE = 2000.0
STEPS = [-260, -140, -60, -20, 0, 20, 60, 140, 260, 400, 600]
FAILS: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  ok  " if cond else " FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)
    if not cond:
        FAILS.append(name)


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    out = os.path.join(ROOT, "build")
    os.makedirs(out, exist_ok=True)

    pr = Project()
    pr.render.width, pr.render.height = W, H
    pr.render.approach_s = 1.0
    pr.chart.add_many([
        Note(BASE, "cyan", 0),
        Note(BASE + 900, "magenta", 1),
    ])

    strip = QImage(W, H * len(STEPS), QImage.Format_ARGB32_Premultiplied)
    strip.fill(QColor("#20242b"))
    p = QPainter(strip)
    r = BarRenderer(pr)
    for i, dt in enumerate(STEPS):
        p.save()
        p.translate(0, i * H)
        r.render(p, W, H, BASE + dt, solid_bg=QColor("#20242b"))
        p.restore()
        p.setPen(QColor("#ffffff"))
        p.drawText(6, i * H + 14, f"{dt:+d}ms")
    p.end()
    path = os.path.join(out, "judge_sequence.png")
    strip.save(path)
    print("wrote", path, strip.width(), strip.height(), flush=True)

    # 逐帧检查判定点中心是否够亮（空心的话中心会是背景色 #20242b）
    jx = int(W * pr.render.judge_ratio)
    cy = H // 2
    img = strip.convertToFormat(QImage.Format_ARGB32)
    raw = np.frombuffer(img.constBits(), dtype=np.uint8).reshape(
        img.height(), img.bytesPerLine() // 4, 4)[:, :W, :]
    for i, dt in enumerate(STEPS):
        y = i * H + cy
        # 判定点中心：只有该帧音符离得远时才不含音符本身
        px = raw[y, jx, :3].astype(int)
        if dt in (-260, -140, 400, 600):
            check(f"dt={dt:+d} 判定点中心是亮色菱形", px.min() > 140, f"RGB={tuple(px)}")
    row = raw[(len(STEPS) - 1) * H + cy, jx - 12: jx + 12, :3].astype(int)
    check("菱形内部几乎无暗色空洞", float((row.max(axis=1) < 90).mean()) < 0.2,
          f"暗色占比={float((row.max(axis=1) < 90).mean()):.2f}")

    print(flush=True)
    if FAILS:
        print("失败项：", FAILS, flush=True)
        sys.exit(1)
    print("判定菱形检查通过", flush=True)
    app.quit()


if __name__ == "__main__":
    main()
