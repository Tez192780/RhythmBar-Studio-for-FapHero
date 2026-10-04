"""程序入口。"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication

from . import APP_NAME, APP_NAME_EN
from .settings import AppSettings

QSS = """
QWidget { font-size: 12px; }
QGroupBox {
    border: 1px solid #333a42; border-radius: 6px; margin-top: 14px; padding-top: 8px;
}
QGroupBox::title { subcontrol-origin: margin; left: 9px; color: #9fb0c0; }
QPushButton {
    background: #2a3038; border: 1px solid #3c444e; border-radius: 5px; padding: 4px 10px;
}
QPushButton:hover { background: #333b45; }
QPushButton:pressed { background: #22272e; }
QPushButton:checked { background: #2d5c8a; border-color: #4b8cc7; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: #1e232a; border: 1px solid #3a424c; border-radius: 4px; padding: 2px 4px;
}
QComboBox QAbstractItemView { background: #1e232a; selection-background-color: #2d5c8a; }
QTableWidget { background: #1b1f25; gridline-color: #303740; }
QHeaderView::section { background: #232930; border: none; padding: 3px; }
QTabBar::tab { background: #232830; padding: 5px 10px; border-top-left-radius: 5px; border-top-right-radius: 5px; }
QTabBar::tab:selected { background: #2f3843; }
QScrollBar:vertical { background: #1a1e23; width: 11px; }
QScrollBar::handle:vertical { background: #3c444e; border-radius: 5px; min-height: 24px; }
QScrollBar:horizontal { background: #1a1e23; height: 11px; }
QScrollBar::handle:horizontal { background: #3c444e; border-radius: 5px; min-width: 24px; }
QSlider::groove:horizontal { height: 6px; background: #2a3038; border-radius: 3px; }
QSlider::handle:horizontal { background: #6fa8dc; width: 12px; margin: -5px 0; border-radius: 6px; }
QToolBar { background: #1c2127; border-bottom: 1px solid #2b323a; spacing: 3px; padding: 3px; }
QStatusBar { background: #1c2127; color: #93a1b0; }
QDockWidget::title { background: #232a32; padding: 4px; }
QMenuBar { background: #1c2127; }
QMenuBar::item:selected { background: #2d5c8a; }
QMenu { background: #1e232a; border: 1px solid #333a42; }
QMenu::item:selected { background: #2d5c8a; }
"""


def build_palette() -> QPalette:
    p = QPalette()
    bg = QColor("#171a1f")
    base = QColor("#1b1f25")
    text = QColor("#e6edf3")
    p.setColor(QPalette.Window, bg)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, QColor("#20252b"))
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, QColor("#252b33"))
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.Highlight, QColor("#2d5c8a"))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ToolTipBase, QColor("#232a32"))
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.PlaceholderText, QColor("#6b7681"))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor("#6b7681"))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#6b7681"))
    return p


def icon_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("icon.ico", "icon_256.png"):
        p = os.path.join(root, "assets", name)
        if os.path.isfile(p):
            return p
    return ""


def tune_gc() -> None:
    """界面程序会长驻大量对象：冻结启动期对象并放宽阈值，避免偶发的长卡顿。"""
    import gc

    gc.collect()
    try:
        gc.freeze()                    # 启动期对象移出扫描范围
    except Exception:
        pass
    gc.set_threshold(50000, 60, 60)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    QApplication.setApplicationName(APP_NAME_EN)
    QApplication.setOrganizationName("rbar")
    app = QApplication(argv)
    app.setStyle("Fusion")
    app.setPalette(build_palette())
    app.setStyleSheet(QSS)
    ic = icon_path()
    if ic:
        app.setWindowIcon(QIcon(ic))

    from .ui.mainwindow import MainWindow

    settings = AppSettings()
    win = MainWindow(settings)
    geo = settings.geometry
    if geo:
        try:
            win.restoreGeometry(geo)
        except Exception:
            pass
    win.show()

    # 命令行给的文件直接打开
    for a in argv[1:]:
        if os.path.isfile(a):
            win.open_path(a)
            break

    # 自检用：设了 RBAR_SELFTEST_MS 就自动退出（不影响正常使用）
    ms = os.environ.get("RBAR_SELFTEST_MS", "")
    if ms.isdigit():
        from PySide6.QtCore import QTimer

        QTimer.singleShot(int(ms), app.quit)

    tune_gc()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
