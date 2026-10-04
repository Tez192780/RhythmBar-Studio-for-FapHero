r"""开发用：用模拟鼠标验证时间轴交互。

覆盖：两种鼠标模式（选择/放置）、区间框选、加点、拖动、长条、框选音符、删除、
打点、复制粘贴、缩放、保存读取、BPM 段。

    cmd /c "python -X utf8 -u tools\ui_interact.py > build\ui_interact.log 2>&1"
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from rbar.doc import Doc  # noqa: E402
from rbar.model import Note, Project  # noqa: E402
from rbar.settings import AppSettings  # noqa: E402
from rbar.ui.mainwindow import MainWindow  # noqa: E402
from rbar.ui.timeline import BPM_H, GUTTER, RULER_H  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  ok  " if cond else " FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)
    if not cond:
        FAILS.append(name)


def drag(widget, p0: QPoint, p1: QPoint, mod=Qt.NoModifier, steps: int = 3) -> None:
    QTest.mousePress(widget, Qt.LeftButton, mod, p0)
    for i in range(1, steps + 1):
        x = p0.x() + (p1.x() - p0.x()) * i // steps
        y = p0.y() + (p1.y() - p0.y()) * i // steps
        QTest.mouseMove(widget, QPoint(x, y))
    QTest.mouseRelease(widget, Qt.LeftButton, mod, p1)


def main() -> None:
    app = QApplication(sys.argv)
    win = MainWindow(AppSettings(), Project())
    win.silent = True
    win.resize(1500, 900)
    win.show()
    app.processEvents()

    doc: Doc = win.doc
    tl = win.timeline
    tl.zoom_to_fit()
    app.processEvents()

    rows = doc.project.theme.active_rows()
    row_y = int(tl.row_y(0) + tl._row_h() / 2)
    row1_y = int(tl.row_y(1) + tl._row_h() / 2)
    ruler_y = BPM_H + RULER_H // 2
    wave_y = int(tl._wave_top() + tl._wave_h() / 2)

    print("[鼠标模式]", flush=True)
    check("默认是选择模式", doc.state.tool == "select", doc.state.tool)
    x = GUTTER + 200
    QTest.mouseClick(tl, Qt.LeftButton, Qt.NoModifier, QPoint(x, row_y))
    app.processEvents()
    check("选择模式下单击空白不加点", len(doc.project.chart) == 0, f"{len(doc.project.chart)}")

    doc.edit("预备", lambda: doc.project.chart.add(Note(2000.0, rows[0], 0)))
    app.processEvents()
    drag(tl, QPoint(int(tl.ms_to_x(1000.0)), row_y - 10),
         QPoint(int(tl.ms_to_x(3200.0)), row_y + 10))
    app.processEvents()
    check("空白拖动框选到音符", len(doc.selection) == 1, f"{len(doc.selection)}")
    doc.clear_selection()
    doc.edit("清空", lambda: doc.project.chart.remove_many(list(doc.project.chart.notes)))

    win.set_tool("draw")
    app.processEvents()
    check("切到放置模式", doc.state.tool == "draw", doc.state.tool)
    t0 = tl.x_to_ms(x)
    QTest.mouseClick(tl, Qt.LeftButton, Qt.NoModifier, QPoint(x, row_y))
    app.processEvents()
    check("放置模式单击加点", len(doc.project.chart) == 1, f"{len(doc.project.chart)}")
    check("音符类型=第 1 行", doc.project.chart.notes[0].type == rows[0],
          doc.project.chart.notes[0].type)

    before = len(doc.project.chart)
    drag(tl, QPoint(int(tl.ms_to_x(t0 + 200)), row_y),
         QPoint(int(tl.ms_to_x(t0 + 2000)), row_y), steps=6)
    app.processEvents()
    added = len(doc.project.chart) - before
    check("拖动连续刷出多个音符", added >= 4, f"+{added}")
    doc.undo()
    app.processEvents()
    check("连续刷可一次撤销", len(doc.project.chart) == before, f"{len(doc.project.chart)}")

    win.set_tool("select")
    app.processEvents()

    # --- 拖动音符 / 换类型 / 长条
    n = doc.project.chart.notes[0]
    t_added = n.t
    npress = QPoint(int(tl.ms_to_x(n.t)), row_y)
    drag(tl, npress, QPoint(npress.x() + 120, row_y))
    app.processEvents()
    check("拖动后时间变了", doc.project.chart.notes[0].t > t_added + 10,
          f"{t_added:.0f} -> {doc.project.chart.notes[0].t:.0f}")
    doc.undo()
    app.processEvents()
    check("撤销回到原位", abs(doc.project.chart.notes[0].t - t_added) < 1.0)

    n = doc.project.chart.notes[0]
    npress = QPoint(int(tl.ms_to_x(n.t)), row_y)
    drag(tl, npress, QPoint(npress.x(), row1_y))
    app.processEvents()
    check("纵向拖动换了类型", doc.project.chart.notes[0].type == rows[1],
          doc.project.chart.notes[0].type)
    doc.undo()
    app.processEvents()

    n = doc.project.chart.notes[0]
    tail = QPoint(int(tl.ms_to_x(n.end)) + 6, row_y)
    check("把手落在长条判定区", tl._tail_at(tail) is n, f"x={tail.x()}")
    drag(tl, tail, QPoint(tail.x() + 90, row_y))
    app.processEvents()
    check("拖尾部生成长条", doc.project.chart.notes[0].dur > 10,
          f"dur={doc.project.chart.notes[0].dur:.0f}ms")

    # --- 区间框选（Shift / 区间模式），普通拖动 = 拖播放头
    print("[区间框选 与 播放头拖动]", flush=True)
    doc.state.clear_range()
    a = int(tl.ms_to_x(500.0))
    b = int(tl.ms_to_x(3500.0))
    win.seek(0.0)
    drag(tl, QPoint(a, ruler_y), QPoint(b, ruler_y))
    app.processEvents()
    check("标尺普通拖动 = 拖播放头（不再被区间占用）",
          not doc.state.has_range() and win.position_ms > 3000,
          f"pos={win.position_ms:.0f} 有区间={doc.state.has_range()}")
    win.seek(0.0)
    drag(tl, QPoint(a, wave_y), QPoint(b, wave_y))
    app.processEvents()
    check("波形区普通拖动 = 拖播放头",
          not doc.state.has_range() and win.position_ms > 3000,
          f"pos={win.position_ms:.0f}")

    drag(tl, QPoint(a, ruler_y), QPoint(b, ruler_y), mod=Qt.ShiftModifier)
    app.processEvents()
    check("Shift+拖动 = 框选区间", doc.state.has_range(),
          f"{doc.state.sel_t0:.0f}~{doc.state.sel_t1:.0f}")
    check("区间宽度≈3000ms", abs((doc.state.sel_t1 - doc.state.sel_t0) - 3000) < 400,
          f"{doc.state.sel_t1 - doc.state.sel_t0:.0f}ms")
    doc.state.clear_range()
    drag(tl, QPoint(a, wave_y), QPoint(b, wave_y), mod=Qt.ShiftModifier)
    app.processEvents()
    check("波形区 Shift+拖动也能框选区间", doc.state.has_range(),
          f"{doc.state.sel_t1 - doc.state.sel_t0:.0f}ms")
    doc.state.clear_range()

    win.set_tool("range")
    drag(tl, QPoint(a, row_y), QPoint(b, row_y))
    app.processEvents()
    check("框选区间模式下在音符行拖动也能框选", doc.state.has_range(),
          f"{doc.state.sel_t1 - doc.state.sel_t0:.0f}ms")
    win.set_tool("select")
    doc.state.clear_range()

    # --- 循环区间：设置 / 取消
    print("[循环区间]", flush=True)
    doc.state.set_range(1000.0, 3000.0)
    tl._range_to_loop()
    app.processEvents()
    check("区间可转成循环区间", doc.state.loop_on and abs(doc.state.loop_b - 3000.0) < 30,
          f"{doc.state.loop_a:.0f}~{doc.state.loop_b:.0f} on={doc.state.loop_on}")
    check("转循环后区间高亮自动清掉", not doc.state.has_range())
    win.clear_loop()
    app.processEvents()
    check("能取消循环区间", not doc.state.loop_on and doc.state.loop_a == 0,
          f"on={doc.state.loop_on} a={doc.state.loop_a}")
    win.chk_loop.setChecked(False)          # 用界面上的勾选框取消循环
    app.processEvents()
    check("取消勾选「循环」后状态正确", not doc.state.loop_on)

    # --- 用框选区间直接填充（对话框 exec 打桩）
    from rbar.ui.dialogs import FillRangeDialog

    doc.state.set_range(1000.0, 3000.0)
    orig_exec = FillRangeDialog.exec

    def fake_exec(self):
        self.chk_manual.setChecked(False)
        self.cb_mode.setCurrentIndex(1)      # 按毫秒
        self.sp_ms.setValue(250)
        return QDialog.DialogCode.Accepted

    FillRangeDialog.exec = fake_exec
    try:
        n0 = len(doc.project.chart)
        win.fill_range()
        added = len(doc.project.chart) - n0
        check("按框选区间填入 9 个", added == 9, f"+{added}")
        got = [n.t for n in doc.project.chart.notes if 1000 <= n.t <= 3000]
        if got:
            check("填充范围正确", min(got) == 1000.0 and max(got) == 3000.0,
                  f"{min(got):.0f}~{max(got):.0f}")
        check("填充后可撤销", win.doc.can_undo() and added == 9)
    finally:
        FillRangeDialog.exec = orig_exec
    doc.state.clear_range()
    app.processEvents()

    # --- 删除 / 打点 / 复制粘贴
    win.select_all()
    before = len(doc.project.chart)
    QTest.keyClick(win, Qt.Key_Delete)
    app.processEvents()
    check("Delete 删除选中", len(doc.project.chart) < before, f"{before} -> {len(doc.project.chart)}")
    doc.undo()
    app.processEvents()

    doc.clear_selection()
    win.seek(4321.0)
    QTest.keyClick(win, Qt.Key_F)
    app.processEvents()
    hit = [n for n in doc.project.chart.notes if abs(n.t - 4321.0) < 260]
    check("F 键在播放头附近打点", len(hit) >= 1, f"{len(hit)}")

    win.select_all()
    win.copy_notes()
    win.seek(20000.0)
    win.paste_notes()
    app.processEvents()
    check("粘贴成功", any(n.t > 19000 for n in doc.project.chart.notes))

    QTest.keyClick(win, Qt.Key_Tab)
    app.processEvents()
    check("Tab 切到放置模式", doc.state.tool == "draw", doc.state.tool)
    QTest.keyClick(win, Qt.Key_Tab)
    app.processEvents()
    check("Tab 再切到框选区间模式", doc.state.tool == "range", doc.state.tool)
    QTest.keyClick(win, Qt.Key_Tab)
    app.processEvents()
    check("Tab 循环回选择模式", doc.state.tool == "select", doc.state.tool)
    QTest.keyClick(win, Qt.Key_R)
    app.processEvents()
    check("R 直接切框选区间", doc.state.tool == "range", doc.state.tool)
    QTest.keyClick(win, Qt.Key_V)
    app.processEvents()
    check("V 切回选择", doc.state.tool == "select", doc.state.tool)

    z0 = doc.state.px_per_ms
    tl.zoom_at(500, 1.5)
    check("缩放生效", doc.state.px_per_ms > z0, f"{z0:.3f} -> {doc.state.px_per_ms:.3f}")

    p = os.path.join(ROOT, "build", "interact.rbarproj")
    doc.project.path = p
    ok = win.save_project()
    check("保存工程", ok and os.path.isfile(p), p)
    cnt = len(doc.project.chart)
    proj2 = Project.load(p)
    check("读回工程音符数一致", len(proj2.chart) == cnt, f"{len(proj2.chart)} vs {cnt}")
    check("读回 BPM 一致",
          abs(proj2.chart.timemap.bpm_at(0) - doc.project.chart.timemap.bpm_at(0)) < 1e-6)

    doc.project.chart.add_segment(5000.0, 90.0)
    check("加 BPM 段后拍映射变化", doc.project.chart.timemap.bpm_at(6000.0) == 90.0)
    doc.project.chart.remove_segment(1)
    check("删 BPM 段", len(doc.project.chart.timemap.segments) == 1)

    win.doc.mark_clean()
    win.close()
    app.processEvents()
    print(flush=True)
    if FAILS:
        print("失败项：", FAILS, flush=True)
        sys.exit(1)
    print("交互全部通过", flush=True)


if __name__ == "__main__":
    main()
