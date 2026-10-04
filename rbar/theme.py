"""音符类型外观 / 节奏条样式（底板、判定线、命中后残影）。"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, asdict

SHAPES = ("diamond", "square", "circle", "bar")
SHAPE_LABELS = {"diamond": "菱形", "square": "方块", "circle": "圆形", "bar": "长条"}


@dataclass
class NoteStyle:
    """一种音符类型的外观。key 是稳定标识，name 只是显示名。"""

    key: str = "cyan"
    name: str = "青菱形"
    shape: str = "diamond"
    color: str = "#3fd2ea"
    outline: str = "#141a20"
    outline_w: float = 0.10      # 描边宽度，相对音符直径
    size: float = 1.0            # 相对 render.note_size 的倍率
    glow: float = 0.0            # 0..1 外发光强度

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "NoteStyle":
        base = NoteStyle()
        for k, v in d.items():
            if hasattr(base, k):
                setattr(base, k, v)
        return base


@dataclass
class PlateStyle:
    """那根「条」的底板。"""

    mode: str = "plate"          # plate | none
    color: str = "#2b1e1a"       # 底色
    alpha: float = 0.30          # 整体不透明度（越低越透，越不挡原视频）
    top_light: float = 0.10      # 顶部提亮
    bottom_shade: float = 0.22   # 底部压暗
    radius: float = 0.0          # 圆角，相对高度（0.5 = 胶囊形）
    margin: float = 0.0          # 内缩，相对高度
    edge_line: float = 0.12      # 上下边缘高光线透明度
    shadow: float = 0.0          # 外阴影强度

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "PlateStyle":
        base = PlateStyle()
        for k, v in d.items():
            if hasattr(base, k):
                setattr(base, k, v)
        return base


@dataclass
class JudgeStyle:
    """中间判定点。"""

    line_alpha: float = 0.16      # 判定竖线透明度
    line_w: float = 0.035         # 判定竖线宽度，相对高度
    flash: bool = True            # 命中时判定菱形染色闪烁
    flash_scale: float = 1.18
    flash_color: str = "#ffffff"
    glow: float = 0.55
    window_ms: float = 90.0       # 判定窗口（前后多少毫秒算“正在打”）
    pulse: bool = True            # 跟随 BPM 的呼吸脉冲
    pulse_strength: float = 0.5
    ring: bool = True             # 命中扩散圈
    # --- 常驻判定菱形（参考图里判定点上那枚灰白菱形）
    idle: bool = True
    idle_color: str = "#e9edf0"   # 常驻颜色（灰白）
    idle_scale: float = 1.06      # 相对音符半径
    flash_ms: float = 260.0       # 命中后染色闪烁持续多久
    tint: float = 0.92            # 染色强度 0~1

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "JudgeStyle":
        base = JudgeStyle()
        for k, v in d.items():
            if hasattr(base, k):
                setattr(base, k, v)
        return base


@dataclass
class GhostStyle:
    """音符越过判定点之后的样子（图中判定点左边那些灰方块）。"""

    mode: str = "square"          # off | fade | style
    shape: str = "square"
    color: str = "#c9c9c9"
    outline: str = "#141a20"
    alpha: float = 0.9
    size: float = 0.82
    fade_s: float = 0.55          # 越过判定点后多少秒彻底消失（0 = 不消失）
    delay_ms: float = 90.0        # 命中后先保持原样这么久，再变残影（避免和判定闪光重叠）

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "GhostStyle":
        base = GhostStyle()
        for k, v in d.items():
            if hasattr(base, k):
                setattr(base, k, v)
        return base


def default_styles() -> dict[str, NoteStyle]:
    return {
        "cyan": NoteStyle("cyan", "青菱形", "diamond", "#3fd2ea", "#141a20", 0.10, 1.0, 0.0),
        "magenta": NoteStyle("magenta", "品红菱形", "diamond", "#ff2d6a", "#141a20", 0.10, 1.0, 0.0),
        "white": NoteStyle("white", "白菱形", "diamond", "#f2f5f7", "#141a20", 0.10, 1.0, 0.0),
        "gray": NoteStyle("gray", "灰方块", "square", "#c9c9c9", "#141a20", 0.10, 0.86, 0.0),
        "amber": NoteStyle("amber", "琥珀菱形", "diamond", "#ffb02e", "#141a20", 0.10, 1.0, 0.0),
        "viol": NoteStyle("viol", "紫菱形", "diamond", "#a86bff", "#141a20", 0.10, 1.0, 0.0),
    }


@dataclass
class Theme:
    styles: dict[str, NoteStyle] = field(default_factory=default_styles)
    order: list[str] = field(default_factory=lambda: ["cyan", "magenta", "gray"])
    plate: PlateStyle = field(default_factory=PlateStyle)
    judge: JudgeStyle = field(default_factory=JudgeStyle)
    ghost: GhostStyle = field(default_factory=GhostStyle)

    # ---------------------------------------------------------------- 便捷访问
    def style(self, key: str) -> NoteStyle:
        s = self.styles.get(key)
        if s is None:
            s = NoteStyle(key=key, name=key)
            self.styles[key] = s
            self.order.append(key)
        return s

    def active_rows(self) -> list[str]:
        """时间轴上的行（=音符类型），顺序即纵向铺开顺序。"""
        rows = [k for k in self.order if k in self.styles]
        return rows or list(self.styles.keys())

    # ---------------------------------------------------------------- 序列化
    def to_dict(self) -> dict:
        return {
            "styles": {k: v.to_dict() for k, v in self.styles.items()},
            "order": list(self.order),
            "plate": self.plate.to_dict(),
            "judge": self.judge.to_dict(),
            "ghost": self.ghost.to_dict(),
        }

    @staticmethod
    def from_dict(d: dict) -> "Theme":
        t = Theme()
        styles = d.get("styles")
        if styles:
            t.styles = {k: NoteStyle.from_dict(v) for k, v in styles.items()}
        order = d.get("order")
        if order:
            t.order = [k for k in order if k in t.styles]
        t.plate = PlateStyle.from_dict(d.get("plate", {}))
        t.judge = JudgeStyle.from_dict(d.get("judge", {}))
        t.ghost = GhostStyle.from_dict(d.get("ghost", {}))
        return t

    def clone(self) -> "Theme":
        return Theme.from_dict(copy.deepcopy(self.to_dict()))


# ---------------------------------------------------------------------- 预设
PRESETS: dict[str, dict] = {
    "图中样式（暖灰半透明）": {
        "plate": PlateStyle("plate", "#2b1e1a", 0.30, 0.10, 0.22, 0.0, 0.0, 0.12, 0.0).to_dict(),
        "judge": JudgeStyle(0.16, 0.035, True, 1.18, "#ffffff", 0.55, 90.0, True, 0.5, True).to_dict(),
        "ghost": GhostStyle("square", "square", "#c9c9c9", "#141a20", 0.9, 0.82, 0.55).to_dict(),
        "colors": {"cyan": "#3fd2ea", "magenta": "#ff2d6a", "gray": "#c9c9c9"},
    },
    "极简·无底板": {
        "plate": PlateStyle("none", "#000000", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0).to_dict(),
        "judge": JudgeStyle(0.22, 0.03, True, 1.15, "#ffffff", 0.6, 90.0, True, 0.6, True).to_dict(),
        "ghost": GhostStyle("fade", "square", "#c9c9c9", "#141a20", 0.55, 0.85, 0.28).to_dict(),
        "colors": {"cyan": "#3fd2ea", "magenta": "#ff2d6a", "gray": "#c9c9c9"},
    },
    "霓虹发光": {
        "plate": PlateStyle("plate", "#0b1020", 0.34, 0.14, 0.30, 0.0, 0.0, 0.18, 0.0).to_dict(),
        "judge": JudgeStyle(0.25, 0.03, True, 1.25, "#ffffff", 0.85, 100.0, True, 0.75, True).to_dict(),
        "ghost": GhostStyle("fade", "diamond", "#8fd8ff", "#0b1020", 0.45, 0.8, 0.30).to_dict(),
        "colors": {"cyan": "#33e6ff", "magenta": "#ff2fb0", "gray": "#b9c6d6"},
        "glow": 0.75,
    },
    "深色玻璃": {
        "plate": PlateStyle("plate", "#101418", 0.42, 0.12, 0.26, 0.5, 0.06, 0.16, 0.35).to_dict(),
        "judge": JudgeStyle(0.14, 0.03, True, 1.18, "#ffffff", 0.5, 90.0, True, 0.45, True).to_dict(),
        "ghost": GhostStyle("square", "square", "#8b939c", "#0d1114", 0.75, 0.8, 0.5).to_dict(),
        "colors": {"cyan": "#3fd2ea", "magenta": "#ff2d6a", "gray": "#c9c9c9"},
    },
    "纯白浅色": {
        "plate": PlateStyle("plate", "#ffffff", 0.26, 0.25, 0.06, 0.5, 0.06, 0.35, 0.2).to_dict(),
        "judge": JudgeStyle(0.20, 0.03, True, 1.18, "#ffffff", 0.6, 90.0, True, 0.5, True).to_dict(),
        "ghost": GhostStyle("square", "square", "#6f767c", "#ffffff", 0.7, 0.8, 0.5).to_dict(),
        "colors": {"cyan": "#12b8d8", "magenta": "#ea1a5c", "gray": "#6f767c"},
    },
}


def apply_preset(theme: Theme, name: str) -> None:
    p = PRESETS.get(name)
    if not p:
        return
    theme.plate = PlateStyle.from_dict(p["plate"])
    theme.judge = JudgeStyle.from_dict(p["judge"])
    theme.ghost = GhostStyle.from_dict(p["ghost"])
    glow = float(p.get("glow", 0.0))
    for k, c in p.get("colors", {}).items():
        s = theme.styles.get(k)
        if s is not None:
            s.color = c
        else:
            theme.styles[k] = NoteStyle(k, k, "diamond", c, "#141a20", 0.10, 1.0, glow)
    for k, s in theme.styles.items():
        s.glow = glow if k in p.get("colors", {}) else s.glow
