"""生成应用图标（assets/icon.ico）。需要 Pillow（写多尺寸 ico）。

    python tools/make_icon.py
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath  # noqa: E402

from rbar.render import shape_path  # noqa: E402


def render_icon(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    s = float(size)

    # 底板
    path = QPainterPath()
    path.addRoundedRect(QRectF(s * 0.04, s * 0.20, s * 0.92, s * 0.60), s * 0.16, s * 0.16)
    g = QLinearGradient(0, s * 0.20, 0, s * 0.80)
    g.setColorAt(0.0, QColor("#3a2b26"))
    g.setColorAt(1.0, QColor("#1d1512"))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawPath(path)
    p.setPen(QColor(255, 255, 255, 46))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)

    def draw(shape: str, cx: float, cy: float, r: float, color: str, outline: str = "#12161b") -> None:
        pa = shape_path(cx, cy, r, shape)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawPath(pa)
        pen = p.pen()
        pen.setColor(QColor(outline))
        pen.setWidthF(max(1.0, r * 0.14))
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(pa)

    mid = s * 0.5
    draw("diamond", s * 0.30, mid, s * 0.115, "#3fd2ea")
    draw("diamond", s * 0.70, mid, s * 0.115, "#ff2d6a")
    # 判定点：白色菱形 + 光晕
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 70))
    p.drawEllipse(QPointF(mid, mid), s * 0.20, s * 0.20)
    draw("diamond", mid, mid, s * 0.16, "#ffffff")

    p.end()
    return img


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    out_dir = os.path.join(ROOT, "assets")
    os.makedirs(out_dir, exist_ok=True)
    sizes = [256, 128, 64, 48, 32, 16]
    pngs = []
    for s in sizes:
        png = os.path.join(out_dir, f"icon_{s}.png")
        render_icon(s).save(png)
        pngs.append(png)
    ico = os.path.join(out_dir, "icon.ico")
    try:
        from PIL import Image

        base = Image.open(pngs[0]).convert("RGBA")
        base.save(ico, sizes=[(s, s) for s in sizes])
        print("wrote", ico)
    except Exception as e:  # noqa: BLE001
        print("写 ico 失败（没装 Pillow？），已生成 PNG：", e)
    for p in pngs[1:]:
        os.remove(p)
    app.quit()


if __name__ == "__main__":
    main()
