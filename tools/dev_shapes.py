r"""开发用：渲染几种音符形态，检查长条/残影/判定高亮是否正确。

    cmd /c "python -X utf8 -u tools\dev_shapes.py > build\shapes.log 2>&1"
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402

from rbar.model import Note, Project  # noqa: E402
from rbar.render import BarRenderer  # noqa: E402


def shot(project: Project, t_ms: float, w: int, h: int, path: str, bg: str = "#20242b") -> None:
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    from PySide6.QtGui import QColor

    BarRenderer(project).render(p, w, h, t_ms, solid_bg=QColor(bg))
    p.end()
    img.save(path)
    print("wrote", path, flush=True)


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    out = os.path.join(ROOT, "build")
    os.makedirs(out, exist_ok=True)

    pr = Project()
    pr.render.width, pr.render.height = 1600, 100
    pr.render.approach_s = 1.0
    base = 2000.0
    pr.chart.add_many([
        Note(base - 700, "gray", 2),          # 早已越过 -> 残影
        Note(base - 320, "gray", 2),
        Note(base - 120, "magenta", 1),       # 刚越过 -> 半透明残影
        Note(base, "cyan", 0),                # 正好在判定点
        Note(base + 260, "cyan", 0, dur=520), # 长条
        Note(base + 900, "magenta", 1),
        Note(base + 1100, "white", 3),
    ])
    shot(pr, base, 1600, 100, os.path.join(out, "shape_hold.png"))

    # 多行铺开 + 双侧汇聚
    pr2 = Project()
    pr2.render.width, pr2.render.height = 1600, 160
    pr2.render.vspread = 0.6
    pr2.render.flow = "converge"
    pr2.render.approach_s = 1.2
    notes = []
    for i in range(8):
        t = base + (i - 3) * 220
        notes.append(Note(t, ["cyan", "magenta", "gray"][i % 3], i % 3))
    pr2.chart.add_many(notes)
    shot(pr2, base, 1600, 160, os.path.join(out, "shape_converge.png"))

    # 霓虹预设 + 刻度 + 脉冲
    from rbar.theme import apply_preset

    pr3 = Project()
    pr3.render.width, pr3.render.height = 1600, 100
    pr3.render.ticks = "beat"
    apply_preset(pr3.theme, "霓虹发光")
    pr3.chart.add_many([Note(base - 200, "cyan", 0), Note(base, "magenta", 1),
                        Note(base + 300, "cyan", 0), Note(base + 600, "magenta", 1)])
    shot(pr3, base, 1600, 100, os.path.join(out, "shape_neon.png"), bg="#12141a")

    app.quit()


if __name__ == "__main__":
    main()
