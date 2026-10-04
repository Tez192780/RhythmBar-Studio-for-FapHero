"""工程数据模型：音符 / 谱面 / 渲染设置 / 导出设置 / 工程 / 撤销栈。"""

from __future__ import annotations

import copy
import json
import os
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, asdict

from .theme import Theme
from .timing import BpmSegment, TimeMap

PROJECT_EXT = ".rbarproj"


# --------------------------------------------------------------------- 音符
@dataclass(eq=False)          # 用对象身份做相等/哈希：选中集合靠它
class Note:
    t: float                    # 毫秒
    type: str = "cyan"
    lane: int = 0               # 时间轴行号（= 类型行），渲染时决定纵向位置
    dur: float = 0.0            # 长条时长（毫秒），0 = 单点
    side: int = 0               # 0 自动 / -1 左 / +1 右（双侧汇聚模式用）

    @property
    def end(self) -> float:
        return self.t + self.dur

    def to_dict(self) -> dict:
        d = {"t": round(float(self.t), 4), "type": self.type, "lane": int(self.lane)}
        if self.dur:
            d["dur"] = round(float(self.dur), 4)
        if self.side:
            d["side"] = int(self.side)
        return d

    @staticmethod
    def from_dict(d: dict) -> "Note":
        return Note(
            float(d.get("t", 0.0)),
            str(d.get("type", "cyan")),
            int(d.get("lane", 0)),
            float(d.get("dur", 0.0)),
            int(d.get("side", 0)),
        )


# --------------------------------------------------------------------- 谱面
class Chart:
    def __init__(self, timemap: TimeMap | None = None):
        self.timemap = timemap or TimeMap()
        self.notes: list[Note] = []
        self._times: list[float] = []
        self.max_dur: float = 0.0        # 缓存：渲染时判断可见区间用

    # ------------------------------------------------------------- 增删改查
    def sort(self) -> None:
        self.notes.sort(key=lambda n: (n.t, n.lane))
        self._times = [n.t for n in self.notes]
        md = 0.0
        for n in self.notes:
            if n.dur > md:
                md = n.dur
        self.max_dur = md

    def add(self, note: Note) -> Note:
        self.notes.append(note)
        self.sort()
        return note

    def add_many(self, notes: list[Note]) -> None:
        self.notes.extend(notes)
        self.sort()

    def remove_many(self, notes: list[Note] | set[Note]) -> int:
        remove = set(id(n) for n in notes)
        before = len(self.notes)
        self.notes = [n for n in self.notes if id(n) not in remove]
        self.sort()
        return before - len(self.notes)

    def index_of(self, note: Note) -> int:
        for i, n in enumerate(self.notes):
            if n is note:
                return i
        return -1

    def in_range(self, t0: float, t1: float) -> list[Note]:
        """返回时间落在 [t0, t1] 内的音符（按时间排序）。"""
        i0 = bisect_left(self._times, t0)
        i1 = bisect_right(self._times, t1)
        return self.notes[i0:i1]

    def near(self, t: float, window_ms: float) -> list[Note]:
        return self.in_range(t - window_ms, t + window_ms)

    def find_at(self, t: float, lane: int, tol_ms: float) -> Note | None:
        best, best_d = None, tol_ms
        for n in self.near(t, tol_ms):
            if n.lane != lane:
                continue
            d = abs(n.t - t)
            if d <= best_d:
                best, best_d = n, d
        return best

    def bounds(self) -> tuple[float, float]:
        if not self.notes:
            return (self.timemap.start_ms(), self.timemap.start_ms() + 4000.0)
        lo = min(n.t for n in self.notes)
        hi = max(n.end for n in self.notes)
        return (lo, hi)

    def __len__(self) -> int:
        return len(self.notes)

    # --------------------------------------------------------------- BPM
    @property
    def segments(self) -> list[BpmSegment]:
        return self.timemap.segments

    @property
    def offset_ms(self) -> float:
        return self.timemap.offset_ms

    def set_offset(self, offset_ms: float) -> None:
        self.timemap.set_segments(list(self.timemap.segments), offset_ms)

    def set_segments(self, segs: list[BpmSegment], offset_ms: float | None = None) -> None:
        self.timemap.set_segments(segs, offset_ms)

    def add_segment(self, time_ms: float, bpm: float) -> None:
        segs = [s for s in self.timemap.segments if abs(s.time_ms - time_ms) > 1e-6]
        segs.append(BpmSegment(time_ms, bpm))
        self.timemap.set_segments(segs, self.timemap.offset_ms)

    def remove_segment(self, index: int) -> None:
        segs = list(self.timemap.segments)
        if 0 <= index < len(segs) and len(segs) > 1 and index > 0:
            segs.pop(index)
            self.timemap.set_segments(segs, self.timemap.offset_ms)

    # ------------------------------------------------------------- 序列化
    def to_dict(self) -> dict:
        return {"timemap": self.timemap.to_dict(), "notes": [n.to_dict() for n in self.notes]}

    @staticmethod
    def from_dict(d: dict) -> "Chart":
        c = Chart(TimeMap.from_dict(d.get("timemap", {})))
        c.notes = [Note.from_dict(x) for x in d.get("notes", [])]
        c.sort()
        return c

    def clone(self) -> "Chart":
        return Chart.from_dict(copy.deepcopy(self.to_dict()))


# ----------------------------------------------------------------- 渲染设置
@dataclass
class RenderSettings:
    width: int = 1920
    height: int = 120
    flow: str = "rtl"            # rtl | ltr | converge
    approach_s: float = 1.0      # 音符从边缘走到判定线的时间
    note_size: float = 0.46      # 音符直径 / 条高
    vspread: float = 0.0         # 多行音符纵向铺开程度 0..0.8
    ticks: str = "off"           # off | beat | bar
    tick_alpha: float = 0.22
    judge_ratio: float = 0.5     # 判定点横向位置（0.5 = 正中）
    hide_before: float = 0.0     # 提前隐藏（毫秒，0=不隐藏，走到底自然出画）

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "RenderSettings":
        base = RenderSettings()
        for k, v in (d or {}).items():
            if hasattr(base, k):
                setattr(base, k, v)
        return base


# ----------------------------------------------------------------- 导出设置
FORMATS = {
    "mov_prores": "MOV · ProRes 4444（带透明通道，PR/AE/达芬奇最稳）",
    "mov_qtrle": "MOV · QuickTime 动画 RLE（带透明通道，剪映友好、体积小）",
    "webm_vp9": "WebM · VP9 alpha（带透明通道，体积最小，浏览器/部分软件）",
    "webm_vp8": "WebM · VP8 alpha（兼容老一点的解码器）",
    "png_seq": "PNG 序列（带透明通道，最保险，体积大）",
    "mp4_black": "MP4 · 黑底（无透明，剪辑里用「滤色/变亮」混合模式去黑）",
}


@dataclass
class ExportSettings:
    fmt: str = "mov_prores"
    fps: int = 60
    width: int = 1920
    height: int = 120
    supersample: int = 2         # 1/2/3 倍超采样抗锯齿
    quality: str = "high"        # fast | balanced | high
    range_mode: str = "song"     # song（整首）| notes（最后音符后留白）| custom
    t_start: float = 0.0         # 毫秒
    t_end: float = 0.0           # 毫秒（0 = 自动）
    padding_ms: float = 1200.0
    background: str = "transparent"   # transparent | black | white | color
    bg_color: str = "#000000"
    bar_valign: str = "center"        # 整帧导出时条带的垂直位置 top|center|bottom
    output: str = ""
    open_after: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ExportSettings":
        base = ExportSettings()
        for k, v in (d or {}).items():
            if hasattr(base, k):
                setattr(base, k, v)
        return base


# --------------------------------------------------------------------- 工程
class Project:
    def __init__(self):
        self.chart = Chart()
        self.theme = Theme()
        self.render = RenderSettings()
        self.export = ExportSettings()
        self.audio_path: str = ""
        self.video_path: str = ""      # 参考视频（对照用）
        self.audio_duration_ms: float = 0.0
        self.path: str = ""

    # ------------------------------------------------------------- 序列化
    def to_dict(self) -> dict:
        return {
            "app": "rbar",
            "version": 1,
            "audio_path": self.audio_path,
            "video_path": self.video_path,
            "chart": self.chart.to_dict(),
            "theme": self.theme.to_dict(),
            "render": self.render.to_dict(),
            "export": self.export.to_dict(),
        }

    def load_dict(self, d: dict) -> None:
        """就地更新（保持 chart 对象身份不变，撤销/重做后旧引用依然有效）。"""
        chart = Chart.from_dict(d.get("chart", {}))
        self.chart.timemap = chart.timemap
        self.chart.notes = chart.notes
        self.chart.sort()
        self.theme = Theme.from_dict(d.get("theme", {}))
        self.render = RenderSettings.from_dict(d.get("render", {}))
        self.export = ExportSettings.from_dict(d.get("export", {}))
        self.audio_path = str(d.get("audio_path", ""))
        self.video_path = str(d.get("video_path", ""))

    def save(self, path: str) -> None:
        path = os.path.abspath(path)
        if not path.lower().endswith(PROJECT_EXT):
            path += PROJECT_EXT
        d = self.to_dict()
        base = os.path.dirname(path)
        d["audio_path"] = os.path.relpath(self.audio_path, base) if self.audio_path else ""
        d["video_path"] = os.path.relpath(self.video_path, base) if self.video_path else ""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        self.path = path

    @staticmethod
    def load(path: str) -> "Project":
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        p = Project()
        p.load_dict(d)
        base = os.path.dirname(os.path.abspath(path))
        for key in ("audio_path", "video_path"):
            v = d.get(key, "")
            if v and not os.path.isabs(v):
                v = os.path.normpath(os.path.join(base, v))
            setattr(p, key, v)
        p.path = os.path.abspath(path)
        return p


# ------------------------------------------------------------------- 撤销栈
class UndoStack:
    """整工程快照式撤销（谱面很小，快照最简单可靠）。"""

    def __init__(self, limit: int = 150):
        self.limit = limit
        self._undo: list[tuple[str, dict, dict]] = []
        self._redo: list[tuple[str, dict, dict]] = []

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    def push(self, label: str, before: dict, after: dict) -> None:
        if before == after:
            return
        self._undo.append((label, before, after))
        if len(self._undo) > self.limit:
            self._undo.pop(0)
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str:
        return self._undo[-1][0] if self._undo else ""

    def redo_label(self) -> str:
        return self._redo[-1][0] if self._redo else ""

    def undo(self) -> tuple[str, dict] | None:
        if not self._undo:
            return None
        item = self._undo.pop()
        self._redo.append(item)
        return (item[0], copy.deepcopy(item[1]))

    def redo(self) -> tuple[str, dict] | None:
        if not self._redo:
            return None
        item = self._redo.pop()
        self._undo.append(item)
        return (item[0], copy.deepcopy(item[2]))
