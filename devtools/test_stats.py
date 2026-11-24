r"""文字与统计：分段计数、三种显示方式、Phase 前缀、进度条四种依据。

用法：
    .\.venv\Scripts\python.exe devtools\test_stats.py
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
from rbar.model import RenderSettings, StatSegment  # noqa: E402
from rbar.render import BarRenderer  # noqa: E402

W, H = 1920, 200
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{' OK ' if ok else 'FAIL'}] {name}" + (f" -> {detail}" if detail else ""), flush=True)
    if not ok:
        failures.append(name)


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setFont(QFont("Arial", 10))
    p = make_demo_project(bpm=128.0, bars=16)
    p.render.width, p.render.height = W, H
    p.theme.plate.mode = "none"
    p.render.ticks = "off"
    r = BarRenderer(p)
    rs = p.render
    tm = p.chart.timemap
    notes = sorted(n.t for n in p.chart.notes)
    mid = notes[len(notes) // 2]

    # ---------------------------------------------------------- 1) 统计口径
    info = r.stats(mid)
    check("无分段时 total_passed 等于已过音符数",
          info["total_passed"] == sum(1 for x in notes if x <= mid),
          str(info["total_passed"]))
    check("无分段时 seg_index = -1", info["seg_index"] == -1)
    check("无分段时 seg_passed 等于 total_passed", info["seg_passed"] == info["total_passed"])

    rs.stat_segments = [StatSegment(0.0, notes[4]), StatSegment(notes[4], 0.0)]
    i1 = r.stats(notes[2])
    check("第一段内 seg_index = 0", i1["seg_index"] == 0)
    check("第一段的段内已过数正确",
          i1["seg_passed"] == sum(1 for x in notes if x <= notes[2]), str(i1["seg_passed"]))
    i2 = r.stats(mid)
    check("第二段内 seg_index = 1", i2["seg_index"] == 1)
    check("第二段的段内总数正确", i2["seg_all"] == len(notes) - 4,
          f"{i2['seg_all']} vs {len(notes) - 4}")

    # ---------------------------------------------------------- 2) 显示方式
    rs.beat_counter = True
    rs.beat_counter_label = "BEAT"
    rs.beat_counter_show_phase = False
    rs.beat_counter_display = "total"
    t_total = r._count_text(mid)
    check("仅总：只出现一个数字", t_total == f"BEAT {i2['total_passed']}", t_total)

    rs.beat_counter_display = "segment"
    t_seg = r._count_text(mid)
    check("仅段：只显示段内数", t_seg == f"BEAT {i2['seg_passed']}", t_seg)

    rs.beat_counter_display = "both"
    t_both = r._count_text(mid)
    check("两者：段 // 总", t_both == f"BEAT {i2['seg_passed']} // {i2['total_passed']}", t_both)

    rs.beat_counter_show_phase = True
    t_phase = r._count_text(mid)
    check("Phase 前缀出现且序号为 2", t_phase.startswith("Phase 2"), t_phase)
    rs.beat_counter_show_phase = False
    rs.beat_counter_display = "total"

    # ---------------------------------------------------------- 3) 进度条依据
    info_probe = dict(i2)
    probe_t = mid
    rs.progress_fill = "#ff0000"
    rs.progress_bar = True
    rs.progress_border_w = 0.0
    rs.progress_w, rs.progress_h = 400.0, 40.0
    rs.progress_x, rs.progress_y = 0.5, 0.15      # 上方，避开中间的音符带

    def bar_fill_frac(mode: str) -> float:
        rs.progress_mode = mode
        img = render_frame_image(p, probe_t, W, H, 1)
        y = int(rs.progress_y * H)
        xs = [x for x in range(W)
              if (lambda c: c.alpha() > 60 and c.red() > 150 and c.green() < 120)(
                  img.pixelColor(x, y))]
        return (max(xs) - min(xs) + 1) / rs.progress_w if xs else 0.0

    expect = {
        "time": min(1.0, probe_t / info_probe["duration"]),
        "beats": info_probe["total_passed"] / max(1, info_probe["total_all"]),
        "seg_time": min(1.0, info_probe["seg_elapsed"] / info_probe["seg_span"]),
        "seg_beats": info_probe["seg_passed"] / max(1, info_probe["seg_all"]),
    }
    measured = {}
    for mode, name in (("time", "时间"), ("beats", "Beats"),
                       ("seg_time", "段内时间"), ("seg_beats", "段内 Beats")):
        measured[mode] = bar_fill_frac(mode)
        check(f"{name}依据的填充比例正确",
              abs(measured[mode] - expect[mode]) < 0.08,
              f"测得 {measured[mode]:.3f} / 期望 {expect[mode]:.3f}")
    check("四种依据的填充长度不全相同",
          len({round(v, 2) for v in measured.values()}) >= 2,
          str({k: round(v, 2) for k, v in measured.items()}))

    rs.progress_bar = False
    img = render_frame_image(p, probe_t, W, H, 1)
    y = int(rs.progress_y * H)
    has = any((lambda c: c.alpha() > 60 and c.red() > 150 and c.green() < 120)(
        img.pixelColor(x, y)) for x in range(0, W, 3))
    check("关闭进度条后不绘制", not has)
    rs.progress_bar = True

    # ---------------------------------------------------------- 4) 位置与序列化
    rs.progress_bar = False
    rs.beat_counter = True
    rs.beat_counter_size = 0.20
    rs.beat_counter_y = 0.93

    def ink_left() -> int:
        img = render_frame_image(p, probe_t, W, H, 1)
        return sum(1 for x in range(0, W // 3, 2) for yy in range(int(H * 0.80), H, 2)
                   if img.pixelColor(x, yy).alpha() > 60)

    rs.beat_counter_x = 0.05
    left_a = ink_left()
    rs.beat_counter_x = 0.95
    left_b = ink_left()
    check("计数位置可移动（靠左时左下有字，靠右时没有）", left_a > left_b + 10,
          f"{left_a} vs {left_b}")

    d = rs.to_dict()
    check("序列化包含统计分段", "stat_segments" in d and isinstance(d["stat_segments"], list))
    back = RenderSettings.from_dict(d)
    check("分段往返正确",
          len(back.stat_segments) == 2
          and abs(float(back.stat_segments[1].start_ms) - float(notes[4])) < 1e-6)
    check("进度条字段往返正确",
          abs(back.progress_w - rs.progress_w) < 1e-9
          and back.progress_mode == rs.progress_mode)
    old = RenderSettings.from_dict({"width": 1920})
    check("旧工程默认无分段、进度条关闭",
          old.stat_segments == [] and old.progress_bar is False)

    print()
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {', '.join(failures)}")
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
