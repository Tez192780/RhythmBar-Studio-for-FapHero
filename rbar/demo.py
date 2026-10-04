"""示例工程：没加载音频时也能立刻看到效果 / 试用编辑器。"""

from __future__ import annotations

from .model import Note, Project
from .timing import BpmSegment


def make_demo_project(bpm: float = 128.0, bars: int = 16) -> Project:
    p = Project()
    p.chart.set_segments([BpmSegment(0.0, bpm), BpmSegment(0.0, bpm)], 0.0)
    tm = p.chart.timemap
    notes: list[Note] = []
    beat = 1.0 / 4.0

    def at(b: float) -> float:
        return tm.ms(b)

    types = ["cyan", "magenta"]
    for bar in range(bars):
        base = bar * 4
        # 每小节：1、3 拍青，2、4 拍品红
        for i, b in enumerate((0, 1, 2, 3)):
            notes.append(Note(at(base + b), types[i % 2], i % 2))
        # 八分音符加花
        if bar % 4 == 3:
            for b in (0.5, 1.5, 2.5, 3.5):
                notes.append(Note(at(base + b), "gray", 2))
        # 十六分音符跑动
        if bar % 8 == 7:
            for i in range(8):
                notes.append(Note(at(base + 3 + i * beat), "cyan", 0))
    # 一个长条
    notes.append(Note(at(4.0), "cyan", 0, dur=tm.ms(6.0) - tm.ms(4.0)))
    # 变速段：第 12 小节起提速到 160
    p.chart.set_segments([BpmSegment(0.0, bpm), BpmSegment(at(bars * 0.75), 160.0)], 0.0)

    p.chart.add_many(notes)
    p.render.width, p.render.height = 1920, 120
    p.export.width, p.export.height = 1920, 120
    return p
