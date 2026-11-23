"""节奏条渲染器：预览与导出共用同一份绘制代码，保证所见即所得。

坐标系：一律使用「逻辑像素」= 导出尺寸（默认 1920×120），
所有尺寸都相对条高 H 计算，所以放大到 4K 或缩小成小预览条都不会跑形。
"""

from __future__ import annotations

import math
import os

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)

from .model import Note, Project, RenderSettings
from .theme import GhostStyle, JudgeStyle, NoteStyle, PlateStyle, Theme


# --------------------------------------------------------------------- 小工具
def qc(value, alpha: float | None = None) -> QColor:
    """字符串颜色带缓存。

    注意：缓存里必须存「原色」——曾经把改过 alpha 的对象存回缓存，
    结果同一颜色后续再取时带着 0 alpha，画面上就是「只有描边的空心图形」
    （判定菱形空心就是这么来的）。所以每次都拷贝一份再改 alpha。
    """
    if isinstance(value, str):
        base = _COLOR_CACHE.get(value)
        if base is None:
            base = QColor(value)
            _COLOR_CACHE[value] = base
    elif isinstance(value, QColor):
        base = value
    else:
        base = QColor(value)
    c = QColor(base)
    if alpha is not None:
        c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


_COLOR_CACHE: dict[str, QColor] = {}


def mix(a: QColor, b: QColor, k: float) -> QColor:
    k = max(0.0, min(1.0, k))
    return QColor(
        int(a.red() + (b.red() - a.red()) * k),
        int(a.green() + (b.green() - a.green()) * k),
        int(a.blue() + (b.blue() - a.blue()) * k),
        int(a.alpha() + (b.alpha() - a.alpha()) * k),
    )


WHITE = QColor(255, 255, 255)
BLACK = QColor(0, 0, 0)

SHAPE_SQUEEZE = {
    "diamond": 1.0, "square": 0.84, "circle": 0.92, "bar": 0.42,
    "triangle": 0.98, "star": 1.0, "cross": 0.96, "hexagon": 0.95,
}


def shape_path(cx: float, cy: float, r: float, shape: str) -> QPainterPath:
    """以 (cx,cy) 为中心、半径 r 的外形路径。"""
    p = QPainterPath()
    if shape == "square":
        s = r * SHAPE_SQUEEZE["square"]
        p.addRect(QRectF(cx - s, cy - s, s * 2, s * 2))
    elif shape == "circle":
        p.addEllipse(QPointF(cx, cy), r * SHAPE_SQUEEZE["circle"], r * SHAPE_SQUEEZE["circle"])
    elif shape == "bar":
        # 横向长条：宽 ≈ 2r，高 ≈ 0.84r（原来的实现画成了圆角小方块）
        bw = r * 2.0
        bh = r * SHAPE_SQUEEZE["bar"] * 2.0
        p.addRoundedRect(QRectF(cx - bw * 0.5, cy - bh * 0.5, bw, bh), bh * 0.5, bh * 0.5)
    elif shape == "triangle":
        p.addPolygon(QPolygonF([
            QPointF(cx, cy - r * 1.05),
            QPointF(cx + r * 1.00, cy + r * 0.82),
            QPointF(cx - r * 1.00, cy + r * 0.82),
        ]))
        p.closeSubpath()
    elif shape == "star":
        pts = []
        for i in range(10):
            rr = r if i % 2 == 0 else r * 0.44
            a = -math.pi / 2.0 + i * math.pi / 5.0
            pts.append(QPointF(cx + math.cos(a) * rr, cy + math.sin(a) * rr))
        p.addPolygon(QPolygonF(pts))
        p.closeSubpath()
    elif shape == "cross":
        arm, ext = r * 0.34, r
        p.addPolygon(QPolygonF([
            QPointF(cx - arm, cy - ext), QPointF(cx + arm, cy - ext),
            QPointF(cx + arm, cy - arm), QPointF(cx + ext, cy - arm),
            QPointF(cx + ext, cy + arm), QPointF(cx + arm, cy + arm),
            QPointF(cx + arm, cy + ext), QPointF(cx - arm, cy + ext),
            QPointF(cx - arm, cy + arm), QPointF(cx - ext, cy + arm),
            QPointF(cx - ext, cy - arm), QPointF(cx - arm, cy - arm),
        ]))
        p.closeSubpath()
    elif shape == "hexagon":
        pts = []
        for i in range(6):
            a = math.pi / 6.0 + i * math.pi / 3.0
            pts.append(QPointF(cx + math.cos(a) * r, cy + math.sin(a) * r))
        p.addPolygon(QPolygonF(pts))
        p.closeSubpath()
    else:  # diamond
        poly = QPolygonF(
            [QPointF(cx, cy - r), QPointF(cx + r, cy), QPointF(cx, cy + r), QPointF(cx - r, cy)]
        )
        p.addPolygon(poly)
        p.closeSubpath()
    return p


def shape_radius(r: float, shape: str) -> float:
    return r * SHAPE_SQUEEZE.get(shape, 1.0)


# ---------------------------------------------------------------------- 渲染器
class BarRenderer:
    def __init__(self, project: Project):
        self.p = project
        self._plate_key: tuple | None = None
        self._plate_pm = None
        self._style_cache: dict[str, tuple] = {}
        self._style_sig: tuple | None = None
        self._image_cache: dict[tuple[str, float], QPixmap | None] = {}

    def _pixmap(self, path: str) -> QPixmap | None:
        """加载并缓存用户皮肤图；文件缺失时返回 None，调用方回退到形状。"""
        if not path:
            return None
        try:
            ap = os.path.abspath(path)
            mtime = os.path.getmtime(ap)
        except OSError:
            return None
        key = (ap, mtime)
        if key in self._image_cache:
            return self._image_cache[key]
        pm = QPixmap(ap)
        if pm.isNull():
            pm = None
        self._image_cache[key] = pm
        return pm

    def _image_rect(self, x: float, y: float, box: float, pm: QPixmap) -> QRectF:
        """在 box×box 的框内按原始比例居中放置图片。"""
        iw, ih = max(1, pm.width()), max(1, pm.height())
        scale = min(box / iw, box / ih)
        w, h = iw * scale, ih * scale
        return QRectF(x - w / 2.0, y - h / 2.0, w, h)

    # ------------------------------------------------------------- 几何计算
    @property
    def rs(self) -> RenderSettings:
        return self.p.render

    @property
    def th(self) -> Theme:
        return self.p.theme

    def judge_x(self, w: float) -> float:
        return w * max(0.05, min(0.95, self.rs.judge_ratio))

    def speed(self, w: float) -> float:
        """像素 / 秒。音符从远端边缘走到判定线正好花 approach_s 秒。"""
        jx = self.judge_x(w)
        span = max(jx, w - jx)
        return span / max(0.05, float(self.rs.approach_s))

    def side_of(self, n: Note) -> int:
        if self.rs.flow == "ltr":
            return -1
        if self.rs.flow == "rtl":
            return 1
        if n.side:
            return 1 if n.side > 0 else -1
        rows = max(1, len(self.th.active_rows()))
        return -1 if (n.lane % max(2, rows)) % 2 == 1 else 1

    def lane_y(self, lane: int, h: float) -> float:
        rows = self.th.active_rows()
        n = len(rows)
        spread = max(0.0, min(0.85, float(self.rs.vspread)))
        if n <= 1 or spread <= 0:
            return h * 0.5
        idx = max(0, min(n - 1, int(lane)))
        step = (h * spread) / (n - 1)
        return h * 0.5 + (idx - (n - 1) / 2.0) * step

    def note_x(self, t_note: float, t_now: float, w: float, side: int = 1,
               post: bool = True) -> float:
        """音符的横向位置。

        过了判定点（t_note < t_now）的按 post_speed 倍率加速离场；
        参考视频里命中后是「嗖」地飞走（实测约 3.8 倍），1.0 则与旧行为一致。
        post=False 用于刻度 / 网格这类「时间尺」：它们保持匀速，不跟着音符飞。
        """
        dt = float(t_note) - float(t_now)
        k = float(self.rs.post_speed) if (post and dt < 0.0) else 1.0
        k = max(0.05, min(20.0, k))
        return self.judge_x(w) + side * dt / 1000.0 * self.speed(w) * k

    def content_h(self, h: float) -> float:
        """内容（音符 / 文字）的尺寸基准高度。

        「条带尺寸」只改画布，不该把 Beat 和字体一起放大，所以内容按
        `120 × content_scale` 的基准画；导出放大到更大的画布时再按比例跟随。
        """
        rs = self.rs
        scale = max(0.2, min(4.0, float(getattr(rs, "content_scale", 1.0))))
        nominal = max(1.0, float(getattr(rs, "height", 120) or 120))
        return float(h) * (120.0 * scale / nominal)

    def note_radius(self, h: float, st: NoteStyle, scale: float = 1.0) -> float:
        return 0.5 * float(self.rs.note_size) * self.content_h(h) * float(st.size) * scale

    # --------------------------------------------------------------- 主入口
    def render(self, p: QPainter, w: float, h: float, t_ms: float, *,
               solid_bg: QColor | None = None, clear: bool = True) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        rect = QRectF(0, 0, w, h)
        if solid_bg is not None:
            p.fillRect(rect, solid_bg)
        elif clear:
            # Source 模式的透明填充 = 快速清零，比 SourceOver 混合便宜得多
            p.setCompositionMode(QPainter.CompositionMode_Source)
            p.fillRect(rect, Qt.transparent)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        if self.th.plate.mode == "plate":
            self._draw_plate(p, w, h)
        self._draw_axis(p, w, h)
        if self.rs.ticks != "off":
            self._draw_ticks(p, w, h, t_ms)
        # 哑铃样式要压在音符上面（胶囊最顶层）；菱形式保持原来的层级。
        dumbbell = str(getattr(self.th.judge, "mode", "diamond")) == "dumbbell"
        if not dumbbell:
            self._draw_judge(p, w, h, t_ms)
        self._draw_notes(p, w, h, t_ms)
        if dumbbell:
            self._draw_judge(p, w, h, t_ms)
        self._draw_overlays(p, w, h, t_ms)

    # ------------------------------------------------------------ 横向中轴线
    def _draw_axis(self, p: QPainter, w: float, h: float) -> None:
        """基础设置里的横向中轴线：画在正中、位于最底层，两种判定区样式都生效。"""
        ax = self.th.axis
        if not getattr(ax, "enabled", False):
            return
        y = h * 0.5
        lw = max(0.0, float(getattr(ax, "width", 0.012)) * h)
        ow = max(0.0, float(getattr(ax, "outline_w", 0.0)) * h)
        if ow > 0.05:
            # 描边画成「线宽 + 两侧各 ow」，否则会被主线完全盖住
            pen = QPen(qc(str(getattr(ax, "outline_color", "#141a20")),
                          max(0.0, min(1.0, float(getattr(ax, "outline_alpha", 0.6))))),
                       lw + ow * 2.0)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawLine(QPointF(0.0, y), QPointF(w, y))
        if lw <= 0.05:
            return
        pen = QPen(qc(str(getattr(ax, "color", "#ffffff")),
                      max(0.0, min(1.0, float(getattr(ax, "alpha", 0.2))))), lw)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(0.0, y), QPointF(w, y))

    # ------------------------------------------------------- 水印 / Beat 计数
    def _text_rect(self, pos: str, w: float, h: float, size: float) -> tuple[QRectF, int]:
        """按角落名算出文字排版区域与对齐标志。"""
        margin = max(1.0, h * 0.08)
        band = max(2.0, size * 1.5)
        left = str(pos).endswith("left")
        top = str(pos).endswith("top")
        y = margin if top else h - margin - band
        rect = QRectF(margin, y, max(1.0, w - 2 * margin), band)
        flags = int(Qt.AlignLeft if left else Qt.AlignRight) | int(Qt.AlignVCenter)
        return rect, flags

    def _corner_font(self, size: float, *, bold: bool) -> QFont:
        f = QFont()
        f.setPixelSize(max(1, int(round(size))))
        f.setBold(bold)
        return f

    def _draw_overlays(self, p: QPainter, w: float, h: float, t_ms: float) -> None:
        rs = self.rs
        text = str(getattr(rs, "watermark_text", "") or "")
        if text:
            size = max(1.0, float(getattr(rs, "watermark_size", 0.14)) * self.content_h(h))
            rect, flags = self._text_rect(str(getattr(rs, "watermark_pos", "bottom_left")),
                                          w, h, size)
            p.setFont(self._corner_font(size, bold=False))
            p.setPen(qc(str(getattr(rs, "watermark_color", "#ffffff")),
                        max(0.0, min(1.0, float(getattr(rs, "watermark_alpha", 0.55))))))
            p.drawText(rect, flags, text)
        if getattr(rs, "beat_counter", False):
            n = self.beat_count(t_ms)
            label = str(getattr(rs, "beat_counter_label", "BEAT") or "").strip()
            body = f"{label} {n}" if label else str(n)
            size = max(1.0, float(getattr(rs, "beat_counter_size", 0.14)) * self.content_h(h))
            rect, flags = self._text_rect(str(getattr(rs, "beat_counter_pos", "bottom_right")),
                                          w, h, size)
            p.setFont(self._corner_font(size, bold=True))
            p.setPen(qc(str(getattr(rs, "beat_counter_color", "#ffffff") or "#ffffff"),
                        max(0.0, min(1.0, float(getattr(rs, "beat_counter_alpha", 0.92))))))
            p.drawText(rect, flags, body)

    def beat_count(self, t_ms: float) -> int:
        """角落实时计数：notes = 已命中音符数；beats = 按 BPM 的拍号（从 0 起）。"""
        rs = self.rs
        if str(getattr(rs, "beat_counter_mode", "notes")) == "beats":
            clip = self.p.audio_clip_at(t_ms)
            if clip is not None:
                b = clip.timemap.beat(clip.timeline_to_source(t_ms))
            else:
                b = self.p.chart.timemap.beat(t_ms)
            return max(0, int(math.floor(b)))
        return self.p.chart.count_before(t_ms)

    # ----------------------------------------------------------------- 底板
    def _plate_pixmap(self, w: float, h: float, dpr: float):
        """底板与时间无关 —— 缓存成位图，每帧只做一次贴图（导出也快很多）。"""
        pl: PlateStyle = self.th.plate
        key = (
            round(w, 3), round(h, 3), round(dpr, 3), pl.mode, pl.color, round(pl.alpha, 4),
            round(pl.top_light, 4), round(pl.bottom_shade, 4), round(pl.radius, 4),
            round(pl.margin, 4), round(pl.edge_line, 4), round(pl.shadow, 4),
        )
        if key == self._plate_key and self._plate_pm is not None:
            return self._plate_pm
        from PySide6.QtGui import QPixmap

        pw = max(1, int(math.ceil(w * dpr)))
        ph = max(1, int(math.ceil(h * dpr)))
        pm = QPixmap(pw, ph)
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.scale(dpr, dpr)
        self._paint_plate(p, w, h)
        p.end()
        self._plate_key, self._plate_pm = key, pm
        return pm

    def _draw_plate(self, p: QPainter, w: float, h: float) -> None:
        try:
            dpr = float(p.device().devicePixelRatio())
        except Exception:
            dpr = 1.0
        p.drawPixmap(0, 0, self._plate_pixmap(w, h, max(0.5, dpr)))

    def _paint_plate(self, p: QPainter, w: float, h: float) -> None:
        pl: PlateStyle = self.th.plate
        m = max(0.0, float(pl.margin)) * h
        rect = QRectF(m, m, max(1.0, w - 2 * m), max(1.0, h - 2 * m))
        r = max(0.0, float(pl.radius)) * rect.height()
        path = QPainterPath()
        path.addRoundedRect(rect, r, r)

        base = QColor(pl.color)
        a = max(0.0, min(1.0, float(pl.alpha)))
        g = QLinearGradient(0.0, rect.top(), 0.0, rect.bottom())
        g.setColorAt(0.0, qc(mix(base, WHITE, max(0.0, pl.top_light)), a))
        g.setColorAt(0.45, qc(base, a))
        g.setColorAt(1.0, qc(mix(base, BLACK, max(0.0, pl.bottom_shade)), a))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        p.drawPath(path)

        if pl.edge_line > 0:
            pen = QPen(qc(WHITE, pl.edge_line * 0.9))
            pen.setWidthF(max(1.0, h * 0.012))
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            inset = pen.widthF() * 0.5
            p.drawLine(
                QPointF(rect.left() + r, rect.top() + inset),
                QPointF(rect.right() - r, rect.top() + inset),
            )
            pen2 = QPen(qc(BLACK, pl.edge_line))
            pen2.setWidthF(pen.widthF())
            p.setPen(pen2)
            p.drawLine(
                QPointF(rect.left() + r, rect.bottom() - inset),
                QPointF(rect.right() - r, rect.bottom() - inset),
            )

        if pl.shadow > 0:
            p.setBrush(Qt.NoBrush)
            for i in range(4):
                k = (i + 1) / 4.0
                pen = QPen(qc(BLACK, pl.shadow * 0.22 * (1.0 - k)))
                pen.setWidthF(h * 0.05 * k)
                p.setPen(pen)
                p.drawPath(path)

    # ----------------------------------------------------------------- 刻度
    def _draw_ticks(self, p: QPainter, w: float, h: float, t_ms: float) -> None:
        speed = self.speed(w)
        half = max(w * self.rs.judge_ratio, w * (1 - self.rs.judge_ratio))
        t0 = t_ms - (half / speed) * 1000.0 - 50
        t1 = t_ms + (half / speed) * 1000.0 + 50
        alpha = max(0.0, min(1.0, float(self.rs.tick_alpha)))
        if alpha <= 0:
            return
        pen = QPen(qc(WHITE, alpha))
        pen.setWidthF(max(1.0, h * 0.014))
        p.setPen(pen)
        top = h * 0.06
        bot = h * 0.94
        major = self.rs.ticks == "bar"
        if not self.p.audio_clips:
            tm = self.p.chart.timemap
            for t in tm.grid_lines(t0, t1, 1.0 if major else 4.0):
                b = tm.beat(t)
                is_bar = abs(b - round(b / 4.0) * 4.0) < 1e-6
                if major and not is_bar:
                    continue
                for side in (1, -1):
                    x = self.note_x(t, t_ms, w, side, post=False)
                    if -4 <= x <= w + 4:
                        ln = (bot - top) * (0.5 if is_bar else 0.28)
                        cy = h * 0.5
                        p.drawLine(QPointF(x, cy - ln / 2), QPointF(x, cy + ln / 2))
            return
        for clip in self.p.audio_clips:
            lo = max(t0, clip.offset_ms)
            hi = min(t1, clip.end_ms())
            if hi <= lo:
                continue
            tm = clip.timemap
            s0 = clip.timeline_to_source(lo)
            s1 = clip.timeline_to_source(hi)
            for src in tm.grid_lines(s0, s1, 1.0 if major else 4.0):
                t = clip.source_to_timeline(src)
                b = tm.beat(src)
                is_bar = abs(b - round(b / 4.0) * 4.0) < 1e-6
                if major and not is_bar:
                    continue
                for side in (1, -1):
                    x = self.note_x(t, t_ms, w, side, post=False)
                    if -4 <= x <= w + 4:
                        ln = (bot - top) * (0.5 if is_bar else 0.28)
                        cy = h * 0.5
                        p.drawLine(QPointF(x, cy - ln / 2), QPointF(x, cy + ln / 2))

    # --------------------------------------------------------------- 判定点
    def _judge_pulse(self, t_ms: float) -> float:
        js: JudgeStyle = self.th.judge
        if not js.pulse:
            return 0.0
        clip = self.p.audio_clip_at(t_ms)
        if clip is None:
            if self.p.audio_clips:
                return 0.0
            tm = self.p.chart.timemap
            b = tm.beat(t_ms)
        else:
            tm = clip.timemap
            b = tm.beat(clip.timeline_to_source(t_ms))
        frac = b - math.floor(b)
        decay = math.exp(-frac * 5.0)
        accent = 1.0 if abs((math.floor(b) % 4)) < 1e-6 else 0.55
        return decay * accent * max(0.0, min(1.0, float(js.pulse_strength)))

    def _draw_judge(self, p: QPainter, w: float, h: float, t_ms: float) -> None:
        js: JudgeStyle = self.th.judge
        if str(getattr(js, "mode", "diamond")) == "dumbbell":
            self._draw_judge_dumbbell(p, w, h, t_ms)
            return
        jx = self.judge_x(w)
        pulse = self._judge_pulse(t_ms)
        if js.line_alpha > 0 or pulse > 0:
            a = max(0.0, js.line_alpha) + 0.5 * pulse * max(0.0, js.line_alpha + 0.10)
            pen = QPen(qc(WHITE, min(0.9, a)))
            pen.setWidthF(max(1.0, js.line_w * h * (1.0 + 0.6 * pulse)))
            p.setPen(pen)
            p.drawLine(QPointF(jx, h * 0.04), QPointF(jx, h * 0.96))
        if pulse > 0.02:
            r = h * (0.55 + 0.35 * pulse)
            g = QRadialGradient(QPointF(jx, h * 0.5), r)
            g.setColorAt(0.0, qc(WHITE, 0.22 * pulse))
            g.setColorAt(1.0, qc(WHITE, 0.0))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(QPointF(jx, h * 0.5), r, r)
        self._draw_judge_diamond(p, w, h, t_ms)

    def _judge_target(self, t_ms: float, span: float | None = None) -> tuple[Note | None, float]:
        """找当前“正在被打 / 刚被打到”的音符，返回 (音符, dt)。

        优先选已经命中（0 <= dt <= flash_ms）的音符；否则退而选最近的未来音符。
        原先直接把未来音符也算进“最近”，密集谱面（音符间隔 < flash_ms）会把
        已经命中的音符挤掉，导致判定菱形不闪。

        span 可以调大，让「命中前的渐大」也有目标音符可跟。
        """
        js: JudgeStyle = self.th.judge
        near_span = (float(span) if span else
                     max(float(js.window_ms), float(js.flash_ms)) + 40.0)
        best_hit, best_hit_dt = None, 1e18
        best_future, best_future_dt = None, 1e18
        for n in self.p.chart.near(t_ms, near_span):
            dt = t_ms - n.t
            if 0.0 <= dt <= float(js.flash_ms):
                if dt < best_hit_dt:
                    best_hit, best_hit_dt = n, dt
            elif -near_span <= dt < 0.0:
                if abs(dt) < abs(best_future_dt):
                    best_future, best_future_dt = n, dt
        if best_hit is not None:
            return best_hit, best_hit_dt
        return best_future, best_future_dt

    def _tint_k(self, dt: float) -> float:
        """变色强度：命中前渐入（db_tint_ramp_ms），命中那一帧最浓，之后按 flash_ms 淡出。"""
        js: JudgeStyle = self.th.judge
        ramp = max(1.0, float(getattr(js, "db_tint_ramp_ms", 140.0)))
        fade = max(1.0, float(js.flash_ms))
        if dt < -ramp or dt > fade:
            return 0.0
        if dt < 0.0:
            u = (dt + ramp) / ramp                 # 0 → 1
            return u * u * (3.0 - 2.0 * u)
        return 1.0 - dt / fade

    # --------------------------------------------- 命中脉冲包络（渐大 → 峰值 → 渐小）
    @staticmethod
    def _impact_env(dt: float, total_ms: float, grow: float | None = None) -> float:
        """命中脉冲：dt=0（命中那一帧）为峰值 1.0。

        dt < 0 = 还在接近（渐大），dt > 0 = 已经命中（渐小）。
        传入 grow（0~1）时，渐大改用「胶囊下落进程」而不是时间：
        胶囊在最高点时 grow=0，落到最低点 grow=1 —— 所以峰值必定出现在最低点，
        不会出现"降到一半就最大"或"到点才突然变大"。
        """
        total = float(total_ms or 0.0)
        if total <= 0.0:
            return 0.0
        half = total * 0.5
        if dt < 0.0:
            if grow is None:
                if dt <= -half:
                    return 0.0
                u = (dt + half) / half                  # 0 → 1 逐渐接近命中
            else:
                u = max(0.0, min(1.0, float(grow)))
            return u * u * (3.0 - 2.0 * u)              # smoothstep：渐大
        if dt >= half:
            return 0.0
        u = dt / half
        return (1.0 - u) ** 1.6                         # 渐小

    # ------------------------------------------------------- 哑铃判定点（新样式）
    def _feedback_kind(self, st: NoteStyle | None) -> str:
        """这个音符类型在哑铃判定点上用 A（浅）/ B（深）/ C（微）反馈。

        auto = 按尺寸给个建议：明显比默认大（size ≥ 1.2）用 B，否则 A。
        尺寸只是默认建议，用户可以在「音符类型」面板里对每种类型手动指定。
        """
        if st is None:
            return "A"
        want = str(getattr(st, "feedback", "auto") or "auto").upper()
        if want in ("A", "B", "C"):
            return want
        return "B" if float(getattr(st, "size", 1.0)) >= 1.2 else "A"

    def _anchor_notes(self, t_ms: float) -> tuple[Note | None, Note | None]:
        """当前时间前后最近的两颗音符（用于把胶囊相位锚在「Beat 到达」上）。"""
        prev_note = next_note = None
        for n in self.p.chart.near(t_ms, 6000.0):
            if n.t <= t_ms:
                if prev_note is None or n.t > prev_note.t:
                    prev_note = n
            else:
                if next_note is None or n.t < next_note.t:
                    next_note = n
        return prev_note, next_note

    def _judge_phase(self, t_ms: float) -> tuple[float, Note | None, Note | None, bool]:
        """返回 (相位 0..1, 上一颗音符, 下一颗音符, 是否静止)。

        相位 0 = 上一颗音符刚到达判定点（胶囊最低点），相位 1 = 下一颗到达。
        锚在音符上而不是音频拍网格上：这样「最低点 = Beat 到达」永远成立。
        没有上一颗 / 没有下一颗 / 两颗间隔超过 db_idle_gap_ms 时返回静止，
        长条不再空转往复（参考视频里没有 Beat 的段落也没有这种运动）。
        """
        js: JudgeStyle = self.th.judge
        prev_note, next_note = self._anchor_notes(t_ms)
        gap_ms = max(50.0, float(getattr(js, "db_idle_gap_ms", 1500.0)))
        if prev_note is None:
            return 0.0, None, next_note, True          # 还没到第一颗 Beat
        if next_note is None:
            return 0.0, prev_note, None, True          # 后面再也没有 Beat
        span = float(next_note.t) - float(prev_note.t)
        if span <= 0.0 or span > gap_ms:
            return 0.0, prev_note, next_note, True     # 间隔太长：停住
        return (t_ms - prev_note.t) / span, prev_note, next_note, False

    def _top_mult(self, note: Note | None) -> float:
        """胶囊最高点（倍 half_len）。开启「分模式最高点」后按该音符的 A/B/C 取各自的值。"""
        js: JudgeStyle = self.th.judge
        base = max(0.05, float(getattr(js, "db_travel_top", 0.85)))
        if not bool(getattr(js, "db_top_split", False)) or note is None:
            return base
        st = self.th.styles.get(note.type)
        if st is None:
            return base
        kind = self._feedback_kind(st).lower()
        return max(0.05, float(getattr(js, f"db_top_{kind}", base)))

    def _dip_mult(self, note: Note | None) -> float:
        """某颗音符对应的下沉深度（单位 = half_len 的倍数）。"""
        js: JudgeStyle = self.th.judge
        if note is None:
            return float(getattr(js, "db_dip_a", 0.77))
        st = self.th.styles.get(note.type)
        if st is None:
            st = NoteStyle(key=note.type, name=note.type)
        kind = self._feedback_kind(st)
        if kind == "B":
            return float(getattr(js, "db_dip_b", 1.45))
        if kind == "C":
            return float(getattr(js, "db_dip_c", 0.50))
        return float(getattr(js, "db_dip_a", 0.77))

    def _dumbbell_geometry(self, h: float) -> tuple[float, float, float, float, float]:
        """返回 (中轴 y, 上圆 y, 下圆 y, 圆点半径, half_len 像素)。"""
        js: JudgeStyle = self.th.judge
        scale = max(0.2, float(getattr(js, "db_scale", 1.0)))
        cy = h * 0.5
        half_px = max(1.0, float(getattr(js, "db_half_len", 0.31)) * h * scale)
        return cy, cy - half_px, cy + half_px, max(1.0, float(
            getattr(js, "db_circle_r", 0.040)) * h * scale), half_px

    def _capsule_ynorm(self, t_ms: float) -> float:
        """胶囊中心高度（相对条高）。缓升 + 加速下落，Beat 到达时正好在最低点。

        进出空闲（前面/后面没有 Beat、或间隔太长）时不跳变：
        在 db_idle_glide_ms 内平滑滑向最高点，或从最高点平滑落向「即将到来的那一拍」。
        """
        js: JudgeStyle = self.th.judge
        frac, prev_note, next_note, idle = self._judge_phase(t_ms)
        half = max(0.01, float(getattr(js, "db_half_len", 0.31))
                   * max(0.2, float(getattr(js, "db_scale", 1.0))))

        def top_of(note: Note | None) -> float:
            return 0.5 - self._top_mult(note) * half

        def dip(note: Note | None) -> float:
            return 0.5 + self._dip_mult(note) * half

        if idle:
            rest = top_of(prev_note if prev_note is not None else next_note)
            # 平滑过渡：刚过完一颗 → 从最低点升回最高点；快要来下一颗 → 从最高点落下去
            glide = max(0.0, float(getattr(js, "db_idle_glide_ms", 400.0)))
            if glide <= 0.0:
                return rest
            if prev_note is not None:
                since = t_ms - float(prev_note.t)
                if 0.0 <= since < glide:
                    u = since / glide
                    return dip(prev_note) + (rest - dip(prev_note)) * (u * u * (3.0 - 2.0 * u))
            if next_note is not None:
                left = float(next_note.t) - t_ms
                if 0.0 <= left < glide:
                    u = 1.0 - left / glide
                    return rest + (dip(next_note) - rest) * (u * u * (3.0 - 2.0 * u))
            return rest

        mode = str(getattr(js, "db_ease_mode", "both") or "both").lower()
        strength = max(0.0, min(1.0, float(getattr(js, "db_ease_strength", 0.7))))
        slow_top = mode in ("top", "both")          # 最高点缓入缓出
        slow_bottom = mode == "both"                # 最低点（命中处）也缓入缓出

        rise = max(0.05, min(0.95, float(getattr(js, "db_rise_frac", 0.71))))
        if frac < rise:
            top = top_of(prev_note)                           # 上升段用这一颗对应的最高点
            d = dip(prev_note)                                # 刚从最低点往上走
            u = frac / rise
            k = self._ease(u, at_start=slow_bottom, at_end=slow_top, strength=strength)
            return d + (top - d) * k
        top = top_of(next_note)                               # 下落段用下一颗对应的最高点
        d = dip(next_note)                                    # 正在砸向下一颗
        u = (frac - rise) / (1.0 - rise)
        k = self._ease(u, at_start=slow_top, at_end=slow_bottom, strength=strength)
        return top + (d - top) * k

    @staticmethod
    def _ease(u: float, *, at_start: bool, at_end: bool, strength: float) -> float:
        """把 0→1 的进度 u 做缓动整形；at_start/at_end 表示该端点处速度为 0。

        strength 是"往缓动方向混合多少"：0 = 完全线性（匀速），1 = 完全缓动。
        """
        u = max(0.0, min(1.0, float(u)))
        k = max(0.0, min(1.0, float(strength)))
        if at_start and at_end:
            shaped = u * u * (3.0 - 2.0 * u)          # smoothstep：两端都停一下
        elif at_end:
            shaped = 1.0 - (1.0 - u) ** 2.2           # ease-out：快到终点时减速
        elif at_start:
            shaped = u ** 2.2                         # ease-in：起步时加速
        else:
            shaped = u
        return u + (shaped - u) * k

    def _cap_pop_mult(self, st: NoteStyle | None) -> float:
        """胶囊膨胀倍率：默认统一值；开启「分模式」后按该音符的 A/B/C 取各自的值。"""
        js: JudgeStyle = self.th.judge
        base = max(1.0, float(getattr(js, "db_cap_pop", 1.16)))
        if not bool(getattr(js, "db_cap_pop_split", False)) or st is None:
            return base
        kind = self._feedback_kind(st).lower()
        return max(1.0, float(getattr(js, f"db_cap_pop_{kind}", base)))

    def _dumbbell_feedback(self, st: NoteStyle | None, dt: float, h: float,
                           hit: bool, grow: float | None = None) -> tuple[float, float, float, float, float]:
        """命中形变：(圆点膨胀, 整体缩放, x 偏移, y 偏移, 胶囊膨胀)。

        三者都用同一个"命中脉冲"包络：命中那一帧最大，之前渐大、之后渐小。
        grow（0~1，胶囊下落进程）给了就用它决定渐大量，保证峰值正好在最低点。
        A：圆点膨胀；B：整体放大 + 颤抖；C：只有胶囊膨胀。
        抖动只在命中之后（dt ≥ 0）出现。
        """
        js: JudgeStyle = self.th.judge
        if not hit:
            return 1.0, 1.0, 0.0, 0.0, 1.0

        kind = self._feedback_kind(st)
        # 胶囊膨胀：A/B/C 都有（倍率可分别设置，1.0 = 不膨胀）
        cap_env = self._impact_env(dt, float(getattr(js, "db_cap_pop_ms", 280.0)), grow)
        cap_mult = self._cap_pop_mult(st)
        cap_pop = 1.0 + (cap_mult - 1.0) * cap_env

        if kind == "C":
            # 微下沉：只保留胶囊膨胀，不膨胀圆点、不整体放大、也不抖
            return 1.0, 1.0, 0.0, 0.0, cap_pop

        if kind == "A":
            env = self._impact_env(dt, float(getattr(js, "db_pop_ms", 320.0)), grow)
            pop = 1.0 + (float(getattr(js, "db_pop_scale", 1.65)) - 1.0) * env
            return pop, 1.0, 0.0, 0.0, cap_pop

        total = float(getattr(js, "db_shake_ms", 320.0))
        env = self._impact_env(dt, total, grow)
        scale = 1.0 + (float(getattr(js, "db_shake_scale", 1.12)) - 1.0) * env
        amp = 0.0
        shake_dur = float(getattr(js, "db_shake_dur_ms", 600.0))
        if dt >= 0.0 and shake_dur > 0.0:
            u = min(1.0, dt / shake_dur)
            amp = float(getattr(js, "db_shake_amp", 0.025)) * h * (1.0 - u) ** 1.5
        ph = 2.0 * math.pi * float(getattr(js, "db_shake_hz", 30.0)) * dt / 1000.0
        return 1.0, scale, amp * math.sin(ph), amp * 0.55 * math.sin(ph * 1.7 + 0.9), cap_pop

    def _draw_judge_dumbbell(self, p: QPainter, w: float, h: float, t_ms: float) -> None:
        """哑铃判定点：上下圆点 + 细竖杆 + 沿杆滑动的横胶囊。

        几何以 Beat 中轴为中心上下对称；绘制顺序为 杆 → 圆点 → 胶囊，
        胶囊永远在最上层，不会被杆、圆点或音符遮住。
        """
        js: JudgeStyle = self.th.judge
        jx = self.judge_x(w)
        # 命中前后都要参与：渐大发生在命中之前，所以回看得比判定窗口更远
        span = max(float(js.window_ms),
                   float(getattr(js, "db_tint_ramp_ms", 140.0)),
                   float(getattr(js, "db_pop_ms", 320.0)) / 2.0,
                   float(getattr(js, "db_shake_ms", 320.0)) / 2.0,
                   float(getattr(js, "db_cap_pop_ms", 280.0)) / 2.0)
        n, dt = self._judge_target(t_ms, span)
        st = self.th.styles.get(n.type) if n is not None else None
        if n is not None and st is None:
            st = NoteStyle(key=n.type, name=n.type)

        # 命中染色：命中前就开始向该 Beat 的颜色渐变，命中那一帧达到最浓，然后慢慢退回
        k = 0.0
        hit = n is not None and abs(dt) <= span
        if hit and js.flash:
            k = self._tint_k(dt) * max(0.0, min(1.0, float(js.tint)))
        db_color = qc(str(getattr(js, "db_color", "") or js.idle_color))
        color = mix(db_color, qc(st.color), k) if (st is not None and k > 0.001) else db_color
        outline_w_scale = max(0.0, float(getattr(js, "db_outline_w", 1.0)))
        outline_alpha = max(0.0, min(1.0, float(getattr(js, "db_outline_alpha", 1.0))))
        outline_src = (str(getattr(js, "db_outline_color", "") or "")
                       or (st.outline if st is not None else "#141a20"))
        outline = qc(outline_src, outline_alpha)
        outline_on = outline_w_scale > 0.001 and outline_alpha > 0.001

        # 胶囊下落进程：最高点 0 → 最低点 1；用它驱动"渐大"，保证峰值正好在最低点
        half_n = max(0.01, float(getattr(js, "db_half_len", 0.31))
                     * max(0.2, float(getattr(js, "db_scale", 1.0))))
        top_n = 0.5 - self._top_mult(n) * half_n
        dip_n = 0.5 + self._dip_mult(n) * half_n
        yn = self._capsule_ynorm(t_ms)
        grow = 0.0 if abs(dip_n - top_n) < 1e-6 else max(
            0.0, min(1.0, (yn - top_n) / (dip_n - top_n)))

        pop_circle, scale, dx, dy, cap_pop = self._dumbbell_feedback(
            st, dt, h, hit, grow=grow)
        cy, y_top, y_bot, r_c, half_px = self._dumbbell_geometry(h)
        scale_geo = max(0.2, float(getattr(js, "db_scale", 1.0)))
        cw = max(2.0, float(getattr(js, "db_capsule_w", 0.26)) * h * scale_geo)
        ch = max(2.0, float(getattr(js, "db_capsule_h", 0.10)) * h * scale_geo)
        hw = max(1.0, float(getattr(js, "db_handle_w", 0.016)) * h * scale_geo)

        p.save()
        p.translate(jx + dx, cy + dy)
        p.scale(scale, scale)
        p.translate(-jx, -cy)

        # 1) 竖杆：实心纯色杆 + 两侧描边（中间不留空，所以不会透出背景）
        rod_w = max(2.0, hw * 2.0)
        rod = QRectF(jx - rod_w * 0.5, y_top, rod_w, max(1.0, y_bot - y_top))
        rod_path = QPainterPath()
        rod_path.addRoundedRect(rod, rod_w * 0.5, rod_w * 0.5)
        p.setPen(QPen(outline, hw * 0.7 * outline_w_scale) if outline_on else Qt.NoPen)
        p.setBrush(QBrush(color))
        p.drawPath(rod_path)

        # 2) 上下圆点（A 反馈时在命中瞬间膨胀）
        rr = r_c * pop_circle
        p.setPen(QPen(outline, r_c * 0.30 * outline_w_scale) if outline_on else Qt.NoPen)
        p.setBrush(QBrush(color))
        p.drawEllipse(QPointF(jx, y_top), rr, rr)
        p.drawEllipse(QPointF(jx, y_bot), rr, rr)

        # 3) 滑动的横胶囊：最后画，永远压在最上层
        cap_y = min(h - ch * 0.5, max(ch * 0.5, yn * h))
        cw2, ch2 = cw * cap_pop, ch * cap_pop
        rect = QRectF(jx - cw2 * 0.5, cap_y - ch2 * 0.5, cw2, ch2)
        path = QPainterPath()
        path.addRoundedRect(rect, ch2 * 0.5, ch2 * 0.5)
        p.setPen(QPen(outline, ch * 0.16 * outline_w_scale) if outline_on else Qt.NoPen)
        p.setBrush(QBrush(color))
        p.drawPath(path)

        # 命中扩散圈（默认关）
        if hit and k > 0.02 and bool(getattr(js, "db_ring", False)) and st is not None:
            rad = cw * (0.55 + 0.55 * (1.0 - k))
            p.setPen(QPen(qc(st.color, 0.5 * k), max(1.0, cw * 0.03)))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(jx, cap_y), rad, rad)
        p.restore()

    def _draw_judge_diamond(self, p: QPainter, w: float, h: float, t_ms: float) -> None:
        """常驻的 45° 灰白菱形；音符命中后按该音符颜色闪一下。"""
        js: JudgeStyle = self.th.judge
        if not js.idle:
            return
        jx = self.judge_x(w)
        n, dt = self._judge_target(t_ms)
        st = self.th.styles.get(n.type) if n is not None else None
        if n is not None and st is None:
            # 未知类型（手改工程/旧数据）也要能画，不能 AttributeError
            st = NoteStyle(key=n.type, name=n.type)
        y = self.lane_y(n.lane, h) if n is not None else h * 0.5
        # 判定点大小只由判定点自身设置决定，不能跟着当前音符类型的 size 变。
        base_r = 0.5 * float(self.rs.note_size) * self.content_h(h)
        r = base_r * float(js.idle_scale)
        k = 0.0
        if n is not None and js.flash and 0.0 <= dt <= float(js.flash_ms):
            k = 1.0 - dt / max(1.0, float(js.flash_ms))
            k *= max(0.0, min(1.0, float(js.tint)))
        # 命中瞬间的扩散圈
        if k > 0.02 and js.ring and st is not None:
            rr = r * (1.0 + 1.6 * (1.0 - k))
            pen = QPen(qc(st.color, 0.55 * k))
            pen.setWidthF(max(1.0, r * 0.18 * k))
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(jx, y), rr, rr)
        fill = qc(js.idle_color) if st is None or k <= 0 else mix(qc(js.idle_color), qc(st.color), k)
        if k > 0.02:
            g = QRadialGradient(QPointF(jx, y), r * 2.4)
            g.setColorAt(0.0, qc(st.color if st is not None else js.idle_color, 0.35 * k * float(js.glow)))
            g.setColorAt(1.0, qc(js.idle_color, 0.0))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(QPointF(jx, y), r * 2.4, r * 2.4)
        rr = r * (1.0 + 0.26 * k)
        pm = self._pixmap(getattr(js, "image", ""))
        if pm is not None:
            p.save()
            p.drawPixmap(self._image_rect(jx, y, rr * 2.0, pm),
                         pm, QRectF(pm.rect()))
            p.restore()
        else:
            path = shape_path(jx, y, rr, getattr(js, "shape", "diamond"))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(fill))
            p.drawPath(path)
            outline = st.outline if st is not None else "#141a20"
            pen = QPen(qc(outline), max(0.9, rr * 0.17))
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)

    # ----------------------------------------------------------------- 音符
    def _visible(self, t_ms: float, w: float) -> list[Note]:
        speed = self.speed(w)
        traverse = (w / speed) * 1000.0
        lo = t_ms - traverse - float(self.p.chart.max_dur) - 200.0
        hi = t_ms + traverse + 200.0
        return self.p.chart.in_range(lo, hi)

    def _draw_notes(self, p: QPainter, w: float, h: float, t_ms: float) -> None:
        rs = self.rs
        th = self.th
        js: JudgeStyle = th.judge
        gs: GhostStyle = th.ghost
        jx = self.judge_x(w)
        win = max(1.0, float(js.window_ms))
        visible = self._visible(t_ms, w)
        if not visible:
            return
        pad = h * 0.8
        x_lo, x_hi = -pad, w + pad
        speed = self.speed(w)
        # 每种类型只解析一次颜色 / 只建一次画笔
        sig = tuple((k, s.color, s.outline, round(float(s.size), 4), round(float(s.glow), 4))
                    for k, s in th.styles.items()) + (
            gs.mode, gs.shape, gs.color, gs.outline, float(gs.size), round(float(gs.alpha), 4),
        )
        if sig != self._style_sig:
            self._style_cache.clear()
            self._style_sig = sig

        # 长条单独处理：命中后音符头钉在判定点，长条身体随着尾巴接近不断缩短，
        # 尾巴到判定点时长度归零，整个长条才消失。
        for n in visible:
            if n.dur > 0:
                st = th.styles.get(n.type) or th.style(n.type)
                self._draw_hold_note(p, w, h, t_ms, n, st, jx, speed)

        # 先画“已越过判定点”的残影，再画正常音符，保证当前音符在最上层
        for phase in (0, 1):
            for n in visible:
                if n.dur > 0:
                    continue
                side = self.side_of(n)
                x = self.note_x(n.t, t_ms, w, side)
                y = self.lane_y(n.lane, h)
                st = th.styles.get(n.type) or th.style(n.type)
                r = self.note_radius(h, st)
                dt = t_ms - n.t
                passed = dt >= 0.0
                ghosted = passed and dt >= float(gs.delay_ms)
                if phase == 0 and not ghosted:
                    continue
                if phase == 1 and ghosted:
                    continue
                if ghosted:
                    if gs.mode == "off":
                        continue
                    if x < x_lo or x > x_hi:          # 屏幕外直接跳过（省掉 AA 光栅化）
                        continue
                    if gs.fade_s > 0:
                        alpha = max(0.0, 1.0 - max(0.0, dt - float(gs.delay_ms)) / 1000.0 / float(gs.fade_s))
                        if alpha <= 0.01:
                            continue
                    else:
                        alpha = 1.0
                    if gs.mode == "fade":
                        self._draw_note(p, st, x, y, r, alpha * 0.85, 1.0, 0.25)
                        continue
                    gst = self._ghost_style(gs, st)
                    morph_s = max(0.0, float(getattr(gs, "morph_ms", 0.0))) / 1000.0
                    elapsed = max(0.0, dt - float(gs.delay_ms)) / 1000.0
                    if morph_s > 0 and elapsed < morph_s:
                        u = max(0.0, min(1.0, elapsed / morph_s))
                        u = u * u * (3.0 - 2.0 * u)
                        angle = 90.0 * u
                        # 原音符旋转并淡出，灰块旋转并淡入，中间是两者叠加
                        self._draw_note(p, st, x, y, r, alpha * (1.0 - u),
                                        1.0 + 0.06 * u, 0.18 * (1.0 - u), angle=angle)
                        self._draw_note(p, gst, x, y, r * float(gs.size),
                                        alpha * u * float(gs.alpha), 1.0, 0.0,
                                        angle=angle)
                    else:
                        self._draw_note(p, gst, x, y, r * float(gs.size),
                                        alpha * float(gs.alpha), 1.0, 0.0)
                    continue

                # 尚未命中：正常音符
                if x < x_lo or x > x_hi:
                    continue
                k = 0.0
                if abs(dt) <= win:
                    k = 1.0 - abs(dt) / win
                self._draw_note(p, st, x, y, r, 1.0, 1.0 + 0.14 * k, 0.22 * k)

    def _draw_hold_note(
        self,
        p: QPainter,
        w: float,
        h: float,
        t_ms: float,
        n: Note,
        st: NoteStyle,
        jx: float,
        speed: float,
    ) -> None:
        """长条专用：头部命中后钉在判定点，身体缩短到 0 才消失。"""
        if t_ms >= n.end:
            return
        side = self.side_of(n)
        y = self.lane_y(n.lane, h)
        r = self.note_radius(h, st)
        pad = h * 0.8
        x_lo, x_hi = -pad, w + pad
        if t_ms >= n.t:
            # 活动中的长条：头固定在判定点保持原样，尾巴继续向判定点移动。
            xt = jx + side * (n.end - t_ms) / 1000.0 * speed
            if not (max(jx, xt) < x_lo or min(jx, xt) > x_hi):
                self._draw_hold(p, st, jx, y, r, n, t_ms, w, side, jx, h)
            self._draw_note(p, st, jx, y, r, 1.0)
            return
        # 还没命中：正常滚动，头在判定点右侧。
        x = jx + side * (n.t - t_ms) / 1000.0 * speed
        xt = jx + side * (n.end - t_ms) / 1000.0 * speed
        if max(x, xt) < x_lo or min(x, xt) > x_hi:
            return
        self._draw_hold(p, st, x, y, r, n, t_ms, w, side, jx, h)
        js = self.th.judge
        win = max(1.0, float(js.window_ms))
        k = max(0.0, 1.0 - abs(t_ms - n.t) / win)
        self._draw_note(p, st, x, y, r, 1.0, 1.0 + 0.14 * k, 0.22 * k)

    def _ghost_style(self, gs: GhostStyle, st: NoteStyle) -> NoteStyle:
        c = self._style_cache.get("__ghost__")
        if c is None:
            c = NoteStyle(
                key="__ghost__",
                shape=gs.shape,
                color=gs.color,
                outline=gs.outline,
                outline_w=st.outline_w,
                size=1.0,
                glow=0.0,
            )
            self._style_cache["__ghost__"] = c
        return c

    def _draw_hold(
        self,
        p: QPainter,
        st: NoteStyle,
        x: float,
        y: float,
        r: float,
        n: Note,
        t_ms: float,
        w: float,
        side: int,
        jx: float,
        h: float,
    ) -> None:
        """长条：从音符头一直延伸到尾巴（未来方向）。"""
        xt = self.note_x(n.end, t_ms, w, side)
        lo, hi = (x, xt) if xt >= x else (xt, x)
        thick = max(2.0, r * 0.9)
        rect = QRectF(lo, y - thick * 0.5, max(thick, hi - lo), thick)
        path = QPainterPath()
        path.addRoundedRect(rect, thick * 0.5, thick * 0.5)
        col = qc(st.color, 0.55)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(col))
        p.drawPath(path)
        pen = QPen(qc(st.outline, 0.75))
        pen.setWidthF(max(1.0, st.outline_w * r * 1.6))
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

    def _draw_note(
        self,
        p: QPainter,
        st: NoteStyle,
        x: float,
        y: float,
        r: float,
        alpha: float = 1.0,
        scale: float = 1.0,
        brighten: float = 0.0,
        angle: float = 0.0,
    ) -> None:
        if r <= 0.4 or alpha <= 0.01:
            return
        rr = r * scale
        if abs(angle) > 0.01:
            p.save()
            p.translate(x, y)
            p.rotate(angle)
            x = y = 0.0
        pm = self._pixmap(getattr(st, "image", ""))
        if pm is not None:
            p.save()
            p.setOpacity(max(0.0, min(1.0, alpha)))
            p.drawPixmap(self._image_rect(x, y, rr * 2.0, pm),
                         pm, QRectF(pm.rect()))
            p.restore()
            if abs(angle) > 0.01:
                p.restore()
            return
        path = shape_path(x, y, rr, st.shape)
        fill = qc(st.color, alpha)
        if brighten > 0:
            fill = mix(fill, WHITE, brighten)
            fill.setAlphaF(alpha)
        if st.glow > 0:
            for i in range(3, 0, -1):
                gw = st.outline_w * rr * 2.0 + i * rr * 0.45 * float(st.glow)
                pen = QPen(qc(st.color, alpha * 0.20 * float(st.glow) / i))
                pen.setWidthF(gw)
                pen.setJoinStyle(Qt.RoundJoin)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(fill))
        p.drawPath(path)
        ow = max(0.8, float(st.outline_w) * rr * 2.0)
        pen = QPen(qc(st.outline, min(1.0, alpha)))
        pen.setWidthF(ow)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        if abs(angle) > 0.01:
            p.restore()

    def _draw_flash(self, p: QPainter, st: NoteStyle, jx: float, y: float, r: float, k: float) -> None:
        """（保留给外部调用）判定点高亮：现在由常驻判定菱形负责，见 _draw_judge_diamond。"""
        return
