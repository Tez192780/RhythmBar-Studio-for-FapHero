r"""哑铃判定点：几何 / 相位 / 两种命中反馈 / 与旧样式的隔离。

分两类检查：
  * 数学层：直接调 BarRenderer 的纯函数（相位、胶囊高度、形变），断言数值；
  * 渲染层：真的渲染一帧，检查「哑铃式」画了胶囊、「菱形式」没画，两者互不影响。

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
from rbar.render import BarRenderer  # noqa: E402
from rbar.theme import JudgeStyle, NoteStyle, Theme  # noqa: E402

W, H = 1920, 200
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{' OK ' if ok else 'FAIL'}] {name}" + (f" -> {detail}" if detail else ""), flush=True)
    if not ok:
        failures.append(name)


def make(bpm: float = 128.0) -> tuple:
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

    # ---------------------------------------------------------------- 1. 相位
    hit_t = tm.ms(10.0)
    frac, prev_note, next_note = r._judge_phase(hit_t)
    check("命中时刻的相位为 0", abs(frac) < 1e-6, f"frac={frac:.4f}")
    check("相位锚点是音符（上一颗/下一颗都给出来了）",
          prev_note is not None and next_note is not None,
          f"prev={getattr(prev_note, 't', None)} next={getattr(next_note, 't', None)}")
    check("锚点间距 = 一个节拍", abs((next_note.t - prev_note.t) - tm.ms(1.0) + tm.ms(0.0)) < 1.0,
          f"{next_note.t - prev_note.t:.1f} ms")

    # ---------------------------------------------------------- 2. 胶囊行程
    top = js.db_travel_top
    dip_a = js.db_dip_a
    y0 = r._capsule_ynorm(hit_t)                       # 相位 0 → 最低点
    y_peak = r._capsule_ynorm(tm.ms(10.0 + js.db_rise_frac))
    y_before_next = r._capsule_ynorm(tm.ms(11.0) - 1.0)
    check("相位 0 时胶囊在最低点", abs(y0 - dip_a) < 0.01, f"{y0:.3f} vs {dip_a:.3f}")
    check("升到顶时等于上限", abs(y_peak - top) < 0.02, f"{y_peak:.3f} vs {top:.3f}")
    check("下一拍前回到最低点附近", abs(y_before_next - dip_a) < 0.03, f"{y_before_next:.3f}")
    check("中途高度介于两者之间", top < r._capsule_ynorm(tm.ms(10.5)) < dip_a,
          f"{r._capsule_ynorm(tm.ms(10.5)):.3f}")
    # 单调上升段
    ys = [r._capsule_ynorm(tm.ms(10.0 + k * 0.05)) for k in range(0, 15)]
    check("上升段单调（越高越小）", all(ys[i] >= ys[i + 1] - 1e-9 for i in range(len(ys) - 1)),
          f"{[round(v, 2) for v in ys[:5]]}...")

    # ------------------------------------------------------ 3. A/B 反馈形变
    st = p.theme.styles["cyan"]
    st.feedback = "A"
    pop0, sc0, dx0, dy0 = r._dumbbell_feedback(st, 0.0, H, True)
    pop_mid, sc_mid, _, _ = r._dumbbell_feedback(st, js.db_pop_ms / 2, H, True)
    pop_end, _, _, _ = r._dumbbell_feedback(st, js.db_pop_ms + 1, H, True)
    check("A：命中瞬间圆点从 1.0 开始膨胀", abs(pop0 - 1.0) < 1e-6, f"{pop0:.3f}")
    check("A：中途膨胀到设定倍率", abs(pop_mid - js.db_pop_scale) < 0.05,
          f"{pop_mid:.3f} vs {js.db_pop_scale:.3f}")
    check("A：结束后回到 1.0", abs(pop_end - 1.0) < 1e-6, f"{pop_end:.3f}")
    check("A：不整体缩放、不抖动", abs(sc_mid - 1.0) < 1e-9 and abs(dx0) < 1e-9)

    st.feedback = "B"
    pop_b, sc_b, dx_b, _ = r._dumbbell_feedback(st, js.db_shake_ms / 2, H, True)
    check("B：中途整体放大到设定倍率", abs(sc_b - js.db_shake_scale) < 0.02,
          f"{sc_b:.3f} vs {js.db_shake_scale:.3f}")
    check("B：不膨胀圆点", abs(pop_b - 1.0) < 1e-9)
    check("B：存在横向抖动", abs(dx_b) > 0.0, f"dx={dx_b:.3f}px")
    xs = [r._dumbbell_feedback(st, float(d), H, True)[2] for d in range(0, 200, 5)]
    check("B：抖动来回摆（符号有正有负）",
          min(xs) < -0.01 and max(xs) > 0.01, f"min={min(xs):.2f} max={max(xs):.2f}")
    check("B：结束后回正", abs(r._dumbbell_feedback(st, js.db_shake_ms + 5, H, True)[1] - 1.0) < 1e-6)

    # ------------------------------------------------ 4. auto 与手动指定
    st.feedback = "auto"
    st.size = 1.0
    check("auto：默认大小 → A", r._feedback_kind(st) == "A")
    st.size = 1.3
    check("auto：明显更大 → B", r._feedback_kind(st) == "B")
    st.size = 1.0
    st.feedback = "B"
    check("手动指定 B 覆盖尺寸判断", r._feedback_kind(st) == "B")
    y_deep = r._capsule_ynorm(hit_t)
    check("手动 B 时胶囊下沉更深", abs(y_deep - js.db_dip_b) < 0.01,
          f"{y_deep:.3f} vs {js.db_dip_b:.3f}")
    st.feedback = "A"
    check("手动 A 时回到浅下沉", abs(r._capsule_ynorm(hit_t) - js.db_dip_a) < 0.01)
    st.feedback = "auto"

    # ------------------------------------------------------ 5. 渲染层隔离
    def lit_rows(img) -> set[int]:
        """判定点附近「够宽」的亮行（≥10px 宽），用来排除 1~2px 的判定竖线。"""
        jx = int(W * 0.33)
        out = set()
        for y in range(0, H, 2):
            n = sum(1 for x in range(jx - 30, jx + 31, 2)
                    if img.pixelColor(x, y).alpha() > 60)
            if n >= 5:
                out.add(y)
        return out

    p.theme.judge.mode = "dumbbell"
    # 关掉两种样式共用的命中扩散圈与判定竖线，只比较「判定点本体」画在哪
    p.theme.judge.ring = False
    p.theme.judge.pulse = False
    p.theme.judge.line_alpha = 0.0
    db_rows = lit_rows(render_frame_image(p, hit_t + 60, W, H, 1))
    p.theme.judge.mode = "diamond"
    dm_rows = lit_rows(render_frame_image(p, hit_t + 60, W, H, 1))

    top_row = int(H * p.theme.judge.db_top_y)
    bot_row = int(H * p.theme.judge.db_bottom_y)

    def near(rows: set[int], target: int, tol: int = 6) -> bool:
        return any(abs(r - target) <= tol for r in rows)

    check("哑铃式在上圆点高度有像素", near(db_rows, top_row), f"目标行 {top_row}")
    check("哑铃式在下圆点高度有像素", near(db_rows, bot_row), f"目标行 {bot_row}")
    check("菱形式不在下圆点高度画东西（两种样式互不干扰）", not near(dm_rows, bot_row),
          f"目标行 {bot_row}")
    check("两种样式的画面确实不同", db_rows != dm_rows,
          f"哑铃 {len(db_rows)} 行 / 菱形 {len(dm_rows)} 行")

    # 旧主题（没有新字段）也能读：默认仍是菱形
    old = Theme.from_dict({"styles": {}, "order": []})
    check("旧工程/旧主题默认仍是菱形式", old.judge.mode == "diamond", old.judge.mode)
    check("NoteStyle 默认反馈为 auto", NoteStyle().feedback == "auto")

    # 往返序列化
    from rbar.theme import JudgeStyle as JS

    back = JS.from_dict(js.to_dict())
    check("判定点样式参数可往返序列化",
          back.mode == js.mode and abs(back.db_dip_b - js.db_dip_b) < 1e-9)

    print()
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {', '.join(failures)}")
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
