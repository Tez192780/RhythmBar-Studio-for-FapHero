r"""哑铃判定点：对称几何 / 层级 / 静止 / 两种命中反馈。

分两类检查：
  * 数学层：直接调 BarRenderer 的纯函数（相位、胶囊高度、几何、形变），断言数值；
  * 渲染层：真的渲染一帧，检查样式隔离与绘制顺序。

用法：
    .\.venv\Scripts\python.exe devtools\test_dumbbell.py
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR",
                          os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rbar.demo import make_demo_project  # noqa: E402
from rbar.exporter import render_frame_image  # noqa: E402
from rbar.model import Note  # noqa: E402
from rbar.render import BarRenderer  # noqa: E402
from rbar.theme import JudgeStyle, NoteStyle, Theme  # noqa: E402

W, H = 1920, 200
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{' OK ' if ok else 'FAIL'}] {name}" + (f" -> {detail}" if detail else ""), flush=True)
    if not ok:
        failures.append(name)


def make(bpm: float = 128.0):
    p = make_demo_project(bpm=bpm, bars=16)
    p.render.width, p.render.height = W, H
    p.render.judge_ratio = 0.33
    p.render.ticks = "off"
    p.theme.plate.mode = "none"
    p.theme.judge.mode = "dumbbell"
    return p, BarRenderer(p)


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setFont(QFont("Arial", 10))
    p, r = make()
    tm = p.chart.timemap
    js: JudgeStyle = p.theme.judge
    half = js.db_half_len * js.db_scale
    hit_t = tm.ms(10.0)

    # ---------------------------------------------------------------- 1. 几何
    cy, y_top, y_bot, r_c, half_px = r._dumbbell_geometry(H)
    check("中轴在条带垂直中央", abs(cy - H / 2) < 1e-6, f"cy={cy}")
    check("上下圆点相对中轴对称", abs((y_top + y_bot) - 2 * cy) < 1e-6,
          f"上 {y_top:.1f} / 下 {y_bot:.1f}")
    check("长度等于 half_len×条高", abs(half_px - js.db_half_len * H * js.db_scale) < 1e-6,
          f"{half_px:.1f}px")
    # 整体缩放
    js.db_scale = 1.5
    _c2, t2, b2, r2, h2 = r._dumbbell_geometry(H)
    check("整体缩放同时放大长度与圆点",
          abs(h2 / half_px - 1.5) < 1e-6 and abs(r2 / r_c - 1.5) < 1e-6,
          f"half {half_px:.1f}->{h2:.1f}, r {r_c:.2f}->{r2:.2f}")
    js.db_scale = 1.0

    # ---------------------------------------------------------------- 2. 相位
    frac, prev_note, next_note, idle = r._judge_phase(hit_t)
    check("命中时刻相位为 0", abs(frac) < 1e-6, f"frac={frac:.4f}")
    check("命中时刻不是静止状态", not idle)
    check("相位锚点是音符", prev_note is not None and next_note is not None)

    # ---------------------------------------------------------- 3. 胶囊行程
    top_n = 0.5 - js.db_travel_top * half
    dip_a_n = 0.5 + js.db_dip_a * half
    dip_b_n = 0.5 + js.db_dip_b * half
    y0 = r._capsule_ynorm(hit_t)
    y_peak = r._capsule_ynorm(tm.ms(10.0 + js.db_rise_frac))
    check("相位 0 时胶囊在最低点", abs(y0 - dip_a_n) < 0.005, f"{y0:.3f} vs {dip_a_n:.3f}")
    check("升到顶时等于上限", abs(y_peak - top_n) < 0.01, f"{y_peak:.3f} vs {top_n:.3f}")
    check("最低点仍在中轴下方、最高点在上方", y0 > 0.5 > y_peak,
          f"low={y0:.2f} high={y_peak:.2f}")
    ys = [r._capsule_ynorm(tm.ms(10.0 + k * 0.05)) for k in range(0, 15)]
    check("上升段单调（越高数值越小）", all(ys[i] >= ys[i + 1] - 1e-9 for i in range(len(ys) - 1)),
          f"{[round(v, 2) for v in ys[:5]]}...")

    # ------------------------------------------------------ 4. 空闲时静止
    last_t = max(n.t for n in p.chart.notes)
    y_far1 = r._capsule_ynorm(last_t + 3000.0)
    y_far2 = r._capsule_ynorm(last_t + 6000.0)
    check("最后一颗之后不再往复（两个时刻高度相同）", abs(y_far1 - y_far2) < 1e-9,
          f"{y_far1:.4f} / {y_far2:.4f}")
    check("空闲时停在最高点（第一拍前的静止位）", abs(y_far1 - top_n) < 0.01,
          f"{y_far1:.3f} vs top {top_n:.3f}")

    # 平滑过渡：最后一颗之后应当「逐渐」升到最高点，而不是瞬移
    glide = float(js.db_idle_glide_ms)
    seq = [r._capsule_ynorm(last_t + glide * k / 8.0) for k in range(9)]
    check("空闲过渡从最低点起步", abs(seq[0] - dip_a_n) < 0.01, f"{seq[0]:.3f}")
    check("空闲过渡在设定时长后到达最高点", abs(seq[-1] - top_n) < 0.01, f"{seq[-1]:.3f}")
    check("空闲过渡是单调的（不会来回跳）",
          all(seq[i] >= seq[i + 1] - 1e-9 for i in range(len(seq) - 1)),
          f"{[round(v, 2) for v in seq]}")

    # 反过来：第一颗之前应当从最高点平滑落下去
    first_t = min(n.t for n in p.chart.notes)
    pre = [r._capsule_ynorm(first_t - glide * (8 - k) / 8.0) for k in range(9)]
    check("首拍前从最高点起步", abs(pre[0] - top_n) < 0.01, f"{pre[0]:.3f}")
    check("首拍时正好落到最低点", abs(pre[-1] - dip_a_n) < 0.01, f"{pre[-1]:.3f}")
    check("首拍前过渡单调（越来越低）",
          all(pre[i] <= pre[i + 1] + 1e-9 for i in range(len(pre) - 1)),
          f"{[round(v, 2) for v in pre]}")
    # 把 glide 设成 0 时应当直接跳（老行为，供对比）
    js.db_idle_glide_ms = 0.0
    check("滑动时长设 0 时直接停在最高点",
          abs(r._capsule_ynorm(last_t + 1.0) - top_n) < 0.01)
    js.db_idle_glide_ms = glide

    # 插入一颗很远的音符，间隔超过阈值 → 中间依然静止
    far = Note(last_t + 5000.0, p.theme.active_rows()[0], 0)
    p.chart.add(far)
    check("间隔超过阈值时仍静止", r._judge_phase(last_t + 2000.0)[3],
          f"gap={js.db_idle_gap_ms:.0f}ms")
    p.chart.notes.remove(far)
    p.chart.sort()

    # --------------------------------------------- 命中脉冲：渐大 → 峰值 → 渐小
    st = p.theme.styles["cyan"]
    def env_curve(total: float) -> list[float]:
        step = total / 12.0
        return [r._impact_env(-total / 2 + k * step, total) for k in range(13)]

    curve = env_curve(js.db_cap_pop_ms)
    check("脉冲包络在命中前为 0（还没开始）", abs(curve[0]) < 1e-9, f"{curve[0]:.4f}")
    check("脉冲包络在命中那一帧为 1（峰值）", abs(curve[6] - 1.0) < 1e-9, f"{curve[6]:.4f}")
    check("脉冲包络在命中后回到 0", abs(curve[-1]) < 1e-9, f"{curve[-1]:.4f}")
    check("脉冲包络前段单调增大",
          all(curve[i] <= curve[i + 1] + 1e-9 for i in range(6)),
          f"{[round(v, 2) for v in curve[:7]]}")
    check("脉冲包络后段单调减小",
          all(curve[i] >= curve[i + 1] - 1e-9 for i in range(6, 12)),
          f"{[round(v, 2) for v in curve[6:]]}")

    for kind in ("A", "B"):
        st.feedback = kind
        total = js.db_pop_ms if kind == "A" else js.db_shake_ms
        span_half = total / 2
        f_start = r._dumbbell_feedback(st, -span_half, H, True)
        f_peak = r._dumbbell_feedback(st, 0.0, H, True)
        f_end = r._dumbbell_feedback(st, span_half, H, True)
        idx = 0 if kind == "A" else 1
        check(f"{kind}：命中前处于原尺寸（不是突然变大）", abs(f_start[idx] - 1.0) < 1e-9,
              f"{f_start[idx]:.3f}")
        check(f"{kind}：命中那一帧为最大值",
              abs(f_peak[idx] - (js.db_pop_scale if kind == "A" else js.db_shake_scale)) < 1e-9,
              f"{f_peak[idx]:.3f}")
        check(f"{kind}：之后回到原尺寸", abs(f_end[idx] - 1.0) < 1e-9, f"{f_end[idx]:.3f}")
        # 中途应该已经比原尺寸大（确实是渐变而不是阶跃）
        mid = r._dumbbell_feedback(st, -span_half / 2, H, True)[idx]
        check(f"{kind}：命中前一半时已经在逐渐变大", 1.0 < mid < f_peak[idx],
              f"{mid:.3f}")

    # 胶囊膨胀同样遵循脉冲包络（A/B 有、C 没有）
    for kind in ("A", "B"):
        st.feedback = kind
        c_half = js.db_cap_pop_ms / 2
        c0 = r._dumbbell_feedback(st, -c_half, H, True)[4]
        cm = r._dumbbell_feedback(st, -c_half / 2, H, True)[4]
        cp = r._dumbbell_feedback(st, 0.0, H, True)[4]
        c9 = r._dumbbell_feedback(st, c_half, H, True)[4]
        check(f"{kind}：胶囊命中前已在变大", abs(c0 - 1.0) < 1e-9 and 1.0 < cm < cp,
              f"{c0:.3f} -> {cm:.3f} -> {cp:.3f}")
        check(f"{kind}：胶囊命中帧最大、之后回落",
              abs(cp - js.db_cap_pop) < 1e-9 and abs(c9 - 1.0) < 1e-9,
              f"peak={cp:.3f} end={c9:.3f}")

    # ---------------------------------------------------------- 变色渐入
    ramp = js.db_tint_ramp_ms
    check("命中前才开始变色（渐入前为 0）", abs(r._tint_k(-ramp - 1.0)) < 1e-9)
    check("变色在命中帧最浓", abs(r._tint_k(0.0) - 1.0) < 1e-9)
    check("命中前一半时颜色已经部分过渡", 0.0 < r._tint_k(-ramp / 2) < 1.0,
          f"{r._tint_k(-ramp / 2):.3f}")
    check("命中后按 flash_ms 淡出", r._tint_k(js.flash_ms / 2) < 1.0
          and abs(r._tint_k(js.flash_ms)) < 1e-9)

    # ------------------------------------------------------ C 微下沉
    st.feedback = "C"
    check("C：识别为微下沉", r._feedback_kind(st) == "C")
    dip_c_n = 0.5 + js.db_dip_c * half
    check("C：最低点 = 中轴 + 0.5 半长（默认）", abs(r._capsule_ynorm(hit_t) - dip_c_n) < 0.01,
          f"{r._capsule_ynorm(hit_t):.3f} vs {dip_c_n:.3f}")
    check("C：比 A 浅、比 A 高（落在中轴与浅下沉之间）",
          dip_c_n < dip_a_n, f"C={dip_c_n:.2f} A={dip_a_n:.2f}")
    for dt in (0.0, js.db_pop_ms / 2, js.db_shake_ms / 2):
        pop, sc, dx, dy, cap = r._dumbbell_feedback(st, dt, H, True)
        check(f"C：dt={dt:.0f}ms 时没有任何命中特效",
              abs(pop - 1.0) < 1e-9 and abs(sc - 1.0) < 1e-9
              and abs(dx) < 1e-9 and abs(dy) < 1e-9 and abs(cap - 1.0) < 1e-9,
              f"pop={pop:.2f} scale={sc:.2f} dx={dx:.2f} cap={cap:.2f}")

    # ---------------------------------------------------- 分模式最高点
    def tops_at_peak() -> float:
        # 取上升段靠顶的位置（严格在上升段内，避开边界）
        return r._capsule_ynorm(tm.ms(10.0 + js.db_rise_frac * 0.95))

    js.db_top_split = False
    top_base = 0.5 - js.db_travel_top * half
    js.db_top_b = 1.15
    check("未开启分模式时，B 的最高点仍用统一值", abs(tops_at_peak() - top_base) < 0.02,
          f"{tops_at_peak():.3f} vs {top_base:.3f}")
    js.db_top_split = True
    for style in p.theme.styles.values():
        style.feedback = "B"
    y_b_top = tops_at_peak()
    for style in p.theme.styles.values():
        style.feedback = "A"
    y_a_top = tops_at_peak()
    check("开启分模式后 B 的最高点更高", y_b_top < y_a_top - 0.01,
          f"B={y_b_top:.3f} A={y_a_top:.3f}")
    js.db_top_split = False
    js.db_top_b = 0.85
    for style in p.theme.styles.values():
        style.feedback = "auto"

    # 哑铃颜色可调
    st.feedback = "auto"
    js.db_color = "#ff3060"
    img_color = render_frame_image(p, tm.ms(10.5), W, H, 1)   # 拍中间，无染色
    jx0 = int(W * 0.33)
    cap_y0 = int(r._capsule_ynorm(tm.ms(10.5)) * H)
    cc = img_color.pixelColor(jx0, cap_y0)
    check("哑铃颜色可调（胶囊取到设定色）", cc.red() > 180 and cc.green() < 140,
          f"rgb={cc.red()},{cc.green()},{cc.blue()}")
    js.db_color = "#e9edf0"

    # ------------------------------------------------- 内容尺寸与画布解耦
    rs = p.render
    st2 = p.theme.styles["cyan"]
    rs.height = 120
    r120 = r.note_radius(120.0, st2)
    rs.height = 200
    r200 = r.note_radius(200.0, st2)
    check("条带高度从 120 改到 200：音符半径不变", abs(r200 - r120) < 1e-6,
          f"{r120:.2f} -> {r200:.2f}")
    rs.content_scale = 2.0
    check("内容缩放 2× 时音符半径翻倍", abs(r.note_radius(200.0, st2) / r120 - 2.0) < 1e-6,
          f"{r.note_radius(200.0, st2):.2f}")
    rs.content_scale = 1.0
    # 导出到更大的画布时仍然跟随（h 是渲染目标高度）
    check("渲染目标放大 2 倍时内容也放大 2 倍",
          abs(r.note_radius(400.0, st2) / r120 - 2.0) < 1e-6,
          f"{r.note_radius(400.0, st2):.2f}")
    rs.height = H

    # ------------------------------------------------ 6. auto 与手动指定
    st.feedback = "auto"
    st.size = 1.0
    check("auto：默认大小 → A", r._feedback_kind(st) == "A")
    st.size = 1.3
    check("auto：明显更大 → B", r._feedback_kind(st) == "B")
    st.size = 1.0
    st.feedback = "B"
    check("手动指定 B 覆盖尺寸判断", r._feedback_kind(st) == "B")
    check("手动 B 时胶囊下沉更深", abs(r._capsule_ynorm(hit_t) - dip_b_n) < 0.01,
          f"{r._capsule_ynorm(hit_t):.3f} vs {dip_b_n:.3f}")
    st.feedback = "auto"

    # ------------------------------------------------------ 7. 绘制顺序
    order: list[str] = []
    orig_judge, orig_notes = r._draw_judge, r._draw_notes
    r._draw_judge = lambda *a, **k: order.append("judge")
    r._draw_notes = lambda *a, **k: order.append("notes")
    from PySide6.QtGui import QImage, QPainter

    canvas = QImage(W, H, QImage.Format_ARGB32)
    painter = QPainter(canvas)
    r.render(painter, W, H, hit_t)
    painter.end()
    check("哑铃式：判定点画在音符之后（压在上层）", order == ["notes", "judge"], str(order))

    order.clear()
    p.theme.judge.mode = "diamond"
    painter = QPainter(canvas)
    r.render(painter, W, H, hit_t)
    painter.end()
    check("菱形式：层级保持原样（先判定点后音符）", order == ["judge", "notes"], str(order))
    p.theme.judge.mode = "dumbbell"
    r._draw_judge, r._draw_notes = orig_judge, orig_notes

    # ------------------------------------------------------ 8. 样式隔离
    def lit_rows(img) -> set[int]:
        jx = int(W * 0.33)
        out = set()
        for y in range(0, H, 2):
            n = sum(1 for x in range(jx - 30, jx + 31, 2)
                    if img.pixelColor(x, y).alpha() > 60)
            if n >= 5:
                out.add(y)
        return out

    p.theme.judge.ring = False
    p.theme.judge.pulse = False
    p.theme.judge.line_alpha = 0.0
    db_rows = lit_rows(render_frame_image(p, hit_t + 60, W, H, 1))
    p.theme.judge.mode = "diamond"
    dm_rows = lit_rows(render_frame_image(p, hit_t + 60, W, H, 1))
    p.theme.judge.mode = "dumbbell"

    # 菱形式集中在条带中部，不会像哑铃那样在上下圆点位置各有一个圆斑；
    # 这里只保证「哑铃确实画在两端」+「两种样式的画面明显不同」。
    check("哑铃式在上圆点高度有像素", any(abs(rr - int(y_top)) <= 6 for rr in db_rows),
          f"目标行 {int(y_top)}")
    check("哑铃式在下圆点高度有像素", any(abs(rr - int(y_bot)) <= 6 for rr in db_rows),
          f"目标行 {int(y_bot)}")
    check("两种样式的画面明显不同", len(db_rows ^ dm_rows) > 10,
          f"差异行数 {len(db_rows ^ dm_rows)}")

    # 竖杆中轴不该有贯穿的黑线：清空音符后再看（否则会被音符本体挡住）
    saved_notes = list(p.chart.notes)
    p.chart.notes = []
    p.chart.sort()
    img = render_frame_image(p, hit_t + 60, W, H, 1)
    jx = int(W * 0.33)
    mid_y = int((y_top + y_bot) / 2) + 8        # 取杆的中段（空闲时胶囊停在顶部，不挡这里）
    c = img.pixelColor(jx, mid_y)
    hw_px = js.db_handle_w * H * js.db_scale
    side_x = jx + int(round(max(1.0, hw_px * 1.5)))
    side = img.pixelColor(side_x, mid_y)
    check("竖杆中轴是实心纯色（不再透明）", c.alpha() > 200, f"中轴像素 alpha={c.alpha()}")
    check("竖杆中轴不是黑线（颜色接近哑铃色）",
          min(c.red(), c.green(), c.blue()) > 120,
          f"rgb={c.red()},{c.green()},{c.blue()}")
    rod_w = max(2.0, hw_px * 2.0)
    run = sum(1 for dx in range(-12, 13) if img.pixelColor(jx + dx, mid_y).alpha() > 40)
    check("竖杆宽度约等于设定粗细", 0.6 * rod_w <= run <= rod_w + 6,
          f"实测 {run}px / 设定杆宽 {rod_w:.1f}px")
    p.chart.notes = saved_notes
    p.chart.sort()

    # 旧主题兼容
    check("旧工程/旧主题默认仍是菱形式",
          Theme.from_dict({"styles": {}, "order": []}).judge.mode == "diamond")
    check("NoteStyle 默认反馈为 auto", NoteStyle().feedback == "auto")
    legacy = JudgeStyle.from_dict({"mode": "dumbbell", "db_top_y": 0.2575,
                                   "db_bottom_y": 0.875, "db_dip_a": 0.74,
                                   "db_dip_b": 0.95, "db_travel_top": 0.235})
    check("旧哑铃参数能迁移到新语义",
          abs(legacy.db_half_len - 0.30875) < 0.01 and abs(legacy.db_dip_a - 0.78) < 0.05,
          f"half={legacy.db_half_len:.3f} dip_a={legacy.db_dip_a:.3f}")

    print()
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {', '.join(failures)}")
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
