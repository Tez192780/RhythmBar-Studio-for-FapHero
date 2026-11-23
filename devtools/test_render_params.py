r"""验证新增的渲染参数：判定点后流速 / 角落水印 / Beat 计数。

除了断言，还会把对比图写到 _video_analysis/ 下，方便人眼核对。

用法：
    .\.venv\Scripts\python.exe devtools\test_render_params.py
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")
# 离屏平台默认不带字体（families = 0），文字会画不出来；指向系统字体目录
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR",
                          os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rbar.demo import make_demo_project  # noqa: E402
from rbar.exporter import render_frame_image  # noqa: E402
from rbar.render import BarRenderer  # noqa: E402

OUT = os.path.join(os.path.dirname(ROOT), "_video_analysis")
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{' OK ' if ok else 'FAIL'}] {name}" + (f" -> {detail}" if detail else ""), flush=True)
    if not ok:
        failures.append(name)


def text_pixels(img, x0, x1, y0, y1) -> int:
    """数“接近纯白且不透明”的像素——水印/计数是白字，底板是半透明的，
    所以不能只看 alpha（那块底板本身就让全图都有 alpha）。"""
    n = 0
    for x in range(x0, x1, 2):
        for y in range(y0, y1, 2):
            c = img.pixelColor(x, y)
            if c.alpha() > 150 and min(c.red(), c.green(), c.blue()) > 175:
                n += 1
    return n


def main() -> int:
    app = QApplication(sys.argv[:1])
    # 离屏平台默认字体可能解析到符号字体，显式指定一个常规无衬线字体
    app.setFont(QFont("Arial", 10))
    p = make_demo_project()
    w, h = p.render.width, p.render.height
    t = 4000.0

    # ---------------------------------------------------- 1. 判定点后流速
    r = BarRenderer(p)
    jx = r.judge_x(w)
    n = p.chart.notes[0]
    p.render.post_speed = 1.0
    x1 = r.note_x(n.t - 200.0, t, w, 1)
    p.render.post_speed = 4.0
    x4 = r.note_x(n.t - 200.0, t, w, 1)
    d1, d4 = abs(x1 - jx), abs(x4 - jx)
    check("post_speed=1 时过判定点的位置不变", d1 > 0, f"距离 {d1:.1f}px")
    check("post_speed=4 时离场距离是 4 倍", abs(d4 / d1 - 4.0) < 0.05,
          f"{d1:.1f}px -> {d4:.1f}px（{d4 / d1:.2f}x）")
    # 未过判定点的音符不应受影响（挑一个真正在未来的音符）
    future = max(p.chart.notes, key=lambda x: x.t)
    p.render.post_speed = 4.0
    xa = r.note_x(future.t, t, w, 1)
    p.render.post_speed = 1.0
    xb = r.note_x(future.t, t, w, 1)
    check("未过判定点的音符速度不受影响", abs(xa - xb) < 1e-6, f"{xa:.2f} vs {xb:.2f}")
    # 刻度保持匀速
    p.render.post_speed = 4.0
    check("刻度线不跟着加速（时间尺保持匀速）",
          abs(r.note_x(n.t - 200.0, t, w, 1, post=False) - x1) < 1e-6)

    # ---------------------------------------------------- 基本图 / 带水印计数
    p.render.post_speed = 1.0
    p.render.watermark_text = ""
    p.render.beat_counter = False
    base = render_frame_image(p, t, w, h, 1)

    p.render.watermark_text = "Fesutora"
    p.render.watermark_pos = "bottom_left"
    p.render.watermark_size = 0.20
    p.render.watermark_alpha = 0.75
    p.render.beat_counter = True
    p.render.beat_counter_pos = "bottom_right"
    p.render.beat_counter_size = 0.20
    decorated = render_frame_image(p, t, w, h, 1)

    left_base = text_pixels(base, 0, w // 3, int(h * 0.7), h)
    left_new = text_pixels(decorated, 0, w // 3, int(h * 0.7), h)
    right_base = text_pixels(base, int(w * 2 / 3), w, int(h * 0.7), h)
    right_new = text_pixels(decorated, int(w * 2 / 3), w, int(h * 0.7), h)
    check("左下角出现了水印像素", left_new > left_base + 30, f"{left_base} -> {left_new}")
    check("右下角出现了计数像素", right_new > right_base + 20, f"{right_base} -> {right_new}")
    check("中部区域没有多出东西",
          text_pixels(base, int(w * 0.45), int(w * 0.55), 0, int(h * 0.6))
          == text_pixels(decorated, int(w * 0.45), int(w * 0.55), 0, int(h * 0.6)))

    # 关掉之后应当与 base 完全一致
    p.render.watermark_text = ""
    p.render.beat_counter = False
    again = render_frame_image(p, t, w, h, 1)
    same = all(again.pixelColor(x, y) == base.pixelColor(x, y)
               for x in range(0, w, 17) for y in range(0, h, 7))
    check("水印与计数都关闭时画面与原来一致", same)

    # ---------------------------------------------------- 3. 计数语义
    p.render.beat_counter = True
    p.render.beat_counter_mode = "notes"
    rr = BarRenderer(p)
    count_t = rr.beat_count(t)
    expect = sum(1 for nn in p.chart.notes if nn.t <= t)
    check("notes 模式计数=已命中音符数", count_t == expect, f"{count_t} vs {expect}")
    p.render.beat_counter_mode = "beats"
    beat0 = rr.beat_count(t)
    tm = p.chart.timemap
    check("beats 模式计数=按 BPM 的拍号", beat0 == int(tm.beat(t)),
          f"{beat0} vs {int(tm.beat(t))}")
    p.render.beat_counter_mode = "notes"

    # ---------------------------------------------------- 存档兼容
    d = p.render.to_dict()
    check("序列化包含新字段",
          all(k in d for k in ("post_speed", "watermark_text", "beat_counter")),
          ", ".join(k for k in ("post_speed", "watermark_text", "beat_counter") if k in d))
    from rbar.model import RenderSettings

    back = RenderSettings.from_dict(d)
    check("往返序列化保持数值", back.post_speed == p.render.post_speed
          and back.watermark_text == p.render.watermark_text)

    # ---------------------------------------------------- 输出对比图
    os.makedirs(OUT, exist_ok=True)
    p.render.post_speed = 3.8
    p.render.watermark_text = "Fesutora"
    p.render.beat_counter = True
    p.render.beat_counter_size = 0.18
    shot = render_frame_image(p, t, w, h, 2)
    shot_path = os.path.join(OUT, "new_params_preview.png")
    shot.save(shot_path)
    print(f"\n预览图已写出: {shot_path}")

    print()
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {', '.join(failures)}")
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
