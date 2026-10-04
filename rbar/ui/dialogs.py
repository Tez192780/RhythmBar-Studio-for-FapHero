"""对话框：BPM 段编辑、Tap 测速、区间填充、自动铺点。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .panels import FloatSlider


def _pair(*widgets) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    for x in widgets:
        lay.addWidget(x)
    return w


def _wrap(layout) -> QWidget:
    w = QWidget()
    w.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    return w


class BpmSegmentDialog(QDialog):
    """编辑一个 BPM 段的起点与 BPM。"""

    def __init__(self, parent, time_ms: float, bpm: float, first: bool = False):
        super().__init__(parent)
        self.setWindowTitle("BPM 段")
        self.setMinimumWidth(340)
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.sp_time = QDoubleSpinBox()
        self.sp_time.setRange(0, 3600000)
        self.sp_time.setDecimals(1)
        self.sp_time.setSuffix(" ms")
        self.sp_time.setValue(time_ms)
        self.sp_time.setEnabled(not first)
        self.sp_bpm = QDoubleSpinBox()
        self.sp_bpm.setRange(1, 1200)
        self.sp_bpm.setDecimals(3)
        self.sp_bpm.setValue(bpm)
        f.addRow("起点", self.sp_time)
        f.addRow("BPM", self.sp_bpm)
        lay.addLayout(f)
        if first:
            hint = QLabel("第一段的起点 = 节拍偏移（beat 0），请在「对齐」里改。")
            hint.setStyleSheet("color:#8b949e;")
            lay.addWidget(hint)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def values(self) -> tuple[float, float]:
        return float(self.sp_time.value()), float(self.sp_bpm.value())


class TapTempoDialog(QDialog):
    """连点测速：点按钮或按键盘任意键都可以打点。"""

    def __init__(self, parent, current_bpm: float):
        super().__init__(parent)
        self.setWindowTitle("Tap 测速")
        self.setMinimumWidth(360)
        self.times: list[float] = []
        self.bpm = float(current_bpm)
        lay = QVBoxLayout(self)
        self.lbl = QLabel("连续点下面的按钮（或按空格/回车）至少 4 次")
        self.lbl.setAlignment(Qt.AlignCenter)
        self.big = QPushButton("拍！")
        self.big.setMinimumHeight(90)
        self.big.clicked.connect(self._tap)
        self.lbl_bpm = QLabel(f"当前：{current_bpm:g} BPM")
        self.lbl_bpm.setAlignment(Qt.AlignCenter)
        self.lbl_bpm.setStyleSheet("font-size:22px;")
        lay.addWidget(self.lbl)
        lay.addWidget(self.big)
        lay.addWidget(self.lbl_bpm)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("用这个 BPM")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.big.setFocus()

    def _tap(self) -> None:
        import time

        from ..timing import tap_tempo

        now = time.perf_counter() * 1000.0
        if self.times and now - self.times[-1] < 40:
            return
        self.times.append(now)
        del self.times[:-12]
        b = tap_tempo(self.times)
        if b:
            self.bpm = b
            self.lbl_bpm.setText(f"当前：{b:.2f} BPM　（已点 {len(self.times)} 次）")

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        if ev.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self._tap()
            return
        if ev.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(ev)


class FillRangeDialog(QDialog):
    """在区间里按自定义间隔填满音符。

    区间默认来自时间轴上的**框选**（在标尺/波形处拖一下），不用手打时间；
    需要精确数值时勾「手动输入区间」或点下面的快捷按钮。
    """

    def __init__(self, parent, tm, t0: float, t1: float, rows: list[tuple[str, str]],
                 current_lane: int = 0, snap_on: bool = True, snap_divisor: float = 4.0,
                 audio_dur: float = 0.0, locked: bool = False, state=None):
        super().__init__(parent)
        self.tm = tm
        self.rows = rows
        self.snap_on = snap_on
        self.snap_divisor = snap_divisor
        self.audio_dur = audio_dur
        self._locked = bool(locked)
        self._state = state
        self.setWindowTitle("区间批量填充音符")
        self.setMinimumWidth(470)
        self._loading = False
        lay = QVBoxLayout(self)

        self.lbl_range = QLabel()
        self.lbl_range.setStyleSheet("color:#bfe2ff;font-weight:bold;")
        self.lbl_range.setWordWrap(True)
        lay.addWidget(self.lbl_range)
        hint = QLabel("在时间轴上方**标尺**或波形/频谱区域横向拖一下即可框选区间（单击则定位播放头）")
        hint.setStyleSheet("color:#8b949e;")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.chk_manual = QCheckBox("手动输入区间")
        self.chk_manual.setChecked(not self._locked)
        self.chk_manual.toggled.connect(self._sync_range)
        lay.addWidget(self.chk_manual)

        f = QFormLayout()
        self.sp_start = QDoubleSpinBox()
        self.sp_start.setRange(0, 3600000)
        self.sp_start.setDecimals(0)
        self.sp_start.setSuffix(" ms")
        self.sp_start.setValue(t0)
        self.sp_start.valueChanged.connect(self._update_hint)
        self.sp_end = QDoubleSpinBox()
        self.sp_end.setRange(0, 3600000)
        self.sp_end.setDecimals(0)
        self.sp_end.setSuffix(" ms")
        self.sp_end.setValue(t1)
        self.sp_end.valueChanged.connect(self._update_hint)
        b_sel = QPushButton("用当前框选")
        b_loop = QPushButton("用循环区间")
        b_notes = QPushButton("用全部音符")
        b_all = QPushButton("整首")
        for b in (b_sel, b_loop, b_notes, b_all):
            b.setFixedHeight(22)
        b_sel.clicked.connect(self._use_selection)
        b_loop.clicked.connect(self._use_loop)
        b_notes.clicked.connect(self._use_notes)
        b_all.clicked.connect(self._use_song)
        self.row_range = _pair(self.sp_start, QLabel("→"), self.sp_end)
        qrow = QHBoxLayout()
        for b in (b_sel, b_loop, b_notes, b_all):
            qrow.addWidget(b)
        qrow.addStretch(1)
        f.addRow("起点 / 终点", self.row_range)
        f.addRow("", _wrap(qrow))

        self.cb_mode = QComboBox()
        self.cb_mode.addItem("按节拍", "beat")
        self.cb_mode.addItem("按毫秒", "ms")
        self.cb_mode.currentIndexChanged.connect(self._update_hint)
        self.sp_beats = QDoubleSpinBox()
        self.sp_beats.setRange(0.0625, 512)
        self.sp_beats.setDecimals(4)
        self.sp_beats.setSingleStep(0.25)
        self.sp_beats.setValue(0.25)
        self.sp_beats.setSuffix(" 拍")
        self.sp_beats.valueChanged.connect(self._update_hint)
        self.sp_ms = QDoubleSpinBox()
        self.sp_ms.setRange(5, 600000)
        self.sp_ms.setDecimals(0)
        self.sp_ms.setValue(125)
        self.sp_ms.setSuffix(" ms")
        self.sp_ms.valueChanged.connect(self._update_hint)
        f.addRow("间隔方式", self.cb_mode)
        f.addRow("间隔", _pair(self.sp_beats, self.sp_ms))

        self.cb_type = QComboBox()
        for key, name in rows:
            self.cb_type.addItem(name, key)
        self.cb_type.setCurrentIndex(max(0, min(len(rows) - 1, current_lane)))
        self.chk_alt = QCheckBox("与下一种类型交替")
        self.chk_alt.toggled.connect(self._update_hint)
        self.cb_type2 = QComboBox()
        for key, name in rows:
            self.cb_type2.addItem(name, key)
        if len(rows) > 1:
            self.cb_type2.setCurrentIndex(1)
        f.addRow("音符类型", self.cb_type)
        f.addRow("", _pair(self.chk_alt, self.cb_type2))
        self.chk_snap = QCheckBox("起点吸附到当前网格")
        self.chk_snap.setChecked(True)
        self.chk_snap.toggled.connect(self._update_hint)
        f.addRow("", self.chk_snap)
        lay.addLayout(f)

        self.lbl = QLabel()
        self.lbl.setStyleSheet("color:#8bd0ff;")
        lay.addWidget(self.lbl)
        tip = QLabel("提示：已存在的重复位置不会重复添加；上限 20000 个音符。")
        tip.setStyleSheet("color:#8b949e;")
        lay.addWidget(tip)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("填充")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        # 沿用上次用的间隔/类型
        if state is not None:
            i = self.cb_mode.findData(getattr(state, "fill_mode", "beat"))
            self.cb_mode.setCurrentIndex(max(0, i))
            self.sp_beats.setValue(float(getattr(state, "fill_beats", 0.25)))
            self.sp_ms.setValue(float(getattr(state, "fill_ms", 125.0)))
            self.chk_alt.setChecked(bool(getattr(state, "fill_alt", False)))
        self._sync_range()

    # ------------------------------------------------------------ 区间
    def _sync_range(self, *_) -> None:
        manual = self.chk_manual.isChecked()
        self.row_range.setVisible(manual)
        self.adjustSize()
        self._update_hint()

    def _use_selection(self) -> None:
        s = getattr(self._state, "sel_t0", None)
        if s is None:
            d = getattr(self.parent(), "doc", None)
            if d is None:
                return
            s = d.state.sel_t0
            e = d.state.sel_t1
        else:
            e = self._state.sel_t1
        if e - s <= 20:
            return
        self.chk_manual.setChecked(True)
        self.sp_start.setValue(s)
        self.sp_end.setValue(e)

    # ------------------------------------------------------------ 快捷区间
    def _use_loop(self) -> None:
        st = getattr(self.parent(), "doc", None)
        if st is None:
            return
        s = st.state
        if s.loop_b > s.loop_a:
            self.chk_manual.setChecked(True)
            self.sp_start.setValue(s.loop_a)
            self.sp_end.setValue(s.loop_b)

    def _use_notes(self) -> None:
        d = getattr(self.parent(), "doc", None)
        if d is None or not len(d.project.chart):
            return
        lo, hi = d.project.chart.bounds()
        self.chk_manual.setChecked(True)
        self.sp_start.setValue(lo)
        self.sp_end.setValue(hi)

    def _use_song(self) -> None:
        self.chk_manual.setChecked(True)
        self.sp_start.setValue(0.0)
        self.sp_end.setValue(self.audio_dur or self.sp_end.value())

    # ------------------------------------------------------------ 计算
    def _mode(self) -> str:
        return self.cb_mode.currentData()

    def times(self) -> list[float]:
        t0, t1 = float(self.sp_start.value()), float(self.sp_end.value())
        if t1 <= t0:
            return []
        if self.chk_snap.isChecked() and self.snap_on and self._mode() == "beat":
            t0 = self.tm.snap(t0, self.snap_divisor)
        out: list[float] = []
        if self._mode() == "beat":
            step = float(self.sp_beats.value())
            if step <= 0:
                return []
            b0 = self.tm.beat(t0)
            k = 0
            while k < 20001:
                t = self.tm.ms(b0 + k * step)
                if t > t1 + 1e-6:
                    break
                if t >= t0 - 1e-6:
                    out.append(round(t, 3))
                k += 1
        else:
            step = float(self.sp_ms.value())
            if step <= 0:
                return []
            t = t0
            while t <= t1 + 1e-6 and len(out) < 20001:
                out.append(round(t, 3))
                t += step
        return out[:20000]

    def type_keys(self) -> list[str]:
        k1 = self.cb_type.currentData()
        if self.chk_alt.isChecked():
            return [k1, self.cb_type2.currentData()]
        return [k1]

    def _update_hint(self, *_) -> None:
        beat = self._mode() == "beat"
        self.sp_beats.setEnabled(beat)
        self.sp_ms.setEnabled(not beat)
        self.cb_type2.setEnabled(self.chk_alt.isChecked())
        t0 = float(self.sp_start.value())
        t1 = float(self.sp_end.value())
        src = "手动输入" if self.chk_manual.isChecked() else "时间轴框选"
        self.lbl_range.setText(
            f"区间（{src}）：{t0 / 1000.0:.3f}s → {t1 / 1000.0:.3f}s　"
            f"共 {(t1 - t0) / 1000.0:.3f}s")
        ts = self.times()
        if not ts:
            self.lbl.setText("区间无内容：先在时间轴上框选一段，或点「手动输入区间」")
            return
        gap = (ts[1] - ts[0]) if len(ts) > 1 else 0.0
        bpm = self.tm.bpm_at(ts[0])
        self.lbl.setText(
            f"将生成 {len(ts)} 个音符　首尾 {ts[0]:.0f} → {ts[-1]:.0f} ms　"
            f"实际间隔 {gap:.1f} ms（{bpm:g} BPM）"
        )

    def remember(self) -> None:
        """把这次用的参数记回编辑器状态，下次开对话框沿用。"""
        if self._state is None:
            return
        self._state.fill_mode = self._mode()
        self._state.fill_beats = float(self.sp_beats.value())
        self._state.fill_ms = float(self.sp_ms.value())
        self._state.fill_alt = self.chk_alt.isChecked()


class AutoNotesDialog(QDialog):
    """自动铺点：按音频起音（或视频镜头切换）批量生成音符。"""

    detectRequested = Signal(dict)

    def __init__(self, parent, rows: list[tuple[str, str]], has_video: bool = False):
        super().__init__(parent)
        self.setWindowTitle("自动铺点")
        self.setMinimumWidth(440)
        self.times: list[float] = []
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.cb_source = QComboBox()
        self.cb_source.addItem("音频起音（听到的鼓点/音头）", "audio")
        if has_video:
            self.cb_source.addItem("视频镜头切换（画面剪切点）", "video")
        self.cb_source.currentIndexChanged.connect(self._sync)
        f.addRow("依据", self.cb_source)
        self.sl_sens = FloatSlider(0.0, 1.0, 0.5, 0.01, 2)
        self.sl_sens.setToolTip("音频：越大越灵敏，音符越多")
        self.sl_gap = FloatSlider(30, 1000, 140, 1, 0, " ms")
        self.sl_scene = FloatSlider(0.1, 0.9, 0.35, 0.01, 2)
        self.sl_scene.setToolTip("视频：画面变化超过这个幅度算一次镜头切换")
        self.cb_type = QComboBox()
        for key, name in rows:
            self.cb_type.addItem(name, key)
        self.chk_quant = QCheckBox("吸附到当前网格")
        self.chk_quant.setChecked(True)
        f.addRow("灵敏度", self.sl_sens)
        f.addRow("最小间隔", self.sl_gap)
        f.addRow("镜头阈值", self.sl_scene)
        f.addRow("音符类型", self.cb_type)
        f.addRow("", self.chk_quant)
        lay.addLayout(f)
        self.lbl = QLabel("点「试检测」看看会生成多少音符")
        self.lbl.setStyleSheet("color:#8b949e;")
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        row = QHBoxLayout()
        b_detect = QPushButton("试检测")
        b_detect.clicked.connect(self._detect)
        row.addWidget(b_detect)
        row.addStretch(1)
        lay.addLayout(row)
        self.box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.box.button(QDialogButtonBox.Ok).setText("生成音符")
        self.box.accepted.connect(self.accept)
        self.box.rejected.connect(self.reject)
        lay.addWidget(self.box)
        self._sync()

    def _sync(self, *_) -> None:
        video = self.cb_source.currentData() == "video"
        self.sl_scene.setEnabled(video)
        self.sl_sens.setEnabled(not video)
        self.sl_gap.setEnabled(not video)

    def _detect(self) -> None:
        self.lbl.setText("检测中…")
        self.detectRequested.emit(self.params())

    def params(self) -> dict:
        return {
            "source": self.cb_source.currentData(),
            "sensitivity": self.sl_sens.value(),
            "min_gap_ms": self.sl_gap.value(),
            "scene_threshold": self.sl_scene.value(),
            "quantize": self.chk_quant.isChecked(),
            "type": self.cb_type.currentData(),
        }

    def set_result(self, times: list[float], elapsed_s: float) -> None:
        self.times = list(times)
        self.lbl.setText(f"检测到 {len(times)} 个点（用时 {elapsed_s:.1f}s）。"
                         f"点「生成音符」写入谱面。")
        f.addRow("", self.chk_quant)
        lay.addLayout(f)
        self.lbl = QLabel("点「试检测」看看会生成多少音符")
        self.lbl.setStyleSheet("color:#8b949e;")
        lay.addWidget(self.lbl)
        row = QHBoxLayout()
        b_detect = QPushButton("试检测")
        b_detect.clicked.connect(self._detect)
        row.addWidget(b_detect)
        row.addStretch(1)
        lay.addLayout(row)
        self.box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.box.button(QDialogButtonBox.Ok).setText("生成音符")
        self.box.accepted.connect(self.accept)
        self.box.rejected.connect(self.reject)
        lay.addWidget(self.box)

    def _detect(self) -> None:
        self.lbl.setText("检测中…")
        self.detectRequested.emit(self.params())

    def params(self) -> dict:
        return {
            "source": self.cb_source.currentData(),
            "sensitivity": self.sl_sens.value(),
            "min_gap_ms": self.sl_gap.value(),
            "scene_threshold": self.sl_scene.value(),
            "quantize": self.chk_quant.isChecked(),
            "type": self.cb_type.currentData(),
        }

    def set_result(self, times: list[float], elapsed_s: float) -> None:
        self.times = list(times)
        self.lbl.setText(f"检测到 {len(times)} 个点（用时 {elapsed_s:.1f}s）。"
                         f"点「生成音符」写入谱面。")
