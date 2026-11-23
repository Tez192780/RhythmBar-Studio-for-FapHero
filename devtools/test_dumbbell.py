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

    # 插入一颗很远的音符，间隔超过阈值 → 中间依然静止
    far = Note(last_t + 5000.0, p.theme.active_rows()[0], 0)
    p.chart.add(far)
    check("间隔超过阈值时仍静止", r._judge_phase(last_t + 2000.0)[3],
          f"gap={js.db_idle_gap_ms:.0f}ms")
    p.chart.notes.remove(far)
    p.chart.sort()

    # ------------------------------------------------------ 5. A/B 反馈形变
    st = p.theme.styles["cyan"]
    st.feedback = "A"
    pop0, _s0, dx0, _dy0, cap0 = r._dumbbell_feedback(st, 0.0, H, True)
    pop_mid, _s1, _dx1, _dy1, cap_mid = r._dumbbell_feedback(st, js.db_pop_ms / 2, H, True)
    pop_end, _s2, _d3, _d4, cap_end = r._dumbbell_feedback(st, js.db_pop_ms + 1, H, True)
    check("A：圆点从 1.0 开始膨胀", abs(pop0 - 1.0) < 1e-6, f"{pop0:.3f}")
    check("A：中途膨胀到设定倍率", abs(pop_mid - js.db_pop_scale) < 0.05,
          f"{pop_mid:.3f} vs {js.db_pop_scale:.3f}")
    check("A：结束后回到 1.0", abs(pop_end - 1.0) < 1e-6, f"{pop_end:.3f}")
    check("A：不整体缩放、不抖动", abs(_s1 - 1.0) < 1e-9 and abs(dx0) < 1e-9)

    st.feedback = "B"
    pop_b, sc_b, dx_b, _dyb, _capb = r._dumbbell_feedback(st, js.db_shake_ms / 2, H, True)
    check("B：整体放大到设定倍率", abs(sc_b - js.db_shake_scale) < 0.02,
          f"{sc_b:.3f} vs {js.db_shake_scale:.3f}")
    check("B：不膨胀圆点", abs(pop_b - 1.0) < 1e-9)
    xs = [r._dumbbell_feedback(st, float(d), H, True)[2] for d in range(0, 200, 5)]
    check("B：抖动来回摆", min(xs) < -0.01 and max(xs) > 0.01,
          f"min={min(xs):.2f} max={max(xs):.2f}")

    # 胶囊膨胀：A/B 有，C 没有
    for kind in ("A", "B"):
        st.feedback = kind
        c0 = r._dumbbell_feedback(st, 0.0, H, True)[4]
        cm = r._dumbbell_feedback(st, js.db_cap_pop_ms / 2, H, True)[4]
        c9 = r._dumbbell_feedback(st, js.db_cap_pop_ms + 1, H, True)[4]
        check(f"{kind}：胶囊命中时也轻微膨胀", abs(c0 - 1.0) < 1e-6
              and abs(cm - js.db_cap_pop) < 0.02 and abs(c9 - 1.0) < 1e-6,
              f"{c0:.2f} -> {cm:.2f} -> {c9:.2f}")

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
