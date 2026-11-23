r"""判定区分段：分层选择、交叉淡化、配置导入导出。

用法：
    .\.venv\Scripts\python.exe devtools\test_segments.py
"""

from __future__ import annotations

import copy
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR",
                          os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rbar import segments as segments_io  # noqa: E402
from rbar.demo import make_demo_project  # noqa: E402
from rbar.exporter import render_frame_image  # noqa: E402
from rbar.render import BarRenderer  # noqa: E402
from rbar.theme import FadeSettings, JudgeSegment, JudgeStyle, Theme  # noqa: E402

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
    return p, BarRenderer(p)


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setFont(QFont("Arial", 10))
    p, r = build()
    th = p.theme

    dumb = JudgeStyle(mode="dumbbell")
    dia = JudgeStyle(mode="diamond")
    th.segments = [JudgeSegment(0.0, 1000.0, copy.deepcopy(dumb)),
                   JudgeSegment(1000.0, 0.0, copy.deepcopy(dia))]
    th.fade = FadeSettings(enabled=True, ms=400.0, scope="judge")

    # ---------------------------------------------------- 1) 选层
    segs = r.judge_segments()
    check("段落按开始时间排序", [s.start_ms for s in segs] == [0.0, 1000.0])

    layers = r.judge_layers(400.0)
    check("第一段内只有一层", len(layers) == 1 and abs(layers[0][1] - 1.0) < 1e-9,
          f"{len(layers)} 层")
    check("第一段用的是哑铃", str(getattr(layers[0][0], "mode")) == "dumbbell")

    layers2 = r.judge_layers(1500.0)
    check("第二段内只有一层且是菱形",
          len(layers2) == 1 and str(getattr(layers2[0][0], "mode")) == "diamond")

    # ---------------------------------------------------- 2) 交叉淡化
    mid = r.judge_layers(1000.0)
    check("边界处有两层（交叉淡化）", len(mid) == 2, f"{len(mid)} 层")
    check("边界处两层不透明度各半",
          abs(mid[0][1] - 0.5) < 0.05 and abs(mid[1][1] - 0.5) < 0.05,
          f"{mid[0][1]:.2f} / {mid[1][1]:.2f}")
    check("边界处左侧是哑铃、右侧是菱形",
          str(getattr(mid[0][0], "mode")) == "dumbbell"
          and str(getattr(mid[1][0], "mode")) == "diamond")

    before = r.judge_layers(1000.0 - 300.0)
    check("过渡范围之外恢复单层", len(before) == 1)

    check("过渡中点的淡出曲线为 0（轴线会完全淡出）", abs(r.fade_dip(1000.0)) < 0.02,
          f"{r.fade_dip(1000.0):.3f}")
    check("过渡之外不淡出", abs(r.fade_dip(400.0) - 1.0) < 1e-9)

    th.fade.enabled = False
    check("关掉淡入淡出后边界处只有一层", len(r.judge_layers(1000.0)) == 1)
    th.fade.enabled = True

    # ---------------------------------------------------- 3) 渲染差异
    def lit_rows(img) -> int:
        jx = int(W * 0.33)
        n = 0
        for y in range(0, H, 2):
            if sum(1 for x in range(jx - 30, jx + 31, 2)
                   if img.pixelColor(x, y).alpha() > 60) >= 5:
                n += 1
        return n

    rows_a = lit_rows(render_frame_image(p, 400.0, W, H, 1))
    rows_b = lit_rows(render_frame_image(p, 1500.0, W, H, 1))
    check("两段画出来的判定区确实不同", rows_a != rows_b, f"哑铃段 {rows_a} 行 / 菱形段 {rows_b} 行")

    # 过渡中点：两层都画 → 判定区像素介于两者
    rows_mid = lit_rows(render_frame_image(p, 1000.0, W, H, 1))
    check("过渡帧的判定区像素数与两端都不同", rows_mid not in (rows_a, rows_b),
          f"mid={rows_mid}")

    # ---------------------------------------------------- 4) 配置读写
    tmp = tempfile.mkdtemp(prefix="rbar_seg_")
    try:
        path = segments_io.save(th, os.path.join(tmp, "cfg"))
        check("导出文件已生成", os.path.isfile(path), path)

        other = Theme()
        n = segments_io.load(other, path)
        check("读回的段数一致", n == 2, str(n))
        check("读回的段落时间一致",
              [(s.start_ms, s.end_ms) for s in other.segments] == [(0.0, 1000.0), (1000.0, 0.0)])
        check("读回的样式也一致",
              str(getattr(other.segments[0].style, "mode")) == "dumbbell"
              and str(getattr(other.segments[1].style, "mode")) == "diamond")
        check("读回的淡入淡出设置一致",
              other.fade.enabled and abs(other.fade.ms - 400.0) < 1e-9
              and other.fade.scope == "judge")

        # 非法文件
        bad = os.path.join(tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write('{"app": "something-else"}')
        try:
            segments_io.load(Theme(), bad)
            check("非法配置文件会被拒绝", False)
        except ValueError:
            check("非法配置文件会被拒绝", True)
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)

    # ---------------------------------------------------- 5) 工程存档
    d = th.to_dict()
    check("主题序列化包含 segments/fade", "segments" in d and "fade" in d)
    back = Theme.from_dict(d)
    check("主题往返后段落仍正确",
          len(back.segments) == 2 and abs(back.segments[1].start_ms - 1000.0) < 1e-9)

    print()
    if failures:
        print(f"结果: {len(failures)} 项失败 -> {', '.join(failures)}")
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
