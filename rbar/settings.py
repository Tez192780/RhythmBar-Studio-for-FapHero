"""应用级设置（当前目录的 JSON 配置文件 + QSettings 兜底/兼容）。"""

from __future__ import annotations

import json
import os

from PySide6.QtCore import QSettings

from .audio import find_ffmpeg

ORG = "rbar"
APP = "RhythmBarStudio"
CONFIG_NAME = "rbar_settings.json"


def _as_float(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_bool(v, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes", "on")


class AppSettings:
    def __init__(self):
        # 先读 QSettings（兼容旧版本/窗口几何），再用当前目录的简单配置文件覆盖。
        self._s = QSettings(ORG, APP)
        g = self._s.value
        self.ffmpeg: str = str(g("ffmpeg", "") or "") or find_ffmpeg()
        self.latency_ms: float = _as_float(g("latency_ms", 0.0), 0.0)
        self.volume: float = _as_float(g("volume", 0.85), 0.85)
        self.rate: float = _as_float(g("rate", 1.0), 1.0)
        self.tap_min_ms: float = _as_float(g("tap_min_ms", 90.0), 90.0)
        self.language: str = str(g("language", "zh") or "zh")
        self.check_updates: bool = _as_bool(g("check_updates", "true"), True)
        # 更新检查状态：success/attempt 分开，失败不再算“已检查”。
        legacy = _as_float(g("last_update_check", 0.0), 0.0)
        self.update_last_success: float = _as_float(
            g("update_last_success", legacy), legacy)
        self.update_last_attempt: float = _as_float(
            g("update_last_attempt", legacy), legacy)
        self.update_fail_count: int = int(_as_float(g("update_fail_count", 0), 0))
        self.update_skip_version: str = str(g("update_skip_version", "") or "")
        self.update_snooze_until: float = _as_float(
            g("update_snooze_until", 0.0), 0.0)
        self.update_etag: str = str(g("update_etag", "") or "")
        self.last_update_check: float = self.update_last_success   # 旧字段兼容
        self.last_dir: str = str(g("last_dir", os.path.expanduser("~")) or "")
        recent = g("recent", [])
        if isinstance(recent, str):
            recent = [recent]
        self.recent: list[str] = [str(x) for x in (recent or []) if x]
        self.show_safe: bool = _as_bool(g("show_safe", "true"), True)
        geo = g("geometry")
        self.geometry = geo

        # 外观 / 导出默认值：由 MainWindow 在启动、新建工程、改动外观/导出时写入。
        self.theme: dict | None = None
        self.export: dict | None = None

        self.config_path = os.path.join(os.getcwd(), CONFIG_NAME)
        self._load_file()
        if not os.path.isfile(self.config_path):
            self._save_file()          # 首次启动就在当前目录落地一个可读可改的配置

    # ------------------------------------------------------------ 配置文件
    def _load_file(self) -> None:
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(d, dict):
            return
        if "ffmpeg" in d:
            self.ffmpeg = str(d.get("ffmpeg") or "")
        if "latency_ms" in d:
            self.latency_ms = _as_float(d.get("latency_ms"), self.latency_ms)
        if "volume" in d:
            self.volume = _as_float(d.get("volume"), self.volume)
        if "rate" in d:
            self.rate = _as_float(d.get("rate"), self.rate)
        if "tap_min_ms" in d:
            self.tap_min_ms = _as_float(d.get("tap_min_ms"), self.tap_min_ms)
        if "language" in d:
            self.language = str(d.get("language") or self.language)
        if "check_updates" in d:
            self.check_updates = _as_bool(d.get("check_updates"), self.check_updates)
        if "last_update_check" in d:
            self.update_last_success = _as_float(
                d.get("last_update_check"), self.update_last_success)
            self.update_last_attempt = self.update_last_success
            self.last_update_check = self.update_last_success
        if "update_last_success" in d:
            self.update_last_success = _as_float(
                d.get("update_last_success"), self.update_last_success)
        if "update_last_attempt" in d:
            self.update_last_attempt = _as_float(
                d.get("update_last_attempt"), self.update_last_attempt)
        if "update_fail_count" in d:
            try:
                self.update_fail_count = max(0, int(d.get("update_fail_count") or 0))
            except (TypeError, ValueError):
                pass
        if "update_skip_version" in d:
            self.update_skip_version = str(d.get("update_skip_version") or "")
        if "update_snooze_until" in d:
            self.update_snooze_until = _as_float(
                d.get("update_snooze_until"), self.update_snooze_until)
        if "update_etag" in d:
            self.update_etag = str(d.get("update_etag") or "")
        if "last_dir" in d:
            self.last_dir = str(d.get("last_dir") or self.last_dir)
        if "recent" in d:
            self.recent = [str(x) for x in (d.get("recent") or []) if x][:12]
        if "show_safe" in d:
            self.show_safe = _as_bool(d.get("show_safe"), self.show_safe)
        if isinstance(d.get("theme"), dict):
            self.theme = d["theme"]
        if isinstance(d.get("export"), dict):
            self.export = d["export"]

    def _save_file(self) -> None:
        data = {
            "_comment": "节奏条工作室设置（含外观与导出默认值）；可以直接编辑，重启后生效。",
            "ffmpeg": self.ffmpeg,
            "latency_ms": self.latency_ms,
            "volume": self.volume,
            "rate": self.rate,
            "tap_min_ms": self.tap_min_ms,
            "language": self.language,
            "check_updates": bool(self.check_updates),
            "last_update_check": float(self.update_last_success),
            "update_last_success": float(self.update_last_success),
            "update_last_attempt": float(self.update_last_attempt),
            "update_fail_count": int(self.update_fail_count),
            "update_skip_version": self.update_skip_version,
            "update_snooze_until": float(self.update_snooze_until),
            "update_etag": self.update_etag,
            "last_dir": self.last_dir,
            "recent": self.recent[:12],
            "show_safe": bool(self.show_safe),
        }
        if self.theme is not None:
            data["theme"] = self.theme
        if self.export is not None:
            data["export"] = self.export
        tmp = self.config_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.config_path)
        except OSError:
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    def save(self) -> None:
        s = self._s
        s.setValue("ffmpeg", self.ffmpeg)
        s.setValue("latency_ms", self.latency_ms)
        s.setValue("volume", self.volume)
        s.setValue("rate", self.rate)
        s.setValue("tap_min_ms", self.tap_min_ms)
        s.setValue("language", self.language)
        s.setValue("check_updates", "true" if self.check_updates else "false")
        s.setValue("last_update_check", float(self.update_last_success))
        s.setValue("update_last_success", float(self.update_last_success))
        s.setValue("update_last_attempt", float(self.update_last_attempt))
        s.setValue("update_fail_count", int(self.update_fail_count))
        s.setValue("update_skip_version", self.update_skip_version)
        s.setValue("update_snooze_until", float(self.update_snooze_until))
        s.setValue("update_etag", self.update_etag)
        s.setValue("last_dir", self.last_dir)
        s.setValue("recent", self.recent[:12])
        s.setValue("show_safe", "true" if self.show_safe else "false")
        s.setValue("geometry", self.geometry)
        s.sync()
        self._save_file()

    def add_recent(self, path: str) -> None:
        path = os.path.abspath(path)
        self.recent = [p for p in self.recent if os.path.normcase(p) != os.path.normcase(path)]
        self.recent.insert(0, path)
        del self.recent[12:]

    def remember_dir(self, path: str) -> None:
        d = path if os.path.isdir(path) else os.path.dirname(path)
        if d:
            self.last_dir = d
