r"""验证 Ctrl+C / Ctrl+V 快捷键实装（离屏，真实按键事件）。

检查四件事：
  1. 菜单 QAction 上确实绑定了 Ctrl+C / Ctrl+V；
  2. 真正按下 Ctrl+C 会把选中音符放进内部剪贴板；
  3. 真正按下 Ctrl+V 会在时间轴鼠标位置粘贴，音符数按数量增加；
  4. 焦点在输入框时 Ctrl+C / Ctrl+V 不被抢（不会误复制音符、不会误粘贴音符）。

用法：
    .\.venv\Scripts\python.exe devtools\test_shortcuts.py

退出码：0 = 全通过，1 = 有失败项。
"""

from __future__ import annotations

import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

from rbar.model import Note  # noqa: E402
from rbar.settings import AppSettings  # noqa: E402
from rbar.ui.mainwindow import MainWindow  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{' OK ' if ok else 'FAIL'}] {name}" + (f" -> {detail}" if detail else ""), flush=True)
    if not ok:
        failures.append(name)


def run_checks(app: QApplication, win: MainWindow) -> None:
    # ---------------------------------------------------------- 1. 快捷键绑定
    copy_sc = win.a_copy.shortcut().toString(QKeySequence.PortableText)
    paste_sc = win.a_paste.shortcut().toString(QKeySequence.PortableText)
    check("Ctrl+C 已绑定到「复制」", copy_sc == "Ctrl+C", f"实际 {copy_sc!r}")
    check("Ctrl+V 已绑定到「粘贴」", paste_sc == "Ctrl+V", f"实际 {paste_sc!r}")
    check("粘贴动作文案不再手工重复快捷键",
          "Ctrl+V" not in win.a_paste.text(), f"文案 {win.a_paste.text()!r}")

    # ---------------------------------------------------------- 2. Ctrl+C
    rows = win.doc.project.theme.active_rows()
    p = win.doc.project
    notes = [Note(1000.0, rows[0], 0), Note(1500.0, rows[1], 1), Note(2000.0, rows[0], 0)]
    p.chart.add_many(notes)
    win.doc.set_selection(notes[:2])          # 只选前两个
    win.timeline.setFocus()
    QTest.qWait(50)

    QTest.keyClick(win.timeline, Qt.Key_C, Qt.ControlModifier)
    QTest.qWait(60)
    clip = getattr(win, "_clipboard", [])
    check("Ctrl+C 复制了选中的 2 个音符", len(clip) == 2, f"剪贴板 {len(clip)} 项")
    check("剪贴板锚点是最早音符的时间",
          abs(float(getattr(win, "_clip_anchor", -1)) - 1000.0) < 1e-6,
          f"anchor={getattr(win, '_clip_anchor', None)}")

    # ---------------------------------------------------------- 3. Ctrl+V
    before = len(p.chart.notes)
    win.timeline._mouse_ms = 5000.0
    win.timeline._mouse_row = 0
    QTest.keyClick(win.timeline, Qt.Key_V, Qt.ControlModifier)
    QTest.qWait(60)
    after = len(p.chart.notes)
    new_notes = [n for n in p.chart.notes if n.t >= 5000.0]
    check("Ctrl+V 新增了 2 个音符", after - before == 2, f"{before} -> {after}")
    check("粘贴位置落在鼠标时间 5000ms",
          bool(new_notes) and abs(min(n.t for n in new_notes) - 5000.0) < 1.0,
          f"最小时间 {min((n.t for n in new_notes), default=-1)}")
    check("粘贴后自动选中新音符", len(win.doc.selection) == 2, f"选中 {len(win.doc.selection)}")

    # ---------------------------------------------------------- 4. 输入框不抢键
    edits = [w for w in win.findChildren(QLineEdit) if w.isVisible()]
    if not edits:
        check("找到可聚焦的输入框", False, "面板里没有可见 QLineEdit")
        return
    le = edits[0]
    le.setText("abcdef")
    le.setFocus()
    le.selectAll()
    QTest.qWait(80)

    clip_before = list(win._clipboard)
    app.clipboard().clear()
    QTest.keyClick(le, Qt.Key_C, Qt.ControlModifier)
    QTest.qWait(60)
    check("输入框里按 Ctrl+C 不会覆盖音符剪贴板",
          win._clipboard == clip_before, f"剪贴板项数 {len(win._clipboard)}")
    check("输入框里按 Ctrl+C 交给系统文本剪贴板",
          app.clipboard().text() == "abcdef", f"系统剪贴板={app.clipboard().text()!r}")

    n_before = len(p.chart.notes)
    le.setCursorPosition(len(le.text()))          # 取消全选，光标放到末尾
    QTest.keyClick(le, Qt.Key_V, Qt.ControlModifier)
    QTest.qWait(60)
    check("输入框里按 Ctrl+V 不会粘贴出音符",
          len(p.chart.notes) == n_before, f"{n_before} -> {len(p.chart.notes)}")
    check("输入框里按 Ctrl+V 正常粘贴文本",
          le.text() == "abcdefabcdef", f"文本框={le.text()!r}")
    le.clear()


def main() -> int:
    app = QApplication(sys.argv[:1])
    win = MainWindow(AppSettings())
    win.resize(1280, 820)
    win.show()
    win.activateWindow()
    QTest.qWait(200)
    try:
        run_checks(app, win)
    except Exception:
        # 任何异常都要先收掉音频线程，否则 QThread 在析构时会让进程 abort
        failures.append("检查过程抛出异常")
        traceback.print_exc()
    finally:
        win.hide()
        win.shutdown()
        del win
        app.processEvents()

    print()
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {', '.join(failures)}")
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
