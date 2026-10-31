"""工程数据模型：音符 / 谱面 / 渲染设置 / 导出设置 / 工程 / 撤销栈。"""

from __future__ import annotations

import copy
import json
import os
import uuid
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, asdict, field

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
    clip_id: str = ""           # 所属音频片段；空 = 旧工程/未绑定

    @property
    def end(self) -> float:
        return self.t + self.dur

    def to_dict(self) -> dict:
        d = {"t": round(float(self.t), 4), "type": self.type, "lane": int(self.lane)}
        if self.dur:
            d["dur"] = round(float(self.dur), 4)
        if self.side:
            d["side"] = int(self.side)
        if self.clip_id:
            d["clip_id"] = self.clip_id
        return d

    @staticmethod
    def from_dict(d: dict) -> "Note":
        return Note(
            float(d.get("t", 0.0)),
            str(d.get("type", "cyan")),
            int(d.get("lane", 0)),
            float(d.get("dur", 0.0)),
            int(d.get("side", 0)),
            str(d.get("clip_id", "") or ""),
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
    "mov_qtrle": "MOV · QuickTime 动画 RLE（透明，剪映友好，体积中等，推荐）",
    "webm_vp9": "WebM · VP9 alpha（透明，体积最小，兼容性一般）",
    "mov_prores": "MOV · ProRes 4444（透明，PR/AE/达芬奇最稳，体积很大）",
    "webm_vp8": "WebM · VP8 alpha（兼容老一点的解码器）",
    "png_seq": "PNG 序列（透明，最保险，体积很大）",
    "mp4_black": "MP4 · 黑底（无透明，剪辑里用「滤色/变亮」混合模式去黑）",
}


@dataclass
class ExportSettings:
    fmt: str = "mov_qtrle"
    fps: int = 30
    width: int = 1920
    height: int = 120
    supersample: int = 2         # 1/2/3 倍超采样抗锯齿
    quality: str = "balanced"    # fast | balanced | high
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
@dataclass(eq=False)
class MediaClip:
    """一段独立媒体（视频或音频），可以单独移动、切分。

    音频片段的 timemap 使用“源文件时间”：0 = 源文件开头；
    画到总时间轴时用 offset_ms + (source - src_start_ms) 换算。
    """

    path: str = ""
    kind: str = "audio"          # audio | video
    offset_ms: float = 0.0       # 在时间轴上的起点
    src_start_ms: float = 0.0    # 从源文件的哪一刻开始取
    duration_ms: float = 0.0     # 取多长（0 = 取到源结尾）
    name: str = ""
    has_audio: bool = True       # 仅视频用于兼容旧工程；新视频片段为 False
    id: str = ""
    link_id: str = ""            # 视频 + 分离音频用同一个链接组
    timemap: TimeMap = field(default_factory=TimeMap)

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex[:10]

    @property
    def label(self) -> str:
        return self.name or os.path.basename(self.path)

    def end_ms(self) -> float:
        return self.offset_ms + self.duration_ms

    def source_end_ms(self) -> float:
        return self.src_start_ms + self.duration_ms

    def contains_time(self, ms: float, pad: float = 0.0) -> bool:
        return (self.offset_ms - pad <= float(ms)
                <= self.offset_ms + self.duration_ms + pad)

    def source_to_timeline(self, source_ms: float) -> float:
        return self.offset_ms + (float(source_ms) - self.src_start_ms)

    def timeline_to_source(self, timeline_ms: float) -> float:
        return self.src_start_ms + (float(timeline_ms) - self.offset_ms)

    def linked_clips(self, clips: list["MediaClip"]) -> list["MediaClip"]:
        if not self.link_id:
            return [self]
        return [c for c in clips if c is self or (c.link_id and c.link_id == self.link_id)]

    def clone(self) -> "MediaClip":
        d = self.to_dict()
        d["id"] = uuid.uuid4().hex[:10]
        return MediaClip.from_dict(d)

    def to_dict(self) -> dict:
        return {
            "path": self.path, "kind": self.kind, "offset_ms": round(self.offset_ms, 3),
            "src_start_ms": round(self.src_start_ms, 3),
            "duration_ms": round(self.duration_ms, 3),
            "name": self.name, "has_audio": bool(self.has_audio),
            "id": self.id, "link_id": self.link_id,
            "timemap": self.timemap.to_dict(),
        }

    @staticmethod
    def from_dict(d: dict) -> "MediaClip":
        c = MediaClip(path=str(d.get("path", "") or ""),
                      kind=str(d.get("kind", "audio") or "audio"),
                      offset_ms=float(d.get("offset_ms", 0.0) or 0.0),
                      src_start_ms=float(d.get("src_start_ms", 0.0) or 0.0),
                      duration_ms=float(d.get("duration_ms", 0.0) or 0.0),
                      name=str(d.get("name", "") or ""),
                      has_audio=bool(d.get("has_audio", True)),
                      id=str(d.get("id", "") or ""),
                      link_id=str(d.get("link_id", "") or ""))
        c.timemap = TimeMap.from_dict(d.get("timemap", {}))
        return c


class Project:
    def __init__(self):
        self.chart = Chart()
        self.theme = Theme()
        self.render = RenderSettings()
        self.export = ExportSettings()
        self.audio_path: str = ""      # 兼容旧工程：= 第一个音频片段
        self.video_path: str = ""      # 兼容旧工程：= 第一个视频片段
        self.clips: list[MediaClip] = []
        self.audio_duration_ms: float = 0.0
        self.path: str = ""

    # --------------------------------------------------------- 片段辅助
    @property
    def audio_clips(self) -> list[MediaClip]:
        return sorted((c for c in self.clips if c.kind == "audio"),
                      key=lambda c: (c.offset_ms, c.id))

    @property
    def video_clips(self) -> list[MediaClip]:
        return sorted((c for c in self.clips if c.kind == "video"),
                      key=lambda c: (c.offset_ms, c.id))

    def clip_by_id(self, clip_id: str) -> MediaClip | None:
        for c in self.clips:
            if c.id == clip_id:
                return c
        return None

    def clip_at(self, ms: float, kind: str = "audio") -> MediaClip | None:
        for c in self.clips:
            if c.kind == kind and c.contains_time(ms):
                return c
        return None

    def audio_clip_at(self, ms: float) -> MediaClip | None:
        return self.clip_at(ms, "audio")

    def video_clip_at(self, ms: float) -> MediaClip | None:
        return self.clip_at(ms, "video")

    def nearest_audio_clip(self, ms: float) -> MediaClip | None:
        clips = self.audio_clips
        if not clips:
            return None
        return min(clips, key=lambda c: min(abs(ms - c.offset_ms),
                                           abs(ms - c.end_ms())))

    def timemap_at(self, ms: float) -> TimeMap:
        c = self.audio_clip_at(ms)
        return c.timemap if c is not None else self.chart.timemap

    def linked_clips(self, clip: MediaClip) -> list[MediaClip]:
        return clip.linked_clips(self.clips)

    def linked_group(self, clip: MediaClip) -> list[MediaClip]:
        return clip.linked_clips(self.clips)

    def notes_of_clip(self, clip_id: str) -> list[Note]:
        return [n for n in self.chart.notes if n.clip_id == clip_id]

    def notes_in_clip_range(self, clip: MediaClip) -> list[Note]:
        return [n for n in self.chart.notes
                if n.clip_id == clip.id and clip.contains_time(n.t, 0.6)]

    def nearest_note_clip(self, ms: float) -> MediaClip | None:
        """旧工程音符归属：优先包含它的片段，否则最近的音频片段。"""
        c = self.audio_clip_at(ms)
        return c or self.nearest_audio_clip(ms)

    def audio_end_ms(self) -> float:
        return max((c.end_ms() for c in self.audio_clips), default=0.0)

    def video_end_ms(self) -> float:
        return max((c.end_ms() for c in self.video_clips), default=0.0)

    def media_end_ms(self) -> float:
        return max(self.audio_end_ms(), self.video_end_ms(), 0.0)

    # ------------------------------------------------------------- 序列化
    def to_dict(self) -> dict:
        return {
            "app": "rbar",
            "version": 3,
            "audio_path": self.audio_path,
            "video_path": self.video_path,
            "clips": [c.to_dict() for c in self.clips],
            "chart": self.chart.to_dict(),
            "theme": self.theme.to_dict(),
            "render": self.render.to_dict(),
            "export": self.export.to_dict(),
        }

    def load_dict(self, d: dict) -> None:
        """就地更新（保持 chart 对象身份不变，撤销/重做后旧引用依然有效）。"""
        old_version = int(d.get("version", 1) or 1)
        chart = Chart.from_dict(d.get("chart", {}))
        global_tm = chart.timemap
        self.chart.timemap = chart.timemap
        self.chart.notes = chart.notes
        self.chart.sort()
        self.theme = Theme.from_dict(d.get("theme", {}))
        self.render = RenderSettings.from_dict(d.get("render", {}))
        self.export = ExportSettings.from_dict(d.get("export", {}))
        self.audio_path = str(d.get("audio_path", ""))
        self.video_path = str(d.get("video_path", ""))
        raw_clips = d.get("clips") or []
        self.clips = [MediaClip.from_dict(x) for x in raw_clips]
        if not self.clips:
            # v1 工程只有 audio_path / video_path
            if self.audio_path:
                self.clips.append(MediaClip(path=self.audio_path, kind="audio",
                                            name=os.path.basename(self.audio_path),
                                            has_audio=True))
            if self.video_path:
                self.clips.append(MediaClip(path=self.video_path, kind="video",
                                            name=os.path.basename(self.video_path),
                                            has_audio=False))

        # 旧版视频+音频混在一起：拆成视频轨与音频轨，并建立链接
        for c in list(self.clips):
            if old_version < 3 and c.kind == "video" and c.has_audio:
                c.has_audio = False
                link = c.link_id or uuid.uuid4().hex[:10]
                c.link_id = link
                ac = MediaClip(path=c.path, kind="audio", offset_ms=c.offset_ms,
                               src_start_ms=c.src_start_ms, duration_ms=c.duration_ms,
                               name=c.name, has_audio=True, link_id=link)
                self.clips.append(ac)

        # 旧工程没有片段级 BPM：把全局 BPM 按源文件时间复制给每个音频片段
        if old_version < 3:
            for c in self.clips:
                if c.kind != "audio":
                    continue
                off = c.src_start_ms + (global_tm.offset_ms - c.offset_ms)
                segs = [BpmSegment(c.src_start_ms + (s.time_ms - c.offset_ms), s.bpm)
                        for s in global_tm.segments]
                c.timemap = TimeMap(segs, off)
        if not any(c.kind == "audio" and c.timemap for c in self.clips):
            # 旧工程没有音频时仍保留全局 BPM 供界面兜底
            pass

        # 旧音符没有归属：按包含/最近的音频片段补 clip_id
        for n in self.chart.notes:
            if not n.clip_id:
                c = self.nearest_note_clip(n.t)
                if c is not None:
                    n.clip_id = c.id

        # 兼容字段
        acs = self.audio_clips
        vcs = self.video_clips
        if acs and not self.audio_path:
            self.audio_path = acs[0].path
        if vcs and not self.video_path:
            self.video_path = vcs[0].path

    @staticmethod
    def _rel(path: str, base: str) -> str:
        if not path:
            return ""
        try:
            return os.path.relpath(path, base)
        except ValueError:
            return path

    def save(self, path: str) -> None:
        path = os.path.abspath(path)
        if not path.lower().endswith(PROJECT_EXT):
            path += PROJECT_EXT
        d = self.to_dict()
        base = os.path.dirname(path)
        d["audio_path"] = self._rel(self.audio_path, base)
        d["video_path"] = self._rel(self.video_path, base)
        for cd, raw in zip(d["clips"], self.clips):
            cd["path"] = self._rel(raw.path, base)
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
        for c in p.clips:
            if c.path and not os.path.isabs(c.path):
                c.path = os.path.normpath(os.path.join(base, c.path))
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
