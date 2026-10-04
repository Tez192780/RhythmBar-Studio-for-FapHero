"""开发用：用真实窗口平台截图（能正确显示中文），检查真实观感。

    python tools/ui_shot.py [输出目录]
运行时会短暂弹出一个窗口然后自动关闭。
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.pop("QT_QPA_PLATFORM", None)
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rbar.app import QSS, build_palette  # noqa: E402
from rbar.demo import make_demo_project  # noqa: E402
from rbar.settings import AppSettings  # noqa: E402
from rbar.ui.mainwindow import MainWindow  # noqa: E402


def main() -> None:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build")
    os.makedirs(out_dir, exist_ok=True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(build_palette())
    app.setStyleSheet(QSS)

    proj = make_demo_project(bars=8)
    win = MainWindow(AppSettings(), proj)
    win.resize(1600, 960)
    win.show()
    win.timeline.zoom_to_fit()
    win.seek(3200.0)
    # 展示「框选区间」效果
    win.doc.state.set_range(1800.0, 4600.0)
    app.processEvents()

    shots: list[str] = []

    def step(n: int) -> None:
        app.processEvents()
        if n == 0:
            p = os.path.join(out_dir, "shot_main.png")
            win.grab().save(p)
            shots.append(p)
            win.tabs.setCurrentIndex(1)      # 外观
        elif n == 1:
            p = os.path.join(out_dir, "shot_style.png")
            win.grab().save(p)
            shots.append(p)
            win.tabs.setCurrentIndex(2)      # 导出
        elif n == 2:
            p = os.path.join(out_dir, "shot_export.png")
            win.grab().save(p)
            shots.append(p)
            p = os.path.join(out_dir, "shot_preview.png")
            win.preview.grab().save(p)
            shots.append(p)
        else:
            for s in shots:
                print("wrote", s)
            win.doc.mark_clean()
            win.close()
            app.quit()
            return
        QTimer.singleShot(180, lambda: step(n + 1))

    QTimer.singleShot(320, lambda: step(0))
    app.exec()


if __name__ == "__main__":
    main()
