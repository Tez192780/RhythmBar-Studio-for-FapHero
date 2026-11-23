r"""哑铃判定点的补充特性：膨胀跟着下落进程、抖动频率/时长、描边、横向中轴线。

用法：
    .\.venv\Scripts\python.exe devtools\test_judge_extra.py
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
from rbar.theme import AxisStyle, Theme  # noqa: E402

W, H = 1920, 200
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{' OK ' if ok else 'FAIL'}] {name}" + (f" -> {detail}" if detail else ""), flush=True)
    if not ok:
        failures.append(name)


def build():
    p = make_demo_project(bpm=128.0, bars=16)
    p.render.width, p.render.height = W, H
    p.render.judge_ratio = 0.33
    p.render.ticks = "off"
    p.theme.plate.mode = "none"
    p.theme.judge.mode = "dumbbell"
    return p, BarRenderer(p)


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setFont(QFont("Arial", 10))
    p, r = build()
    js = p.theme.judge
    tm = p.chart.timemap
    st = p.theme.styles["cyan"]

    # ------------------------------------------------ 1) 膨胀跟着下落进程
    st.feedback = "A"
    g0 = r._dumbbell_feedback(st, -60.0, H, True, grow=0.0)[4]
    g5 = r._dumbbell_feedback(st, -60.0, H, True, grow=0.5)[4]
    g1 = r._dumbbell_feedback(st, -60.0, H, True, grow=1.0)[4]
    check("下落起点（grow=0）胶囊不膨胀", abs(g0 - 1.0) < 1e-9, f"{g0:.3f}")
    check("落到最低点（grow=1）胶囊最大", abs(g1 - js.db_cap_pop) < 1e-9, f"{g1:.3f}")
    check("膨胀随下落单调增大", g0 < g5 < g1, f"{g0:.3f} < {g5:.3f} < {g1:.3f}")

    # 渲染层：胶囊到最低点时 grow 应该接近 1
    hit = tm.ms(10.0)
    yn = r._capsule_ynorm(hit)
    half = js.db_half_len * js.db_scale
    top_n = 0.5 - r._top_mult(None) * half
    dip_n = 0.5 + r._dip_mult(None) * half
    grow_at_bottom = (yn - top_n) / (dip_n - top_n)
    check("命中帧的胶囊确实在最低点（grow≈1）", abs(grow_at_bottom - 1.0) < 0.02,
          f"{grow_at_bottom:.3f}")

    # ------------------------------------------------ 2) 抖动频率与时长
    def shakes(hz: float, dur_ms: float, steps: int = 60) -> list[float]:
        js.db_shake_hz = hz
        js.db_shake_dur_ms = dur_ms
        st.feedback = "B"
        return [r._dumbbell_feedback(st, dur_ms * k / steps, H, True)[2]
                for k in range(steps + 1)]

    def flips(xs: list[float]) -> int:
        return sum(1 for i in range(1, len(xs)) if xs[i] * xs[i - 1] < 0)

    slow = shakes(8.0, 600.0)
    fast = shakes(45.0, 600.0)
    check("抖频越高，来回摆动的次数越多", flips(fast) > flips(slow) * 2,
          f"8Hz {flips(slow)} 次 / 45Hz {flips(fast)} 次")

    js.db_shake_hz = 30.0
    js.db_shake_dur_ms = 400.0

    def shake_amp_at(dt: float) -> float:
        # 取一个小窗口内的最大位移，避免正好采到正弦零点
        return max(abs(r._dumbbell_feedback(st, dt + k * 2.0, H, True)[2]) for k in range(6))

    early = shake_amp_at(30.0)
    late = shake_amp_at(360.0)
    after = abs(r._dumbbell_feedback(st, 450.0, H, True)[2])
    check("抖动随时间衰减", early > late > 0.0, f"{early:.2f} > {late:.2f}")
    check("抖动时长结束后为 0", after == 0.0, f"{after:.3f}")
    js.db_shake_dur_ms = 600.0
    st.feedback = "auto"

    # ------------------------------------------------ 3) 描边可调
    js.db_outline_color = "#ff2d6a"
    js.db_outline_w = 2.5
    js.db_outline_alpha = 1.0
    img = render_frame_image(p, hit + 40, W, H, 1)
    jx = int(W * 0.33)
    cap_y = int(r._capsule_ynorm(hit + 40) * H)
    # 胶囊左右两侧边缘应当出现设定的描边色
    edge = None
    for dx in range(20, 45):
        c = img.pixelColor(jx + dx, cap_y)
        if c.alpha() > 100 and c.red() > 180 and c.green() < 120:
            edge = c
            break
    check("哑铃/胶囊描边颜色生效", edge is not None,
          f"rgb={None if edge is None else (edge.red(), edge.green(), edge.blue())}")

    js.db_outline_w = 0.0
    img2 = render_frame_image(p, hit + 40, W, H, 1)
    has_red = any(
        img2.pixelColor(jx + dx, cap_y).alpha() > 100
        and img2.pixelColor(jx + dx, cap_y).red() > 180
        and img2.pixelColor(jx + dx, cap_y).green() < 120
        for dx in range(20, 45)
    )
    check("描边宽度设 0 后不再有描边色", not has_red)
    js.db_outline_color = ""
    js.db_outline_w = 1.0

    # ------------------------------------------------ 4) 横向中轴线
    saved = list(p.chart.notes)
    p.chart.notes = []
    p.chart.sort()

    ax = p.theme.axis
    ax.enabled = True
    ax.width = 0.03
    ax.color = "#33ff66"
    ax.alpha = 1.0
    ax.outline_w = 0.0
    img3 = render_frame_image(p, 5000.0, W, H, 1)
    c_mid = img3.pixelColor(W // 2, H // 2)
    check("轴线画在画面正中", c_mid.green() > 180 and c_mid.red() < 120,
          f"rgb={c_mid.red()},{c_mid.green()},{c_mid.blue()}")
    c_off = img3.pixelColor(W // 2, H // 2 + 20)
    check("轴线之外仍是透明", c_off.alpha() < 20, f"alpha={c_off.alpha()}")

    ax.enabled = False
    img4 = render_frame_image(p, 5000.0, W, H, 1)
    check("关闭后轴线消失", img4.pixelColor(W // 2, H // 2).alpha() < 20)

    # 轴线与判定区样式无关：切回菱形式也要画
    p.theme.judge.mode = "diamond"
    ax.enabled = True
    img5 = render_frame_image(p, 5000.0, W, H, 1)
    check("菱形式下轴线同样生效", img5.pixelColor(W // 2, H // 2).green() > 180)
    p.theme.judge.mode = "dumbbell"

    # 描边
    ax.outline_w = 0.02
    ax.outline_color = "#ff0000"
    img6 = render_frame_image(p, 5000.0, W, H, 1)
    row = [img6.pixelColor(W // 2, y) for y in range(H // 2 - 4, H // 2 + 5)]
    check("轴线描边生效（出现描边色）",
          any(c.red() > 150 and c.green() < 120 for c in row),
          f"{[(c.red(), c.green()) for c in row[:3]]}")

    # 存档兼容
    back = Theme.from_dict(p.theme.to_dict())
    check("轴线配置可往返序列化",
          back.axis.enabled == ax.enabled and abs(back.axis.width - ax.width) < 1e-9)
    old = Theme.from_dict({"styles": {}, "order": []})
    check("旧工程默认轴线开启", isinstance(old.axis, AxisStyle) and old.axis.enabled)

    p.chart.notes = saved
    p.chart.sort()

    print()
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {', '.join(failures)}")
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
