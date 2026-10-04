r"""开发用：语言切换验证（中 / 日 / 英）+ 出图。

    cmd /c "python -X utf8 -u tools\i18n_check.py > build\i18n.log 2>&1"
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("QT_QPA_PLATFORM", None)

from PySide6.QtWidgets import QApplication  # noqa: E402

from rbar import i18n  # noqa: E402
from rbar.app import QSS, build_palette  # noqa: E402
from rbar.demo import make_demo_project  # noqa: E402
from rbar.settings import AppSettings  # noqa: E402
from rbar.ui.mainwindow import MainWindow  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
FAILS: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  ok  " if cond else " FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)
    if not cond:
        FAILS.append(name)


def menu_titles(win) -> list[str]:
    return [a.text() for a in win.menuBar().actions()]


def tab_titles(win) -> list[str]:
    return [win.tabs.tabText(i) for i in range(win.tabs.count())]


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(build_palette())
    app.setStyleSheet(QSS)
    st = AppSettings()
    st.language = "zh"
    win = MainWindow(st, make_demo_project(bars=8))
    win.silent = True
    win.resize(1600, 960)
    win.show()
    app.processEvents()

    print("[中文]", flush=True)
    check("菜单是中文", menu_titles(win)[:3] == ["文件", "编辑", "视图"], f"{menu_titles(win)[:3]}")
    check("标签页是中文", "谱面 / BPM" in tab_titles(win), f"{tab_titles(win)}")
    check("播放按钮中文", "播放" in win.btn_play.text(), win.btn_play.text())
    win.grab().save(os.path.join(BUILD, "i18n_zh.png"))

    print("[English]", flush=True)
    win.set_language("en")
    app.processEvents()
    check("菜单变英文", menu_titles(win)[:3] == ["File", "Edit", "View"], f"{menu_titles(win)[:3]}")
    check("标签页变英文", "Chart / BPM" in tab_titles(win), f"{tab_titles(win)}")
    check("播放按钮英文", "Play" in win.btn_play.text(), win.btn_play.text())
    check("循环勾选框英文", win.chk_loop.text() == "Loop", win.chk_loop.text())
    check("类型面板标题英文", "Note Types" in win.dock_types.windowTitle(), win.dock_types.windowTitle())
    win.refresh_all()
    app.processEvents()
    win.grab().save(os.path.join(BUILD, "i18n_en.png"))

    print("[日本語]", flush=True)
    win.set_language("ja")
    app.processEvents()
    check("メニューが日本語", menu_titles(win)[:2] == ["ファイル", "編集"], f"{menu_titles(win)[:2]}")
    check("タブが日本語", "譜面 / BPM" in tab_titles(win), f"{tab_titles(win)}")
    check("再生ボタン", "再生" in win.btn_play.text(), win.btn_play.text())
    win.grab().save(os.path.join(BUILD, "i18n_ja.png"))

    print("[切回中文]", flush=True)
    win.set_language("zh")
    app.processEvents()
    check("菜单回到中文", menu_titles(win)[:2] == ["文件", "编辑"], f"{menu_titles(win)[:2]}")
    check("标签页回到中文", "谱面 / BPM" in tab_titles(win), f"{tab_titles(win)}")
    check("按钮回到中文", "播放" in win.btn_play.text(), win.btn_play.text())
    check("设置里记住了语言", AppSettings().language == "zh")

    print("[对话框]", flush=True)
    from rbar.ui.dialogs import FillRangeDialog

    i18n.set_language("en")
    dlg = FillRangeDialog(win, win.doc.project.chart.timemap, 0, 2000,
                          [("cyan", "青菱形")], 0, True, 4.0)
    texts = [w.text() for w in dlg.findChildren(type(dlg.chk_manual)) if w.text()]
    check("对话框标题英文", dlg.windowTitle() == "Fill Range with Notes", dlg.windowTitle())
    from PySide6.QtWidgets import QDialogButtonBox

    box = dlg.findChild(QDialogButtonBox)
    ok_btn = box.button(QDialogButtonBox.StandardButton.Ok)
    check("对话框按钮英文", ok_btn.text() == "Fill", ok_btn.text())
    check("勾选项英文", "Snap start to grid" in texts, f"{texts[:6]}")
    dlg.close()
    i18n.set_language("zh")

    win.doc.mark_clean()
    win.close()
    app.processEvents()
    print(flush=True)
    if FAILS:
        print("失败项：", FAILS, flush=True)
        sys.exit(1)
    print("语言切换全部通过", flush=True)


if __name__ == "__main__":
    main()
