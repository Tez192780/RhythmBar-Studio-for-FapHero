"""应用级设置（QSettings 持久化）。"""

from __future__ import annotations

import os

from PySide6.QtCore import QSettings

from .audio import find_ffmpeg

ORG = "rbar"
APP = "RhythmBarStudio"


class AppSettings:
    def __init__(self):
        self._s = QSettings(ORG, APP)
        g = self._s.value
        self.ffmpeg: str = str(g("ffmpeg", "") or "") or find_ffmpeg()
        self.latency_ms: float = float(g("latency_ms", 0.0) or 0.0)
        self.volume: float = float(g("volume", 0.85) or 0.85)
        self.rate: float = float(g("rate", 1.0) or 1.0)
        self.tap_min_ms: float = float(g("tap_min_ms", 90.0) or 90.0)
        self.language: str = str(g("language", "zh") or "zh")
        self.check_updates: bool = str(g("check_updates", "true")).lower() in ("true", "1")
        self.last_dir: str = str(g("last_dir", os.path.expanduser("~")) or "")
        recent = g("recent", [])
        if isinstance(recent, str):
            recent = [recent]
        self.recent: list[str] = [str(x) for x in (recent or []) if x]
        self.show_safe: bool = str(g("show_safe", "true")).lower() in ("true", "1")
        geo = g("geometry")
        self.geometry = geo

    def save(self) -> None:
        s = self._s
        s.setValue("ffmpeg", self.ffmpeg)
        s.setValue("latency_ms", self.latency_ms)
        s.setValue("volume", self.volume)
        s.setValue("rate", self.rate)
        s.setValue("tap_min_ms", self.tap_min_ms)
        s.setValue("language", self.language)
        s.setValue("check_updates", "true" if self.check_updates else "false")
        s.setValue("last_dir", self.last_dir)
        s.setValue("recent", self.recent[:12])
        s.setValue("show_safe", "true" if self.show_safe else "false")
        s.sync()

    def add_recent(self, path: str) -> None:
        path = os.path.abspath(path)
        self.recent = [p for p in self.recent if os.path.normcase(p) != os.path.normcase(path)]
        self.recent.insert(0, path)
        del self.recent[12:]

    def remember_dir(self, path: str) -> None:
        d = path if os.path.isdir(path) else os.path.dirname(path)
        if d:
            self.last_dir = d
