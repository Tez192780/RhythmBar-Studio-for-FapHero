"""波形时间轴：谱面编辑主界面（行 = 音符类型，横向 = 时间）。"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QMenu, QWidget

from ..doc import Doc
from ..i18n import tr, trf
from ..model import Note

GUTTER = 108
BPM_H = 22
RULER_H = 20
ROW_H = 26
WAVE_MIN = 46
FILM_H = 58
SPEC_MIN = 76
VIDEO_H = 58
AUDIO_WAVE_H = 52
AUDIO_SPEC_H = 58
NOTE_W = 9
CLIP_SNAP_PX = 8.0

BG = QColor("#191b1f")
BG_ROW = QColor("#212429")
BG_ROW_ALT = QColor("#1d2025")
BG_ROW_ACTIVE = QColor("#2a3038")
GRID = QColor("#2f343c")
GRID_BAR = QColor("#464e59")
GRID_SUB = QColor("#262b32")
WAVE = QColor("#3f5f7a")
WAVE_EDGE = QColor("#5f8cb0")
RULER_BG = QColor("#14161a")
RULER_FG = QColor("#8b949e")
PLAYHEAD = QColor("#ff3b5c")
SEL = QColor("#ffffff")
LOOP = QColor("#3fd2ea")
RANGE_C = QColor("#7ec8ff")
BPM_BG = QColor("#1b1e23")

# 吸附网格选项：显示名 -> 每拍切几格
SNAP_CHOICES = [
    ("四分音符", 1.0),
    ("八分音符", 2.0),
    ("八分三连", 3.0),
    ("十六分", 4.0),
    ("十六三连", 6.0),
    ("三十二分", 8.0),
    ("六十四分", 16.0),
    ("不吸附", 0.0),
]


def fmt_time(ms: float) -> str:
    if ms < 0:
        ms = 0.0
    total = ms / 1000.0
    m = int(total // 60)
    s = total - m * 60
    return f"{m}:{s:06.3f}"


class TimelineWidget(QWidget):
    """谱面编辑时间轴。

    性能要点：静态层（行底色 / 波形 / 网格 / 标尺 / BPM 条 / 左侧标题）
    渲染到一块比控件更宽的 QPixmap 上，播放滚动时直接平移复用，
    只有滚动超过余量才重画一次 —— 否则每帧全量重绘会拖垮主线程，
    连带音频拉取被饿死（界面上表现为持续卡顿）。
    """

    BG_MARGIN = 320          # 背景缓存向右侧多画这么多像素

    seekRequested = Signal(float)
    statusMessage = Signal(str)
    rowChanged = Signal(int)
    bpmEditRequested = Signal(int)        # BPM 段序号
    bpmAddRequested = Signal(float)       # 在此时间加 BPM 段
    notesChanged = Signal()               # 谱面结构变化（需要刷新预览）
    copyRequested = Signal()
    pasteRequested = Signal(float, int)   # 粘贴到这里：(时间ms, 行号；-1=保持原来的行)
    duplicateRequested = Signal()         # 向后复制一份
    selectAllRequested = Signal()
    fillRequested = Signal(float, float)  # 区间填充：(t0, t1)；t1<=t0 表示“从 t0 开始”
    loopChanged = Signal()                # 循环区间被改（同步 UI 勾选状态）
    clipSelected = Signal(str)
    clipMoveStarted = Signal()
    clipMoved = Signal()                  # 拖动结束，需要重建混音
    clipSplitRequested = Signal()
    clipDeleteRequested = Signal()

    def __init__(self, doc: Doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(BPM_H + RULER_H + ROW_H + WAVE_MIN)
        self.setContextMenuPolicy(Qt.DefaultContextMenu)

        self._peaks: np.ndarray | None = None
        self._dur_ms = 0.0
        self._pos_ms = 0.0
        self._playing = False
        self._clips: list = []
        self._video_items: list = []
        self._audio_items: list = []
        self._specs: dict = {}
        self._spec = None
        self._spec_clip_id = ""
        self._clip_drag = None
        self._clip_hover = None
        self._snap_guide = None
        self._xr: tuple[float, float] | None = None      # 增量补画时的像素范围
        self._hover: Note | None = None
        self._hover_bpm = -1
        self._mode = ""
        self._press_pos = QPoint()
        self._press_ms = 0.0
        self._anchor: Note | None = None
        self._bpm_index = -1
        self._paint_row = 0
        self._paint_key = ""
        self._paint_last = 0.0
        self._paint_anchor = 0.0
        self._paint_count = 0
        self._range_anchor = 0.0
        self._mouse_ms = 0.0            # 鼠标在时间轴上的时间/行（粘贴用）
        self._mouse_row = -1
        self._clipboard_has = False
        self._drag_orig: dict[int, tuple[float, int, float]] = {}
        self._rubber: QRect | None = None
        self._ruler_font = QFont("Consolas", 8)
        self._small = QFont("Microsoft YaHei UI", 8)
        self._fps_times: list[float] = []
        # 背景缓存
        self._bg: QPixmap | None = None
        self._bg_alt: QPixmap | None = None
        self._bg_t0 = 0.0
        self._bg_meta: tuple | None = None
        self._rev = 0            # 内容版本号，变了就丢弃缓存
        # 音符画笔缓存（逐音符 new QPen/QColor 是最大的性能杀手）
        self._pens: dict[str, tuple] = {}
        self._pens_sig: tuple | None = None
        self._handle_pen = QPen(QColor("#8ab4f8"), 1.6)
        self._handle_brush = QBrush(QColor("#8ab4f8"))

    # ------------------------------------------------------------ 缓存控制
    def invalidate(self) -> None:
        """谱面/外观/音频变了以后调用，让背景缓存重画。"""
        self._rev += 1
        self._bg = None
        self.update()

    # ------------------------------------------------------------ 外部接口
    def set_audio(self, peaks: np.ndarray | None, duration_ms: float) -> None:
        self._peaks = peaks
        self._dur_ms = float(duration_ms)
        self._bg = None
        self.update()

    def set_duration(self, duration_ms: float) -> None:
        self._dur_ms = float(duration_ms)
        self._bg = None
        self.update()

    def set_clips(self, items) -> None:
        """媒体片段条：items = [(MediaClip, Filmstrip|peaks|None), ...]。"""
        self._clips = list(items or [])
        self._video_items = [(c, x) for c, x in self._clips if c.kind == "video"]
        self._audio_items = [(c, x) for c, x in self._clips if c.kind == "audio"]
        self._bg = None
        self._update_min_height()
        self.update()

    def set_filmstrip(self, film) -> None:
        """兼容旧调用（单个片段）。"""
        self.set_clips([] if film is None else [(film, film)])

    def set_spectrograms(self, specs: dict) -> None:
        self._specs = dict(specs or {})
        self._spec = next(iter(self._specs.values()), None)
        self._bg = None
        self._update_min_height()
        self.update()

    def clear_spectrograms(self) -> None:
        self.set_spectrograms({})

    def set_spectrogram(self, spec) -> None:
        """兼容旧调用：只有一段时也能用。"""
        if spec is None:
            self.clear_spectrograms()
        else:
            cid = self._audio_items[0][0].id if self._audio_items else ""
            self.set_spectrograms({cid: spec})

    def _update_min_height(self) -> None:
        extra = (VIDEO_H if self._video_items and self.doc.state.show_video else 0)
        if self._audio_items:
            extra += AUDIO_WAVE_H
        if self._specs:
            extra += AUDIO_SPEC_H
        self.setMinimumHeight(BPM_H + RULER_H + ROW_H + extra)

    def set_position(self, ms: float, playing: bool = False) -> None:
        st = self.doc.state
        prev_x = self._pos_ms
        self._pos_ms = float(ms)
        self._playing = playing
        if playing and st.follow:
            anchor = (0.45 * max(1.0, self.width() - GUTTER)) / max(1e-6, st.px_per_ms)
            st.view_t0 = self._pos_ms - anchor
            self.update()
            return
        # 只刷新当前时间附近，省性能
        for t in (prev_x, self._pos_ms):
            x = self.ms_to_x(t)
            if -20 <= x <= self.width() + 20:
                self.update(QRect(int(x) - 3, 0, 8, self.height()))

    def set_selection_visible(self) -> None:
        self.update()

    def zoom_to_fit(self) -> None:
        st = self.doc.state
        span = self._total_span()
        w = max(120.0, self.width() - GUTTER - 20.0)
        st.px_per_ms = max(0.0005, min(20.0, w / max(1.0, span)))
        st.view_t0 = -400.0
        self.update()

    def zoom_at(self, x: float, factor: float) -> None:
        st = self.doc.state
        anchor_ms = self.x_to_ms(x)
        st.px_per_ms = max(0.0005, min(40.0, st.px_per_ms * factor))
        st.view_t0 = anchor_ms - (x - GUTTER) / st.px_per_ms
        self.update()

    def scroll_by(self, dt_ms: float) -> None:
        self.doc.state.view_t0 += dt_ms
        self.update()

    def ensure_visible(self, ms: float) -> None:
        st = self.doc.state
        x = self.ms_to_x(ms)
        if x < GUTTER + 40:
            st.view_t0 = ms - 40.0 / st.px_per_ms
            self.update()
        elif x > self.width() - 40:
            st.view_t0 = ms - (self.width() - 40 - GUTTER) / st.px_per_ms
            self.update()

    def _total_span(self) -> float:
        lo, hi = self.doc.project.chart.bounds()
        if self._clips:
            lo = min([lo] + [c.offset_ms for c, _ in self._clips])
        lo = min(0.0, lo)
        hi = max(hi + 1500.0, self._dur_ms, self.doc.project.media_end_ms())
        return max(4000.0, hi - lo + 500.0)

    # ------------------------------------------------------------ 坐标换算
    def ms_to_x(self, ms: float) -> float:
        return GUTTER + (float(ms) - self.doc.state.view_t0) * self.doc.state.px_per_ms

    def x_to_ms(self, x: float) -> float:
        return self.doc.state.view_t0 + (float(x) - GUTTER) / max(1e-6, self.doc.state.px_per_ms)

    def rows(self) -> list[str]:
        return self.doc.project.theme.active_rows()

    def _row_h(self) -> float:
        n = max(1, len(self.rows()))
        extra = (VIDEO_H if self._video_items and self.doc.state.show_video else 0)
        if self._audio_items:
            extra += AUDIO_WAVE_H
        if self._spec_on():
            extra += AUDIO_SPEC_H
        avail = self.height() - BPM_H - RULER_H - extra
        return max(ROW_H, min(54.0, avail / n * 0.45))

    def _rows_top(self) -> float:
        return BPM_H + RULER_H

    def _rows_bottom(self) -> float:
        return self._rows_top() + len(self.rows()) * self._row_h()

    def _video_h(self) -> float:
        return VIDEO_H if self._video_items and self.doc.state.show_video else 0.0

    def _video_top(self) -> float:
        return self._rows_bottom()

    def _audio_top(self) -> float:
        return self._video_top() + self._video_h()

    def _audio_h(self) -> float:
        return AUDIO_WAVE_H if self._audio_items else 0.0

    def _film_h(self) -> float:
        return self._video_h() + self._audio_h()

    def _film_top(self) -> float:
        return self._rows_bottom()

    def _spec_on(self) -> bool:
        return bool(self._specs) and bool(self._audio_items) and self.doc.state.show_spectrum

    def _spec_top(self) -> float:
        return self._audio_top() + self._audio_h()

    def _spec_h(self) -> float:
        return AUDIO_SPEC_H if self._spec_on() else 0.0

    def _wave_top(self) -> float:
        return self._audio_top()

    def _wave_h(self) -> float:
        return self._audio_h()

    def _media_bottom(self) -> float:
        return self._spec_top() + self._spec_h()

    def row_y(self, i: int) -> float:
        return self._rows_top() + i * self._row_h()

    def row_at(self, y: float) -> int:
        n = len(self.rows())
        i = int((y - self._rows_top()) // self._row_h())
        return max(0, min(n - 1, i))

    def snap(self, ms: float) -> float:
        st = self.doc.state
        if not st.snap_on or st.snap_divisor <= 0:
            return float(ms)
        clip = self.doc.project.audio_clip_at(ms)
        if clip is None:
            if not self.doc.project.audio_clips:
                return self.doc.project.chart.timemap.snap(ms, st.snap_divisor)
            return float(ms)
        src = clip.timeline_to_source(ms)
        return clip.source_to_timeline(clip.timemap.snap(src, st.snap_divisor))

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, ev) -> None:  # noqa: N802
        import time as _t

        t_start = _t.perf_counter()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        st = self.doc.state
        p.fillRect(0, 0, w, h, BG)
        # 静态层：直接平移复用缓存
        self._ensure_bg(w, h)
        if self._bg is not None:
            dx = int(round((self._bg_t0 - st.view_t0) * st.px_per_ms))
            p.drawPixmap(dx, 0, self._bg)
        # 动态层
        self._draw_notes(p, w)
        self._draw_clip_snap_guide(p, h)
        self._draw_range(p, w, h)
        self._draw_rubber(p)
        self._draw_loop(p, w, h)
        self._draw_playhead(p, h)
        p.end()

        dt = _t.perf_counter() - t_start
        self._fps_times.append(dt)
        del self._fps_times[:-30]

    def _bg_sig(self) -> tuple:
        """背景内容指纹：只有它变了才需要重画缓存（比手动标记更省心）。"""
        st = self.doc.state
        th = self.doc.project.theme
        rows = th.active_rows()
        clip_sig = tuple(
            (c.id, c.kind, round(c.offset_ms, 4), round(c.duration_ms, 4),
             round(c.src_start_ms, 4), c.link_id,
             tuple((round(s.time_ms, 4), round(s.bpm, 6)) for s in c.timemap.segments),
             round(c.timemap.offset_ms, 4))
            for c, _x in self._clips)
        spec_sig = tuple(sorted((cid, getattr(sp, "frames", 0))
                                for cid, sp in self._specs.items()))
        return (
            round(st.px_per_ms, 9), self.width(), self.height(), st.show_waveform,
            st.snap_divisor, st.current_lane, tuple(rows),
            tuple((th.style(k).name, th.style(k).color) for k in rows),
            clip_sig, st.selected_clip_id, st.show_video, st.show_spectrum,
            round(self._dur_ms, 2), spec_sig,
        )

    def _bg_buffer(self, pw: int, h: int) -> QPixmap:
        """双缓冲：重画时复用旧的那块，避免每 1.4 秒新分配 5MB 位图（那本身就要 5~20ms）。"""
        for attr in ("_bg_alt", "_bg"):
            pm = getattr(self, attr, None)
            if pm is not None and pm.width() == pw and pm.height() == h:
                return pm
        return QPixmap(pw, h)

    def _ensure_bg(self, w: int, h: int) -> None:
        """背景缓存：比控件宽 BG_MARGIN，滚动时平移复用。

        滚过 BG_MARGIN 之后**不再整块重画**（放大编辑时那是每 1.6 秒一次的
        15~33ms 卡顿，正是「隔几秒一小卡」），改成：
        把旧图左移，只重画右侧新露出来的那一条。
        """
        st = self.doc.state
        sig = self._bg_sig()
        pw = w + self.BG_MARGIN
        if self._bg is not None and self._bg_meta == sig:
            dx = (self._bg_t0 - st.view_t0) * st.px_per_ms
            if -self.BG_MARGIN + 1 <= dx <= 0.5:
                return                                  # 直接复用
            if dx < -self.BG_MARGIN + 1:
                # 平移复用：只补画右侧露出的一条
                shift = int(min(self.BG_MARGIN, -dx))
                if 0 < shift < pw - GUTTER:
                    pm = self._bg_buffer(pw, h)
                    p = QPainter(pm)
                    p.setRenderHint(QPainter.Antialiasing, False)
                    p.drawPixmap(0, 0, self._bg, shift, 0, pw - shift, h)
                    self._bg_t0 = st.view_t0             # 平移后 dx = 0
                    self._bg_alt = self._bg              # 旧的留作下次缓冲
                    self._bg = pm
                    self._xr = (float(pw - shift), float(pw))
                    p.setClipRect(QRectF(pw - shift, 0, shift, h))
                    self._render_bg_layers(p, pw, h)
                    self._xr = None
                    p.end()
                    self._bg_meta = sig
                    return
        # 全量重画
        pm = self._bg_buffer(pw, h)
        pm.fill(BG)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, False)
        self._bg_t0 = st.view_t0
        self._bg_meta = sig
        self._xr = None
        self._render_bg_layers(p, pw, h)
        p.end()
        if self._bg is not pm:
            self._bg_alt = self._bg
        self._bg = pm

    def _render_bg_layers(self, p: QPainter, pw: int, h: int) -> None:
        """画静态层；_xr 非空时表示只画那一段像素（增量补画）。"""
        st = self.doc.state
        x0, x1 = self._xrange(pw)
        self._draw_rows(p, pw, h)
        if self._spec_on():
            self._draw_spectrum(p, pw)
        if st.show_waveform:
            self._draw_wave(p, pw)
        if self._clips:
            self._draw_media(p, pw)
        self._draw_grid(p, pw, h)
        self._draw_bpm_lane(p, pw)
        self._draw_ruler(p, pw)
        if x0 <= GUTTER:                    # 左侧标题栏只在全量重画时更新
            self._draw_gutter(p, h)
        if self._spec_on():
            self._draw_spec_labels(p, pw)

    def _xrange(self, w: float) -> tuple[float, float]:
        """当前要画的像素范围（增量补画时只有一条，其余是全宽）。"""
        if self._xr is None:
            return (0.0, float(w))
        return self._xr

    # -- 每个音频片段自己的频谱图
    def _draw_spectrum(self, p: QPainter, w: int) -> None:
        if not self._specs:
            return
        y0 = self._spec_top()
        hh = self._spec_h()
        if hh <= 2:
            return
        p.fillRect(QRectF(0, y0, w, hh), QColor("#070910"))
        sx0, sx1 = self._xrange(w)
        sx0 = max(GUTTER, sx0)
        if sx1 - sx0 < 1:
            return
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        for clip in self.doc.project.audio_clips:
            sp = self._specs.get(clip.id)
            if sp is None:
                continue
            left = max(sx0, self.ms_to_x(clip.offset_ms))
            right = min(sx1, self.ms_to_x(clip.end_ms()))
            if right - left < 1:
                continue
            src0 = clip.timeline_to_source(self.x_to_ms(left))
            src1 = clip.timeline_to_source(self.x_to_ms(right))
            c0 = max(0.0, (src0 - clip.src_start_ms) / max(1e-6, sp.frame_ms))
            c1 = min(float(sp.frames),
                     (src1 - clip.src_start_ms) / max(1e-6, sp.frame_ms))
            if c1 - c0 < 0.5:
                continue
            p.drawImage(QRectF(left, y0, right - left, hh), sp.image(),
                        QRectF(c0, 0.0, c1 - c0, float(sp.bins)))
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)

    def _draw_spec_labels(self, p: QPainter, w: int) -> None:
        """频率刻度画在左侧标题栏里（要在 _draw_gutter 之后画）。"""
        sp = self._spec
        if sp is None:
            return
        y0 = self._spec_top()
        hh = self._spec_h()
        if hh <= 8:
            return
        p.setFont(self._ruler_font)
        for f, row in sp.freq_labels():
            yy = y0 + (row + 0.5) / sp.bins * hh
            p.setPen(QPen(QColor(255, 255, 255, 26)))
            p.drawLine(QPointF(GUTTER, yy), QPointF(w, yy))
            p.setPen(QColor("#93a1b0"))
            txt = f"{int(f)}" if f < 1000 else f"{f / 1000:g}k"
            p.drawText(QRectF(6, yy - 7, GUTTER - 12, 14), Qt.AlignRight | Qt.AlignVCenter, txt)
        p.setPen(QColor("#5c6672"))
        p.drawText(QRectF(6, y0 - 13, GUTTER - 12, 12), Qt.AlignRight | Qt.AlignTop, tr("Hz"))

    # -- 媒体片段：视频轨 + 音频轨
    def _clip_rect(self, clip):
        dur = clip.duration_ms or 1000.0
        x0 = self.ms_to_x(clip.offset_ms)
        x1 = self.ms_to_x(clip.offset_ms + dur)
        if clip.kind == "video":
            y0, hh = self._video_top(), self._video_h()
        else:
            y0, hh = self._audio_top(), self._audio_h()
        return QRectF(x0, y0 + 1.0, max(2.0, x1 - x0), max(2.0, hh - 2.0))

    def _clip_at_pos(self, pos: QPoint):
        y = pos.y()
        if self._video_h() > 0 and self._video_top() <= y < self._video_top() + self._video_h():
            for c, fs in self._video_items:
                if self._clip_rect(c).adjusted(-2, 0, 2, 0).contains(QPointF(pos)):
                    return c
        if self._audio_h() > 0 and self._audio_top() <= y < self._audio_top() + self._audio_h():
            for c, pk in self._audio_items:
                if self._clip_rect(c).adjusted(-2, 0, 2, 0).contains(QPointF(pos)):
                    return c
        return None

    def _draw_media(self, p: QPainter, w: int) -> None:
        if not self._clips:
            return
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        # 视频轨
        vh = self._video_h()
        if vh > 0:
            y0 = self._video_top()
            p.fillRect(QRectF(0, y0, w, vh), QColor("#0d1013"))
            for clip, fs in self._video_items:
                rect = self._clip_rect(clip)
                if rect.right() < GUTTER or rect.left() > w + 30:
                    continue
                if fs is not None and getattr(fs, "count", 0):
                    sw, sh = fs.thumb_w, fs.thumb_h
                    scale = (vh - 2.0) / max(1, sh)
                    a = max(0, int((self.x_to_ms(max(GUTTER, rect.left()))
                                    - clip.offset_ms + clip.src_start_ms)
                                   / max(1.0, fs.step_ms)) - 1)
                    b = min(fs.count - 1, int((self.x_to_ms(min(w, rect.right()))
                                               - clip.offset_ms + clip.src_start_ms)
                                              / max(1.0, fs.step_ms)) + 1)
                    for i in range(a, b + 1):
                        img = fs.image(i)
                        if img is None:
                            continue
                        x = self.ms_to_x(clip.offset_ms + fs.time_of(i) - clip.src_start_ms)
                        xn = (self.ms_to_x(clip.offset_ms + fs.time_of(i + 1) - clip.src_start_ms)
                              if i + 1 < fs.count else x + fs.step_ms * self.doc.state.px_per_ms)
                        cell = max(1.0, min(xn, rect.right()) - max(x, rect.left()))
                        if cell <= 0.5:
                            continue
                        img_w = sw * scale
                        src_x, src_w = 0.0, float(sw)
                        if img_w > cell:
                            src_w = sw * (cell / img_w)
                            src_x = (sw - src_w) * 0.5
                            img_w = cell
                        p.drawImage(QRectF(max(x, rect.left()), rect.top(), img_w, rect.height()),
                                    img, QRectF(src_x, 0.0, src_w, float(sh)))
                self._draw_clip_frame(p, clip, rect, QColor("#1e5a8c"), QColor("#7ec8ff"))
        # 音频轨
        ah = self._audio_h()
        if ah > 0:
            y0 = self._audio_top()
            if not self.doc.state.show_waveform:
                p.fillRect(QRectF(0, y0, w, ah), QColor("#101619"))
            for clip, _pk in self._audio_items:
                rect = self._clip_rect(clip)
                if rect.right() < GUTTER or rect.left() > w + 30:
                    continue
                self._draw_clip_frame(p, clip, rect, QColor("#1e6b52"), QColor("#5fe0a0"))
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        if vh > 0:
            p.setPen(QPen(QColor("#39414b")))
            p.drawLine(QPointF(0, self._video_top()), QPointF(w, self._video_top()))
            p.drawLine(QPointF(0, self._video_top() + vh), QPointF(w, self._video_top() + vh))
        if ah > 0:
            p.setPen(QPen(QColor("#39414b")))
            p.drawLine(QPointF(0, self._audio_top()), QPointF(w, self._audio_top()))
            p.drawLine(QPointF(0, self._audio_top() + ah), QPointF(w, self._audio_top() + ah))

    def _draw_clip_frame(self, p: QPainter, clip, rect: QRectF, tint: QColor, edge: QColor) -> None:
        selected = clip.id == self.doc.state.selected_clip_id
        fill = QColor(tint)
        fill.setAlpha(55)
        p.setBrush(QBrush(fill))
        p.setPen(QPen(QColor("#ffffff") if selected else edge, 2.0 if selected else 1.2))
        p.drawRoundedRect(rect, 3, 3)
        if rect.width() > 46:
            self._small.setBold(selected)
            p.setFont(self._small)
            label_rect = QRectF(rect.left() + 4, rect.top() + 2,
                                min(rect.width() - 8, 280), 14)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(235, 244, 252, 215)))
            p.drawRoundedRect(label_rect, 2, 2)
            p.setPen(QColor("#16202a"))
            icon = "🎬" if clip.kind == "video" else "♪"
            link = " 🔗" if clip.link_id else ""
            p.drawText(label_rect.adjusted(4, 0, -3, 0), Qt.AlignVCenter | Qt.AlignLeft,
                       f"{icon} {clip.label}{link}")
            self._small.setBold(False)


    def fps(self) -> float:
        if not self._fps_times:
            return 0.0
        avg = sum(self._fps_times) / len(self._fps_times)
        return 1.0 / avg if avg > 0 else 0.0

    # -- 各行底
    def _draw_rows(self, p: QPainter, w: int, h: int) -> None:
        rows = self.rows()
        top = self._rows_top()
        rh = self._row_h()
        rx0, rx1 = self._xrange(w)
        rw = max(1.0, rx1 - rx0)
        for i, key in enumerate(rows):
            y = self.row_y(i)
            active = i == self.doc.state.current_lane
            p.fillRect(QRectF(rx0, y, rw, rh),
                       BG_ROW_ACTIVE if active else (BG_ROW if i % 2 == 0 else BG_ROW_ALT))
            st = self.doc.project.theme.style(key)
            if rx0 <= 3:                        # 左侧类型色条只在全量时画
                p.fillRect(QRectF(0, y, 3, rh), QColor(st.color))
            p.setPen(QPen(QColor("#2b3037")))
            p.drawLine(QPointF(rx0, y + rh), QPointF(rx1, y + rh))
        bottom = top + len(rows) * rh
        p.fillRect(QRectF(rx0, bottom, rw, max(0.0, h - bottom)), QColor("#15171b"))

    # -- 每个音频片段自己的波形
    def _draw_wave(self, p: QPainter, w: int) -> None:
        y0 = self._wave_top()
        hh = self._wave_h()
        if hh <= 1:
            return
        p.fillRect(QRectF(0, y0, w, hh), QColor("#151a20"))
        p.setPen(QPen(QColor("#262c34")))
        p.drawLine(QPointF(0, y0), QPointF(w, y0))
        if not self._audio_items:
            if self._xr is None:
                p.setPen(QColor("#4a5560"))
                p.setFont(self._small)
                p.drawText(QRectF(GUTTER + 12, y0, w - GUTTER - 20, hh), Qt.AlignCenter,
                           tr("未加载音频 —— 把 mp3 / wav / flac 拖进窗口，或点工具栏「打开音频」"))
            return
        for clip, pk in self._audio_items:
            if pk is None or len(pk) == 0:
                continue
            rect = self._clip_rect(clip)
            left = max(GUTTER, rect.left())
            right = min(w, rect.right())
            if right - left < 2:
                continue
            cols = max(2, int(right - left))
            idx = np.linspace(0, len(pk) - 1, cols).astype(np.int64)
            vals = np.clip(pk[idx], 0.0, 1.0)
            mid = rect.top() + rect.height() * 0.5
            half = max(1.0, rect.height() * 0.46)
            xs = np.arange(cols, dtype=np.float32) + left
            top = [QPointF(float(xs[i]), float(mid - vals[i] * half)) for i in range(cols)]
            bottom = [QPointF(float(xs[i]), float(mid + vals[i] * half))
                      for i in range(cols - 1, -1, -1)]
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(WAVE))
            p.drawPolygon(QPolygonF(top + bottom))
            p.setPen(QPen(WAVE_EDGE, 1))
            p.drawPolyline(QPolygonF(top))
            p.drawLine(QPointF(left, mid), QPointF(right, mid))
        p.setPen(QPen(QColor("#39414b")))
        p.drawLine(QPointF(0, y0 + hh), QPointF(w, y0 + hh))

    # -- 节拍网格（每个音频片段自己的 BPM；空档不画）
    def _draw_grid(self, p: QPainter, w: int, h: int) -> None:
        st = self.doc.state
        gx0, gx1 = self._xrange(w)
        t0, t1 = self.x_to_ms(max(GUTTER, gx0)), self.x_to_ms(gx1)
        top = BPM_H + RULER_H
        div = max(1.0, st.snap_divisor or 1.0)
        if not self.doc.project.audio_clips:
            tm = self.doc.project.chart.timemap
            p.setPen(QPen(GRID_SUB, 1))
            for t in tm.grid_lines(t0, t1, div, limit=3000):
                x = self.ms_to_x(t)
                if x >= GUTTER:
                    p.drawLine(QPointF(x, top), QPointF(x, h))
            p.setFont(self._ruler_font)
            for t in tm.grid_lines(t0, t1, 1.0, limit=1200):
                x = self.ms_to_x(t)
                if x < GUTTER:
                    continue
                b = tm.beat(t)
                is_bar = abs(b - round(b / 4.0) * 4.0) < 1e-6
                p.setPen(QPen(GRID_BAR if is_bar else GRID, 1))
                p.drawLine(QPointF(x, top), QPointF(x, h))
                if is_bar:
                    p.setPen(QColor("#5c6672"))
                    p.drawText(QPointF(x + 3, top + 11),
                               str(int(round(b / 4.0)) + 1))
            return
        for clip in self.doc.project.audio_clips:
            left = max(t0, clip.offset_ms)
            right = min(t1, clip.end_ms())
            if right <= left:
                continue
            tm = clip.timemap
            s0 = clip.timeline_to_source(left)
            s1 = clip.timeline_to_source(right)
            respace = (tm.ms(tm.beat(s0) + 1.0) - tm.ms(tm.beat(s0))
                       if st.px_per_ms else 0.0)
            beat_px = max(1e-6, respace * st.px_per_ms)
            clip_l = self.ms_to_x(clip.offset_ms)
            clip_r = self.ms_to_x(clip.end_ms())
            if beat_px / div >= 7.0:
                p.setPen(QPen(GRID_SUB, 1))
                for src in tm.grid_lines(s0, s1, div, limit=3000):
                    t = clip.source_to_timeline(src)
                    x = self.ms_to_x(t)
                    if x >= max(GUTTER, clip_l) and x <= clip_r:
                        p.drawLine(QPointF(x, top), QPointF(x, h))
            p.setFont(self._ruler_font)
            for src in tm.grid_lines(s0, s1, 1.0, limit=1200):
                t = clip.source_to_timeline(src)
                x = self.ms_to_x(t)
                if x < max(GUTTER, clip_l) or x > clip_r:
                    continue
                b = tm.beat(src)
                is_bar = abs(b - round(b / 4.0) * 4.0) < 1e-6
                p.setPen(QPen(GRID_BAR if is_bar else GRID, 1))
                p.drawLine(QPointF(x, top), QPointF(x, h))
                if is_bar:
                    p.setPen(QColor("#5c6672"))
                    p.drawText(QPointF(x + 3, top + 11),
                               str(int(round(b / 4.0)) + 1))

    # -- BPM 段（按音频片段分段显示）
    def _draw_bpm_lane(self, p: QPainter, w: int) -> None:
        p.fillRect(QRectF(0, 0, w, BPM_H), BPM_BG)
        p.setFont(self._small)
        if not self.doc.project.audio_clips:
            tm = self.doc.project.chart.timemap
            segs = tm.segments
            for i, s in enumerate(segs):
                x = self.ms_to_x(s.time_ms)
                nx = (self.ms_to_x(segs[i + 1].time_ms)
                      if i + 1 < len(segs) else w)
                a, b = max(x, GUTTER), min(nx, w)
                if b <= a:
                    continue
                p.fillRect(QRectF(a, 2, b - a, BPM_H - 4),
                           QColor("#2f6f4f") if i % 2 == 0 else QColor("#2b5c74"))
                p.setPen(QColor("#dfe7ee"))
                if b - a > 46:
                    p.drawText(QRectF(a + 5, 0, b - a - 8, BPM_H),
                               Qt.AlignVCenter | Qt.AlignLeft, f"{s.bpm:g}")
            return
        for clip in self.doc.project.audio_clips:
            left = max(self.ms_to_x(clip.offset_ms), GUTTER)
            right = min(self.ms_to_x(clip.end_ms()), w)
            if right <= left:
                continue
            tm = clip.timemap
            segs = tm.segments
            for i, s in enumerate(segs):
                x = self.ms_to_x(clip.source_to_timeline(s.time_ms))
                nx_src = (segs[i + 1].time_ms if i + 1 < len(segs)
                          else clip.source_end_ms())
                nx = self.ms_to_x(clip.source_to_timeline(nx_src))
                a = max(left, x)
                b = min(right, nx)
                if b <= a:
                    continue
                col = QColor("#2f6f4f") if i % 2 == 0 else QColor("#2b5c74")
                p.fillRect(QRectF(a, 2, max(0.0, b - a), BPM_H - 4), col)
                p.setPen(QPen(QColor("#0e1114"), 1))
                p.drawLine(QPointF(a, 0), QPointF(a, BPM_H))
                p.setPen(QColor("#dfe7ee"))
                if b - a > 46:
                    p.drawText(QRectF(a + 5, 0, b - a - 8, BPM_H),
                               Qt.AlignVCenter | Qt.AlignLeft, f"{s.bpm:g}")
            # 拍 0 标记
            x0 = self.ms_to_x(clip.source_to_timeline(tm.offset_ms))
            if left <= x0 <= right:
                p.setPen(QPen(QColor("#ffd166"), 1, Qt.DashLine))
                p.drawLine(QPointF(x0, 0), QPointF(x0, BPM_H))
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(QColor("#ffd166")))
                p.drawEllipse(QPointF(x0, BPM_H - 3), 2.5, 2.5)
                p.setBrush(Qt.NoBrush)

    # -- 音符
    def _note_owner(self, n: Note):
        c = self.doc.project.clip_by_id(n.clip_id) if n.clip_id else None
        if c is None:
            c = self.doc.project.audio_clip_at(n.t)
        if c is None or c.kind != "audio":
            return None
        return c

    def _note_visible(self, n: Note) -> bool:
        if not self.doc.project.audio_clips:
            return True                 # 无音频的示例/旧工程：保留旧行为
        c = self._note_owner(n)
        return c is not None and c.contains_time(n.t, 0.8)

    def _note_x_range(self, n: Note) -> tuple[float, float]:
        x = self.ms_to_x(n.t)
        x2 = self.ms_to_x(n.end)
        return (min(x, x2), max(x, x2))

    def _style_cache(self, key: str):
        """按类型缓存 QBrush/QPen —— 逐音符 new 对象是最大的开销来源。"""
        c = self._pens.get(key)
        if c is None:
            st = self.doc.project.theme.style(key)
            col = QColor(st.color)
            outline = QColor("#0b0d10")
            pen = QPen(outline, 1.4)
            pen.setJoinStyle(Qt.RoundJoin)
            bar = QColor(col)
            bar.setAlpha(150)
            sel_pen = QPen(SEL, 2.0)
            c = (QBrush(col), pen, QBrush(bar), sel_pen, QColor(255, 255, 255, 120))
            self._pens[key] = c
        return c

    def _draw_notes(self, p: QPainter, w: int) -> None:
        st = self.doc.state
        chart = self.doc.project.chart
        t0v, ppm = st.view_t0, st.px_per_ms
        x_lo, x_hi = GUTTER - 14.0, w + 14.0
        # 只取真正落在屏幕上的音符（时间->像素单调，直接换算窗口）
        t_lo = t0v + (x_lo - GUTTER) / ppm - chart.max_dur - 40.0
        t_hi = t0v + (x_hi - GUTTER) / ppm + 40.0
        vis = [n for n in chart.in_range(t_lo, t_hi) if self._note_visible(n)]
        if not vis:
            return
        styles = self.doc.project.theme.styles
        sig = tuple((k, s.color, s.outline) for k, s in styles.items())
        if sig != self._pens_sig:
            self._pens.clear()
            self._pens_sig = sig
        rows = self.rows()
        row_index = {k: i for i, k in enumerate(rows)}
        rh = self._row_h()
        half = rh * 0.5
        rows_top = self._rows_top()
        sel = self.doc.selection
        n_sel = len(sel)
        hover = self._hover
        NH = NOTE_W * 0.5
        p.setRenderHint(QPainter.Antialiasing, True)
        for n in vis:
            x = GUTTER + (n.t - t0v) * ppm
            x2 = GUTTER + (n.end - t0v) * ppm
            if n.dur > 0:
                if x2 < x_lo or x > x_hi:
                    continue
            elif x < x_lo or x > x_hi:
                continue
            c = self._pens.get(n.type)
            if c is None:
                if n.type not in styles:
                    continue
                c = self._style_cache(n.type)
            br_fill, pen_out, br_bar, sel_pen, hov_pen = c
            y = rows_top + row_index.get(n.type, n.lane) * rh + half
            if n.dur > 0:
                p.setPen(Qt.NoPen)
                p.setBrush(br_bar)
                p.drawRoundedRect(QRectF(x, y - 5.0, max(2.0, x2 - x), 10.0), 4.0, 4.0)
            rect = QRectF(x - NH, y - 7.0, NOTE_W, 14.0)
            p.setPen(pen_out)
            p.setBrush(br_fill)
            p.drawRoundedRect(rect, 3.0, 3.0)      # 一次调用同时画填充与描边
            if n_sel and n in sel:
                p.setPen(sel_pen)
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 4.0, 4.0)
            elif n is hover:
                p.setPen(hov_pen)
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 4.0, 4.0)
                # 悬停时右侧显示「拉长条」把手
                hx = x + NH + 4.0
                p.setPen(self._handle_pen)
                p.drawLine(QPointF(hx, y - 5), QPointF(hx, y + 5))
                tri = QPainterPath()
                tri.moveTo(hx + 1, y - 4)
                tri.lineTo(hx + 5, y)
                tri.lineTo(hx + 1, y + 4)
                tri.closeSubpath()
                p.setPen(Qt.NoPen)
                p.setBrush(self._handle_brush)
                p.drawPath(tri)
        p.setRenderHint(QPainter.Antialiasing, False)

    # -- 框选
    def _draw_rubber(self, p: QPainter) -> None:
        if self._rubber is None:
            return
        p.setPen(QPen(QColor("#7ec8ff"), 1, Qt.DashLine))
        c = QColor("#7ec8ff")
        c.setAlpha(38)
        p.setBrush(QBrush(c))
        p.drawRect(self._rubber)

    # -- 区间选择（在标尺/波形处拖出来，用来填充/循环）
    def _draw_range(self, p: QPainter, w: int, h: int) -> None:
        st = self.doc.state
        if not st.has_range():
            return
        xa, xb = self.ms_to_x(st.sel_t0), self.ms_to_x(st.sel_t1)
        if xb < GUTTER or xa > w:
            return
        c = QColor(RANGE_C)
        c.setAlpha(26)
        p.fillRect(QRectF(max(xa, GUTTER), BPM_H, min(xb, w) - max(xa, GUTTER), h - BPM_H), c)
        p.setPen(QPen(RANGE_C, 1.4))
        for x in (xa, xb):
            if GUTTER - 4 <= x <= w + 4:
                p.drawLine(QPointF(x, BPM_H), QPointF(x, h))
                p.setBrush(QBrush(RANGE_C))
                p.setPen(Qt.NoPen)
                p.drawRect(QRectF(x - 2.5, BPM_H + 1, 5.0, 9.0))
                p.setPen(QPen(RANGE_C, 1.4))
        dur = (st.sel_t1 - st.sel_t0) / 1000.0
        txt = f"{dur:.3f}s"
        xm = (max(xa, GUTTER) + min(xb, w)) / 2.0
        if min(xb, w) - max(xa, GUTTER) > 54:
            p.setFont(self._small)
            tw = 56.0
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(20, 30, 40, 210)))
            p.drawRoundedRect(QRectF(xm - tw / 2, BPM_H + RULER_H - 15, tw, 14), 3, 3)
            p.setPen(QColor("#bfe2ff"))
            p.drawText(QRectF(xm - tw / 2, BPM_H + RULER_H - 15, tw, 14),
                       Qt.AlignCenter, txt)

    # -- 循环区间
    def _draw_loop(self, p: QPainter, w: int, h: int) -> None:
        st = self.doc.state
        if not st.loop_on or st.loop_b <= st.loop_a:
            return
        xa, xb = self.ms_to_x(st.loop_a), self.ms_to_x(st.loop_b)
        if xb < GUTTER or xa > w:
            return
        xa, xb = max(xa, GUTTER), min(xb, w)
        c = QColor(LOOP)
        c.setAlpha(24)
        p.fillRect(QRectF(xa, BPM_H, xb - xa, h - BPM_H), c)
        p.setPen(QPen(LOOP, 1, Qt.DashLine))
        p.drawLine(QPointF(xa, BPM_H), QPointF(xa, h))
        p.drawLine(QPointF(xb, BPM_H), QPointF(xb, h))
        # 左上角标一下，避免和「区间选择」混淆
        p.setFont(self._small)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(16, 40, 48, 220)))
        p.drawRoundedRect(QRectF(xa + 3, BPM_H + RULER_H + 2, 62, 14), 3, 3)
        p.setPen(QColor("#8fe6f5"))
        p.drawText(QRectF(xa + 3, BPM_H + RULER_H + 2, 62, 14), Qt.AlignCenter,
                   trf("循环 {dur:.2f}s", dur=(st.loop_b - st.loop_a) / 1000.0))

    # -- 时间标尺
    def _draw_ruler(self, p: QPainter, w: int) -> None:
        y = BPM_H
        p.fillRect(QRectF(0, y, w, RULER_H), RULER_BG)
        st = self.doc.state
        span_ms = max(1.0, (w - GUTTER) / max(1e-6, st.px_per_ms))
        steps = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 15000, 30000, 60000, 120000]
        step = steps[-1]
        for s in steps:
            if s * st.px_per_ms >= 62:
                step = s
                break
        rx0, rx1 = self._xrange(w)
        t0, t1 = self.x_to_ms(max(GUTTER, rx0)), self.x_to_ms(rx1)
        p.setFont(self._ruler_font)
        start = math.floor(t0 / step) * step
        t = start
        while t <= t1:
            x = self.ms_to_x(t)
            if x >= GUTTER - 1:
                p.setPen(QColor("#3d454e"))
                p.drawLine(QPointF(x, y + RULER_H - 6), QPointF(x, y + RULER_H))
                p.setPen(RULER_FG)
                p.drawText(QPointF(x + 3, y + 13), fmt_time(max(0.0, t)))
            t += step
        p.setPen(QColor("#0c0e11"))
        p.drawLine(QPointF(0, y + RULER_H), QPointF(w, y + RULER_H))
        _ = span_ms

    # -- 当前时间
    def _draw_playhead(self, p: QPainter, h: int) -> None:
        x = self.ms_to_x(self._pos_ms)
        if x < GUTTER - 6 or x > self.width() + 6:
            return
        p.setPen(QPen(PLAYHEAD, 1.6))
        p.drawLine(QPointF(x, BPM_H), QPointF(x, h))
        path = QPainterPath()
        path.moveTo(x - 6, BPM_H)
        path.lineTo(x + 6, BPM_H)
        path.lineTo(x, BPM_H + 8)
        path.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(PLAYHEAD))
        p.drawPath(path)

    # -- 左侧行标题
    def _draw_gutter(self, p: QPainter, h: int) -> None:
        p.fillRect(QRectF(0, 0, GUTTER, h), QColor("#15171b"))
        p.setPen(QPen(QColor("#2b3037")))
        p.drawLine(QPointF(GUTTER, 0), QPointF(GUTTER, h))
        p.setFont(self._small)
        rows = self.rows()
        rh = self._row_h()
        for i, key in enumerate(rows):
            y = self.row_y(i)
            st = self.doc.project.theme.style(key)
            active = i == self.doc.state.current_lane
            if active:
                p.fillRect(QRectF(0, y, GUTTER - 1, rh), QColor("#2a3038"))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(st.color)))
            p.drawRoundedRect(QRectF(8, y + rh * 0.5 - 6, 12, 12), 2.5, 2.5)
            p.setPen(QColor("#e6edf3") if active else QColor("#9aa4af"))
            p.drawText(QRectF(26, y, GUTTER - 30, rh), Qt.AlignVCenter | Qt.AlignLeft,
                       f"{i + 1} {st.name}")
        # 轨道标签
        if self._video_h() > 0:
            p.fillRect(QRectF(0, self._video_top(), GUTTER, self._video_h()),
                       QColor("#131a21"))
            p.setPen(QColor("#7ec8ff"))
            p.drawText(QRectF(8, self._video_top(), GUTTER - 12, self._video_h()),
                       Qt.AlignVCenter | Qt.AlignLeft, tr("视频轨"))
        if self._audio_h() > 0:
            p.fillRect(QRectF(0, self._audio_top(), GUTTER, self._audio_h()),
                       QColor("#121c19"))
            p.setPen(QColor("#5fe0a0"))
            p.drawText(QRectF(8, self._audio_top(), GUTTER - 12, self._audio_h()),
                       Qt.AlignVCenter | Qt.AlignLeft, tr("音频轨"))
        # BPM 区角标（当前片段/当前时间所在音频片段）
        clip = self.doc.project.clip_by_id(self.doc.state.selected_clip_id)
        if clip is None or clip.kind != "audio":
            clip = self.doc.project.audio_clip_at(self._pos_ms)
        bpm_txt = "—"
        if clip is not None:
            src = clip.timeline_to_source(self._pos_ms)
            bpm_txt = f"{clip.timemap.bpm_at(src):g}"
        p.setPen(QColor("#8b949e"))
        p.drawText(QRectF(8, 0, GUTTER - 12, BPM_H), Qt.AlignVCenter | Qt.AlignLeft,
                   f"BPM  {bpm_txt}")

    # ------------------------------------------------------------ 命中测试
    def _note_at(self, pos: QPoint) -> Note | None:
        if pos.x() < GUTTER or pos.y() < self._rows_top() or pos.y() >= self._rows_bottom():
            return None
        row = self.row_at(pos.y())
        rows = self.rows()
        if not rows:
            return None
        key = rows[row]
        ms = self.x_to_ms(pos.x())
        tol = max(60.0, 10.0 / max(1e-6, self.doc.state.px_per_ms))
        best = None
        for n in self.doc.project.chart.in_range(ms - tol - 2000.0, ms + tol):
            if not self._note_visible(n):
                continue
            if n.type != key and n.lane != row:
                continue
            x, x2 = self._note_x_range(n)
            if n.dur > 0 and x - 4 <= pos.x() <= x2 + 10:
                return n
            if abs(x - pos.x()) <= NOTE_W / 2 + 6:
                return n
            if best is None and abs(self.ms_to_x(n.t) - pos.x()) <= 4:
                best = n
        return best

    def _tail_at(self, pos: QPoint) -> Note | None:
        """命中「长条尾端」把手。

        已有长条：抓尾端附近（±5px）。
        单点音符：抓它右侧外侧那一小条（4~10px），音符本体始终是「拖动移动」。
        """
        n = self._note_at(pos)
        if n is None:
            return None
        x2 = self.ms_to_x(n.end)
        if n.dur > 0:
            return n if abs(pos.x() - x2) <= 5 else None
        dx = pos.x() - x2
        return n if 4.0 <= dx <= 10.0 else None

    def _bpm_at(self, pos: QPoint) -> int:
        if pos.y() > BPM_H:
            return -1
        ms = self.x_to_ms(pos.x())
        clip = self.doc.project.clip_by_id(self.doc.state.selected_clip_id)
        if clip is None or clip.kind != "audio":
            clip = self.doc.project.audio_clip_at(ms)
        if clip is None:
            return -1
        segs = clip.timemap.segments
        for i, s in enumerate(segs):
            if i == 0:
                continue
            if abs(self.ms_to_x(clip.source_to_timeline(s.time_ms)) - pos.x()) <= 5:
                return i
        for i in range(len(segs) - 1, -1, -1):
            if ms >= clip.source_to_timeline(segs[i].time_ms):
                return i
        return 0

    # ------------------------------------------------------------ 交互
    def _start_range(self, pos: QPoint) -> None:
        """开始框选时间区间（Shift 拖动 / 框选区间模式）。"""
        st = self.doc.state
        t = max(0.0, self.snap(self.x_to_ms(pos.x())))
        st.set_range(t, t)
        self._range_anchor = t
        self._press_pos = pos
        self._mode = "range"
        self.statusMessage.emit(tr("框选区间：拖动确定范围（右键可填充/设为循环）"))
        self.update()

    def _start_paint(self, pos: QPoint, row: int) -> None:
        """放置模式：按下即在该网格点放一个音符，拖动可连续刷。"""
        rows = self.rows()
        if not rows:
            return
        key = rows[row]
        ms = max(0.0, self.snap(self.x_to_ms(pos.x())))
        self.doc.begin()
        self._paint_row = row
        self._paint_key = key
        self._paint_last = ms
        self._paint_count = 0
        self._paint_anchor = ms
        self._mode = "paint"
        self._paint_add(ms)
        self.notesChanged.emit()

    def _grid_times(self, t_a: float, t_b: float) -> list[float]:
        """两个时间点之间经过的所有网格点（按各音频片段的吸附网格）。"""
        st = self.doc.state
        step = 1.0 / max(1.0, st.snap_divisor or 1.0)
        if not self.doc.project.audio_clips:
            tm = self.doc.project.chart.timemap
            b0, b1 = tm.beat(min(t_a, t_b)), tm.beat(max(t_a, t_b))
            k0 = int(math.floor(b0 / step + 1e-6))
            k1 = int(math.ceil(b1 / step - 1e-6))
            return [tm.ms(k * step) for k in range(k0, k1 + 1)]
        out: list[float] = []
        for clip in self.doc.project.audio_clips:
            lo = max(min(t_a, t_b), clip.offset_ms)
            hi = min(max(t_a, t_b), clip.end_ms())
            if hi <= lo:
                continue
            tm = clip.timemap
            b0 = tm.beat(clip.timeline_to_source(lo))
            b1 = tm.beat(clip.timeline_to_source(hi))
            k0 = int(math.floor(b0 / step + 1e-6))
            k1 = int(math.ceil(b1 / step - 1e-6))
            if k1 - k0 > 2000:
                k1 = k0 + 2000
            out.extend(clip.source_to_timeline(tm.ms(k * step))
                       for k in range(k0, k1 + 1))
        return sorted(out)

    def _paint_add(self, ms: float) -> None:
        chart = self.doc.project.chart
        clip = self.doc.project.audio_clip_at(ms)
        if clip is None and self.doc.project.audio_clips:
            return
        tol = max(6.0, 4.0 / max(1e-6, self.doc.state.px_per_ms))
        if self._paint_count > 2000:
            return
        if chart.find_at(ms, self._paint_row, tol) is not None:
            return
        chart.add(Note(max(0.0, ms), self._paint_key, self._paint_row,
                       clip_id=clip.id if clip else ""))
        self._paint_count += 1

    def _select_clip(self, clip) -> None:
        st = self.doc.state
        cid = clip.id if clip is not None else ""
        if st.selected_clip_id == cid:
            self.update()
            return
        st.selected_clip_id = cid
        self.doc.clear_selection()
        self.clipSelected.emit(cid)
        self._bg = None
        self.update()

    def _begin_clip_drag(self, clip, pos: QPoint) -> None:
        self._select_clip(clip)
        self.clipMoveStarted.emit()
        group = self.doc.project.linked_clips(clip)
        if not group:
            group = [clip]
        self.doc.begin(include_clips=True)
        self._mode = "clip"
        self._press_pos = pos
        self._press_ms = self.x_to_ms(pos.x())
        self._clip_drag = {
            "clip": clip,
            "group": group,
            "orig": {c.id: float(c.offset_ms) for c in group},
            "notes": {c.id: [(n, float(n.t)) for n in self.doc.project.notes_of_clip(c.id)]
                      for c in group if c.kind == "audio"},
            "last": 0.0,
        }
        self._snap_guide = None
        self.update()

    def _clips_overlap(self, group) -> bool:
        gids = {c.id for c in group}
        for a in group:
            for b in self.doc.project.clips:
                if b.id in gids or b.kind != a.kind:
                    continue
                if (a.offset_ms < b.end_ms() - 1e-6
                        and a.end_ms() > b.offset_ms + 1e-6):
                    return True
        return False

    def _clip_snap(self, raw_delta: float, group) -> tuple[float, float | None]:
        st = self.doc.state
        if not st.snap_on:
            return raw_delta, None
        threshold = CLIP_SNAP_PX / max(1e-6, st.px_per_ms)
        gids = {c.id for c in group}
        targets = [0.0, self._pos_ms]
        for c in self.doc.project.clips:
            if c.id in gids:
                continue
            targets.extend([c.offset_ms, c.end_ms()])
        best_d = raw_delta
        best_gap = threshold + 1.0
        best_target = None
        for c in group:
            orig = self._clip_drag["orig"][c.id]
            for edge in (orig, orig + c.duration_ms):
                for tgt in targets:
                    d = tgt - edge
                    gap = abs(d - raw_delta)
                    if gap < best_gap:
                        best_gap = gap
                        best_d = d
                        best_target = tgt
        return best_d, best_target

    def _move_clip_drag(self, pos: QPoint, modifiers) -> None:
        d = self._clip_drag
        if not d:
            return
        raw = self.x_to_ms(pos.x()) - self._press_ms
        group = d["group"]
        min_orig = min(d["orig"].values())
        raw = max(-min_orig, raw)
        if modifiers & Qt.AltModifier:
            delta, guide = raw, None
        else:
            delta, guide = self._clip_snap(raw, group)
            delta = max(-min_orig, delta)
        # 先试探位置，音频/视频轨都不允许重叠
        for c in group:
            c.offset_ms = d["orig"][c.id] + delta
        if self._clips_overlap(group):
            for c in group:
                c.offset_ms = d["orig"][c.id] + d["last"]
            self.statusMessage.emit(tr("片段重叠：换个位置（按住 Alt 可临时关吸附）"))
            self._snap_guide = None
            self.update()
            return
        shift = delta - d["last"]
        if abs(shift) > 1e-9:
            for c in group:
                if c.kind != "audio":
                    continue
                for n, _orig_t in d["notes"].get(c.id, []):
                    n.t = max(0.0, n.t + shift)
            d["last"] = delta
            self.doc.project.chart.sort()
            self.notesChanged.emit()
            self._bg = None
            self.update()
        self._snap_guide = guide
        self.statusMessage.emit(
            trf("移动片段：{a} → {b}", a=fmt_time(d["orig"][d["clip"].id]),
                b=fmt_time(d["clip"].offset_ms)))

    def _finish_clip_drag(self) -> None:
        d = self._clip_drag
        self._clip_drag = None
        self._mode = ""
        self._snap_guide = None
        if not d:
            return
        if abs(d["last"]) > 1e-9:
            self.doc.commit("移动片段")
            self.clipMoved.emit()
        else:
            self.doc.cancel()
        self.update()

    def _draw_clip_snap_guide(self, p: QPainter, h: int) -> None:
        if self._snap_guide is None:
            return
        x = self.ms_to_x(self._snap_guide)
        if x < GUTTER or x > self.width():
            return
        p.setPen(QPen(QColor("#ffd166"), 1, Qt.DashLine))
        p.drawLine(QPointF(x, BPM_H + RULER_H), QPointF(x, self._media_bottom()))

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        pos = ev.position().toPoint()
        st = self.doc.state
        self.setFocus(Qt.MouseFocusReason)
        if ev.button() == Qt.MiddleButton:
            self._mode = "pan"
            self._press_pos = pos
            self._press_ms = st.view_t0
            self.setCursor(Qt.ClosedHandCursor)
            return
        if ev.button() == Qt.RightButton:
            self._open_context_menu(pos)
            return
        clip = self._clip_at_pos(pos)
        if ev.button() == Qt.LeftButton and clip is not None:
            if st.tool == "range" or (ev.modifiers() & Qt.ShiftModifier):
                self._select_clip(clip)
                self._start_range(pos)
            else:
                self._begin_clip_drag(clip, pos)
            return
        if pos.y() < BPM_H:
            ms = max(0.0, self.x_to_ms(pos.x()))
            c = self.doc.project.audio_clip_at(ms)
            if c is not None:
                self._select_clip(c)
            self.seekRequested.emit(ms)
            return
        if pos.y() < BPM_H + RULER_H or pos.y() >= self._rows_bottom():
            # 标尺 / 空档 / 波形 / 频谱：移动当前时间或框选；点空档取消片段选择
            if ev.button() == Qt.LeftButton and pos.y() >= self._rows_bottom():
                self._select_clip(None)
            if st.tool == "range" or (ev.modifiers() & Qt.ShiftModifier):
                self._start_range(pos)
            else:
                self._mode = "scrub"
                self.seekRequested.emit(max(0.0, self.x_to_ms(pos.x())))
            return

        row = self.row_at(pos.y())
        st.current_lane = row
        self.rowChanged.emit(row)

        tail = self._tail_at(pos)
        if tail is not None:
            self._mode = "tail"
            self.doc.begin()
            self._anchor = tail
            self.doc.set_selection([tail])
            return

        note = self._note_at(pos)
        if note is not None:
            owner = self._note_owner(note)
            if owner is not None and owner.id != st.selected_clip_id:
                st.selected_clip_id = owner.id
                self.clipSelected.emit(owner.id)
                self._bg = None
        if note is None:
            if st.tool == "draw":
                self._start_paint(pos, row)
            elif st.tool == "range":
                self._start_range(pos)
            else:
                # 选择模式：空白处直接拖出框选
                self._mode = "rubber"
                self._press_pos = pos
                self._rubber = QRect(pos, pos)
            self.update()
            return

        self._mode = "drag"
        self._anchor = note
        self._press_pos = pos
        self._press_ms = self.x_to_ms(pos.x())
        if ev.modifiers() & Qt.ControlModifier:
            if note in self.doc.selection:
                self.doc.selection.discard(note)
                self.doc.selectionChanged.emit()
            else:
                self.doc.add_to_selection([note])
        elif note not in self.doc.selection:
            self.doc.set_selection([note])
        self._drag_orig = {id(n): (n.t, n.lane, n.dur) for n in self.doc.selection}
        self.doc.begin()
        self.update()

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        pos = ev.position().toPoint()
        self._mouse_ms = max(0.0, self.x_to_ms(pos.x()))
        self._mouse_row = (self.row_at(pos.y())
                           if self._rows_top() <= pos.y() < self._rows_bottom() else -1)
        st = self.doc.state
        if self._mode == "clip" and self._clip_drag:
            self._move_clip_drag(pos, ev.modifiers())
            return
        if self._mode == "pan":
            st.view_t0 = self._press_ms - (pos.x() - self._press_pos.x()) / max(1e-6, st.px_per_ms)
            self.update()
            return
        if self._mode == "scrub":
            self.seekRequested.emit(max(0.0, self.x_to_ms(pos.x())))
            return
        if self._mode == "range":
            st.set_range(self._range_anchor, max(0.0, self.snap(self.x_to_ms(pos.x()))))
            self.statusMessage.emit(
                trf("区间 {a} → {b}（{dur:.3f}s）",
                    a=fmt_time(st.sel_t0), b=fmt_time(st.sel_t1),
                    dur=(st.sel_t1 - st.sel_t0) / 1000.0))
            self.update()
            return
        if self._mode == "paint":
            ms = max(0.0, self.snap(self.x_to_ms(pos.x())))
            for t in self._grid_times(self._paint_last, ms):
                self._paint_add(t)
            self._paint_last = ms
            self.statusMessage.emit(trf("放置音符：已放 {n} 个", n=self._paint_count))
            self.doc.project.chart.sort()
            self.notesChanged.emit()
            self.update()
            return
        if self._mode == "bpm":
            # 旧版直接拖顶部 BPM 段；现在 BPM 按片段独立，统一在右侧表格编辑
            return
        if self._mode == "tail" and self._anchor is not None:
            ms = max(self._anchor.t, self.snap(max(0.0, self.x_to_ms(pos.x()))))
            self._anchor.dur = ms - self._anchor.t
            self.notesChanged.emit()
            self.update()
            return
        if self._mode == "rubber" and self._rubber is not None:
            self._rubber = QRect(self._press_pos, pos).normalized()
            self.update()
            return
        if self._mode == "drag" and self._anchor is not None:
            delta = self.x_to_ms(pos.x()) - self._press_ms
            anchor_orig = self._drag_orig.get(id(self._anchor))
            if anchor_orig is None:
                return
            new_t = self.snap(max(0.0, anchor_orig[0] + delta))
            d_ms = new_t - anchor_orig[0]
            d_row = self.row_at(pos.y()) - self.row_at(self._press_pos.y()) if len(self.rows()) > 1 else 0
            for n in self.doc.selection:
                o = self._drag_orig.get(id(n))
                if o is None:
                    continue
                n.t = max(0.0, o[0] + d_ms)
                n.lane = max(0, min(len(self.rows()) - 1, o[1] + d_row))
                n.type = self.rows()[n.lane]
            self.statusMessage.emit(f"{fmt_time(self._anchor.t)}   Δ{d_ms:+.0f}ms")
            self.doc.project.chart.sort()
            self.notesChanged.emit()
            self.update()
            return

        # 悬停
        n = self._note_at(pos)
        bpm = self._bpm_at(pos) if pos.y() <= BPM_H else -1
        if n is not self._hover or bpm != self._hover_bpm:
            self._hover = n
            self._hover_bpm = bpm
            self.update()
        if self._tail_at(pos) is not None:
            self.setCursor(Qt.SizeHorCursor)
        elif n is not None:
            self.setCursor(Qt.PointingHandCursor)
        elif self.doc.state.tool == "draw" and pos.y() >= self._rows_top() and pos.y() < self._rows_bottom():
            self.setCursor(Qt.CrossCursor)       # 放置模式：十字光标
        elif self.doc.state.tool == "range":
            self.setCursor(Qt.SizeHorCursor)     # 框选区间模式
        else:
            self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        mode = self._mode
        self._mode = ""
        self.setCursor(Qt.ArrowCursor)
        if mode == "clip":
            self._finish_clip_drag()
            return
        if mode == "rubber" and self._rubber is not None:
            r = self._rubber
            picked = []
            for n in self.doc.project.chart.in_range(self.x_to_ms(r.left()), self.x_to_ms(r.right())):
                if not self._note_visible(n):
                    continue
                y = self.row_y(n.lane) + self._row_h() * 0.5
                if r.top() - 8 <= y <= r.bottom() + 8:
                    picked.append(n)
            if ev.modifiers() & Qt.ShiftModifier:
                self.doc.add_to_selection(picked)
            else:
                self.doc.set_selection(picked)
            self._rubber = None
            self.update()
            return
        if mode == "range":
            st = self.doc.state
            if not st.has_range():
                # 没拖出长度：当作单击（定位当前时间 / 清除区间）
                t = st.sel_t0
                st.clear_range()
                self.seekRequested.emit(max(0.0, t))
                self.statusMessage.emit(trf("定位到 {t}", t=fmt_time(t)))
            else:
                self.statusMessage.emit(trf(
                    "已框选区间 {a} → {b}　右键可「在区间填充音符」",
                    a=fmt_time(st.sel_t0), b=fmt_time(st.sel_t1)))
            self.update()
            return
        if mode == "paint":
            self.doc.commit("放置音符")
            self.statusMessage.emit(trf("已放置 {n} 个音符", n=self._paint_count))
            self._paint_count = 0
            self.notesChanged.emit()
            self.update()
            return
        if mode in ("drag", "tail", "bpm"):
            self.doc.commit("移动音符" if mode == "drag" else ("调整长条" if mode == "tail" else "调整 BPM 段"))
            self._anchor = None
            self.notesChanged.emit()
            self.update()

    def mouseDoubleClickEvent(self, ev) -> None:  # noqa: N802
        pos = ev.position().toPoint()
        if pos.y() <= BPM_H:
            idx = self._bpm_at(pos)
            if idx >= 0:
                self.bpmEditRequested.emit(idx)
            return
        n = self._note_at(pos)
        if n is not None:
            self.doc.set_selection([n])
            self._open_context_menu(pos)

    def wheelEvent(self, ev) -> None:  # noqa: N802
        delta = ev.angleDelta().y()
        mods = ev.modifiers()
        if mods & Qt.ControlModifier or mods & Qt.AltModifier:
            self.zoom_at(ev.position().x(), 1.18 if delta > 0 else 1 / 1.18)
        elif mods & Qt.ShiftModifier:
            self.scroll_by(-delta / 120.0 * 120.0 / max(1e-6, self.doc.state.px_per_ms))
        else:
            self.scroll_by(-delta / 120.0 * 60.0 / max(1e-6, self.doc.state.px_per_ms))
        ev.accept()

    # ------------------------------------------------------------ 右键菜单
    def _range_to_loop(self) -> None:
        st = self.doc.state
        if not st.has_range():
            return
        st.loop_a, st.loop_b = st.sel_t0, st.sel_t1
        st.loop_on = True
        st.loop_armed = False
        st.clear_range()          # 区间已经变成循环区间，别再叠一层高亮
        self.statusMessage.emit(trf(
            "循环区间：{a} → {b}（播放菜单或循环勾选框可取消）",
            a=fmt_time(st.loop_a), b=fmt_time(st.loop_b)))
        self.loopChanged.emit()
        self.update()

    def _clear_loop(self) -> None:
        st = self.doc.state
        st.loop_on = False
        st.loop_armed = False
        st.loop_a = st.loop_b = 0.0
        self.statusMessage.emit(tr("已清除循环区间"))
        self.loopChanged.emit()
        self.update()

    def _clear_range(self) -> None:
        self.doc.state.clear_range()
        self.update()

    def _open_context_menu(self, pos: QPoint) -> None:
        chart = self.doc.project.chart
        st = self.doc.state
        menu = QMenu(self)
        clip_hit = self._clip_at_pos(pos)
        if clip_hit is not None:
            self._select_clip(clip_hit)
            menu.addAction(tr("在当前时间切分片段"), self.clipSplitRequested.emit)
            menu.addAction(tr("删除片段"), self.clipDeleteRequested.emit)
            menu.exec(self.mapToGlobal(pos))
            return
        has_clip = bool(getattr(self, "_clipboard_has", False))
        if st.has_range():
            menu.addAction(tr("在这个区间填充音符…"),
                           lambda: self.fillRequested.emit(st.sel_t0, st.sel_t1))
            menu.addAction(tr("把区间设为循环区间"), self._range_to_loop)
            menu.addAction(tr("清除区间选择"), self._clear_range)
            menu.addSeparator()
        if st.loop_on and st.loop_b > st.loop_a:
            menu.addAction(tr("清除循环区间"), self._clear_loop)
            menu.addSeparator()
        if pos.y() <= BPM_H:
            ms = self.snap(max(0.0, self.x_to_ms(pos.x())))
            clip = self.doc.project.audio_clip_at(ms)
            if clip is not None:
                self._select_clip(clip)
            if clip is not None:
                menu.addAction(tr("在此处添加 BPM 段…"),
                               lambda: self.bpmAddRequested.emit(ms))
                idx = self._bpm_at(pos)
                if 0 < idx < len(clip.timemap.segments):
                    menu.addAction(tr("编辑此 BPM 段…"),
                                   lambda: self.bpmEditRequested.emit(idx))
                    menu.addAction(tr("删除此 BPM 段"),
                                   lambda: self._remove_segment(idx))
            menu.exec(self.mapToGlobal(pos))
            return

        note = self._note_at(pos)
        if note is not None and note not in self.doc.selection:
            self.doc.set_selection([note])
        sel = list(self.doc.selection)
        if sel:
            type_menu = menu.addMenu(tr("改成类型"))
            for i, key in enumerate(self.rows()):
                sty = self.doc.project.theme.style(key)
                type_menu.addAction(f"{i + 1}. {sty.name}", lambda k=key: self._set_type(k))
            menu.addSeparator()
            menu.addAction(tr("量化到网格"), lambda: self._quantize())
            menu.addAction(tr("复制 (Ctrl+C)"), self._copy)
            menu.addAction(tr("粘贴到这里 (Ctrl+V)"),
                           lambda: self._paste_at(pos))
            menu.addAction(tr("向后复制一份 (Ctrl+D)"), self.duplicateRequested.emit)
            menu.addAction(tr("删除 (Del)"), self._delete_selected)
            menu.addSeparator()
            menu.addAction(tr("长条延长一小节"), lambda: self._extend_hold(1))
            menu.addAction(tr("长条缩短一小节"), lambda: self._extend_hold(-1))
        else:
            ms = self.snap(max(0.0, self.x_to_ms(pos.x())))
            row = self.row_at(pos.y())
            if has_clip:
                menu.addAction(tr("粘贴到这里 (Ctrl+V)"), lambda: self._paste_at(pos))
                menu.addSeparator()
            menu.addAction(tr("在此添加音符"), lambda: self._add_at(ms, row))
            if not st.has_range():
                menu.addAction(tr("从这一点开始填充音符…"),
                               lambda: self.fillRequested.emit(ms, -1.0))
            menu.addSeparator()
            menu.addAction(tr("全选"), self.selectAllRequested.emit)
            menu.addSeparator()
            menu.addAction(tr("把循环起点设到这里"), lambda: self._set_loop("a", ms))
            menu.addAction(tr("把循环终点设到这里"), lambda: self._set_loop("b", ms))
        menu.exec(self.mapToGlobal(pos))

    # -- 菜单动作
    def _paste_at(self, pos: QPoint) -> None:
        """粘贴到右键点的地方（时间 + 行，整块跟着挪到那一行）。"""
        ms = max(0.0, self.x_to_ms(pos.x()))
        row = self.row_at(pos.y()) if self._rows_top() <= pos.y() < self._rows_bottom() else -1
        self.pasteRequested.emit(ms, row)

    def set_clipboard_state(self, has_clip: bool) -> None:
        """由主窗口告知剪贴板有没有内容（右键菜单据此决定是否显示"粘贴"）。"""
        self._clipboard_has = bool(has_clip)
    def _set_type(self, key: str) -> None:
        rows = self.rows()
        lane = rows.index(key) if key in rows else 0

        def fn():
            for n in self.doc.selection:
                n.type = key
                n.lane = lane

        self.doc.edit("修改类型", fn)
        self.notesChanged.emit()
        self.update()

    def _quantize(self) -> None:
        def fn():
            for n in self.doc.selection:
                n.t = self.snap(n.t)

        self.doc.edit("量化", fn)
        self.notesChanged.emit()
        self.update()

    def _copy(self) -> None:
        self.copyRequested.emit()

    def _delete_selected(self) -> None:
        self.delete_selected()

    def delete_selected(self) -> None:
        if not self.doc.selection:
            return
        sel = list(self.doc.selection)
        self.doc.edit("删除音符", lambda: self.doc.project.chart.remove_many(sel))
        self.doc.clear_selection()
        self.notesChanged.emit()
        self.update()

    def _extend_hold(self, bars: float) -> None:
        sel = list(self.doc.selection)
        if not sel:
            return
        n0 = sel[0]
        clip = self.doc.project.clip_by_id(n0.clip_id) or self.doc.project.audio_clip_at(n0.t)
        bpm = 120.0
        if clip is not None:
            src = clip.timeline_to_source(self._pos_ms)
            bpm = clip.timemap.bpm_at(src)
        elif not self.doc.project.audio_clips:
            bpm = self.doc.project.chart.timemap.bpm_at(self._pos_ms)
        delta = (60000.0 / bpm) * 4.0 * bars

        def fn():
            for n in self.doc.selection:
                n.dur = max(0.0, n.dur + delta)

        self.doc.edit("调整长条", fn)
        self.notesChanged.emit()
        self.update()

    def _add_at(self, ms: float, row: int) -> None:
        clip = self.doc.project.audio_clip_at(ms)
        if clip is None and self.doc.project.audio_clips:
            self.statusMessage.emit(tr("空档不能添加音符"))
            return
        key = self.rows()[row]
        nn = Note(ms, key, row, clip_id=clip.id if clip else "")
        self.doc.edit("添加音符", lambda: self.doc.project.chart.add(nn))
        self.doc.set_selection([nn])
        self.notesChanged.emit()
        self.update()

    def _set_loop(self, which: str, ms: float) -> None:
        st = self.doc.state
        st.loop_armed = False
        if which == "a":
            st.loop_a = ms
        else:
            st.loop_b = ms
        if st.loop_b < st.loop_a:
            st.loop_a, st.loop_b = st.loop_b, st.loop_a
        st.loop_on = st.loop_b > st.loop_a
        self.update()

    def _remove_segment(self, idx: int) -> None:
        clip = self.doc.project.clip_by_id(self.doc.state.selected_clip_id)
        if clip is None or clip.kind != "audio":
            clip = self.doc.project.audio_clip_at(self._pos_ms)
        if clip is None or idx <= 0 or idx >= len(clip.timemap.segments):
            return
        before = self.doc.snapshot(include_clips=True)
        segs = list(clip.timemap.segments)
        segs.pop(idx)
        clip.timemap.set_segments(segs, clip.timemap.offset_ms)
        self.doc.commit_external("删除 BPM 段", before)
        self.notesChanged.emit()
        self.update()
