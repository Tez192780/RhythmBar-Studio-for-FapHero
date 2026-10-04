"""实时预览条 + 参考视频画面。"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ..doc import Doc
from ..render import BarRenderer


class PreviewWidget(QWidget):
    """所见即所得的节奏条预览；拖动可擦洗时间。"""

    seekRequested = Signal(float)

    def __init__(self, doc: Doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.t_ms = 0.0
        self.setMinimumHeight(90)
        self.setMouseTracking(True)
        self._show_safe = True
        self._checker: QBrush | None = None
        self._checker_key: tuple | None = None
        self._paint_times: list[float] = []
        self._renderer = BarRenderer(doc.project)

    def set_position(self, ms: float) -> None:
        self.t_ms = float(ms)
        self.update()

    def set_safe_area(self, on: bool) -> None:
        self._show_safe = bool(on)
        self.update()

    def fps(self) -> float:
        if not self._paint_times:
            return 0.0
        avg = sum(self._paint_times) / len(self._paint_times)
        return 1.0 / avg if avg > 0 else 0.0

    def _checker_brush(self) -> QBrush:
        """棋盘底用纹理笔刷一次填完（原先逐格 fillRect 有 700+ 次调用）。"""
        if self._checker is None:
            cell = 12
            pm = QPixmap(cell * 2, cell * 2)
            pm.fill(QColor("#1b1e24"))
            p = QPainter(pm)
            p.fillRect(0, 0, cell, cell, QColor("#242830"))
            p.fillRect(cell, cell, cell, cell, QColor("#242830"))
            p.end()
            self._checker = QBrush(pm)
        return self._checker

    def _bar_rect(self) -> QRectF:
        rs = self.doc.project.render
        aw = max(1.0, self.width() - 16.0)
        ah = max(1.0, self.height() - 24.0)
        ar = rs.width / max(1.0, rs.height)
        w = aw
        h = w / ar
        if h > ah:
            h = ah
            w = h * ar
        return QRectF((self.width() - w) / 2.0, (self.height() - h) / 2.0, w, h)

    def paintEvent(self, ev) -> None:  # noqa: N802
        import time as _t

        t0 = _t.perf_counter()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor("#101215"))
        r = self._bar_rect()
        p.fillRect(r, self._checker_brush())
        rs = self.doc.project.render
        p.save()
        p.translate(r.topLeft())
        p.setClipRect(QRectF(0, 0, r.width(), r.height()))
        self._renderer.p = self.doc.project     # 工程可能被换过（打开/新建）
        self._renderer.render(p, r.width(), r.height(), self.t_ms)
        p.restore()
        if self._show_safe:
            p.setPen(QPen(QColor("#3a4048"), 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
            p.setPen(QColor("#5b6470"))
            p.drawText(QRectF(r.left(), r.bottom() + 2, r.width(), 20), Qt.AlignLeft | Qt.AlignVCenter,
                       f"预览 {rs.width}×{rs.height}  ·  "
                       f"{ {'rtl': '右→左', 'ltr': '左→右', 'converge': '双侧汇聚'}.get(rs.flow, rs.flow) }"
                       f"  ·  流速 {rs.approach_s:g}s")
        p.end()
        dt = _t.perf_counter() - t0
        self._paint_times.append(dt)
        del self._paint_times[:-30]

    # 拖动预览条 = 擦洗
    def mousePressEvent(self, ev) -> None:  # noqa: N802
        self._scrub(ev)

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if ev.buttons() & Qt.LeftButton:
            self._scrub(ev)

    def _scrub(self, ev) -> None:
        r = self._bar_rect()
        if r.width() <= 1:
            return
        k = (ev.position().x() - r.left()) / r.width()
        k = max(0.0, min(1.0, k))
        jx = self.doc.project.render.judge_ratio
        # 大约把鼠标位置当成判定点位置来估算时间
        dt = (k - jx) * float(self.doc.project.render.approach_s) * 1000.0
        self.seekRequested.emit(max(0.0, self.t_ms + dt))


class SpectrumWidget(QWidget):
    """实时频谱条：播放时显示当前时刻各频段的能量，方便跟着鼓点打点。"""

    BARS = 56

    def __init__(self, doc: Doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.setMinimumHeight(44)
        self.setMaximumHeight(72)
        self.t_ms = 0.0
        self.playing = False
        self.samples = None            # float32 (n,2)
        self.sr = 48000
        self._bars = np.zeros(self.BARS, dtype=np.float32)
        self._peak = np.zeros(self.BARS, dtype=np.float32)
        self._edges: np.ndarray | None = None
        self._n_fft = 4096

    def set_audio(self, samples, sr: int) -> None:
        self.samples = samples
        self.sr = int(sr or 48000)
        self._edges = None
        self.update()

    def _band_edges(self) -> np.ndarray:
        if self._edges is None:
            k = np.arange(self.BARS + 1, dtype=np.float64) / self.BARS
            f = 40.0 * np.power(16000.0 / 40.0, k)
            nyq = self.sr / 2.0
            e = np.clip(np.round(f / nyq * (self._n_fft // 2)).astype(np.int64),
                        0, self._n_fft // 2)
            self._edges = np.maximum.accumulate(e)
        return self._edges

    def set_position(self, ms: float, playing: bool) -> None:
        self.t_ms = float(ms)
        self.playing = bool(playing)
        if self.samples is not None:
            self._compute()
        self.update()

    def _compute(self) -> None:
        s = self.samples
        n = self._n_fft
        pos = int(self.t_ms / 1000.0 * self.sr)
        a = max(0, min(max(0, s.shape[0] - n - 1), pos - n // 2))
        seg = s[a: a + n]
        if seg.shape[0] < n:
            seg = np.pad(seg, ((0, n - seg.shape[0]), (0, 0)))
        mono = seg.mean(axis=1) if seg.ndim > 1 else seg
        win = np.hanning(n).astype(np.float32)
        spec = np.abs(np.fft.rfft(mono * win)).astype(np.float32)
        spec *= (2.0 / n)
        edges = self._band_edges()
        edges = np.clip(edges, 0, spec.size - 1)
        bars = np.maximum.reduceat(spec, edges[:-1]) if edges.size == self.BARS + 1 else None
        if bars is None or bars.size != self.BARS:
            bars = np.zeros(self.BARS, dtype=np.float32)
        db = 20.0 * np.log10(np.maximum(bars, 1e-7))
        v = np.clip((db + 66.0) / 66.0, 0.0, 1.0).astype(np.float32)
        v = np.power(v, 0.8)
        # 平滑上升快、回落慢，看起来更稳
        self._bars = np.where(v > self._bars, v, self._bars * 0.72 + v * 0.28)
        self._peak = np.maximum(self._peak * 0.94, self._bars)

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0d1013"))
        w, h = self.width(), self.height()
        if self.samples is None:
            p.setPen(QColor("#4a5560"))
            p.drawText(self.rect(), Qt.AlignCenter, "实时频谱（载入音频后显示）")
            p.end()
            return
        bw = w / self.BARS
        grad = QLinearGradient(0, h - 4, 0, 4)
        grad.setColorAt(0.0, QColor("#1f6f8b"))
        grad.setColorAt(0.6, QColor("#3fd2ea"))
        grad.setColorAt(1.0, QColor("#b9f4ff"))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        for i in range(self.BARS):
            v = float(self._bars[i])
            bh = max(1.0, v * (h - 8))
            p.drawRect(QRectF(i * bw + 0.6, h - 4 - bh, max(1.0, bw - 1.2), bh))
        p.setBrush(QBrush(QColor(255, 255, 255, 150)))
        for i in range(self.BARS):
            v = float(self._peak[i])
            if v > 0.02:
                y = h - 4 - v * (h - 8)
                p.drawRect(QRectF(i * bw + 0.6, max(1.0, y - 1.5), max(1.0, bw - 1.2), 1.6))
        p.setPen(QColor("#39414b"))
        p.drawLine(0, h - 3, w, h - 3)
        p.end()


class RefFrameWidget(QWidget):
    """参考视频画面：跟随播放头显示；暂停后自动取一帧精确画面。"""

    seekRequested = Signal(float)

    def __init__(self, doc: Doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.film = None
        self.ffmpeg = ""
        self.t_ms = 0.0
        self.playing = False
        self.setMinimumWidth(180)
        self.setMinimumHeight(110)
        self._precise: dict[int, tuple[QImage, object]] = {}
        self._busy = False
        self._task = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(320)
        self._timer.timeout.connect(self._want_precise)

    def set_filmstrip(self, film, ffmpeg: str = "") -> None:
        self.film = film
        self.ffmpeg = ffmpeg or self.ffmpeg
        self._precise.clear()
        self.update()

    def set_playhead(self, ms: float, playing: bool) -> None:
        self.t_ms = float(ms)
        self.playing = bool(playing)
        if playing:
            self._timer.stop()
        else:
            self._timer.start()
        self.update()

    # ---------------------------------------------------------- 精确取帧
    def _want_precise(self) -> None:
        if self.film is None or self.playing or self._busy:
            return
        key = int(self.t_ms // 40)
        if key in self._precise:
            return
        from ..tasks import Task
        from ..video import decode_frame, probe_video

        path = self.film.path

        def work():
            arr, w, h = decode_frame(path, key * 40.0, None, 270, self.ffmpeg)
            import numpy as np

            a = np.ascontiguousarray(arr)
            img = QImage(a.data, w, h, w * 4, QImage.Format_RGBA8888)
            return (img.copy(), None)      # copy：脱离 numpy 内存

        self._busy = True
        self._task = Task(work, parent=self)
        self._task.done.connect(lambda res, k=key: self._precise_done(k, res))
        self._task.failed.connect(lambda _m: self._precise_fail())
        self._task.start()
        _ = probe_video

    def _precise_done(self, key: int, res) -> None:
        self._busy = False
        img, _ = res
        self._precise[key] = (img, None)
        if len(self._precise) > 40:
            for k in list(self._precise.keys())[:20]:
                self._precise.pop(k, None)
        self.update()

    def _precise_fail(self) -> None:
        self._busy = False

    # ---------------------------------------------------------------- 绘制
    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.fillRect(self.rect(), QColor("#101215"))
        area = QRectF(4, 18, max(1.0, self.width() - 8), max(1.0, self.height() - 34))
        p.setPen(QColor("#5b6470"))
        p.drawText(QRectF(6, 2, self.width() - 12, 14), Qt.AlignLeft | Qt.AlignVCenter,
                   "参考视频" + ("（取帧中…）" if self._busy else ""))
        img = None
        key = int(self.t_ms // 40)
        if key in self._precise:
            img = self._precise[key][0]
        elif self.film is not None and self.film.count:
            img = self.film.image(self.film.index_at(self.t_ms))
        if img is None or img.isNull():
            p.setPen(QColor("#4a5560"))
            p.drawText(area, Qt.AlignCenter, "没有参考视频\n文件 → 导入参考视频")
        else:
            iw, ih = img.width(), img.height()
            k = min(area.width() / max(1, iw), area.height() / max(1, ih))
            w, h = iw * k, ih * k
            r = QRectF(area.left() + (area.width() - w) / 2,
                       area.top() + (area.height() - h) / 2, w, h)
            p.drawImage(r, img)
            p.setPen(QPen(QColor("#39414b")))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
        p.setPen(QColor("#8b949e"))
        p.drawText(QRectF(6, self.height() - 16, self.width() - 12, 14),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   f"{self.t_ms / 1000.0:8.3f}s")
        p.end()

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        area = QRectF(4, 18, max(1.0, self.width() - 8), max(1.0, self.height() - 34))
        if area.width() <= 1 or self.film is None:
            return
        k = (ev.position().x() - area.left()) / area.width()
        lo = self.t_ms - 1500.0
        hi = self.t_ms + 1500.0
        self.seekRequested.emit(max(0.0, lo + (hi - lo) * max(0.0, min(1.0, k))))
