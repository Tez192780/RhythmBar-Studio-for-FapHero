r"""开发用：验证频谱图（已知频率应落在正确的行上）。

    cmd /c "python -X utf8 -u tools\spec_check.py > build\spec.log 2>&1"
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from rbar.audio import spectrogram  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  ok  " if cond else " FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)
    if not cond:
        FAILS.append(name)


def tone(freq: float, seconds: float = 2.0, sr: int = 48000, amp: float = 0.6) -> np.ndarray:
    t = np.arange(int(sr * seconds)) / sr
    x = (np.sin(2 * np.pi * freq * t) * amp).astype(np.float32)
    return np.repeat(x[:, None], 2, axis=1)


def main() -> None:
    sr = 48000
    print("[单音]", flush=True)
    sp = spectrogram(tone(1000.0), sr)
    check("形状", sp.data.shape[0] == 192 and sp.data.shape[1] > 100, f"{sp.data.shape}")
    col = sp.data[:, sp.frames // 2].astype(np.int32)
    row = int(np.argmax(col))
    f_row = sp.f_max * (sp.f_min / sp.f_max) ** (row / (sp.bins - 1))
    check("峰值行对应 ~1kHz", abs(f_row - 1000) / 1000 < 0.12, f"row={row} f={f_row:.0f}Hz")
    check("能量集中在少数行", (col > 128).sum() < 24, f"{(col > 128).sum()} 行")
    img = sp.image()
    check("能生成 QImage", img is not None and not img.isNull(), f"{img.width()}x{img.height()}")

    print("[低频/高频分开]", flush=True)
    t = np.arange(sr * 2) / sr
    both = ((np.sin(2 * np.pi * 80 * t) * 0.6 + np.sin(2 * np.pi * 8000 * t) * 0.6)
            .astype(np.float32))
    sp2 = spectrogram(np.repeat(both[:, None], 2, axis=1), sr)
    c = sp2.data[:, sp2.frames // 2].astype(np.int32)
    top = int(np.argmax(c[: sp2.bins // 4]))              # 上半 = 高频
    bot = int(np.argmax(c[3 * sp2.bins // 4:])) + 3 * sp2.bins // 4
    f_top = sp2.f_max * (sp2.f_min / sp2.f_max) ** (top / (sp2.bins - 1))
    f_bot = sp2.f_max * (sp2.f_min / sp2.f_max) ** (bot / (sp2.bins - 1))
    check("高频在 8kHz 附近", abs(f_top - 8000) / 8000 < 0.25, f"{f_top:.0f}Hz")
    check("低频在 80Hz 附近", abs(f_bot - 80) / 80 < 0.3, f"{f_bot:.0f}Hz")

    print("[静音/极短输入不崩]", flush=True)
    sp3 = spectrogram(np.zeros((48000, 2), dtype=np.float32), sr)
    check("静音可处理", sp3.data.shape[1] > 0)
    sp4 = spectrogram(np.zeros((100, 2), dtype=np.float32), sr)
    check("超短输入兜底", sp4.data.shape[0] == 192)

    print(flush=True)
    if FAILS:
        print("失败项：", FAILS, flush=True)
        sys.exit(1)
    print("频谱图检查通过", flush=True)


if __name__ == "__main__":
    main()
