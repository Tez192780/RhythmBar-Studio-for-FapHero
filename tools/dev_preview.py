"""开发用：离屏渲染若干帧到 PNG，用来核对视觉效果（不需要显示器）。

用法：
    python tools/dev_preview.py
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402

from rbar.demo import make_demo_project  # noqa: E402
from rbar.model import Note, Project  # noqa: E402
from rbar.render import BarRenderer  # noqa: E402
from rbar.theme import apply_preset  # noqa: E402

REF_T = 1000.0


def render_to(path: str, project: Project, w: int, h: int, t_ms: float, checker: bool = True) -> None:
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    BarRenderer(project).render(p, w, h, t_ms)
    p.end()
    out = img.convertToFormat(QImage.Format_ARGB32)
    if checker:
        flat = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        flat.fill(QColor(255, 255, 255))
        q = QPainter(flat)
        cell = max(6, h // 6)
        for yy in range(0, h, cell):
            for xx in range(0, w, cell):
                if ((xx // cell) + (yy // cell)) % 2 == 0:
                    q.fillRect(QRectF(xx, yy, cell, cell), QColor(205, 205, 205))
        q.drawImage(0, 0, out)
        q.end()
        flat.save(path)
    else:
        out.save(path)
    print("wrote", os.path.basename(path), f"{w}x{h}")


def reference_like() -> Project:
    """尽量复刻参考图那一帧：左三灰方块、中间白高亮、右青/品红菱形。"""
    p = Project()
    p.render.width, p.render.height = 1590, 64
    p.render.approach_s = 0.62          # judge 795px / 0.62s -> 1282px/s，100ms 约 128px
    p.render.judge_ratio = 0.5
    base = REF_T
    notes = [
        Note(base - 245.0, "gray", 2),
        Note(base - 165.0, "gray", 2),
        Note(base - 85.0, "gray", 2),
        Note(base, "cyan", 0),
        Note(base + 118.0, "cyan", 0),
        Note(base + 218.0, "cyan", 0),
        Note(base + 300.0, "magenta", 1),
        Note(base + 382.0, "magenta", 1),
        Note(base + 464.0, "magenta", 1),
    ]
    p.chart.add_many(notes)
    return p


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    out_dir = os.path.join(ROOT, "build")
    os.makedirs(out_dir, exist_ok=True)

    demo = make_demo_project()
    render_to(os.path.join(out_dir, "demo_1920x120.png"), demo, 1920, 120, 3200.0)

    ref = reference_like()
    render_to(os.path.join(out_dir, "ref_default.png"), ref, 1590, 64, REF_T)

    for i, preset in enumerate(("图中样式（暖灰半透明）", "极简·无底板", "霓虹发光", "深色玻璃", "纯白浅色")):
        apply_preset(ref.theme, preset)
        render_to(os.path.join(out_dir, f"preset_{i}.png"), ref, 1590, 64, REF_T)

    app.quit()


if __name__ == "__main__":
    main()
