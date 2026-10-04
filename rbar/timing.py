"""时间轴 / BPM 映射。

约定：
* 时间统一使用 **毫秒 float**（音频对齐的绝对时间），音符存 ms 而不是 beat，
  这样改 BPM 只影响网格与吸附，不会把已经对好的音符挪走。
* 拍号（beat）是一根随 BPM 分段变化的虚拟标尺：
  beat 0 落在 ``offset_ms``，之后每个 BPM 段内部匀速。
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

MIN_BPM = 1.0
MAX_BPM = 1200.0


@dataclass
class BpmSegment:
    """一个 BPM 段的起点（毫秒）与该段的 BPM。"""

    time_ms: float = 0.0
    bpm: float = 120.0

    def to_dict(self) -> dict:
        return {"t": round(float(self.time_ms), 6), "bpm": round(float(self.bpm), 6)}

    @staticmethod
    def from_dict(d: dict) -> "BpmSegment":
        return BpmSegment(float(d.get("t", 0.0)), float(d.get("bpm", 120.0)))


class TimeMap:
    """毫秒 <-> 拍 的双向映射，支持任意段数的 BPM 变速。"""

    def __init__(self, segments: list[BpmSegment] | None = None, offset_ms: float = 0.0):
        self.offset_ms = float(offset_ms)
        segs = list(segments) if segments else [BpmSegment(self.offset_ms, 120.0)]
        self.set_segments(segs, self.offset_ms)

    # ------------------------------------------------------------------ 构建
    def set_segments(self, segments: list[BpmSegment], offset_ms: float | None = None) -> None:
        if offset_ms is not None:
            self.offset_ms = float(offset_ms)
        cleaned: list[BpmSegment] = []
        for s in sorted(segments, key=lambda s: float(s.time_ms)):
            bpm = min(MAX_BPM, max(MIN_BPM, float(s.bpm)))
            t = float(s.time_ms)
            if cleaned and t - cleaned[-1].time_ms < 1e-6:
                # 同一时刻重复：后写入的覆盖
                cleaned[-1] = BpmSegment(t, bpm)
                continue
            if not cleaned and t > self.offset_ms + 1e-6:
                # 第一段必须在 offset 之前/等于 offset，否则前面补一段同 BPM 的
                cleaned.append(BpmSegment(self.offset_ms, bpm))
            cleaned.append(BpmSegment(t, bpm))
        if not cleaned:
            cleaned = [BpmSegment(self.offset_ms, 120.0)]
        cleaned[0].time_ms = self.offset_ms
        self.segments = cleaned

        self._t = [s.time_ms for s in cleaned]
        self._bpm = [s.bpm for s in cleaned]
        self._ms_per_beat = [60000.0 / b for b in self._bpm]
        self._beat = [0.0] * len(cleaned)
        for i in range(1, len(cleaned)):
            self._beat[i] = self._beat[i - 1] + (self._t[i] - self._t[i - 1]) / self._ms_per_beat[i - 1]

    # ------------------------------------------------------------------ 查询
    def _index_at_ms(self, t: float) -> int:
        i = bisect_right(self._t, float(t)) - 1
        return 0 if i < 0 else i

    def beat(self, t_ms: float) -> float:
        """毫秒 -> 拍。"""
        if not self._t:
            return 0.0
        i = self._index_at_ms(t_ms)
        return self._beat[i] + (float(t_ms) - self._t[i]) / self._ms_per_beat[i]

    def ms(self, beat: float) -> float:
        """拍 -> 毫秒。"""
        i = bisect_right(self._beat, float(beat)) - 1
        if i < 0:
            i = 0
        return self._t[i] + (float(beat) - self._beat[i]) * self._ms_per_beat[i]

    def bpm_at(self, t_ms: float) -> float:
        return self._bpm[self._index_at_ms(t_ms)]

    def ms_per_beat_at(self, t_ms: float) -> float:
        return self._ms_per_beat[self._index_at_ms(t_ms)]

    def start_ms(self) -> float:
        return self._t[0] if self._t else 0.0

    def end_ms(self) -> float:
        return self._t[-1] if self._t else 0.0

    # ------------------------------------------------------------------ 网格
    def snap(self, t_ms: float, divisor: float) -> float:
        """把时间吸附到 1/divisor 拍的网格上（divisor=4 -> 十六分音符网格）。"""
        if divisor <= 0:
            return float(t_ms)
        step = 1.0 / divisor
        b = self.beat(t_ms)
        return self.ms(round(b / step) * step)

    def grid_lines(self, t0: float, t1: float, divisor: float, limit: int = 4000) -> list[float]:
        """返回 [t0, t1] 区间内所有 1/divisor 拍网格线的时间。"""
        if divisor <= 0 or t1 <= t0:
            return []
        step = 1.0 / divisor
        b0 = self.beat(t0)
        b1 = self.beat(t1)
        i0 = int(-(-b0 // step))  # ceil
        i1 = int(b1 // step)
        if i1 - i0 > limit:
            # 太密了就按整数倍稀疏掉
            mult = int((i1 - i0) / limit) + 1
            i0 = int(-(-i0 // mult)) * mult
        out = []
        for i in range(i0, i1 + 1):
            out.append(self.ms(i * step))
        return out

    def beat_index_at(self, t_ms: float) -> int:
        return int(self.beat(t_ms) // 1)

    # ------------------------------------------------------------------ 序列化
    def to_dict(self) -> dict:
        return {"offset_ms": self.offset_ms, "segments": [s.to_dict() for s in self.segments]}

    @staticmethod
    def from_dict(d: dict) -> "TimeMap":
        segs = [BpmSegment.from_dict(x) for x in d.get("segments", [])]
        return TimeMap(segs, float(d.get("offset_ms", 0.0)))


def tap_tempo(times_ms: list[float], reset_after_ms: float = 2000.0) -> float | None:
    """从一串敲击时间估计 BPM（取最近一段连续敲击的平均间隔）。"""
    if len(times_ms) < 2:
        return None
    ts = list(times_ms)
    start = 0
    for i in range(1, len(ts)):
        if ts[i] - ts[i - 1] > reset_after_ms:
            start = i
    ts = ts[start:]
    if len(ts) < 2:
        return None
    span = ts[-1] - ts[0]
    if span <= 0:
        return None
    bpm = 60000.0 * (len(ts) - 1) / span
    while bpm < 60:
        bpm *= 2
    while bpm > 240:
        bpm /= 2
    return bpm
