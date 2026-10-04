"""侧栏面板：音符类型、谱面/BPM、外观、导出、设置。"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..doc import Doc
from ..model import FORMATS, BpmSegment
from ..theme import PRESETS, SHAPE_LABELS, SHAPES, NoteStyle, apply_preset


# --------------------------------------------------------------------- 基础控件
class FloatSlider(QWidget):
    """滑块 + 数字输入，既能拖也能精确输入。"""

    valueChanged = Signal(float)

    def __init__(self, minv: float, maxv: float, value: float, step: float = 0.01,
                 decimals: int = 2, suffix: str = "", parent=None):
        super().__init__(parent)
        self._min, self._max, self._step = float(minv), float(maxv), float(step)
        self._decimals = decimals
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.spin = QDoubleSpinBox()
        self.spin.setRange(minv, maxv)
        self.spin.setSingleStep(step)
        self.spin.setDecimals(decimals)
        self.spin.setSuffix(suffix)
        self.spin.setFixedWidth(84)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)
        self.setValue(value)

    def _to_slider(self, v: float) -> int:
        if self._max <= self._min:
            return 0
        return int(round((v - self._min) / (self._max - self._min) * 1000))

    def _from_slider(self, v: int) -> None:
        val = self._min + (self._max - self._min) * v / 1000.0
        self.spin.blockSignals(True)
        self.spin.setValue(val)
        self.spin.blockSignals(False)
        self.valueChanged.emit(self.value())

    def _from_spin(self, v: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(self._to_slider(v))
        self.slider.blockSignals(False)
        self.valueChanged.emit(self.value())

    def value(self) -> float:
        return float(self.spin.value())

    def setValue(self, v: float) -> None:
        self.spin.blockSignals(True)
        self.slider.blockSignals(True)
        self.spin.setValue(float(v))
        self.slider.setValue(self._to_slider(float(v)))
        self.spin.blockSignals(False)
        self.slider.blockSignals(False)


class ColorButton(QPushButton):
    colorChanged = Signal(str)

    def __init__(self, color: str = "#ffffff", parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(QSize(58, 22))
        self.clicked.connect(self._pick)
        self._apply()

    def _apply(self) -> None:
        self.setStyleSheet(
            f"QPushButton{{background:{self._color};border:1px solid #555;border-radius:3px;}}"
            f"QPushButton:hover{{border:2px solid #8ab4f8;}}"
        )
        self.setToolTip(self._color)

    def color(self) -> str:
        return self._color

    def setColor(self, c: str) -> None:
        self._color = str(c)
        self._apply()

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self._color), self, "选择颜色")
        if c.isValid():
            self.setColor(c.name())
            self.colorChanged.emit(self._color)


def _scroll(inner: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setWidget(inner)
    return sa


def _row(*widgets) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    for x in widgets:
        lay.addWidget(x)
    return w


# ------------------------------------------------------------------ 音符类型
class NoteTypePanel(QWidget):
    """左侧：音符类型列表 + 当前类型外观。"""

    changed = Signal()
    orderChanged = Signal()

    def __init__(self, doc: Doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self._loading = False
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        root.addWidget(QLabel("音符类型（行）"))
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setMaximumHeight(160)
        self.list.currentRowChanged.connect(self._on_row)
        self.list.model().rowsMoved.connect(self._on_rows_moved)
        root.addWidget(self.list)

        btns = QHBoxLayout()
        b_add = QPushButton("＋")
        b_add.setToolTip("新建一种音符类型")
        b_dup = QPushButton("复制")
        b_del = QPushButton("－")
        b_del.setToolTip("删除选中的音符类型（其音符会一起删除）")
        b_add.clicked.connect(self.add_type)
        b_dup.clicked.connect(self.dup_type)
        b_del.clicked.connect(self.del_type)
        btns.addWidget(b_add)
        btns.addWidget(b_dup)
        btns.addWidget(b_del)
        btns.addStretch(1)
        root.addLayout(btns)

        box = QGroupBox("当前类型外观")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignRight)
        self.ed_name = QLineEdit()
        self.ed_name.editingFinished.connect(self._apply_name)
        self.cb_shape = QComboBox()
        for s in SHAPES:
            self.cb_shape.addItem(SHAPE_LABELS[s], s)
        self.cb_shape.currentIndexChanged.connect(self._apply_shape)
        self.btn_color = ColorButton("#3fd2ea")
        self.btn_color.colorChanged.connect(self._apply_color)
        self.btn_outline = ColorButton("#141a20")
        self.btn_outline.colorChanged.connect(self._apply_outline)
        self.sl_size = FloatSlider(0.3, 2.5, 1.0, 0.01, 2, "×")
        self.sl_size.valueChanged.connect(self._apply_size)
        self.sl_glow = FloatSlider(0.0, 1.0, 0.0, 0.01, 2)
        self.sl_glow.valueChanged.connect(self._apply_glow)
        self.sl_ow = FloatSlider(0.0, 0.3, 0.10, 0.005, 3)
        self.sl_ow.valueChanged.connect(self._apply_ow)
        form.addRow("名称", self.ed_name)
        form.addRow("形状", self.cb_shape)
        form.addRow("颜色", self.btn_color)
        form.addRow("描边色", self.btn_outline)
        form.addRow("大小", self.sl_size)
        form.addRow("描边宽", self.sl_ow)
        form.addRow("外发光", self.sl_glow)
        root.addWidget(box)

        tip = QLabel("提示：在时间轴上单击=加点，拖拽=移动，\n拖尾部=长条，右键=更多操作。")
        tip.setStyleSheet("color:#8b949e;")
        root.addWidget(tip)
        root.addStretch(1)
        self.refresh()

    # ---------------------------------------------------------------- 数据
    def refresh(self) -> None:
        self._loading = True
        rows = self.doc.project.theme.active_rows()
        self.list.clear()
        for key in rows:
            st = self.doc.project.theme.style(key)
            item = QListWidgetItem(self._chip(st.color), st.name)
            item.setData(Qt.UserRole, key)
            self.list.addItem(item)
        idx = max(0, min(len(rows) - 1, self.doc.state.current_lane))
        self.list.setCurrentRow(idx)
        self._loading = False
        self._load_style()

    def _chip(self, color: str) -> QIcon:
        pm = QPixmap(16, 16)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(QColor(color))
        p.setPen(QColor("#0b0d10"))
        p.drawRoundedRect(1, 1, 14, 14, 3, 3)
        p.end()
        return QIcon(pm)

    def current_key(self) -> str:
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else ""

    def _load_style(self) -> None:
        key = self.current_key()
        if not key:
            return
        st = self.doc.project.theme.style(key)
        self._loading = True
        self.ed_name.setText(st.name)
        i = self.cb_shape.findData(st.shape)
        self.cb_shape.setCurrentIndex(max(0, i))
        self.btn_color.setColor(st.color)
        self.btn_outline.setColor(st.outline)
        self.sl_size.setValue(st.size)
        self.sl_ow.setValue(st.outline_w)
        self.sl_glow.setValue(st.glow)
        self._loading = False

    def _on_row(self, row: int) -> None:
        if self._loading or row < 0:
            return
        self.doc.state.current_lane = row
        self._load_style()
        self.changed.emit()

    def _on_rows_moved(self, *args) -> None:
        if self._loading:
            return
        order = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        self.doc.project.theme.order = order
        # 重排音符所属行
        for n in self.doc.project.chart.notes:
            if n.type in order:
                n.lane = order.index(n.type)
        self.doc.touch("调整行顺序")
        self.orderChanged.emit()

    # ---------------------------------------------------------------- 编辑
    def _apply(self, fn) -> None:
        if self._loading:
            return
        key = self.current_key()
        if not key:
            return
        st = self.doc.project.theme.style(key)
        fn(st)
        self._update_current_item(st)
        self.doc.touch("外观")
        self.changed.emit()

    def _update_current_item(self, st: NoteStyle) -> None:
        it = self.list.currentItem()
        if it is None:
            return
        self._loading = True
        it.setText(st.name)
        it.setIcon(self._chip(st.color))
        self._loading = False

    def _apply_name(self) -> None:
        self._apply(lambda st: setattr(st, "name", self.ed_name.text() or st.key))
        self.orderChanged.emit()

    def _apply_shape(self) -> None:
        self._apply(lambda st: setattr(st, "shape", self.cb_shape.currentData()))

    def _apply_color(self) -> None:
        self._apply(lambda st: setattr(st, "color", self.btn_color.color()))

    def _apply_outline(self) -> None:
        self._apply(lambda st: setattr(st, "outline", self.btn_outline.color()))

    def _apply_size(self) -> None:
        self._apply(lambda st: setattr(st, "size", self.sl_size.value()))

    def _apply_ow(self) -> None:
        self._apply(lambda st: setattr(st, "outline_w", self.sl_ow.value()))

    def _apply_glow(self) -> None:
        self._apply(lambda st: setattr(st, "glow", self.sl_glow.value()))

    def add_type(self) -> None:
        th = self.doc.project.theme
        i = 1
        while f"type{i}" in th.styles:
            i += 1
        key = f"type{i}"
        th.styles[key] = NoteStyle(key, f"新类型{i}", "diamond", "#ffb02e", "#141a20", 0.10, 1.0, 0.0)
        th.order.append(key)
        self.doc.state.current_lane = len(th.active_rows()) - 1
        self.doc.touch("新建类型")
        self.refresh()
        self.changed.emit()
        self.orderChanged.emit()

    def dup_type(self) -> None:
        key = self.current_key()
        if not key:
            return
        th = self.doc.project.theme
        src = th.style(key)
        i = 1
        while f"{key}_copy{i}" in th.styles:
            i += 1
        nk = f"{key}_copy{i}"
        import copy as _copy

        th.styles[nk] = NoteStyle.from_dict(_copy.deepcopy(src.to_dict()))
        th.styles[nk].key = nk
        th.styles[nk].name = src.name + "副本"
        th.order.append(nk)
        self.doc.touch("复制类型")
        self.refresh()
        self.changed.emit()
        self.orderChanged.emit()

    def del_type(self) -> None:
        key = self.current_key()
        if not key:
            return
        th = self.doc.project.theme
        if len(th.active_rows()) <= 1:
            return
        victims = [n for n in self.doc.project.chart.notes if n.type == key]

        def fn():
            th.order = [k for k in th.order if k != key]
            th.styles.pop(key, None)
            if victims:
                self.doc.project.chart.remove_many(victims)

        self.doc.edit("删除类型", fn)
        self.doc.state.current_lane = 0
        self.refresh()
        self.changed.emit()
        self.orderChanged.emit()


# ------------------------------------------------------------------ 谱面 BPM
class ChartPanel(QWidget):
    """谱面设置：BPM 段、offset、吸附、流速、流向。"""

    changed = Signal()
    seekRequested = Signal(float)
    tapRequested = Signal()
    autoBpmRequested = Signal()
    autoNotesRequested = Signal()

    def __init__(self, doc: Doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self._loading = False
        inner = QWidget()
        root = QVBoxLayout(inner)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # --- BPM 段
        box = QGroupBox("BPM 变速")
        v = QVBoxLayout(box)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["起点 (ms)", "BPM"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMaximumHeight(190)
        self.table.itemChanged.connect(self._on_cell)
        row = self.table.selectionModel() if hasattr(self.table, "selectionModel") else None
        v.addWidget(self.table)
        btns = QHBoxLayout()
        b_add = QPushButton("在播放头添加段")
        b_del = QPushButton("删除段")
        b_reset = QPushButton("整段统一 BPM")
        b_add.clicked.connect(self.add_segment)
        b_del.clicked.connect(self.del_segment)
        b_reset.clicked.connect(self.unify_bpm)
        btns.addWidget(b_add)
        btns.addWidget(b_del)
        btns.addWidget(b_reset)
        v.addLayout(btns)

        btns2 = QHBoxLayout()
        b_tap = QPushButton("Tap 测速（连点）")
        b_auto = QPushButton("自动检测 BPM")
        b_notes = QPushButton("自动铺点（起音）")
        b_tap.clicked.connect(self.tapRequested.emit)
        b_auto.clicked.connect(self.autoBpmRequested.emit)
        b_notes.clicked.connect(self.autoNotesRequested.emit)
        btns2.addWidget(b_tap)
        btns2.addWidget(b_auto)
        btns2.addWidget(b_notes)
        v.addLayout(btns2)
        root.addWidget(box)

        # --- 对齐
        box2 = QGroupBox("对齐")
        f2 = QFormLayout(box2)
        self.sp_offset = QDoubleSpinBox()
        self.sp_offset.setRange(-60000, 60000)
        self.sp_offset.setDecimals(1)
        self.sp_offset.setSuffix(" ms")
        self.sp_offset.setToolTip("第 1 拍（beat 0）所在的时间；波形对不上网格时调它")
        self.sp_offset.valueChanged.connect(self._apply_offset)
        f2.addRow("节拍偏移", _row(self.sp_offset, self._btn_offset_from_playhead()))
        self.cb_snap = QComboBox()
        from .timeline import SNAP_CHOICES

        for label, div in SNAP_CHOICES:
            self.cb_snap.addItem(label, div)
        self.cb_snap.currentIndexChanged.connect(self._apply_snap)
        self.chk_snap = QCheckBox("吸附")
        self.chk_snap.toggled.connect(self._apply_snap)
        f2.addRow("网格", _row(self.cb_snap, self.chk_snap))
        root.addWidget(box2)

        # --- 节奏条参数
        box3 = QGroupBox("节奏条")
        f3 = QFormLayout(box3)
        self.cb_flow = QComboBox()
        self.cb_flow.addItem("右 → 左（推荐）", "rtl")
        self.cb_flow.addItem("左 → 右", "ltr")
        self.cb_flow.addItem("双侧向中心汇聚", "converge")
        self.cb_flow.currentIndexChanged.connect(self._apply_render)
        f3.addRow("流向", self.cb_flow)
        self.sl_approach = FloatSlider(0.15, 4.0, 1.0, 0.05, 2, " s")
        self.sl_approach.valueChanged.connect(self._apply_render)
        self.sl_approach.setToolTip("音符从画面边缘走到中间判定点所需时间，越小越快")
        f3.addRow("流速(走完全程)", self.sl_approach)
        self.sl_judge = FloatSlider(0.1, 0.9, 0.5, 0.01, 2)
        self.sl_judge.valueChanged.connect(self._apply_render)
        f3.addRow("判定点位置", self.sl_judge)
        self.sl_size = FloatSlider(0.15, 1.0, 0.46, 0.01, 2)
        self.sl_size.valueChanged.connect(self._apply_render)
        f3.addRow("音符大小", self.sl_size)
        self.sl_spread = FloatSlider(0.0, 0.85, 0.0, 0.01, 2)
        self.sl_spread.valueChanged.connect(self._apply_render)
        self.sl_spread.setToolTip("多种音符类型时，纵向铺开，避免叠在一条线上")
        f3.addRow("多行铺开", self.sl_spread)
        self.cb_ticks = QComboBox()
        self.cb_ticks.addItem("不显示", "off")
        self.cb_ticks.addItem("每拍刻度", "beat")
        self.cb_ticks.addItem("每小节刻度", "bar")
        self.cb_ticks.currentIndexChanged.connect(self._apply_render)
        f3.addRow("节拍刻度", self.cb_ticks)
        self.cb_bg = QComboBox()
        self.cb_bg.addItem("透明（推荐）", "transparent")
        self.cb_bg.addItem("黑底", "black")
        self.cb_bg.addItem("白底", "white")
        self.cb_bg.addItem("自定义", "color")
        self.cb_bg.currentIndexChanged.connect(self._apply_render)
        self.btn_bg = ColorButton("#000000")
        self.btn_bg.colorChanged.connect(self._apply_render)
        f3.addRow("背景", _row(self.cb_bg, self.btn_bg))
        root.addWidget(box3)
        root.addStretch(1)

        sa = _scroll(inner)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(sa)
        self.refresh()

    def _btn_offset_from_playhead(self) -> QWidget:
        b = QPushButton("取播放头")
        b.setFixedWidth(76)
        b.setToolTip("把节拍偏移设为当前播放头位置")
        b.clicked.connect(lambda: self.sp_offset.setValue(self._playhead_ms))
        return b

    _playhead_ms = 0.0

    def set_playhead(self, ms: float) -> None:
        self._playhead_ms = ms

    # ---------------------------------------------------------------- 刷新
    def refresh(self) -> None:
        self._loading = True
        segs = self.doc.project.chart.timemap.segments
        self.table.setRowCount(len(segs))
        for i, s in enumerate(segs):
            it0 = QTableWidgetItem(f"{s.time_ms:.1f}")
            it0.setFlags(it0.flags() & ~Qt.ItemIsEditable if i == 0 else it0.flags() | Qt.ItemIsEditable)
            it1 = QTableWidgetItem(f"{s.bpm:g}")
            self.table.setItem(i, 0, it0)
            self.table.setItem(i, 1, it1)
        rs = self.doc.project.render
        self.sp_offset.setValue(self.doc.project.chart.offset_ms)
        i = self.cb_flow.findData(rs.flow)
        self.cb_flow.setCurrentIndex(max(0, i))
        self.sl_approach.setValue(rs.approach_s)
        self.sl_judge.setValue(rs.judge_ratio)
        self.sl_size.setValue(rs.note_size)
        self.sl_spread.setValue(rs.vspread)
        i = self.cb_ticks.findData(rs.ticks)
        self.cb_ticks.setCurrentIndex(max(0, i))
        i = self.cb_bg.findData(self.doc.project.export.background)
        self.cb_bg.setCurrentIndex(max(0, i))
        self.btn_bg.setColor(self.doc.project.export.bg_color)
        self.chk_snap.setChecked(self.doc.state.snap_on)
        j = self.cb_snap.findData(self.doc.state.snap_divisor)
        self.cb_snap.setCurrentIndex(max(0, j))
        self._loading = False

    # ---------------------------------------------------------------- 编辑
    def _on_cell(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        i, col = item.row(), item.column()
        segs = list(self.doc.project.chart.timemap.segments)
        if not (0 <= i < len(segs)):
            return
        try:
            val = float(item.text())
        except ValueError:
            self.refresh()
            return
        before = self.doc.snapshot()
        if col == 0:
            if i == 0:
                self.refresh()
                return
            segs[i].time_ms = max(segs[i - 1].time_ms + 1.0, val)
        else:
            segs[i].bpm = max(1.0, min(1200.0, val))
        self.doc.project.chart.set_segments(segs, self.doc.project.chart.offset_ms)
        self.doc.undo_stack.push("修改 BPM", before, self.doc.snapshot())
        self.refresh()
        self.changed.emit()

    def add_segment(self) -> None:
        ms = self.snap(self._playhead_ms)
        segs = self.doc.project.chart.timemap.segments
        bpm = segs[-1].bpm if segs else 120.0
        before = self.doc.snapshot()
        self.doc.project.chart.add_segment(ms, bpm)
        self.doc.undo_stack.push("添加 BPM 段", before, self.doc.snapshot())
        self.refresh()
        self.changed.emit()

    def del_segment(self) -> None:
        row = self.table.currentRow()
        if row <= 0:
            return
        before = self.doc.snapshot()
        self.doc.project.chart.remove_segment(row)
        self.doc.undo_stack.push("删除 BPM 段", before, self.doc.snapshot())
        self.refresh()
        self.changed.emit()

    def unify_bpm(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        cur = self.doc.project.chart.timemap.segments[-1].bpm
        bpm, ok = QInputDialog.getDouble(self, "统一 BPM", "把整首曲子设为同一个 BPM：", cur, 1, 1200, 3)
        if not ok:
            return
        before = self.doc.snapshot()
        off = self.doc.project.chart.offset_ms
        self.doc.project.chart.set_segments([BpmSegment(off, bpm)], off)
        self.doc.undo_stack.push("统一 BPM", before, self.doc.snapshot())
        self.refresh()
        self.changed.emit()

    def snap(self, ms: float) -> float:
        st = self.doc.state
        return self.doc.project.chart.timemap.snap(ms, st.snap_divisor) if st.snap_on else ms

    def _apply_offset(self, v: float) -> None:
        if self._loading:
            return
        self.doc.project.chart.set_offset(float(v))
        self.doc.touch("节拍偏移")
        self.changed.emit()

    def _apply_snap(self, *_) -> None:
        if self._loading:
            return
        self.doc.state.snap_on = self.chk_snap.isChecked()
        self.doc.state.snap_divisor = float(self.cb_snap.currentData())
        self.changed.emit()

    def _apply_render(self, *_) -> None:
        if self._loading:
            return
        rs = self.doc.project.render
        rs.flow = self.cb_flow.currentData()
        rs.approach_s = self.sl_approach.value()
        rs.judge_ratio = self.sl_judge.value()
        rs.note_size = self.sl_size.value()
        rs.vspread = self.sl_spread.value()
        rs.ticks = self.cb_ticks.currentData()
        self.doc.project.export.background = self.cb_bg.currentData()
        self.doc.project.export.bg_color = self.btn_bg.color()
        self.doc.touch("节奏条参数")
        self.changed.emit()


# -------------------------------------------------------------------- 外观
class StylePanel(QWidget):
    changed = Signal()

    def __init__(self, doc: Doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self._loading = False
        inner = QWidget()
        root = QVBoxLayout(inner)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        boxp = QGroupBox("预设")
        fp = QVBoxLayout(boxp)
        self.cb_preset = QComboBox()
        self.cb_preset.addItem("（选择预设…）")
        for name in PRESETS:
            self.cb_preset.addItem(name)
        self.cb_preset.currentIndexChanged.connect(self._apply_preset)
        fp.addWidget(self.cb_preset)
        root.addWidget(boxp)

        pl = self.doc.project.theme.plate
        box1 = QGroupBox("底板（那根条）")
        f1 = QFormLayout(box1)
        self.cb_plate = QComboBox()
        self.cb_plate.addItem("有底板", "plate")
        self.cb_plate.addItem("无底板（只有音符）", "none")
        self.cb_plate.currentIndexChanged.connect(self._apply)
        self.btn_plate = ColorButton(pl.color)
        self.btn_plate.colorChanged.connect(self._apply)
        self.sl_alpha = FloatSlider(0.0, 1.0, pl.alpha, 0.01, 2)
        self.sl_alpha.valueChanged.connect(self._apply)
        self.sl_top = FloatSlider(0.0, 0.6, pl.top_light, 0.01, 2)
        self.sl_top.valueChanged.connect(self._apply)
        self.sl_bot = FloatSlider(0.0, 0.8, pl.bottom_shade, 0.01, 2)
        self.sl_bot.valueChanged.connect(self._apply)
        self.sl_radius = FloatSlider(0.0, 0.5, pl.radius, 0.01, 2)
        self.sl_radius.valueChanged.connect(self._apply)
        self.sl_margin = FloatSlider(0.0, 0.3, pl.margin, 0.005, 3)
        self.sl_margin.valueChanged.connect(self._apply)
        self.sl_edge = FloatSlider(0.0, 0.6, pl.edge_line, 0.01, 2)
        self.sl_edge.valueChanged.connect(self._apply)
        self.sl_shadow = FloatSlider(0.0, 1.0, pl.shadow, 0.01, 2)
        self.sl_shadow.valueChanged.connect(self._apply)
        f1.addRow("模式", self.cb_plate)
        f1.addRow("颜色", self.btn_plate)
        f1.addRow("不透明度", self.sl_alpha)
        f1.addRow("顶部提亮", self.sl_top)
        f1.addRow("底部压暗", self.sl_bot)
        f1.addRow("圆角", self.sl_radius)
        f1.addRow("内缩", self.sl_margin)
        f1.addRow("边缘高光", self.sl_edge)
        f1.addRow("外阴影", self.sl_shadow)
        root.addWidget(box1)

        js = self.doc.project.theme.judge
        box2 = QGroupBox("判定点")
        f2 = QFormLayout(box2)
        self.sl_jline = FloatSlider(0.0, 1.0, js.line_alpha, 0.01, 2)
        self.sl_jline.valueChanged.connect(self._apply)
        self.sl_jw = FloatSlider(0.0, 0.2, js.line_w, 0.005, 3)
        self.sl_jw.valueChanged.connect(self._apply)
        self.chk_idle = QCheckBox("判定点常驻 45° 菱形")
        self.chk_idle.setToolTip("不管有没有音符，判定点上都常驻一枚菱形；命中后按音符颜色闪一下")
        self.chk_idle.toggled.connect(self._apply)
        self.btn_idle = ColorButton(js.idle_color)
        self.btn_idle.colorChanged.connect(self._apply)
        self.sl_idle_scale = FloatSlider(0.4, 2.0, js.idle_scale, 0.01, 2)
        self.sl_idle_scale.valueChanged.connect(self._apply)
        self.chk_flash = QCheckBox("命中后按音符颜色闪一下")
        self.chk_flash.toggled.connect(self._apply)
        self.sl_tint = FloatSlider(0.0, 1.0, js.tint, 0.01, 2)
        self.sl_tint.valueChanged.connect(self._apply)
        self.sl_flash_ms = FloatSlider(40, 800, js.flash_ms, 10, 0, " ms")
        self.sl_flash_ms.valueChanged.connect(self._apply)
        self.sl_window = FloatSlider(10, 400, js.window_ms, 1, 0, " ms")
        self.sl_window.valueChanged.connect(self._apply)
        self.chk_pulse = QCheckBox("随 BPM 脉冲")
        self.chk_pulse.toggled.connect(self._apply)
        self.sl_pulse = FloatSlider(0.0, 1.0, js.pulse_strength, 0.01, 2)
        self.sl_pulse.valueChanged.connect(self._apply)
        self.chk_ring = QCheckBox("命中扩散圈")
        self.chk_ring.toggled.connect(self._apply)
        self.sl_glow = FloatSlider(0.0, 1.0, js.glow, 0.01, 2)
        self.sl_glow.valueChanged.connect(self._apply)
        f2.addRow("竖线透明度", self.sl_jline)
        f2.addRow("竖线粗细", self.sl_jw)
        f2.addRow("", self.chk_idle)
        f2.addRow("菱形颜色", self.btn_idle)
        f2.addRow("菱形大小", self.sl_idle_scale)
        f2.addRow("", self.chk_flash)
        f2.addRow("染色强度", self.sl_tint)
        f2.addRow("染色时长", self.sl_flash_ms)
        f2.addRow("判定窗口", self.sl_window)
        f2.addRow("", self.chk_pulse)
        f2.addRow("脉冲强度", self.sl_pulse)
        f2.addRow("", self.chk_ring)
        f2.addRow("高亮光晕", self.sl_glow)
        root.addWidget(box2)

        gs = self.doc.project.theme.ghost
        box3 = QGroupBox("命中后残影（判定点左侧那串灰块）")
        f3 = QFormLayout(box3)
        self.cb_ghost = QComboBox()
        self.cb_ghost.addItem("灰块（还原参考图）", "square")
        self.cb_ghost.addItem("原样式淡出", "fade")
        self.cb_ghost.addItem("不显示", "off")
        self.cb_ghost.currentIndexChanged.connect(self._apply)
        self.cb_gshape = QComboBox()
        for s in SHAPES:
            self.cb_gshape.addItem(SHAPE_LABELS[s], s)
        self.cb_gshape.currentIndexChanged.connect(self._apply)
        self.btn_gcolor = ColorButton(gs.color)
        self.btn_gcolor.colorChanged.connect(self._apply)
        self.sl_galpha = FloatSlider(0.0, 1.0, gs.alpha, 0.01, 2)
        self.sl_galpha.valueChanged.connect(self._apply)
        self.sl_gsize = FloatSlider(0.2, 1.5, gs.size, 0.01, 2)
        self.sl_gsize.valueChanged.connect(self._apply)
        self.sl_gfade = FloatSlider(0.0, 3.0, gs.fade_s, 0.01, 2, " s")
        self.sl_gfade.valueChanged.connect(self._apply)
        f3.addRow("模式", self.cb_ghost)
        f3.addRow("形状", self.cb_gshape)
        f3.addRow("颜色", self.btn_gcolor)
        f3.addRow("不透明度", self.sl_galpha)
        f3.addRow("大小", self.sl_gsize)
        f3.addRow("消失时长", self.sl_gfade)
        root.addWidget(box3)
        root.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(_scroll(inner))
        self.refresh()

    def refresh(self) -> None:
        self._loading = True
        th = self.doc.project.theme
        pl, js, gs = th.plate, th.judge, th.ghost
        self.cb_plate.setCurrentIndex(0 if pl.mode == "plate" else 1)
        self.btn_plate.setColor(pl.color)
        self.sl_alpha.setValue(pl.alpha)
        self.sl_top.setValue(pl.top_light)
        self.sl_bot.setValue(pl.bottom_shade)
        self.sl_radius.setValue(pl.radius)
        self.sl_margin.setValue(pl.margin)
        self.sl_edge.setValue(pl.edge_line)
        self.sl_shadow.setValue(pl.shadow)
        self.sl_jline.setValue(js.line_alpha)
        self.sl_jw.setValue(js.line_w)
        self.chk_idle.setChecked(bool(js.idle))
        self.btn_idle.setColor(js.idle_color)
        self.sl_idle_scale.setValue(js.idle_scale)
        self.chk_flash.setChecked(js.flash)
        self.sl_tint.setValue(js.tint)
        self.sl_flash_ms.setValue(js.flash_ms)
        self.sl_window.setValue(js.window_ms)
        self.chk_pulse.setChecked(js.pulse)
        self.sl_pulse.setValue(js.pulse_strength)
        self.chk_ring.setChecked(js.ring)
        self.sl_glow.setValue(js.glow)
        i = self.cb_ghost.findData(gs.mode)
        self.cb_ghost.setCurrentIndex(max(0, i))
        i = self.cb_gshape.findData(gs.shape)
        self.cb_gshape.setCurrentIndex(max(0, i))
        self.btn_gcolor.setColor(gs.color)
        self.sl_galpha.setValue(gs.alpha)
        self.sl_gsize.setValue(gs.size)
        self.sl_gfade.setValue(gs.fade_s)
        self._loading = False

    def _apply_preset(self, idx: int) -> None:
        if self._loading or idx <= 0:
            return
        apply_preset(self.doc.project.theme, self.cb_preset.itemText(idx))
        self.refresh()
        self.doc.touch("应用预设")
        self.changed.emit()

    def _apply(self, *_) -> None:
        if self._loading:
            return
        th = self.doc.project.theme
        pl, js, gs = th.plate, th.judge, th.ghost
        pl.mode = self.cb_plate.currentData()
        pl.color = self.btn_plate.color()
        pl.alpha = self.sl_alpha.value()
        pl.top_light = self.sl_top.value()
        pl.bottom_shade = self.sl_bot.value()
        pl.radius = self.sl_radius.value()
        pl.margin = self.sl_margin.value()
        pl.edge_line = self.sl_edge.value()
        pl.shadow = self.sl_shadow.value()
        js.line_alpha = self.sl_jline.value()
        js.line_w = self.sl_jw.value()
        js.idle = self.chk_idle.isChecked()
        js.idle_color = self.btn_idle.color()
        js.idle_scale = self.sl_idle_scale.value()
        js.tint = self.sl_tint.value()
        js.flash_ms = self.sl_flash_ms.value()
        js.flash = self.chk_flash.isChecked()
        js.window_ms = self.sl_window.value()
        js.pulse = self.chk_pulse.isChecked()
        js.pulse_strength = self.sl_pulse.value()
        js.ring = self.chk_ring.isChecked()
        js.glow = self.sl_glow.value()
        gs.mode = self.cb_ghost.currentData()
        gs.shape = self.cb_gshape.currentData()
        gs.color = self.btn_gcolor.color()
        gs.alpha = self.sl_galpha.value()
        gs.size = self.sl_gsize.value()
        gs.fade_s = self.sl_gfade.value()
        self.doc.touch("外观")
        self.changed.emit()


# -------------------------------------------------------------------- 导出
class ExportPanel(QWidget):
    exportRequested = Signal()
    stillRequested = Signal()
    changed = Signal()

    def __init__(self, doc: Doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self._loading = False
        inner = QWidget()
        root = QVBoxLayout(inner)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        box = QGroupBox("格式")
        f = QFormLayout(box)
        self.cb_fmt = QComboBox()
        for key, label in FORMATS.items():
            self.cb_fmt.addItem(label, key)
        self.cb_fmt.currentIndexChanged.connect(self._apply)
        self.cb_quality = QComboBox()
        self.cb_quality.addItem("快速（体积大/快）", "fast")
        self.cb_quality.addItem("标准", "balanced")
        self.cb_quality.addItem("高质量（慢）", "high")
        self.cb_quality.currentIndexChanged.connect(self._apply)
        self.sp_fps = QSpinBox()
        self.sp_fps.setRange(1, 240)
        self.sp_fps.valueChanged.connect(self._apply)
        f.addRow("封装 / 编码", self.cb_fmt)
        f.addRow("质量", self.cb_quality)
        f.addRow("帧率", self.sp_fps)
        root.addWidget(box)

        box2 = QGroupBox("画面")
        f2 = QFormLayout(box2)
        self.cb_size = QComboBox()
        for w, h in ((1920, 120), (2560, 160), (1280, 80), (3840, 240), (1920, 1080)):
            self.cb_size.addItem(f"{w}×{h}", (w, h))
        self.cb_size.addItem("自定义", None)
        self.cb_size.currentIndexChanged.connect(self._apply)
        self.sp_w = QSpinBox()
        self.sp_w.setRange(16, 7680)
        self.sp_w.setSingleStep(2)
        self.sp_w.valueChanged.connect(self._apply)
        self.sp_h = QSpinBox()
        self.sp_h.setRange(16, 4320)
        self.sp_h.setSingleStep(2)
        self.sp_h.valueChanged.connect(self._apply)
        self.cb_ss = QComboBox()
        for s, label in ((1, "1× 快"), (2, "2× 抗锯齿（推荐）"), (3, "3× 最锐利")):
            self.cb_ss.addItem(label, s)
        self.cb_ss.currentIndexChanged.connect(self._apply)
        self.cb_valign = QComboBox()
        self.cb_valign.addItem("居中", "center")
        self.cb_valign.addItem("靠上", "top")
        self.cb_valign.addItem("靠下", "bottom")
        self.cb_valign.setToolTip("导出整帧（比如 1920×1080）时，条带放在画面的哪个位置")
        self.cb_valign.currentIndexChanged.connect(self._apply)
        f2.addRow("尺寸预设", self.cb_size)
        f2.addRow("宽 × 高", _row(self.sp_w, QLabel("×"), self.sp_h))
        f2.addRow("超采样", self.cb_ss)
        f2.addRow("条带位置", self.cb_valign)
        root.addWidget(box2)

        box3 = QGroupBox("时间范围")
        f3 = QFormLayout(box3)
        self.cb_range = QComboBox()
        self.cb_range.addItem("整首音频", "song")
        self.cb_range.addItem("从第一个音符到最后一个音符", "notes")
        self.cb_range.addItem("自定义区间", "custom")
        self.cb_range.currentIndexChanged.connect(self._apply)
        self.sp_start = QDoubleSpinBox()
        self.sp_start.setRange(0, 3600000)
        self.sp_start.setDecimals(0)
        self.sp_start.setSuffix(" ms")
        self.sp_start.valueChanged.connect(self._apply)
        self.sp_end = QDoubleSpinBox()
        self.sp_end.setRange(0, 3600000)
        self.sp_end.setDecimals(0)
        self.sp_end.setSuffix(" ms")
        self.sp_end.valueChanged.connect(self._apply)
        self.sp_pad = QDoubleSpinBox()
        self.sp_pad.setRange(0, 20000)
        self.sp_pad.setDecimals(0)
        self.sp_pad.setSuffix(" ms")
        self.sp_pad.valueChanged.connect(self._apply)
        f3.addRow("范围", self.cb_range)
        f3.addRow("起点 / 终点", _row(self.sp_start, self.sp_end))
        f3.addRow("末尾留白", self.sp_pad)
        root.addWidget(box3)

        box4 = QGroupBox("输出")
        f4 = QFormLayout(box4)
        self.ed_out = QLineEdit()
        self.ed_out.editingFinished.connect(self._apply)
        b_browse = QPushButton("…")
        b_browse.setFixedWidth(30)
        b_browse.clicked.connect(self._browse)
        f4.addRow("文件", _row(self.ed_out, b_browse))
        self.chk_open = QCheckBox("完成后打开所在文件夹")
        self.chk_open.toggled.connect(self._apply)
        f4.addRow("", self.chk_open)
        root.addWidget(box4)

        row = QHBoxLayout()
        self.btn_export = QPushButton("导出视频")
        self.btn_export.setMinimumHeight(34)
        self.btn_export.clicked.connect(self.exportRequested.emit)
        b_still = QPushButton("导出当前帧 PNG")
        b_still.clicked.connect(self.stillRequested.emit)
        row.addWidget(self.btn_export, 2)
        row.addWidget(b_still, 1)
        root.addLayout(row)

        self.lbl_hint = QLabel()
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("color:#8b949e;")
        root.addWidget(self.lbl_hint)
        root.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(_scroll(inner))
        self.refresh()

    def refresh(self) -> None:
        self._loading = True
        ex = self.doc.project.export
        i = self.cb_fmt.findData(ex.fmt)
        self.cb_fmt.setCurrentIndex(max(0, i))
        i = self.cb_quality.findData(ex.quality)
        self.cb_quality.setCurrentIndex(max(0, i))
        self.sp_fps.setValue(int(ex.fps))
        j = self.cb_size.findData((ex.width, ex.height))
        self.cb_size.setCurrentIndex(j if j >= 0 else self.cb_size.count() - 1)
        self.sp_w.setValue(int(ex.width))
        self.sp_h.setValue(int(ex.height))
        i = self.cb_ss.findData(int(ex.supersample))
        self.cb_ss.setCurrentIndex(max(0, i))
        i = self.cb_valign.findData(ex.bar_valign)
        self.cb_valign.setCurrentIndex(max(0, i))
        i = self.cb_range.findData(ex.range_mode)
        self.cb_range.setCurrentIndex(max(0, i))
        self.sp_start.setValue(float(ex.t_start))
        self.sp_end.setValue(float(ex.t_end))
        self.sp_pad.setValue(float(ex.padding_ms))
        self.ed_out.setText(ex.output)
        self.chk_open.setChecked(bool(ex.open_after))
        self._loading = False
        self._hint()

    def _hint(self) -> None:
        f = self.doc.project.export.fmt
        tips = {
            "mov_prores": "ProRes 4444：PR / AE / 达芬奇 直接拖进去就有透明通道，文件较大。",
            "mov_qtrle": "QuickTime 动画：剪映等软件兼容好，纯色图形体积小，推荐先用这个。",
            "webm_vp9": "VP9 alpha：体积最小，Chrome / 部分剪辑软件支持；导入前先在剪辑软件里试一下。",
            "webm_vp8": "VP8 alpha：兼容更老的解码器。",
            "png_seq": "PNG 序列：绝对不会翻车，配合「导入序列」使用，体积大。",
            "mp4_black": "黑底 MP4：没有透明通道，在剪辑软件里把混合模式设为「滤色 / 变亮」即可去掉黑底。",
        }
        self.lbl_hint.setText(tips.get(f, ""))

    def _browse(self) -> None:
        ex = self.doc.project.export
        if ex.fmt == "png_seq":
            d = QFileDialog.getExistingDirectory(self, "选择 PNG 序列输出文件夹")
            if d:
                self.ed_out.setText(d)
        else:
            ext = {"mov_prores": "mov", "mov_qtrle": "mov", "webm_vp9": "webm",
                   "webm_vp8": "webm", "mp4_black": "mp4"}.get(ex.fmt, "mov")
            path, _ = QFileDialog.getSaveFileName(self, "导出为", self.ed_out.text() or f"bar.{ext}",
                                                  f"*.{ext}")
            if path:
                self.ed_out.setText(path)
        self._apply()

    def _apply(self, *_) -> None:
        if self._loading:
            return
        ex = self.doc.project.export
        ex.fmt = self.cb_fmt.currentData()
        ex.quality = self.cb_quality.currentData()
        ex.fps = int(self.sp_fps.value())
        size = self.cb_size.currentData()
        if size:
            ex.width, ex.height = int(size[0]), int(size[1])
            self.sp_w.setValue(ex.width)
            self.sp_h.setValue(ex.height)
        else:
            ex.width, ex.height = int(self.sp_w.value()), int(self.sp_h.value())
        ex.supersample = int(self.cb_ss.currentData())
        ex.bar_valign = self.cb_valign.currentData()
        ex.range_mode = self.cb_range.currentData()
        ex.t_start = float(self.sp_start.value())
        ex.t_end = float(self.sp_end.value())
        ex.padding_ms = float(self.sp_pad.value())
        ex.output = self.ed_out.text()
        ex.open_after = self.chk_open.isChecked()
        self._hint()
        self.changed.emit()


# -------------------------------------------------------------------- 设置
class SettingsPanel(QWidget):
    """应用级设置（不改工程数据）。"""

    changed = Signal()

    def __init__(self, doc: Doc, settings, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.settings = settings
        self._loading = False
        inner = QWidget()
        root = QVBoxLayout(inner)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        box = QGroupBox("音频 / 播放")
        f = QFormLayout(box)
        self.ed_ffmpeg = QLineEdit()
        self.ed_ffmpeg.editingFinished.connect(self._apply)
        b = QPushButton("…")
        b.setFixedWidth(30)
        b.clicked.connect(self._browse_ffmpeg)
        f.addRow("ffmpeg", _row(self.ed_ffmpeg, b))
        self.sl_latency = FloatSlider(-300, 300, 0, 1, 0, " ms")
        self.sl_latency.setToolTip("播放头和声音对不齐时在这校准：画面比声音慢就调小")
        self.sl_latency.valueChanged.connect(self._apply)
        f.addRow("音画延迟校准", self.sl_latency)
        self.sl_volume = FloatSlider(0.0, 1.0, 0.85, 0.01, 2)
        self.sl_volume.valueChanged.connect(self._apply)
        f.addRow("音量", self.sl_volume)
        self.cb_rate = QComboBox()
        for r in (0.5, 0.75, 1.0, 1.25, 1.5):
            self.cb_rate.addItem(f"{r:g}×", r)
        self.cb_rate.currentIndexChanged.connect(self._apply)
        f.addRow("试听速度", self.cb_rate)
        self.sp_tap = QDoubleSpinBox()
        self.sp_tap.setRange(30, 1000)
        self.sp_tap.setSuffix(" ms")
        self.sp_tap.setToolTip("打点时两次点击小于这个间隔就忽略")
        self.sp_tap.valueChanged.connect(self._apply)
        f.addRow("打点最小间隔", self.sp_tap)
        self.chk_safe = QCheckBox("预览显示安全框和尺寸标注")
        self.chk_safe.toggled.connect(self._apply)
        f.addRow("", self.chk_safe)
        root.addWidget(box)

        box2 = QGroupBox("快捷键（部分）")
        v = QVBoxLayout(box2)
        lbl = QLabel(
            "V / B / R / Tab 切换「选择拖拽 / 放置音符 / 框选区间」三种鼠标模式\n"
            "放置模式：单击加点，按住拖动连续刷（一次撤销）\n"
            "选择模式：空白拖动框选音符，拖音符移动，拖右侧把手变长条\n"
            "标尺或波形处直接拖动 = 拖播放头；Shift+拖动 或 R 模式 = 框选时间区间\n"
            "空格 播放/暂停      F 打点（播放中随时按）\n"
            "Ctrl+Z / Ctrl+Y 撤销/重做      Del 删除选中\n"
            "Ctrl+A 全选      Ctrl+C/V 复制粘贴      Ctrl+D 向后复制\n"
            "数字键 1~9 切类型      Q/W 上下换行\n"
            "Ctrl+滚轮 缩放      Shift+滚轮 平移      中键拖动 平移\n"
            "L 设循环      M 节拍器      E 导出      Ctrl+Shift+V 导入视频"
        )
        lbl.setWordWrap(True)
        v.addWidget(lbl)
        root.addWidget(box2)
        root.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(_scroll(inner))
        self.refresh()

    def refresh(self) -> None:
        s = self.settings
        self._loading = True
        self.ed_ffmpeg.setText(s.ffmpeg)
        self.sl_latency.setValue(s.latency_ms)
        self.sl_volume.setValue(s.volume)
        i = self.cb_rate.findData(s.rate)
        self.cb_rate.setCurrentIndex(max(0, i))
        self.sp_tap.setValue(s.tap_min_ms)
        self.chk_safe.setChecked(s.show_safe)
        self._loading = False

    def _browse_ffmpeg(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "选择 ffmpeg.exe", "", "ffmpeg (ffmpeg.exe)")
        if p:
            self.ed_ffmpeg.setText(p)
            self._apply()

    def _apply(self, *_) -> None:
        if self._loading:
            return
        s = self.settings
        s.ffmpeg = self.ed_ffmpeg.text().strip()
        s.latency_ms = self.sl_latency.value()
        s.volume = self.sl_volume.value()
        s.rate = float(self.cb_rate.currentData())
        s.tap_min_ms = self.sp_tap.value()
        s.show_safe = self.chk_safe.isChecked()
        s.save()
        self.changed.emit()
