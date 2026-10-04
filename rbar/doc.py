"""编辑文档：工程 + 撤销栈 + 编辑器视图状态。"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

from .model import Note, Project, UndoStack


@dataclass
class EditorState:
    """纯界面状态（不进工程、不进撤销栈）。"""

    snap_on: bool = True
    snap_divisor: float = 4.0        # 每拍切多少格；4 = 十六分音符
    current_lane: int = 0            # 当前绘制的行
    tool: str = "select"             # select = 选择/拖拽；draw = 放置音符；range = 框选区间
    loop_on: bool = False
    loop_a: float = 0.0
    loop_b: float = 0.0
    sel_t0: float = 0.0              # 区间选择（在标尺/波形处拖出来的时间区间）
    sel_t1: float = 0.0
    follow: bool = True
    tap_quantize: bool = True
    show_waveform: bool = True
    show_spectrum: bool = True       # 时间轴上的频谱图（对音用）
    show_video: bool = True          # 时间轴上的参考视频胶片条
    view_t0: float = 0.0             # 视图左边缘对应的时间
    px_per_ms: float = 0.22
    last_tap: list[float] = field(default_factory=list)
    # 区间填充上次用过的参数（下次开对话框沿用）
    fill_mode: str = "beat"
    fill_beats: float = 0.25
    fill_ms: float = 125.0
    fill_alt: bool = False

    # ------------------------------------------------------------ 区间选择
    def has_range(self) -> bool:
        return self.sel_t1 - self.sel_t0 > 20.0

    def set_range(self, a: float, b: float) -> None:
        self.sel_t0, self.sel_t1 = (a, b) if a <= b else (b, a)

    def clear_range(self) -> None:
        self.sel_t0 = self.sel_t1 = 0.0


class Doc(QObject):
    """一个打开的工程；所有改动都通过这里，自动记录撤销。"""

    changed = Signal(str)            # 参数：改动标签
    selectionChanged = Signal()
    dirtyChanged = Signal(bool)
    audioNeedsReload = Signal()

    def __init__(self, project: Project | None = None, parent=None):
        super().__init__(parent)
        self.project = project or Project()
        self.state = EditorState()
        self.undo_stack = UndoStack()
        self.selection: set[Note] = set()
        self._dirty = False
        self._pending: dict | None = None

    # ------------------------------------------------------------- 撤销
    def snapshot(self) -> dict:
        return copy.deepcopy(self.project.to_dict())

    def _restore(self, snap: dict) -> None:
        self.project.load_dict(snap)
        self.selection.clear()
        self._mark_dirty(True)
        self.changed.emit("撤销")
        self.selectionChanged.emit()
        self.audioNeedsReload.emit()

    def undo(self) -> None:
        r = self.undo_stack.undo()
        if r:
            self._restore(r[1])

    def redo(self) -> None:
        r = self.undo_stack.redo()
        if r:
            self._restore(r[1])

    def can_undo(self) -> bool:
        return self.undo_stack.can_undo()

    def can_redo(self) -> bool:
        return self.undo_stack.can_redo()

    def edit(self, label: str, fn, *, emit: bool = True) -> None:
        """立即执行一次可撤销的修改。"""
        before = self.snapshot()
        fn()
        self.project.chart.sort()
        after = self.snapshot()
        self.undo_stack.push(label, before, after)
        self._mark_dirty(True)
        if emit:
            self.changed.emit(label)

    # 拖动等连续操作：按下时 begin()，松开时 commit()
    def begin(self) -> None:
        self._pending = self.snapshot()

    def commit(self, label: str) -> None:
        if self._pending is None:
            return
        before, self._pending = self._pending, None
        after = self.snapshot()
        if before == after:
            return
        self.undo_stack.push(label, before, after)
        self._mark_dirty(True)
        self.changed.emit(label)

    def cancel(self) -> None:
        self._pending = None

    def commit_external(self, label: str, before: dict) -> None:
        """外部（对话框/批量操作）已经改完数据后统一收尾：记撤销 + 标脏 + 通知。"""
        self.project.chart.sort()
        self.undo_stack.push(label, before, self.snapshot())
        self._mark_dirty(True)
        self.changed.emit(label)

    # ------------------------------------------------------------- 选择
    def set_selection(self, notes) -> None:
        new = set(notes)
        if new == self.selection:
            return
        self.selection = new
        self.selectionChanged.emit()

    def add_to_selection(self, notes) -> None:
        self.selection |= set(notes)
        self.selectionChanged.emit()

    def clear_selection(self) -> None:
        if self.selection:
            self.selection.clear()
            self.selectionChanged.emit()

    # ------------------------------------------------------------- 脏标记
    @property
    def dirty(self) -> bool:
        return self._dirty

    def _mark_dirty(self, v: bool) -> None:
        if self._dirty != v:
            self._dirty = v
            self.dirtyChanged.emit(v)

    def mark_clean(self) -> None:
        self._mark_dirty(False)

    def touch(self, label: str = "修改") -> None:
        """非撤销式改动（例如外观参数实时调节）。"""
        self._mark_dirty(True)
        self.changed.emit(label)
