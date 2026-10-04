"""节奏条渲染器：预览与导出共用同一份绘制代码，保证所见即所得。

坐标系：一律使用「逻辑像素」= 导出尺寸（默认 1920×120），
所有尺寸都相对条高 H 计算，所以放大到 4K 或缩小成小预览条都不会跑形。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
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

SHAPE_SQUEEZE = {"diamond": 1.0, "square": 0.84, "circle": 0.92, "bar": 0.42}


def shape_path(cx: float, cy: float, r: float, shape: str) -> QPainterPath:
    """以 (cx,cy) 为中心、半径 r 的外形路径。"""
    p = QPainterPath()
    if shape == "square":
        s = r * SHAPE_SQUEEZE["square"]
        p.addRect(QRectF(cx - s, cy - s, s * 2, s * 2))
    elif shape == "circle":
        p.addEllipse(QPointF(cx, cy), r * SHAPE_SQUEEZE["circle"], r * SHAPE_SQUEEZE["circle"])
    elif shape == "bar":
        s = r * SHAPE_SQUEEZE["bar"] * 2.0
        p.addRoundedRect(QRectF(cx - s, cy - s, s * 2, s * 2), s * 0.5, s * 0.5)
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

    def note_x(self, t_note: float, t_now: float, w: float, side: int = 1) -> float:
        return self.judge_x(w) + side * (t_note - t_now) / 1000.0 * self.speed(w)

    def note_radius(self, h: float, st: NoteStyle, scale: float = 1.0) -> float:
        return 0.5 * float(self.rs.note_size) * h * float(st.size) * scale

    # --------------------------------------------------------------- 主入口
    def render(self, p: QPainter, w: float, h: float, t_ms: float, *, solid_bg: QColor | None = None) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = QRectF(0, 0, w, h)
        if solid_bg is not None:
            p.fillRect(rect, solid_bg)
        else:
            # Source 模式的透明填充 = 快速清零，比 SourceOver 混合便宜得多
            p.setCompositionMode(QPainter.CompositionMode_Source)
            p.fillRect(rect, Qt.transparent)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        if self.th.plate.mode == "plate":
            self._draw_plate(p, w, h)
        if self.rs.ticks != "off":
            self._draw_ticks(p, w, h, t_ms)
        self._draw_judge(p, w, h, t_ms)
        self._draw_notes(p, w, h, t_ms)

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
        tm = self.p.chart.timemap
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
        for t in tm.grid_lines(t0, t1, 1.0 if major else 4.0):
            b = tm.beat(t)
            is_bar = abs(b - round(b / 4.0) * 4.0) < 1e-6
            if major and not is_bar:
                continue
            for side in (1, -1):
                x = self.note_x(t, t_ms, w, side)
                if -4 <= x <= w + 4:
                    ln = (bot - top) * (0.5 if is_bar else 0.28)
                    cy = h * 0.5
                    p.drawLine(QPointF(x, cy - ln / 2), QPointF(x, cy + ln / 2))

    # --------------------------------------------------------------- 判定点
    def _judge_pulse(self, t_ms: float) -> float:
        js: JudgeStyle = self.th.judge
        if not js.pulse:
            return 0.0
        tm = self.p.chart.timemap
        b = tm.beat(t_ms)
        frac = b - math.floor(b)
        decay = math.exp(-frac * 5.0)
        accent = 1.0 if abs((math.floor(b) % 4)) < 1e-6 else 0.55
        return decay * accent * max(0.0, min(1.0, float(js.pulse_strength)))

    def _draw_judge(self, p: QPainter, w: float, h: float, t_ms: float) -> None:
        js: JudgeStyle = self.th.judge
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

    def _judge_target(self, t_ms: float) -> tuple[Note | None, float]:
        """找当前“正在被打 / 刚被打到”的音符，返回 (音符, dt)。"""
        js: JudgeStyle = self.th.judge
        span = max(float(js.window_ms), float(js.flash_ms)) + 40.0
        best_n, best_dt = None, 1e18
        for n in self.p.chart.near(t_ms, span):
            dt = t_ms - n.t
            if -js.window_ms <= dt <= js.flash_ms:
                if abs(dt) < abs(best_dt):
                    best_n, best_dt = n, dt
        return best_n, best_dt

    def _draw_judge_diamond(self, p: QPainter, w: float, h: float, t_ms: float) -> None:
        """常驻的 45° 灰白菱形；音符命中后按该音符颜色闪一下。"""
        js: JudgeStyle = self.th.judge
        if not js.idle:
            return
        jx = self.judge_x(w)
        n, dt = self._judge_target(t_ms)
        st = self.th.styles.get(n.type) if n is not None else None
        y = self.lane_y(n.lane, h) if n is not None else h * 0.5
        base_r = 0.5 * float(self.rs.note_size) * h * (float(st.size) if st is not None else 1.0)
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
        path = shape_path(jx, y, rr, "diamond")
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(fill))
        p.drawPath(path)
        outline = self.th.styles.get(n.type).outline if n is not None else "#141a20"
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

        # 先画“已越过判定点”的残影，再画正常音符，保证当前音符在最上层
        for phase in (0, 1):
            for n in visible:
                side = self.side_of(n)
                x = self.judge_x(w) + side * (n.t - t_ms) / 1000.0 * speed
                y = self.lane_y(n.lane, h)
                st = th.styles.get(n.type) or th.style(n.type)
                r = self.note_radius(h, st)
                dt = t_ms - n.t
                passed = dt > 0.0
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
                    else:
                        gst = self._ghost_style(gs, st)
                        self._draw_note(p, gst, x, y, r * float(gs.size), alpha * float(gs.alpha), 1.0, 0.0)
                    continue

                # 尚未命中：正常音符（长条先画尾巴）
                if n.dur > 0:
                    xt = self.judge_x(w) + side * (n.end - t_ms) / 1000.0 * speed
                    if max(x, xt) < x_lo or min(x, xt) > x_hi:
                        continue
                    self._draw_hold(p, st, x, y, r, n, t_ms, w, side, jx, h)
                elif x < x_lo or x > x_hi:
                    continue
                k = 0.0
                if abs(dt) <= win:
                    k = 1.0 - abs(dt) / win
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
    ) -> None:
        if r <= 0.4 or alpha <= 0.01:
            return
        rr = r * scale
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

    def _draw_flash(self, p: QPainter, st: NoteStyle, jx: float, y: float, r: float, k: float) -> None:
        """（保留给外部调用）判定点高亮：现在由常驻判定菱形负责，见 _draw_judge_diamond。"""
        return
