r"""哑铃判定点的可视化预览：把一个节拍内的胶囊行程、以及 A/B 两种命中反馈拼成对照图。

用法：
    .\.venv\Scripts\python.exe devtools\preview_dumbbell.py [输出目录]

产物（默认写到 ../_video_analysis）：
    db_phase.png   一个节拍内 8 帧（相位 0 → 0.875）的胶囊行程
    db_fbA.png     命中前后 6 帧（-120/-60/0/60/120/200ms）的「A：圆点膨胀」
    db_fbB.png     命中后 6 帧的「B：整体放大 + 抖动」
    db_fbC.png     命中后 6 帧的「C：微下沉（无特效）」
"""

from __future__ import annotations

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR",
                          os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rbar.demo import make_demo_project  # noqa: E402
from rbar.exporter import render_frame_image  # noqa: E402
from rbar.theme import NoteStyle  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(ROOT), "_video_analysis")
W, H = 1920, 200
JUDGE_RATIO = 0.33
CROP_W = 260                     # 只截判定点附近
ZOOM = 2


def build_project() -> "object":
    p = make_demo_project(bpm=128.0, bars=16)
    p.render.width, p.render.height = W, H
    p.render.judge_ratio = JUDGE_RATIO
    p.render.post_speed = 3.8
    p.render.ticks = "off"
    p.theme.plate.mode = "none"
    p.theme.judge.mode = "dumbbell"
    p.theme.judge.idle_color = "#e9edf0"
    return p


def crop(img: QImage) -> QImage:
    jx = int(W * JUDGE_RATIO)
    x0 = max(0, jx - CROP_W // 2)
    return img.copy(x0, 0, CROP_W, H)


def tile(imgs: list[QImage], bg: str = "#101418") -> QImage:
    z = [i.scaled(CROP_W * ZOOM, H * ZOOM, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
         for i in imgs]
    gap = 4
    out = QImage(len(z) * (CROP_W * ZOOM + gap), H * ZOOM, QImage.Format_ARGB32)
    out.fill(QColor(bg))
    q = QPainter(out)
    for i, im in enumerate(z):
        q.drawImage(i * (CROP_W * ZOOM + gap), 0, im)
    q.end()
    return out


def note_times(p) -> list[float]:
    tm = p.chart.timemap
    return [tm.ms(float(b)) for b in range(0, 24)]


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setFont(QFont("Arial", 10))
    os.makedirs(OUT, exist_ok=True)
    p = build_project()
    tm = p.chart.timemap

    # --- 1. 一个节拍内的胶囊行程：取第 5 拍前后的 8 个相位
    base_beat = 8.0
    frames = []
    for i in range(8):
        phase = i / 8.0
        t = tm.ms(base_beat + phase)
        frames.append(crop(render_frame_image(p, t, W, H, 2)))
    tiled = tile(frames)
    path = os.path.join(OUT, "db_phase.png")
    tiled.save(path)
    print("胶囊行程:", path, f"({tiled.width()}x{tiled.height()})")

    # --- 2/3. A / B 两种命中反馈
    hit_t = tm.ms(10.0)
    for kind in ("A", "B", "C"):
        rows = []
        for st in p.theme.styles.values():
            st.feedback = kind
        for dt in (-120, -60, 0, 60, 120, 200):
            rows.append(crop(render_frame_image(p, hit_t + dt, W, H, 2)))
        tiled = tile(rows)
        path = os.path.join(OUT, f"db_fb{kind}.png")
        tiled.save(path)
        print(f"反馈 {kind}:", path, f"({tiled.width()}x{tiled.height()})")

    # 复原，避免影响后续测试
    for st in p.theme.styles.values():
        st.feedback = "auto"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
