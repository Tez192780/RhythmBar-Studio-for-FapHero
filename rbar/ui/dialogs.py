"""对话框：BPM 段编辑、Tap 测速、区间填充。"""

from __future__ import annotations

import os
import uuid

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr, trf, translate_tree
from ..settings import skins_dir


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


class _SkinCropCanvas(QWidget):
    """在图片上拖动正方形裁剪框；裁剪框中心就是皮肤锚点。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 320)
        self.setMouseTracking(True)
        self._img = QImage()
        self._center = QPointF(0.0, 0.0)
        self._frac = 0.8

    def set_image(self, img: QImage) -> None:
        self._img = img
        self._center = QPointF(img.width() / 2.0, img.height() / 2.0)
        self.update()

    def set_fraction(self, v: int) -> None:
        self._frac = max(0.1, min(1.0, v / 100.0))
        self.update()

    def crop_rect(self) -> QRect:
        w, h = self._img.width(), self._img.height()
        if w <= 0 or h <= 0:
            return QRect()
        side = max(8, min(w, h, int(round(min(w, h) * self._frac))))
        side = min(side, w, h)
        cx = max(side / 2.0, min(w - side / 2.0, self._center.x()))
        cy = max(side / 2.0, min(h - side / 2.0, self._center.y()))
        return QRect(int(round(cx - side / 2.0)), int(round(cy - side / 2.0)),
                     side, side)

    def cropped_image(self) -> QImage:
        r = self.crop_rect()
        return self._img.copy(r) if r.isValid() and not self._img.isNull() else QImage()

    def _view(self) -> tuple[float, float, float]:
        w, h = self._img.width(), self._img.height()
        if w <= 0 or h <= 0:
            return 1.0, 0.0, 0.0
        scale = min(self.width() / w, self.height() / h)
        ox = (self.width() - w * scale) / 2.0
        oy = (self.height() - h * scale) / 2.0
        return scale, ox, oy

    def _move_center(self, pos) -> None:
        scale, ox, oy = self._view()
        if scale <= 0:
            return
        x = (pos.x() - ox) / scale
        y = (pos.y() - oy) / scale
        r = self.crop_rect()
        side = r.width()
        w, h = self._img.width(), self._img.height()
        self._center = QPointF(
            max(side / 2.0, min(w - side / 2.0, x)),
            max(side / 2.0, min(h - side / 2.0, y)),
        )
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._move_center(event.position())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.LeftButton:
            self._move_center(event.position())

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0b0d10"))
        if self._img.isNull():
            p.setPen(QColor("#8b949e"))
            p.drawText(self.rect(), Qt.AlignCenter, tr("没有图片"))
            return
        scale, ox, oy = self._view()
        iw, ih = self._img.width() * scale, self._img.height() * scale
        p.drawImage(QRectF(ox, oy, iw, ih), self._img, QRectF(self._img.rect()))
        r = self.crop_rect()
        cr = QRectF(ox + r.x() * scale, oy + r.y() * scale,
                    r.width() * scale, r.height() * scale)
        shade = QColor(0, 0, 0, 145)
        p.fillRect(QRectF(ox, oy, iw, cr.top() - oy), shade)
        p.fillRect(QRectF(ox, cr.bottom(), iw, oy + ih - cr.bottom()), shade)
        p.fillRect(QRectF(ox, cr.top(), cr.left() - ox, cr.height()), shade)
        p.fillRect(QRectF(cr.right(), cr.top(), ox + iw - cr.right(), cr.height()), shade)
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(cr)
        cx, cy = cr.center().x(), cr.center().y()
        p.setPen(QPen(QColor("#3fd2ea"), 1.5))
        p.drawLine(QPointF(cx - 12, cy), QPointF(cx + 12, cy))
        p.drawLine(QPointF(cx, cy - 12), QPointF(cx, cy + 12))
        p.setPen(QPen(QColor("#ffffff"), 1))
        p.drawEllipse(QPointF(cx, cy), 5, 5)


class SkinCropDialog(QDialog):
    """导入图片后裁剪成正方形皮肤，裁剪框中心就是判定点/音符中心。"""

    def __init__(self, parent, image_path: str):
        super().__init__(parent)
        self.setWindowTitle("裁剪图像皮肤")
        self.resize(540, 520)
        lay = QVBoxLayout(self)
        self.canvas = _SkinCropCanvas(self)
        img = QImage(image_path)
        if img.isNull():
            raise ValueError(image_path)
        self.canvas.set_image(img)
        lay.addWidget(self.canvas, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("裁剪大小"))
        self.sl_size = QSlider(Qt.Horizontal)
        self.sl_size.setRange(15, 100)
        self.sl_size.setValue(80)
        self.sl_size.valueChanged.connect(self.canvas.set_fraction)
        row.addWidget(self.sl_size, 1)
        lay.addLayout(row)
        hint = QLabel("拖动图片调整裁剪位置；裁剪框中心就是判定点/音符中心")
        hint.setStyleSheet("color:#8b949e;")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        translate_tree(self)

    def cropped_image(self) -> QImage:
        return self.canvas.cropped_image()


def choose_skin_image(parent, title: str = "选择图像皮肤") -> str:
    """选择图片、裁剪并保存到 skins 目录，返回路径；取消返回空串。"""
    path, _ = QFileDialog.getOpenFileName(
        parent, tr(title), "", "图片 (*.png *.jpg *.jpeg *.webp *.bmp)")
    if not path:
        return ""
    try:
        dlg = SkinCropDialog(parent, path)
    except (ValueError, OSError):
        return ""
    if dlg.exec() != QDialog.Accepted:
        return ""
    img = dlg.cropped_image()
    if img.isNull():
        return ""
    if max(img.width(), img.height()) > 1024:
        img = img.scaled(1024, 1024, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    out = os.path.join(skins_dir(), uuid.uuid4().hex + ".png")
    if not img.save(out, "PNG"):
        return ""
    return out


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
        translate_tree(self)
        btns.button(QDialogButtonBox.Ok).setText(tr("确定"))
        btns.button(QDialogButtonBox.Cancel).setText(tr("取消"))

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
        self.lbl_bpm = QLabel()
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
        translate_tree(self)
        box.button(QDialogButtonBox.Cancel).setText(tr("取消"))
        self._show_bpm(current_bpm)

    def _show_bpm(self, bpm: float, taps: int = 0) -> None:
        if taps:
            self.lbl_bpm.setText(trf("当前：{bpm:.2f} BPM　（已点 {n} 次）", bpm=bpm, n=taps))
        else:
            self.lbl_bpm.setText(trf("当前：{bpm:g} BPM", bpm=bpm))

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
            self._show_bpm(b, len(self.times))

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
        # 兼容旧调用/工具菜单：只要传入的区间正好等于当前框选，就自动锁定，
        # 不要再要求用户点一次「用当前框选」。
        if (not self._locked and state is not None
                and getattr(state, "has_range", lambda: False)()
                and abs(t0 - state.sel_t0) < 1.0 and abs(t1 - state.sel_t1) < 1.0):
            self._locked = True
        self.setWindowTitle("区间批量填充音符")
        self.setMinimumWidth(470)
        self._loading = False
        lay = QVBoxLayout(self)

        self.lbl_range = QLabel()
        self.lbl_range.setStyleSheet("color:#bfe2ff;font-weight:bold;")
        self.lbl_range.setWordWrap(True)
        lay.addWidget(self.lbl_range)
        hint = QLabel("在时间轴上方标尺或波形/频谱区域横向拖一下即可框选区间（单击则定位当前时间）")
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
        self.b_sel = b_sel
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
        self.sp_beats.setSuffix(tr(" 拍"))
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
        box.button(QDialogButtonBox.Cancel).setText("取消")
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
        translate_tree(self)
        self._sync_range()

    # ------------------------------------------------------------ 区间
    def _sync_range(self, *_) -> None:
        manual = self.chk_manual.isChecked()
        self.row_range.setVisible(manual)
        if self._locked and not manual:
            self.b_sel.setEnabled(False)
            self.b_sel.setText(tr("已自动使用当前框选"))
            self.b_sel.setToolTip(tr("右键打开时已经自动采用当前框选的区间，不需要再点"))
        else:
            self.b_sel.setEnabled(True)
            self.b_sel.setText(tr("用当前框选"))
            self.b_sel.setToolTip("")
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
        manual = self.chk_manual.isChecked()
        src = tr("手动输入") if manual else tr("时间轴框选")
        if self._locked and not manual:
            src = tr("已自动使用当前框选")
        self.lbl_range.setText(trf(
            "区间（{src}）：{a:.3f}s → {b:.3f}s　共 {dur:.3f}s",
            src=src, a=t0 / 1000.0, b=t1 / 1000.0, dur=(t1 - t0) / 1000.0))
        ts = self.times()
        if not ts:
            self.lbl.setText(tr("区间无内容：先在时间轴上框选一段，或点「手动输入区间」"))
            return
        gap = (ts[1] - ts[0]) if len(ts) > 1 else 0.0
        bpm = self.tm.bpm_at(ts[0])
        self.lbl.setText(trf(
            "将生成 {n} 个音符　首尾 {a:.0f} → {b:.0f} ms　实际间隔 {gap:.1f} ms（{bpm:g} BPM）",
            n=len(ts), a=ts[0], b=ts[-1], gap=gap, bpm=bpm
        ))

    def remember(self) -> None:
        """把这次用的参数记回编辑器状态，下次开对话框沿用。"""
        if self._state is None:
            return
        self._state.fill_mode = self._mode()
        self._state.fill_beats = float(self.sp_beats.value())
        self._state.fill_ms = float(self.sp_ms.value())
        self._state.fill_alt = self.chk_alt.isChecked()