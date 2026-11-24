"""侧栏面板：音符类型、谱面/BPM、外观、导出、设置。"""

from __future__ import annotations

import copy
import os

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

from ..audio import FFMPEG_NAME
from ..doc import Doc
from ..exporter import estimate_size_mb
from ..i18n import tr, trf
from ..model import FORMATS, BpmSegment
from .. import segments as segments_io
from ..theme import (PRESETS, SHAPE_LABELS, SHAPES, FadeSettings, JudgeSegment,
                     NoteStyle, apply_preset)
from .dialogs import choose_skin_image

from PySide6.QtWidgets import QMessageBox  # noqa: E402

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
        c = QColorDialog.getColor(QColor(self._color), self, tr("选择颜色"))
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
        self.cb_feedback = QComboBox()
        self.cb_feedback.addItem("自动（按大小建议）", "auto")
        self.cb_feedback.addItem("A · 浅下沉（圆点膨胀）", "A")
        self.cb_feedback.addItem("B · 深下沉（放大+抖动）", "B")
        self.cb_feedback.addItem("C · 微下沉（无特效）", "C")
        self.cb_feedback.setToolTip(
            "仅哑铃判定点生效：这类音符命中时，长条下沉到浅处还是深处、"
            "以及用哪种命中反馈动画。默认按尺寸给建议，可手动指定。")
        self.cb_feedback.currentIndexChanged.connect(self._apply_feedback)
        self.btn_img = QPushButton("导入图像…")
        self.btn_img_clear = QPushButton("恢复形状")
        self.lbl_img = QLabel("未设置")
        self.lbl_img.setStyleSheet("color:#8b949e;")
        self.btn_img.clicked.connect(self._pick_image)
        self.btn_img_clear.clicked.connect(self._clear_image)
        form.addRow("名称", self.ed_name)
        form.addRow("形状", self.cb_shape)
        form.addRow("颜色", self.btn_color)
        form.addRow("描边色", self.btn_outline)
        form.addRow("大小", self.sl_size)
        form.addRow("描边宽", self.sl_ow)
        form.addRow("外发光", self.sl_glow)
        form.addRow("命中反馈", self.cb_feedback)
        form.addRow("图像皮肤", _row(self.btn_img, self.btn_img_clear))
        form.addRow("", self.lbl_img)
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
            item = QListWidgetItem(self._chip(st.color, st.image), st.name)
            item.setData(Qt.UserRole, key)
            self.list.addItem(item)
        idx = max(0, min(len(rows) - 1, self.doc.state.current_lane))
        self.list.setCurrentRow(idx)
        self._loading = False
        self._load_style()

    def _chip(self, color: str, image: str = "") -> QIcon:
        pm = QPixmap(16, 16)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        drawn = False
        if image:
            src = QPixmap(image)
            if not src.isNull():
                p.drawPixmap(1, 1, src.scaled(14, 14, Qt.KeepAspectRatio,
                                              Qt.SmoothTransformation))
                drawn = True
        if not drawn:
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
        i = self.cb_feedback.findData(str(getattr(st, "feedback", "auto") or "auto"))
        self.cb_feedback.setCurrentIndex(max(0, i))
        self.lbl_img.setText(os.path.basename(st.image) if st.image else tr("未设置"))
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
        it.setIcon(self._chip(st.color, st.image))
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

    def _apply_feedback(self) -> None:
        kind = self.cb_feedback.currentData()
        self._apply(lambda st: setattr(st, "feedback", kind))

    def _pick_image(self) -> None:
        path = choose_skin_image(self)
        if not path:
            return
        self._apply(lambda st: setattr(st, "image", path))
        self._load_style()

    def _clear_image(self) -> None:
        self._apply(lambda st: setattr(st, "image", ""))
        self._load_style()

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

        self.doc.edit("删除类型", fn, include_theme=True)
        self.doc.state.current_lane = 0
        self.refresh()
        self.changed.emit()
        self.orderChanged.emit()

# ------------------------------------------------------------------ 谱面 BPM
class ChartPanel(QWidget):
    """谱面设置：BPM 段、offset、吸附、流速、流向。"""

    changed = Signal()
    renderChanged = Signal()
    seekRequested = Signal(float)
    tapRequested = Signal()
    autoBpmRequested = Signal()
    clipSplitRequested = Signal()

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
        self.lbl_clip = QLabel("未选中音频片段")
        self.lbl_clip.setStyleSheet("color:#8b949e;")
        self.lbl_clip.setWordWrap(True)
        v.addWidget(self.lbl_clip)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["起点 (ms)", "BPM"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMaximumHeight(190)
        self.table.itemChanged.connect(self._on_cell)
        v.addWidget(self.table)
        btns = QHBoxLayout()
        b_add = QPushButton("在当前时间切分音视频")
        b_add.setToolTip("在当前时间把选中的视频/音频片段切成两段（链接片段一起切）")
        b_del = QPushButton("删除段")
        b_reset = QPushButton("整段统一 BPM")
        b_add.clicked.connect(self.clipSplitRequested.emit)
        b_del.clicked.connect(self.del_segment)
        b_reset.clicked.connect(self.unify_bpm)
        self.b_add, self.b_del, self.b_reset = b_add, b_del, b_reset
        btns.addWidget(b_add)
        btns.addWidget(b_del)
        btns.addWidget(b_reset)
        v.addLayout(btns)

        btns2 = QHBoxLayout()
        b_tap = QPushButton("Tap 测速（连点）")
        b_auto = QPushButton("自动检测 BPM")
        b_tap.clicked.connect(self.tapRequested.emit)
        b_auto.clicked.connect(self.autoBpmRequested.emit)
        self.b_tap, self.b_auto = b_tap, b_auto
        btns2.addWidget(b_tap)
        btns2.addWidget(b_auto)
        v.addLayout(btns2)
        root.addWidget(box)

        # --- 对齐
        box2 = QGroupBox("对齐")
        f2 = QFormLayout(box2)
        self.sp_offset = QDoubleSpinBox()
        self.sp_offset.setRange(-10_000_000, 10_000_000)
        self.sp_offset.setDecimals(1)
        self.sp_offset.setSuffix(" ms")
        self.sp_offset.setToolTip("第 1 拍（beat 0）在时间轴上的位置；只作用于当前选中的音频片段")
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

        # 判定点之后的速度：参考视频里命中后音符会「嗖」地飞走
        self.sl_post = FloatSlider(1.0, 8.0, 1.0, 0.1, 1, "×")
        self.sl_post.setToolTip("过了判定点之后的速度倍率；1 = 与判定点前一致，越大越快离场")
        self.sl_post.valueChanged.connect(self._apply_render)
        f3.addRow("判定点后流速", self.sl_post)

        # 节奏条本身的画布尺寸（哑铃之类的纵向元素需要足够的高度）
        self.sp_bar_w = QSpinBox()
        self.sp_bar_w.setRange(320, 3840)
        self.sp_bar_w.setSingleStep(10)
        self.sp_bar_w.setSuffix(" px")
        self.sp_bar_w.valueChanged.connect(self._apply_bar_size)
        self.sp_bar_h = QSpinBox()
        self.sp_bar_h.setRange(40, 600)
        self.sp_bar_h.setSingleStep(10)
        self.sp_bar_h.setSuffix(" px")
        self.sp_bar_h.valueChanged.connect(self._apply_bar_size)
        f3.addRow("条带尺寸", _row(self.sp_bar_w, self.sp_bar_h))
        self.sl_content = FloatSlider(0.30, 3.00, 1.0, 0.05, 2, "×")
        self.sl_content.setToolTip(
            "音符 / 水印 / 计数字号的整体缩放基准。调「条带尺寸」只换画布，"
            "不会把 Beat 和字体一起放大；要让内容 visibly 更大，用这个滑杆。")
        self.sl_content.valueChanged.connect(self._apply_render)
        f3.addRow("内容缩放", self.sl_content)
        root.addWidget(box3)
        root.addStretch(1)

        sa = _scroll(inner)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(sa)
        self.refresh()

    def _btn_offset_from_playhead(self) -> QWidget:
        b = QPushButton("取当前时间")
        b.setFixedWidth(88)
        b.setToolTip("把节拍偏移设为当前时间位置")
        b.clicked.connect(lambda: self.sp_offset.setValue(self._playhead_ms))
        return b

    _playhead_ms = 0.0

    def set_playhead(self, ms: float) -> None:
        self._playhead_ms = ms

    def _clip(self):
        """当前选中的音频片段；选中视频时用它链接的音频。"""
        p = self.doc.project
        c = p.clip_by_id(self.doc.state.selected_clip_id)
        if c is None:
            return None
        if c.kind == "audio":
            return c
        for x in p.linked_clips(c):
            if x.kind == "audio":
                return x
        return None

    # ---------------------------------------------------------------- 刷新
    def refresh(self) -> None:
        self._loading = True
        clip = self._clip()
        selected = self.doc.project.clip_by_id(self.doc.state.selected_clip_id)
        if clip is None:
            if selected is None:
                self.lbl_clip.setText(tr("未选中音频片段 —— 先在音频轨点击一段"))
            else:
                self.lbl_clip.setText(trf("片段：{name}（无音频）", name=selected.label))
            self.table.setRowCount(0)
            self.table.setEnabled(False)
            self.sp_offset.setValue(0.0)
            self.sp_offset.setEnabled(False)
            for b in (self.b_del, self.b_reset, self.b_tap, self.b_auto):
                b.setEnabled(False)
            self.b_add.setEnabled(selected is not None)
        else:
            self.lbl_clip.setText(trf("片段：{name}（BPM/偏移只作用于这一段）",
                                      name=clip.label))
            tm = clip.timemap
            segs = tm.segments
            self.table.setEnabled(True)
            self.table.setRowCount(len(segs))
            for i, s in enumerate(segs):
                t_abs = clip.source_to_timeline(s.time_ms)
                it0 = QTableWidgetItem(f"{t_abs:.1f}")
                it0.setFlags(it0.flags() & ~Qt.ItemIsEditable if i == 0
                             else it0.flags() | Qt.ItemIsEditable)
                it1 = QTableWidgetItem(f"{s.bpm:g}")
                self.table.setItem(i, 0, it0)
                self.table.setItem(i, 1, it1)
            self.sp_offset.setEnabled(True)
            self.sp_offset.setValue(clip.source_to_timeline(tm.offset_ms))
            for b in (self.b_add, self.b_del, self.b_reset, self.b_tap, self.b_auto):
                b.setEnabled(True)
        rs = self.doc.project.render
        self.sp_offset_and_render(rs)
        self._loading = False

    def sp_offset_and_render(self, rs) -> None:
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
        self.sl_post.setValue(getattr(rs, "post_speed", 1.0))
        self.sp_bar_w.setValue(int(rs.width))
        self.sp_bar_h.setValue(int(rs.height))
        self.sl_content.setValue(getattr(rs, "content_scale", 1.0))
        self.chk_snap.setChecked(self.doc.state.snap_on)
        j = self.cb_snap.findData(self.doc.state.snap_divisor)
        self.cb_snap.setCurrentIndex(max(0, j))

    # ---------------------------------------------------------------- 编辑
    def _on_cell(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        clip = self._clip()
        if clip is None:
            self.refresh()
            return
        i, col = item.row(), item.column()
        segs = list(clip.timemap.segments)
        if not (0 <= i < len(segs)):
            return
        try:
            val = float(item.text())
        except ValueError:
            self.refresh()
            return
        before = self.doc.snapshot(include_clips=True)
        if col == 0:
            if i == 0:
                self.refresh()
                return
            src = clip.timeline_to_source(val)
            segs[i].time_ms = max(segs[i - 1].time_ms + 1.0, src)
        else:
            segs[i].bpm = max(1.0, min(1200.0, val))
        clip.timemap.set_segments(segs, clip.timemap.offset_ms)
        self.doc.commit_external("修改 BPM", before)
        self.refresh()
        self.changed.emit()

    def del_segment(self) -> None:
        clip = self._clip()
        if clip is None:
            return
        row = self.table.currentRow()
        if row <= 0:
            return
        before = self.doc.snapshot(include_clips=True)
        segs = list(clip.timemap.segments)
        if 0 <= row < len(segs) and len(segs) > 1:
            segs.pop(row)
        clip.timemap.set_segments(segs, clip.timemap.offset_ms)
        self.doc.commit_external("删除 BPM 段", before)
        self.refresh()
        self.changed.emit()

    def unify_bpm(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        clip = self._clip()
        if clip is None:
            return
        cur = clip.timemap.segments[-1].bpm if clip.timemap.segments else 120.0
        bpm, ok = QInputDialog.getDouble(self, tr("统一 BPM"), tr("把这一段设为同一个 BPM："),
                                         cur, 1, 1200, 3)
        if not ok:
            return
        before = self.doc.snapshot(include_clips=True)
        off = clip.timemap.offset_ms
        clip.timemap.set_segments([BpmSegment(off, bpm)], off)
        self.doc.commit_external("统一 BPM", before)
        self.refresh()
        self.changed.emit()

    def snap(self, ms: float) -> float:
        clip = self._clip()
        st = self.doc.state
        if clip is None or not st.snap_on:
            return ms
        src = clip.timeline_to_source(ms)
        return clip.source_to_timeline(clip.timemap.snap(src, st.snap_divisor))

    def _apply_offset(self, v: float) -> None:
        if self._loading:
            return
        clip = self._clip()
        if clip is None:
            return
        src = clip.timeline_to_source(float(v))
        segs = list(clip.timemap.segments)
        clip.timemap.set_segments(segs, src)
        self.doc.touch("节拍偏移")
        self.changed.emit()

    def _apply_snap(self, *_) -> None:
        if self._loading:
            return
        self.doc.state.snap_on = self.chk_snap.isChecked()
        self.doc.state.snap_divisor = float(self.cb_snap.currentData())
        self.changed.emit()

    def _apply_bar_size(self, *_) -> None:
        """条带画布尺寸：同时把「导出尺寸」跟着改（两者原本一致时才跟随，避免覆盖用户自选）。"""
        if self._loading:
            return
        rs = self.doc.project.render
        ex = self.doc.project.export
        old_w, old_h = int(rs.width), int(rs.height)
        w, h = int(self.sp_bar_w.value()), int(self.sp_bar_h.value())
        follows = (int(ex.width), int(ex.height)) == (old_w, old_h)
        rs.width, rs.height = w, h
        if follows:
            ex.width, ex.height = w, h
        self.doc.touch("条带尺寸")
        self.changed.emit()
        self.renderChanged.emit()

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
        rs.post_speed = self.sl_post.value()
        rs.content_scale = self.sl_content.value()
        self.doc.project.export.background = self.cb_bg.currentData()
        self.doc.project.export.bg_color = self.btn_bg.color()
        self.doc.touch("节奏条参数")
        self.changed.emit()
        self.renderChanged.emit()

# -------------------------------------------------------------------- 外观
class StylePanel(QWidget):
    changed = Signal()
    seekRequested = Signal(float)

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
        self.cb_judge_mode = QComboBox()
        self.cb_judge_mode.addItem("菱形式（原样）", "diamond")
        self.cb_judge_mode.addItem("哑铃式（新，随 Beat 滑动）", "dumbbell")
        self.cb_judge_mode.setToolTip(
            "哑铃式：上下两个圆点 + 竖杆 + 一根随 Beat 上下滑动的长条，"
            "到达最低点时正好是 Beat 命中判定区的瞬间")
        self.cb_judge_mode.currentIndexChanged.connect(self._apply)
        f2.addRow("判定点样式", self.cb_judge_mode)
        self.sl_jline = FloatSlider(0.0, 1.0, js.line_alpha, 0.01, 2)
        self.sl_jline.valueChanged.connect(self._apply)
        self.sl_jw = FloatSlider(0.0, 0.2, js.line_w, 0.005, 3)
        self.sl_jw.valueChanged.connect(self._apply)
        self.chk_idle = QCheckBox("判定点常驻")
        self.chk_idle.setToolTip("不管有没有音符，判定点上都常驻一枚标记；命中后按音符颜色闪一下")
        self.chk_idle.toggled.connect(self._apply)
        self.cb_judge_shape = QComboBox()
        for s in SHAPES:
            self.cb_judge_shape.addItem(SHAPE_LABELS[s], s)
        self.cb_judge_shape.currentIndexChanged.connect(self._apply)
        self.btn_judge_img = QPushButton("导入图像…")
        self.btn_judge_clear = QPushButton("恢复形状")
        self.lbl_judge_img = QLabel("未设置")
        self.lbl_judge_img.setStyleSheet("color:#8b949e;")
        self.btn_judge_img.clicked.connect(self._pick_judge_image)
        self.btn_judge_clear.clicked.connect(self._clear_judge_image)
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
        f2.addRow("形状", self.cb_judge_shape)
        f2.addRow("图像皮肤", _row(self.btn_judge_img, self.btn_judge_clear))
        f2.addRow("", self.lbl_judge_img)
        f2.addRow("标记颜色", self.btn_idle)
        f2.addRow("标记大小", self.sl_idle_scale)
        f2.addRow("", self.chk_flash)
        f2.addRow("染色强度", self.sl_tint)
        f2.addRow("染色时长", self.sl_flash_ms)
        f2.addRow("判定窗口", self.sl_window)
        f2.addRow("", self.chk_pulse)
        f2.addRow("脉冲强度", self.sl_pulse)
        f2.addRow("", self.chk_ring)
        f2.addRow("高亮光晕", self.sl_glow)
        root.addWidget(box2)

        box2b = QGroupBox("哑铃参数（判定点样式选「哑铃式」时生效）")
        f2b = QFormLayout(box2b)
        self.btn_dbcolor = ColorButton(str(getattr(js, "db_color", "#e9edf0") or "#e9edf0"))
        self.btn_dbcolor.setToolTip("哑铃本体的常驻颜色（命中时会按音符颜色短暂染色）")
        self.btn_dbcolor.colorChanged.connect(self._apply)
        f2b.addRow("哑铃颜色", self.btn_dbcolor)
        self.sl_db_circle = FloatSlider(0.015, 0.120,
                                        getattr(js, "db_circle_r", 0.040), 0.005, 3)
        self.sl_db_circle.setToolTip("两端圆点的常态半径（相对条高）；嫌小就调大")
        self.sl_db_circle.valueChanged.connect(self._apply)
        f2b.addRow("圆点常态大小", self.sl_db_circle)
        self.sl_db_len = FloatSlider(0.10, 0.45, getattr(js, "db_half_len", 0.31), 0.01, 2)
        self.sl_db_len.setToolTip("哑铃长度：中轴到上下圆点的距离（相对条高）；始终以 Beat 中轴上下对称")
        self.sl_db_gscale = FloatSlider(0.50, 2.00, getattr(js, "db_scale", 1.0), 0.05, 2)
        self.sl_db_gscale.setToolTip("哑铃整体大小：圆点、胶囊、杆宽、长度一起缩放")
        self.sl_db_dipa = FloatSlider(0.20, 1.20, getattr(js, "db_dip_a", 0.77), 0.01, 2)
        self.sl_db_dipa.setToolTip("动画 A（浅）：胶囊最低点 = 中轴往下多少倍半长；1.0 正好到下圆点")
        self.sl_db_dipb = FloatSlider(0.80, 2.20, getattr(js, "db_dip_b", 1.45), 0.01, 2)
        self.sl_db_dipb.setToolTip("动画 B（深）：胶囊最低点；>1 表示穿过下圆点")
        self.sl_db_dipc = FloatSlider(0.00, 1.00, getattr(js, "db_dip_c", 0.50), 0.01, 2)
        self.sl_db_dipc.setToolTip("动画 C（微）：只轻轻下压一点，命中时没有任何特效")
        self.sl_db_capw = FloatSlider(0.10, 0.50, getattr(js, "db_capsule_w", 0.26), 0.01, 2)
        self.sl_db_caph = FloatSlider(0.04, 0.24, getattr(js, "db_capsule_h", 0.10), 0.01, 2)
        self.sl_db_cappop = FloatSlider(1.00, 2.00, getattr(js, "db_cap_pop", 1.16), 0.01, 2)
        self.sl_db_cappop.setToolTip("命中到底部时胶囊自身的轻微膨胀倍率")
        self.sl_db_pop = FloatSlider(1.00, 3.00, getattr(js, "db_pop_scale", 1.65), 0.05, 2)
        self.sl_db_pop.setToolTip("动画 A：命中瞬间两侧圆点的膨胀倍率")
        self.sl_db_scale = FloatSlider(1.00, 1.60, getattr(js, "db_shake_scale", 1.12), 0.01, 2)
        self.sl_db_scale.setToolTip("动画 B：命中瞬间整体的放大倍率")
        self.sl_db_shake = FloatSlider(0.000, 0.080, getattr(js, "db_shake_amp", 0.025), 0.002, 3)
        self.sl_db_shake.setToolTip("动画 B：命中瞬间的抖动幅度（相对条高）")
        self.sl_db_rise = FloatSlider(0.30, 0.90, getattr(js, "db_rise_frac", 0.71), 0.01, 2)
        self.sl_db_rise.setToolTip("一个节拍内「上升」占的比例，剩下的是下落段")
        self.sl_db_gap = FloatSlider(0.30, 8.00, getattr(js, "db_idle_gap_ms", 1500.0) / 1000.0,
                                     0.1, 1, " s")
        self.sl_db_gap.setToolTip("距下一颗 Beat 超过这么久时，长条停止往复、静止在最高点")
        self.sl_db_glide = FloatSlider(0.00, 2.00, getattr(js, "db_idle_glide_ms", 400.0) / 1000.0, 0.05, 2, " s")
        self.sl_db_glide.setToolTip("进出空闲时，长条从当前位置平滑滑到目标位置所用的时间（0 = 直接跳）")
        self.sl_db_cappopms = FloatSlider(0.10, 1.20, getattr(js, "db_cap_pop_ms", 280.0) / 1000.0, 0.02, 2, " s")
        self.sl_db_cappopms.setToolTip("胶囊膨胀的整段时长（命中前一半渐大、命中后一半渐小）")
        self.sl_db_top = FloatSlider(0.40, 1.20, getattr(js, "db_travel_top", 0.85), 0.01, 2)
        self.sl_db_top.setToolTip("胶囊常态最高点（也是空闲停留位置），相对半个哑铃长度")
        self.chk_db_topsplit = QCheckBox("分模式设置最高点")
        self.chk_db_topsplit.toggled.connect(self._apply)
        self.sl_db_topa = FloatSlider(0.40, 1.20, getattr(js, "db_top_a", 0.85), 0.01, 2)
        self.sl_db_topb = FloatSlider(0.40, 1.20, getattr(js, "db_top_b", 0.85), 0.01, 2)
        self.sl_db_topc = FloatSlider(0.40, 1.20, getattr(js, "db_top_c", 0.85), 0.01, 2)
        self.chk_db_topsplit.toggled.connect(self._sync_top_split)
        self.sl_db_popms = FloatSlider(0.10, 1.20, getattr(js, "db_pop_ms", 320.0) / 1000.0, 0.02, 2, " s")
        self.sl_db_popms.setToolTip("A：圆点膨胀的整段时长（命中前一半渐大、命中后一半渐小）")
        self.sl_db_shakems = FloatSlider(0.10, 1.20, getattr(js, "db_shake_ms", 320.0) / 1000.0, 0.02, 2, " s")
        self.sl_db_shakems.setToolTip("B：整体放大的整段时长（命中前一半渐大、命中后一半渐小）")
        self.sl_db_tintramp = FloatSlider(0.00, 0.60, getattr(js, "db_tint_ramp_ms", 140.0) / 1000.0, 0.01, 2, " s")
        self.sl_db_tintramp.setToolTip("命中前多久开始向该 Beat 的颜色渐变")
        self.chk_db_capsplit = QCheckBox("分模式设置胶囊膨胀")
        self.chk_db_capsplit.toggled.connect(self._apply)
        self.sl_db_capa = FloatSlider(1.00, 2.00, getattr(js, "db_cap_pop_a", 1.16), 0.01, 2, "×")
        self.sl_db_capb = FloatSlider(1.00, 2.00, getattr(js, "db_cap_pop_b", 1.16), 0.01, 2, "×")
        self.sl_db_capc = FloatSlider(1.00, 2.00, getattr(js, "db_cap_pop_c", 1.16), 0.01, 2, "×")
        self.chk_db_capsplit.toggled.connect(self._sync_cap_split)
        self.cb_db_ease = QComboBox()
        self.cb_db_ease.addItem("不缓入缓出（匀速）", "none")
        self.cb_db_ease.addItem("仅最高点缓入缓出", "top")
        self.cb_db_ease.addItem("最高点+最低点都缓动", "both")
        self.cb_db_ease.setToolTip("胶囊在两端的速度曲线；最低点就是命中点")
        self.cb_db_ease.currentIndexChanged.connect(self._apply)
        self.sl_db_easestr = FloatSlider(0.0, 1.0, getattr(js, "db_ease_strength", 0.7), 0.01, 2)
        self.sl_db_easestr.setToolTip("缓入缓出的强度：0 = 等同于匀速，1 = 最明显的减速")
        self.sl_db_shakehz = FloatSlider(4.0, 60.0, getattr(js, "db_shake_hz", 30.0), 1.0, 1, " Hz")
        self.sl_db_shakehz.setToolTip("B：抖动的频率，越低越晃、越高越碎")
        self.sl_db_shakedur = FloatSlider(0.10, 2.00, getattr(js, "db_shake_dur_ms", 600.0) / 1000.0, 0.05, 2, " s")
        self.sl_db_shakedur.setToolTip("B：抖动持续多久（从命中开始衰减到 0）")
        self.btn_dboutline = ColorButton(str(getattr(js, "db_outline_color", "") or "#141a20"))
        self.btn_dboutline.setToolTip("哑铃与胶囊的描边颜色")
        self.btn_dboutline.colorChanged.connect(self._apply)
        self.chk_dboutline_auto = QCheckBox("描边跟随音符色")
        self.chk_dboutline_auto.setToolTip("勾选时描边用各音符类型自己的描边色，忽略上面的颜色")
        self.chk_dboutline_auto.setChecked(not str(getattr(js, "db_outline_color", "") or ""))
        self.chk_dboutline_auto.toggled.connect(self._apply)
        self.sl_dboutw = FloatSlider(0.0, 3.0, getattr(js, "db_outline_w", 1.0), 0.05, 2, "×")
        self.sl_dboutw.setToolTip("描边宽度倍率，0 = 不描边")
        self.sl_dbouta = FloatSlider(0.0, 1.0, getattr(js, "db_outline_alpha", 1.0), 0.01, 2)
        self.sl_dbouta.setToolTip("描边透明度")
        for w in (self.sl_db_circle, self.sl_db_len, self.sl_db_gscale, self.sl_db_dipa,
                  self.sl_db_dipb, self.sl_db_dipc, self.sl_db_capw, self.sl_db_caph,
                  self.sl_db_cappop, self.sl_db_pop, self.sl_db_scale, self.sl_db_shake,
                  self.sl_db_rise, self.sl_db_gap, self.sl_db_glide,
                  self.sl_db_cappopms, self.sl_db_top, self.sl_db_topa,
                  self.sl_db_topb, self.sl_db_topc, self.sl_db_popms,
                  self.sl_db_shakems, self.sl_db_tintramp, self.sl_db_capa,
                  self.sl_db_capb, self.sl_db_capc, self.sl_db_easestr,
                  self.sl_db_shakehz, self.sl_db_shakedur, self.sl_dboutw,
                  self.sl_dbouta):
            w.valueChanged.connect(self._apply)
        f2b.addRow("哑铃长度", self.sl_db_len)
        f2b.addRow("哑铃整体大小", self.sl_db_gscale)
        f2b.addRow("A 浅下沉", self.sl_db_dipa)
        f2b.addRow("B 深下沉", self.sl_db_dipb)
        f2b.addRow("C 微下沉", self.sl_db_dipc)
        f2b.addRow("胶囊宽度/高度", _row(self.sl_db_capw, self.sl_db_caph))
        f2b.addRow("胶囊膨胀", self.sl_db_cappop)
        f2b.addRow("A 圆点膨胀", self.sl_db_pop)
        f2b.addRow("B 整体放大", self.sl_db_scale)
        f2b.addRow("B 抖动幅度", self.sl_db_shake)
        f2b.addRow("上升占比", self.sl_db_rise)
        f2b.addRow("空闲静止阈值", self.sl_db_gap)
        f2b.addRow("空闲滑动时长", self.sl_db_glide)
        f2b.addRow("胶囊膨胀时长", self.sl_db_cappopms)
        f2b.addRow("胶囊最高点", self.sl_db_top)
        f2b.addRow("", self.chk_db_topsplit)
        f2b.addRow("A/B/C 最高点", _row(self.sl_db_topa, self.sl_db_topb))
        f2b.addRow("", self.sl_db_topc)
        f2b.addRow("圆点膨胀时长", self.sl_db_popms)
        f2b.addRow("整体放大时长", self.sl_db_shakems)
        f2b.addRow("变色渐入", self.sl_db_tintramp)
        f2b.addRow("", self.chk_db_capsplit)
        f2b.addRow("A/B/C 胶囊膨胀", _row(self.sl_db_capa, self.sl_db_capb))
        f2b.addRow("", self.sl_db_capc)
        f2b.addRow("运动缓动", self.cb_db_ease)
        f2b.addRow("缓动强度", self.sl_db_easestr)
        f2b.addRow("抖动频率", self.sl_db_shakehz)
        f2b.addRow("抖动时长", self.sl_db_shakedur)
        f2b.addRow("描边颜色", _row(self.btn_dboutline, self.chk_dboutline_auto))
        f2b.addRow("描边宽度/透明度", _row(self.sl_dboutw, self.sl_dbouta))
        root.addWidget(box2b)

        ax = self.doc.project.theme.axis
        box2c = QGroupBox("横向中轴线（基础设置，所有判定区样式都生效）")
        f2c = QFormLayout(box2c)
        self.chk_ax = QCheckBox("显示轴线")
        self.chk_ax.setChecked(bool(getattr(ax, "enabled", True)))
        self.chk_ax.toggled.connect(self._apply)
        self.sl_axw = FloatSlider(0.000, 0.120, getattr(ax, "width", 0.012), 0.002, 3)
        self.sl_axw.setToolTip("线宽，相对条高")
        self.sl_axa = FloatSlider(0.0, 1.0, getattr(ax, "alpha", 0.20), 0.01, 2)
        self.btn_axc = ColorButton(str(getattr(ax, "color", "#ffffff")))
        self.btn_axc.colorChanged.connect(self._apply)
        self.sl_axow = FloatSlider(0.000, 0.060, getattr(ax, "outline_w", 0.0), 0.002, 3)
        self.sl_axow.setToolTip("描边宽度；0 = 不描边")
        self.sl_axoa = FloatSlider(0.0, 1.0, getattr(ax, "outline_alpha", 0.6), 0.01, 2)
        self.btn_axoc = ColorButton(str(getattr(ax, "outline_color", "#141a20")))
        self.btn_axoc.colorChanged.connect(self._apply)
        for w in (self.sl_axw, self.sl_axa, self.sl_axow, self.sl_axoa):
            w.valueChanged.connect(self._apply)
        f2c.addRow("", self.chk_ax)
        f2c.addRow("线宽/透明度", _row(self.sl_axw, self.sl_axa))
        f2c.addRow("线条颜色", self.btn_axc)
        f2c.addRow("描边宽度/透明度", _row(self.sl_axow, self.sl_axoa))
        f2c.addRow("描边颜色", self.btn_axoc)
        root.addWidget(box2c)

        box2d = QGroupBox("判定区分段（按时间）")
        v2d = QVBoxLayout(box2d)
        self.list_seg = QListWidget()
        self.list_seg.setMaximumHeight(110)
        v2d.addWidget(self.list_seg)
        rowt = QHBoxLayout()
        self.sp_segtime = QDoubleSpinBox()
        self.sp_segtime.setRange(0.0, 36_000_000.0)
        self.sp_segtime.setDecimals(1)
        self.sp_segtime.setSingleStep(100.0)
        self.sp_segtime.setSuffix(" ms")
        self.sp_segtime.setToolTip("切分点的目标时间；可以直接输入或点「取播放头」")
        self.b_seg_now = QPushButton("取播放头")
        self.b_seg_jump = QPushButton("跳到选中段起点")
        rowt.addWidget(self.sp_segtime, 1)
        rowt.addWidget(self.b_seg_now)
        v2d.addLayout(rowt)
        rowb = QHBoxLayout()
        self.b_seg_split = QPushButton("在此时刻切分")
        self.b_seg_split.setToolTip("以左边输入的时间为界，把所在的段一分为二（两段先沿用同一套配置）")
        self.b_seg_del = QPushButton("删除选中段")
        rowb.addWidget(self.b_seg_split)
        rowb.addWidget(self.b_seg_del)
        v2d.addLayout(rowb)
        rowb2 = QHBoxLayout()
        self.b_seg_save = QPushButton("当前外观 → 选中段")
        self.b_seg_load = QPushButton("选中段 → 面板")
        self.b_seg_save.setToolTip("把下面这些判定区参数复制进选中的段落")
        self.b_seg_load.setToolTip("把选中段落的参数载入面板，方便继续编辑")
        rowb2.addWidget(self.b_seg_save)
        rowb2.addWidget(self.b_seg_load)
        rowb2.addWidget(self.b_seg_jump)
        v2d.addLayout(rowb2)
        rowf = QHBoxLayout()
        self.chk_fade = QCheckBox("段落间淡入淡出")
        self.sl_fadems = FloatSlider(0.10, 3.00, 0.40, 0.05, 2, " s")
        self.sl_fadems.setToolTip("整段过渡时长（边界前后各一半）")
        rowf.addWidget(self.chk_fade)
        rowf.addWidget(self.sl_fadems, 1)
        v2d.addLayout(rowf)
        self.cb_fadescope = QComboBox()
        self.cb_fadescope.addItem("淡入淡出作用：仅判定区", "judge")
        self.cb_fadescope.addItem("淡入淡出作用：判定区 + 横向轴线", "judge_axes")
        self.cb_fadescope.addItem("淡入淡出作用：判定区 + 轴线 + 音符", "all")
        v2d.addWidget(self.cb_fadescope)
        rowio = QHBoxLayout()
        self.b_seg_exp = QPushButton("导出分段配置…")
        self.b_seg_imp = QPushButton("读取分段配置…")
        rowio.addWidget(self.b_seg_exp)
        rowio.addWidget(self.b_seg_imp)
        v2d.addLayout(rowio)
        root.addWidget(box2d)
        # 把「判定区分段」提到表单最上面：选段/找时间不用滚到底
        root.removeWidget(box2d)
        root.insertWidget(1, box2d)
        self.box_seg = box2d

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
        self.sl_gdelay = FloatSlider(0.0, 1000.0, gs.delay_ms, 10, 0, " ms")
        self.sl_gdelay.setToolTip("命中后延迟多久开始变形（0 = 命中瞬间开始）")
        self.sl_gdelay.valueChanged.connect(self._apply)
        self.sl_gmorph = FloatSlider(0.0, 800.0, gs.morph_ms, 10, 0, " ms")
        self.sl_gmorph.setToolTip("从原音符旋转 90° 并渐变成灰块所需时间；0 = 瞬间切换")
        self.sl_gmorph.valueChanged.connect(self._apply)
        self.sl_gfade = FloatSlider(0.0, 3.0, gs.fade_s, 0.01, 2, " s")
        self.sl_gfade.valueChanged.connect(self._apply)
        f3.addRow("模式", self.cb_ghost)
        f3.addRow("形状", self.cb_gshape)
        f3.addRow("颜色", self.btn_gcolor)
        f3.addRow("不透明度", self.sl_galpha)
        f3.addRow("大小", self.sl_gsize)
        f3.addRow("延迟", self.sl_gdelay)
        f3.addRow("过渡时长", self.sl_gmorph)
        f3.addRow("消失时长", self.sl_gfade)
        root.addWidget(box3)
        root.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.time_provider = None          # 由主窗口注入：返回当前播放位置(ms)
        self.b_seg_split.clicked.connect(self.seg_split)
        self.b_seg_now.clicked.connect(self.seg_take_playhead)
        self.b_seg_jump.clicked.connect(self.seg_seek_start)
        self.b_seg_del.clicked.connect(self.seg_delete)
        self.b_seg_save.clicked.connect(self.seg_capture)
        self.b_seg_load.clicked.connect(self.seg_to_panel)
        self.b_seg_exp.clicked.connect(self.seg_export)
        self.b_seg_imp.clicked.connect(self.seg_import)
        self.chk_fade.toggled.connect(self._apply)
        self.sl_fadems.valueChanged.connect(self._apply)
        self.cb_fadescope.currentIndexChanged.connect(self._apply)
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
        i = self.cb_judge_mode.findData(str(getattr(js, "mode", "diamond")))
        self.cb_judge_mode.setCurrentIndex(max(0, i))
        self.btn_dbcolor.setColor(str(getattr(js, "db_color", "#e9edf0") or "#e9edf0"))
        self.sl_db_circle.setValue(getattr(js, "db_circle_r", 0.040))
        self.sl_db_len.setValue(getattr(js, "db_half_len", 0.31))
        self.sl_db_gscale.setValue(getattr(js, "db_scale", 1.0))
        self.sl_db_dipa.setValue(getattr(js, "db_dip_a", 0.77))
        self.sl_db_dipb.setValue(getattr(js, "db_dip_b", 1.45))
        self.sl_db_dipc.setValue(getattr(js, "db_dip_c", 0.50))
        self.sl_db_capw.setValue(getattr(js, "db_capsule_w", 0.26))
        self.sl_db_caph.setValue(getattr(js, "db_capsule_h", 0.10))
        self.sl_db_cappop.setValue(getattr(js, "db_cap_pop", 1.16))
        self.sl_db_pop.setValue(getattr(js, "db_pop_scale", 1.65))
        self.sl_db_scale.setValue(getattr(js, "db_shake_scale", 1.12))
        self.sl_db_shake.setValue(getattr(js, "db_shake_amp", 0.025))
        self.sl_db_rise.setValue(getattr(js, "db_rise_frac", 0.71))
        self.sl_db_gap.setValue(getattr(js, "db_idle_gap_ms", 1500.0) / 1000.0)
        self.sl_db_glide.setValue(getattr(js, "db_idle_glide_ms", 400.0) / 1000.0)
        self.sl_db_cappopms.setValue(getattr(js, "db_cap_pop_ms", 280.0) / 1000.0)
        self.sl_db_top.setValue(getattr(js, "db_travel_top", 0.85))
        self.chk_db_topsplit.setChecked(bool(getattr(js, "db_top_split", False)))
        self.sl_db_topa.setValue(getattr(js, "db_top_a", 0.85))
        self.sl_db_topb.setValue(getattr(js, "db_top_b", 0.85))
        self.sl_db_topc.setValue(getattr(js, "db_top_c", 0.85))
        self.sl_db_popms.setValue(getattr(js, "db_pop_ms", 320.0) / 1000.0)
        self.sl_db_shakems.setValue(getattr(js, "db_shake_ms", 320.0) / 1000.0)
        self.sl_db_tintramp.setValue(getattr(js, "db_tint_ramp_ms", 140.0) / 1000.0)
        self.chk_db_capsplit.setChecked(bool(getattr(js, "db_cap_pop_split", False)))
        self.sl_db_capa.setValue(getattr(js, "db_cap_pop_a", 1.16))
        self.sl_db_capb.setValue(getattr(js, "db_cap_pop_b", 1.16))
        self.sl_db_capc.setValue(getattr(js, "db_cap_pop_c", 1.16))
        i = self.cb_db_ease.findData(str(getattr(js, "db_ease_mode", "both")))
        self.cb_db_ease.setCurrentIndex(max(0, i))
        self.sl_db_easestr.setValue(getattr(js, "db_ease_strength", 0.7))
        self.sl_db_shakehz.setValue(getattr(js, "db_shake_hz", 30.0))
        self.sl_db_shakedur.setValue(getattr(js, "db_shake_dur_ms", 600.0) / 1000.0)
        self.btn_dboutline.setColor(str(getattr(js, "db_outline_color", "") or "#141a20"))
        self.chk_dboutline_auto.setChecked(not str(getattr(js, "db_outline_color", "") or ""))
        self.sl_dboutw.setValue(getattr(js, "db_outline_w", 1.0))
        self.sl_dbouta.setValue(getattr(js, "db_outline_alpha", 1.0))
        self.chk_ax.setChecked(bool(getattr(self.doc.project.theme.axis, "enabled", True)))
        self.sl_axw.setValue(getattr(self.doc.project.theme.axis, "width", 0.012))
        self.sl_axa.setValue(getattr(self.doc.project.theme.axis, "alpha", 0.20))
        self.btn_axc.setColor(str(getattr(self.doc.project.theme.axis, "color", "#ffffff")))
        self.sl_axow.setValue(getattr(self.doc.project.theme.axis, "outline_w", 0.0))
        self.sl_axoa.setValue(getattr(self.doc.project.theme.axis, "outline_alpha", 0.6))
        self.btn_axoc.setColor(str(getattr(self.doc.project.theme.axis, "outline_color", "#141a20")))
        th = self.doc.project.theme
        fd = th.fade
        self.chk_fade.setChecked(bool(getattr(fd, "enabled", True)))
        self.sl_fadems.setValue(float(getattr(fd, "ms", 400.0)) / 1000.0)
        i = self.cb_fadescope.findData(str(getattr(fd, "scope", "judge")))
        self.cb_fadescope.setCurrentIndex(max(0, i))
        self._refresh_segments()
        self._sync_cap_split()
        self._sync_top_split()
        i = self.cb_judge_shape.findData(js.shape)
        self.cb_judge_shape.setCurrentIndex(max(0, i))
        self.lbl_judge_img.setText(os.path.basename(js.image) if js.image else tr("未设置"))
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
        self.sl_gdelay.setValue(gs.delay_ms)
        self.sl_gmorph.setValue(gs.morph_ms)
        self.sl_gfade.setValue(gs.fade_s)
        self._loading = False

    def _apply_preset(self, idx: int) -> None:
        if self._loading or idx <= 0:
            return
        apply_preset(self.doc.project.theme, self.cb_preset.itemText(idx))
        self.refresh()
        self.doc.touch("应用预设")
        self.changed.emit()

    def _sync_top_split(self) -> None:
        """勾了「分模式设置最高点」才允许单独调 A/B/C 的最高点。"""
        on = self.chk_db_topsplit.isChecked()
        for w in (self.sl_db_topa, self.sl_db_topb, self.sl_db_topc):
            w.setEnabled(on)

    # ------------------------------------------------------------ 判定区分段
    @staticmethod
    def _fmt_ms(ms: float) -> str:
        ms = max(0.0, float(ms))
        return f"{int(ms // 60000):02d}:{(ms % 60000) / 1000:06.3f}"

    def _refresh_segments(self) -> None:
        segs = self.doc.project.theme.segments
        row = self.list_seg.currentRow()
        self.list_seg.blockSignals(True)
        self.list_seg.clear()
        for s in segs:
            end = "结尾" if float(s.end_ms) <= float(s.start_ms) else self._fmt_ms(s.end_ms)
            mode = "哑铃" if str(getattr(s.style, "mode", "diamond")) == "dumbbell" else "菱形"
            self.list_seg.addItem(f"{self._fmt_ms(s.start_ms)} → {end}    [{mode}]")
        self.list_seg.blockSignals(False)
        if 0 <= row < len(segs):
            self.list_seg.setCurrentRow(row)

    def _seg_time(self) -> float:
        try:
            return float(self.time_provider()) if self.time_provider else 0.0
        except Exception:
            return 0.0

    def seg_take_playhead(self) -> None:
        self.sp_segtime.setValue(max(0.0, self._seg_time()))

    def seg_seek_start(self) -> None:
        row = self.list_seg.currentRow()
        segs = self.doc.project.theme.segments
        if 0 <= row < len(segs):
            self.seekRequested.emit(float(segs[row].start_ms))

    def seg_split(self) -> None:
        t = float(self.sp_segtime.value())
        th = self.doc.project.theme
        segs = sorted(th.segments, key=lambda s: float(s.start_ms))
        if not segs:
            segs = [JudgeSegment(0.0, t, copy.deepcopy(th.judge)),
                    JudgeSegment(t, 0.0, copy.deepcopy(th.judge))]
        else:
            hit = None
            for s in segs:
                st, en = float(s.start_ms), float(s.end_ms)
                if t >= st and (en <= st or t < en):
                    hit = s
                    break
            if hit is None:
                segs.append(JudgeSegment(t, 0.0, copy.deepcopy(th.judge)))
            elif t > float(hit.start_ms) + 1.0:
                old_end = float(hit.end_ms)
                hit.end_ms = t
                segs.append(JudgeSegment(t, old_end, copy.deepcopy(hit.style)))
        if len(segs) > 1 and abs(float(segs[-1].start_ms)) < 1e-6:
            pass
        th.segments = sorted(segs, key=lambda s: float(s.start_ms))
        self._refresh_segments()
        self.doc.touch("判定区分段")
        self.changed.emit()

    def seg_delete(self) -> None:
        row = self.list_seg.currentRow()
        segs = self.doc.project.theme.segments
        if not (0 <= row < len(segs)):
            return
        segs.pop(row)
        self._refresh_segments()
        self.doc.touch("删除判定区分段")
        self.changed.emit()

    def seg_capture(self) -> None:
        row = self.list_seg.currentRow()
        segs = self.doc.project.theme.segments
        if not (0 <= row < len(segs)):
            QMessageBox.information(self, tr("先选一段"), tr("请先在列表里选中要写入的段落。"))
            return
        segs[row].style = copy.deepcopy(self.doc.project.theme.judge)
        self._refresh_segments()
        self.doc.touch("写入判定区分段")
        self.changed.emit()

    def seg_to_panel(self) -> None:
        row = self.list_seg.currentRow()
        segs = self.doc.project.theme.segments
        if not (0 <= row < len(segs)):
            return
        self.doc.project.theme.judge = copy.deepcopy(segs[row].style)
        self.refresh()
        self.changed.emit()

    def seg_export(self) -> None:
        base = os.path.dirname(self.doc.project.path) if self.doc.project.path else os.getcwd()
        path, _ = QFileDialog.getSaveFileName(
            self, tr("导出分段配置"), os.path.join(base, "judge_segments.json"),
            "JSON (*.json)")
        if not path:
            return
        try:
            saved = segments_io.save(self.doc.project.theme, path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("导出失败"), str(e))
            return
        self.statusTip()
        QMessageBox.information(self, tr("已导出"), saved)

    def seg_import(self) -> None:
        base = os.path.dirname(self.doc.project.path) if self.doc.project.path else os.getcwd()
        path, _ = QFileDialog.getOpenFileName(
            self, tr("读取分段配置"), base, "JSON (*.json)")
        if not path:
            return
        try:
            n = segments_io.load(self.doc.project.theme, path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, tr("读取失败"), str(e))
            return
        self.refresh()
        self.doc.touch("读取判定区分段")
        self.changed.emit()
        QMessageBox.information(self, tr("已读取"), trf("读入 {n} 个判定区分段", n=n))

    def _sync_cap_split(self) -> None:
        """勾了「分模式设置胶囊膨胀」才允许单独调 A/B/C 的膨胀倍率。"""
        on = self.chk_db_capsplit.isChecked()
        for w in (self.sl_db_capa, self.sl_db_capb, self.sl_db_capc):
            w.setEnabled(on)

    def _pick_judge_image(self) -> None:
        path = choose_skin_image(self)
        if not path:
            return
        self.doc.project.theme.judge.image = path
        self._load_style()
        self.doc.touch("外观")
        self.changed.emit()

    def _clear_judge_image(self) -> None:
        self.doc.project.theme.judge.image = ""
        self._load_style()
        self.doc.touch("外观")
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
        js.mode = self.cb_judge_mode.currentData()
        js.db_color = self.btn_dbcolor.color()
        js.db_circle_r = self.sl_db_circle.value()
        js.db_half_len = self.sl_db_len.value()
        js.db_scale = self.sl_db_gscale.value()
        js.db_dip_a = self.sl_db_dipa.value()
        js.db_dip_b = self.sl_db_dipb.value()
        js.db_dip_c = self.sl_db_dipc.value()
        js.db_capsule_w = self.sl_db_capw.value()
        js.db_capsule_h = self.sl_db_caph.value()
        js.db_cap_pop = self.sl_db_cappop.value()
        js.db_pop_scale = self.sl_db_pop.value()
        js.db_shake_scale = self.sl_db_scale.value()
        js.db_shake_amp = self.sl_db_shake.value()
        js.db_rise_frac = self.sl_db_rise.value()
        js.db_idle_gap_ms = self.sl_db_gap.value() * 1000.0
        js.db_idle_glide_ms = self.sl_db_glide.value() * 1000.0
        js.db_cap_pop_ms = self.sl_db_cappopms.value() * 1000.0
        js.db_travel_top = self.sl_db_top.value()
        js.db_top_split = self.chk_db_topsplit.isChecked()
        js.db_top_a = self.sl_db_topa.value()
        js.db_top_b = self.sl_db_topb.value()
        js.db_top_c = self.sl_db_topc.value()
        js.db_pop_ms = self.sl_db_popms.value() * 1000.0
        js.db_shake_ms = self.sl_db_shakems.value() * 1000.0
        js.db_tint_ramp_ms = self.sl_db_tintramp.value() * 1000.0
        js.db_cap_pop_split = self.chk_db_capsplit.isChecked()
        js.db_cap_pop_a = self.sl_db_capa.value()
        js.db_cap_pop_b = self.sl_db_capb.value()
        js.db_cap_pop_c = self.sl_db_capc.value()
        js.db_ease_mode = self.cb_db_ease.currentData()
        js.db_ease_strength = self.sl_db_easestr.value()
        js.db_shake_hz = self.sl_db_shakehz.value()
        js.db_shake_dur_ms = self.sl_db_shakedur.value() * 1000.0
        js.db_outline_color = ("" if self.chk_dboutline_auto.isChecked()
                               else self.btn_dboutline.color())
        js.db_outline_w = self.sl_dboutw.value()
        js.db_outline_alpha = self.sl_dbouta.value()
        ax = self.doc.project.theme.axis
        ax.enabled = self.chk_ax.isChecked()
        ax.width = self.sl_axw.value()
        ax.alpha = self.sl_axa.value()
        ax.color = self.btn_axc.color()
        ax.outline_w = self.sl_axow.value()
        ax.outline_alpha = self.sl_axoa.value()
        ax.outline_color = self.btn_axoc.color()
        fd = self.doc.project.theme.fade
        fd.enabled = self.chk_fade.isChecked()
        fd.ms = self.sl_fadems.value() * 1000.0
        fd.scope = self.cb_fadescope.currentData()
        row = self.list_seg.currentRow()
        segs = self.doc.project.theme.segments
        if 0 <= row < len(segs):
            segs[row].style = copy.deepcopy(js)
        js.shape = self.cb_judge_shape.currentData()
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
        gs.delay_ms = self.sl_gdelay.value()
        gs.morph_ms = self.sl_gmorph.value()
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
        self._custom_size = False
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
        self.sp_fps.setToolTip("60fps 的文件体积约为 30fps 的 2 倍；节奏条一般 30fps 就够")
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
        self.cb_size.currentIndexChanged.connect(self._on_size_changed)
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

    def _on_size_changed(self, *_) -> None:
        if self._loading:
            return
        self._custom_size = self.cb_size.currentData() is None
        self._update_size_enabled()
        self._apply()

    def _update_size_enabled(self) -> None:
        self.sp_w.setEnabled(self._custom_size)
        self.sp_h.setEnabled(self._custom_size)

    def reset_size_mode(self) -> None:
        """切换工程时重新按工程尺寸匹配预设/自定义。"""
        self._custom_size = False

    def refresh(self) -> None:
        self._loading = True
        ex = self.doc.project.export
        i = self.cb_fmt.findData(ex.fmt)
        self.cb_fmt.setCurrentIndex(max(0, i))
        i = self.cb_quality.findData(ex.quality)
        self.cb_quality.setCurrentIndex(max(0, i))
        self.sp_fps.setValue(int(ex.fps))
        # 自定义尺寸要保留“自定义”选择，不能因为宽高刚好等于某个预设就被吸回去。
        target_size = (int(ex.width), int(ex.height))
        j = -1
        if not self._custom_size:
            for k in range(self.cb_size.count()):
                data = self.cb_size.itemData(k)
                if isinstance(data, (tuple, list)) and len(data) == 2:
                    if (int(data[0]), int(data[1])) == target_size:
                        j = k
                        break
            if j < 0:
                self._custom_size = True
        if self._custom_size:
            j = self.cb_size.count() - 1
        self.cb_size.setCurrentIndex(max(0, j))
        self.sp_w.setValue(int(ex.width))
        self.sp_h.setValue(int(ex.height))
        self._update_size_enabled()
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
        ex = self.doc.project.export
        f = ex.fmt
        tips = {
            "mov_prores": "ProRes 4444：PR / AE / 达芬奇 直接拖进去就有透明通道，体积很大。",
            "mov_qtrle": "QuickTime 动画：剪映等软件兼容好，纯色图形体积中等，推荐先用这个。",
            "webm_vp9": "VP9 alpha：体积最小（约为 qtrle 的 1/15），Chrome / 部分剪辑软件支持；导入前先试一下。",
            "webm_vp8": "VP8 alpha：兼容老一点的解码器，体积比 VP9 稍大。",
            "png_seq": "PNG 序列：绝对不会翻车，配合「导入序列」使用，体积很大。",
            "mp4_black": "黑底 MP4：没有透明通道，在剪辑软件里把混合模式设为「滤色 / 变亮」即可去掉黑底，体积小。",
        }
        est = estimate_size_mb(f, ex.width, ex.height, int(ex.fps) * 60)
        self.lbl_hint.setText(
            tr(tips.get(f, ""))
            + "\n"
            + trf("粗略体积：约 {size:.0f} MB/分钟（{w}×{h}@{fps}fps；内容不同会有差异）",
                  size=est, w=ex.width, h=ex.height, fps=ex.fps))

    def _browse(self) -> None:
        ex = self.doc.project.export
        if ex.fmt == "png_seq":
            d = QFileDialog.getExistingDirectory(self, tr("选择 PNG 序列输出文件夹"))
            if d:
                self.ed_out.setText(d)
        else:
            ext = {"mov_prores": "mov", "mov_qtrle": "mov", "webm_vp9": "webm",
                   "webm_vp8": "webm", "mp4_black": "mp4"}.get(ex.fmt, "mov")
            path, _ = QFileDialog.getSaveFileName(self, tr("导出为"), self.ed_out.text() or f"bar.{ext}",
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
        self.sl_latency.setToolTip("当前时间和声音对不齐时在这校准：画面比声音慢就调小")
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
        self.chk_updates = QCheckBox("启动时检查更新")
        self.chk_updates.setToolTip(
            "启动约 2.5 秒后查一次 GitHub Release；成功后 6 小时内不重复，失败会稍后重试")
        self.chk_updates.toggled.connect(self._apply)
        f.addRow("", self.chk_updates)
        root.addWidget(box)

        box2 = QGroupBox("快捷键（部分）")
        v = QVBoxLayout(box2)
        lbl = QLabel(
            "V / B / R / Tab 切换「选择拖拽 / 放置音符 / 框选区间」三种鼠标模式\n"
            "放置模式：单击加点，按住拖动连续刷（一次撤销）\n"
            "选择模式：空白拖动框选音符，拖音符移动，拖右侧把手变长条\n"
            "标尺或波形处直接拖动 = 移动当前时间；Shift+拖动 或 R 模式 = 框选时间区间\n"
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
        self.chk_updates.setChecked(bool(s.check_updates))
        self._loading = False

    def _browse_ffmpeg(self) -> None:
        # macOS 的 ffmpeg 没有扩展名，原生对话框按扩展名过滤会选不中，
        # 所以 Mac 上直接用「所有文件」。
        flt = "ffmpeg (ffmpeg.exe);;所有文件 (*)" if os.name == "nt" else "所有文件 (*)"
        p, _ = QFileDialog.getOpenFileName(self, tr(f"选择 {FFMPEG_NAME}"), "", flt)
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
        s.check_updates = self.chk_updates.isChecked()
        s.save()
        self.changed.emit()


# ------------------------------------------------------------ 文字与统计
class StatsPanel(QWidget):
    """水印、Beats 计数（含统计分段 / Phase / 位置）、可视化进度条。"""

    changed = Signal()
    renderChanged = Signal()

    def __init__(self, doc: Doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self._loading = False
        self.time_provider = None
        inner = QWidget()
        root = QVBoxLayout(inner)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        rs = self.doc.project.render

        # ------------------------------------------------------------ 水印
        box1 = QGroupBox("水印文字")
        f1 = QFormLayout(box1)
        self.ed_wm = QLineEdit()
        self.ed_wm.setPlaceholderText("留空则不显示")
        self.ed_wm.setToolTip("显示在角落的自由输入文字")
        self.ed_wm.editingFinished.connect(self._apply)
        self.cb_wmpos = QComboBox()
        for label, key in (("左下", "bottom_left"), ("右下", "bottom_right"),
                           ("左上", "top_left"), ("右上", "top_right")):
            self.cb_wmpos.addItem(label, key)
        self.cb_wmpos.currentIndexChanged.connect(self._apply)
        self.sl_wmsize = FloatSlider(0.04, 0.60, 0.14, 0.01, 2)
        self.sl_wmsize.setToolTip("字号，相对节奏条高度")
        self.sl_wmsize.valueChanged.connect(self._apply)
        self.btn_wm = ColorButton("#ffffff")
        self.btn_wm.colorChanged.connect(self._apply)
        self.sl_wmalpha = FloatSlider(0.0, 1.0, 0.55, 0.01, 2)
        self.sl_wmalpha.valueChanged.connect(self._apply)
        f1.addRow("内容", self.ed_wm)
        f1.addRow("位置", self.cb_wmpos)
        f1.addRow("字号/颜色", _row(self.sl_wmsize, self.btn_wm))
        f1.addRow("透明度", self.sl_wmalpha)
        root.addWidget(box1)

        # ------------------------------------------------------- Beats 计数
        box2 = QGroupBox("Beats 计数")
        f2 = QFormLayout(box2)
        self.chk_beat = QCheckBox("显示")
        self.chk_beat.toggled.connect(self._apply)
        self.chk_phase = QCheckBox("前面加 Phase 段落号")
        self.chk_phase.setToolTip('开启后显示成 "Phase 2   BEAT 12"')
        self.chk_phase.toggled.connect(self._apply)
        self.cb_beatmode = QComboBox()
        self.cb_beatmode.addItem("已命中音符数", "notes")
        self.cb_beatmode.addItem("按 BPM 的拍号", "beats")
        self.cb_beatmode.currentIndexChanged.connect(self._apply)
        self.cb_beatdisplay = QComboBox()
        self.cb_beatdisplay.addItem("仅显示总 Beats 数", "total")
        self.cb_beatdisplay.addItem("仅显示当前段落的 Beats 数", "segment")
        self.cb_beatdisplay.addItem("当前段 // 总（两者都显示）", "both")
        self.cb_beatdisplay.setToolTip("只影响 Beats 数值；没分段时三种都一样")
        self.cb_beatdisplay.currentIndexChanged.connect(self._apply)
        self.sl_beatsize = FloatSlider(0.04, 0.60, 0.14, 0.01, 2)
        self.sl_beatsize.setToolTip("字号，相对节奏条高度")
        self.sl_beatsize.valueChanged.connect(self._apply)
        self.btn_beat = ColorButton("#ffffff")
        self.btn_beat.colorChanged.connect(self._apply)
        self.sl_beatalpha = FloatSlider(0.0, 1.0, 0.92, 0.01, 2)
        self.sl_beatalpha.valueChanged.connect(self._apply)
        self.sl_bx = FloatSlider(0.0, 1.0, 0.94, 0.01, 2)
        self.sl_bx.setToolTip("横向位置（0=最左，1=最右；文字以该点为锚）")
        self.sl_bx.valueChanged.connect(self._apply)
        self.sl_by = FloatSlider(0.0, 1.0, 0.90, 0.01, 2)
        self.sl_by.setToolTip("纵向位置（0=最上，1=最下）")
        self.sl_by.valueChanged.connect(self._apply)
        f2.addRow("", _row(self.chk_beat, self.chk_phase))
        f2.addRow("计数方式", self.cb_beatmode)
        f2.addRow("分段时显示", self.cb_beatdisplay)
        f2.addRow("字号/颜色", _row(self.sl_beatsize, self.btn_beat))
        f2.addRow("透明度", self.sl_beatalpha)
        f2.addRow("位置 X / Y", _row(self.sl_bx, self.sl_by))
        root.addWidget(box2)

        # ------------------------------------------------------- 统计分段
        box3 = QGroupBox("Beats 统计分段")
        v3 = QVBoxLayout(box3)
        self.list_stat = QListWidget()
        self.list_stat.setMaximumHeight(96)
        v3.addWidget(self.list_stat)
        self.sp_stat_time = QDoubleSpinBox()
        self.sp_stat_time.setRange(0.0, 36_000_000.0)
        self.sp_stat_time.setDecimals(1)
        self.sp_stat_time.setSingleStep(100.0)
        self.sp_stat_time.setSuffix(" ms")
        self.b_stat_now = QPushButton("取播放头")
        v3.addWidget(_row(self.sp_stat_time, self.b_stat_now))
        rowb = QHBoxLayout()
        self.b_stat_split = QPushButton("在此时刻切分")
        self.b_stat_del = QPushButton("删除选中段")
        rowb.addWidget(self.b_stat_split)
        rowb.addWidget(self.b_stat_del)
        v3.addLayout(rowb)
        root.addWidget(box3)

        # --------------------------------------------------------- 进度条
        box4 = QGroupBox("可视化进度条")
        f4 = QFormLayout(box4)
        self.chk_prog = QCheckBox("显示进度条")
        self.chk_prog.toggled.connect(self._apply)
        self.sp_pw = QSpinBox()
        self.sp_pw.setRange(20, 4000)
        self.sp_pw.setSingleStep(10)
        self.sp_pw.setSuffix(" px")
        self.sp_pw.valueChanged.connect(self._apply)
        self.sp_ph = QSpinBox()
        self.sp_ph.setRange(4, 400)
        self.sp_ph.setSingleStep(2)
        self.sp_ph.setSuffix(" px")
        self.sp_ph.valueChanged.connect(self._apply)
        self.sl_px = FloatSlider(0.0, 1.0, 0.5, 0.01, 2)
        self.sl_py = FloatSlider(0.0, 1.0, 0.88, 0.01, 2)
        self.sl_px.valueChanged.connect(self._apply)
        self.sl_py.valueChanged.connect(self._apply)
        self.chk_round = QCheckBox("圆角")
        self.chk_round.toggled.connect(self._apply)
        self.sl_palpha = FloatSlider(0.0, 1.0, 0.85, 0.01, 2)
        self.sl_palpha.valueChanged.connect(self._apply)
        self.btn_pfill = ColorButton("#5ad1ff")
        self.btn_pfill.colorChanged.connect(self._apply)
        self.sl_pbw = FloatSlider(0.0, 0.40, 0.06, 0.01, 2)
        self.sl_pbw.setToolTip("边框粗细，相对进度条高度")
        self.sl_pbw.valueChanged.connect(self._apply)
        self.btn_pborder = ColorButton("#ffffff")
        self.btn_pborder.colorChanged.connect(self._apply)
        self.sl_pba = FloatSlider(0.0, 1.0, 0.8, 0.01, 2)
        self.sl_pba.valueChanged.connect(self._apply)
        self.cb_pmode = QComboBox()
        self.cb_pmode.addItem("时间 / 总时间", "time")
        self.cb_pmode.addItem("Beats 数 / 总 Beats 数", "beats")
        self.cb_pmode.addItem("当前段落经过时间 / 段落总时间", "seg_time")
        self.cb_pmode.addItem("当前段落 Beats / 段落总 Beats", "seg_beats")
        self.cb_pmode.currentIndexChanged.connect(self._apply)
        f4.addRow("", self.chk_prog)
        f4.addRow("宽 × 高", _row(self.sp_pw, self.sp_ph))
        f4.addRow("位置 X / Y", _row(self.sl_px, self.sl_py))
        f4.addRow("", self.chk_round)
        f4.addRow("整体透明度", self.sl_palpha)
        f4.addRow("填充颜色", self.btn_pfill)
        f4.addRow("边框宽度", self.sl_pbw)
        f4.addRow("边框颜色/透明度", _row(self.btn_pborder, self.sl_pba))
        f4.addRow("进度依据", self.cb_pmode)
        root.addWidget(box4)
        root.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(_scroll(inner))

        self.b_stat_now.clicked.connect(self._take_playhead)
        self.b_stat_split.clicked.connect(self.stat_split)
        self.b_stat_del.clicked.connect(self.stat_delete)
        self.refresh()

    # ------------------------------------------------------------- 刷新
    def refresh(self) -> None:
        rs = self.doc.project.render
        self._loading = True
        self.ed_wm.setText(str(getattr(rs, "watermark_text", "") or ""))
        i = self.cb_wmpos.findData(str(getattr(rs, "watermark_pos", "bottom_left")))
        self.cb_wmpos.setCurrentIndex(max(0, i))
        self.sl_wmsize.setValue(getattr(rs, "watermark_size", 0.14))
        self.btn_wm.setColor(str(getattr(rs, "watermark_color", "#ffffff")))
        self.sl_wmalpha.setValue(getattr(rs, "watermark_alpha", 0.55))

        self.chk_beat.setChecked(bool(getattr(rs, "beat_counter", False)))
        self.chk_phase.setChecked(bool(getattr(rs, "beat_counter_show_phase", False)))
        i = self.cb_beatmode.findData(str(getattr(rs, "beat_counter_mode", "notes")))
        self.cb_beatmode.setCurrentIndex(max(0, i))
        i = self.cb_beatdisplay.findData(str(getattr(rs, "beat_counter_display", "total")))
        self.cb_beatdisplay.setCurrentIndex(max(0, i))
        self.sl_beatsize.setValue(getattr(rs, "beat_counter_size", 0.14))
        self.btn_beat.setColor(str(getattr(rs, "beat_counter_color", "#ffffff")))
        self.sl_beatalpha.setValue(getattr(rs, "beat_counter_alpha", 0.92))
        self.sl_bx.setValue(getattr(rs, "beat_counter_x", 0.94))
        self.sl_by.setValue(getattr(rs, "beat_counter_y", 0.90))

        self.chk_prog.setChecked(bool(getattr(rs, "progress_bar", False)))
        self.sp_pw.setValue(int(getattr(rs, "progress_w", 400.0)))
        self.sp_ph.setValue(int(getattr(rs, "progress_h", 40.0)))
        self.sl_px.setValue(getattr(rs, "progress_x", 0.5))
        self.sl_py.setValue(getattr(rs, "progress_y", 0.88))
        self.chk_round.setChecked(bool(getattr(rs, "progress_round", True)))
        self.sl_palpha.setValue(getattr(rs, "progress_opacity", 0.85))
        self.btn_pfill.setColor(str(getattr(rs, "progress_fill", "#5ad1ff")))
        self.sl_pbw.setValue(getattr(rs, "progress_border_w", 0.06))
        self.btn_pborder.setColor(str(getattr(rs, "progress_border_color", "#ffffff")))
        self.sl_pba.setValue(getattr(rs, "progress_border_alpha", 0.8))
        i = self.cb_pmode.findData(str(getattr(rs, "progress_mode", "beats")))
        self.cb_pmode.setCurrentIndex(max(0, i))
        self._refresh_segments()
        self._loading = False

    @staticmethod
    def _fmt_ms(ms: float) -> str:
        ms = max(0.0, float(ms))
        return f"{int(ms // 60000):02d}:{(ms % 60000) / 1000:06.3f}"

    def _segments(self) -> list:
        return self.doc.project.render.stat_segments

    def _refresh_segments(self) -> None:
        segs = self._segments()
        row = self.list_stat.currentRow()
        self.list_stat.blockSignals(True)
        self.list_stat.clear()
        for idx, s in enumerate(segs):
            end = "结尾" if float(s.end_ms) <= float(s.start_ms) else self._fmt_ms(s.end_ms)
            self.list_stat.addItem(f"Phase {idx + 1}   {self._fmt_ms(s.start_ms)} → {end}")
        self.list_stat.blockSignals(False)
        if 0 <= row < len(segs):
            self.list_stat.setCurrentRow(row)

    def _take_playhead(self) -> None:
        try:
            t = float(self.time_provider()) if self.time_provider else 0.0
        except Exception:
            t = 0.0
        self.sp_stat_time.setValue(max(0.0, t))

    def stat_split(self) -> None:
        from ..model import StatSegment

        t = float(self.sp_stat_time.value())
        segs = sorted(self._segments(), key=lambda s: float(s.start_ms))
        if not segs:
            segs = [StatSegment(0.0, t), StatSegment(t, 0.0)]
        else:
            hit = None
            for s in segs:
                st, en = float(s.start_ms), float(s.end_ms)
                if t >= st and (en <= st or t < en):
                    hit = s
                    break
            if hit is None:
                segs.append(StatSegment(t, 0.0))
            elif t > float(hit.start_ms) + 1.0:
                old_end = float(hit.end_ms)
                hit.end_ms = t
                segs.append(StatSegment(t, old_end))
        self.doc.project.render.stat_segments = sorted(segs, key=lambda s: float(s.start_ms))
        self._refresh_segments()
        self.doc.touch("Beats 统计分段")
        self.changed.emit()
        self.renderChanged.emit()

    def stat_delete(self) -> None:
        row = self.list_stat.currentRow()
        segs = self._segments()
        if not (0 <= row < len(segs)):
            return
        segs.pop(row)
        self._refresh_segments()
        self.doc.touch("删除 Beats 统计分段")
        self.changed.emit()
        self.renderChanged.emit()

    # ------------------------------------------------------------- 写入
    def _apply(self, *_) -> None:
        if self._loading:
            return
        rs = self.doc.project.render
        rs.watermark_text = self.ed_wm.text()
        rs.watermark_pos = self.cb_wmpos.currentData()
        rs.watermark_size = self.sl_wmsize.value()
        rs.watermark_color = self.btn_wm.color()
        rs.watermark_alpha = self.sl_wmalpha.value()

        rs.beat_counter = self.chk_beat.isChecked()
        rs.beat_counter_show_phase = self.chk_phase.isChecked()
        rs.beat_counter_mode = self.cb_beatmode.currentData()
        rs.beat_counter_display = self.cb_beatdisplay.currentData()
        rs.beat_counter_size = self.sl_beatsize.value()
        rs.beat_counter_color = self.btn_beat.color()
        rs.beat_counter_alpha = self.sl_beatalpha.value()
        rs.beat_counter_x = self.sl_bx.value()
        rs.beat_counter_y = self.sl_by.value()

        rs.progress_bar = self.chk_prog.isChecked()
        rs.progress_w = float(self.sp_pw.value())
        rs.progress_h = float(self.sp_ph.value())
        rs.progress_x = self.sl_px.value()
        rs.progress_y = self.sl_py.value()
        rs.progress_round = self.chk_round.isChecked()
        rs.progress_opacity = self.sl_palpha.value()
        rs.progress_fill = self.btn_pfill.color()
        rs.progress_border_w = self.sl_pbw.value()
        rs.progress_border_color = self.btn_pborder.color()
        rs.progress_border_alpha = self.sl_pba.value()
        rs.progress_mode = self.cb_pmode.currentData()

        self.doc.touch("文字与统计")
        self.changed.emit()
        self.renderChanged.emit()
