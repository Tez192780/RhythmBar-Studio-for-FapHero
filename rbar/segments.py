"""判定区分段的配置文件读写（JSON）。

格式：
{
  "app": "rbar-judge-segments",
  "version": 1,
  "fade": {"enabled": true, "ms": 400, "scope": "judge"},
  "segments": [
    {"start_ms": 0, "end_ms": 30000, "style": {...}},
    ...
  ]
}
"""

from __future__ import annotations

import json
import os

from .theme import FadeSettings, JudgeSegment, Theme

FORMAT = "rbar-judge-segments"
VERSION = 1


def to_dict(theme: Theme) -> dict:
    return {
        "app": FORMAT,
        "version": VERSION,
        "fade": theme.fade.to_dict(),
        "segments": [s.to_dict() for s in theme.segments],
    }


def save(theme: Theme, path: str) -> str:
    path = os.path.abspath(path)
    if not path.lower().endswith(".json"):
        path += ".json"
    data = to_dict(theme)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path


def load(theme: Theme, path: str) -> int:
    """把文件里的分段读进 theme，返回读到的段数。"""
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict):
        raise ValueError("不是有效的分段配置文件")
    if d.get("app") not in (FORMAT, None):
        raise ValueError("不是节奏条工作室的分段配置文件")
    theme.segments = [JudgeSegment.from_dict(x) for x in (d.get("segments") or [])]
    theme.segments.sort(key=lambda s: float(s.start_ms))
    theme.fade = FadeSettings.from_dict(d.get("fade", {}) or {})
    return len(theme.segments)
