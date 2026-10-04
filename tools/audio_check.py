"""开发用：验证音频播放链路（解码 -> 波形 -> QAudioSink -> 位置推进）。

需要真实窗口/音频设备，会短暂播放很小的声音。
    python tools/audio_check.py
"""

from __future__ import annotations

import os
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("QT_QPA_PLATFORM", None)

import numpy as np  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rbar.audio import AudioEngine, decode_audio, peaks  # noqa: E402


def make_wav(path: str, bpm: float = 120.0, seconds: float = 5.0, sr: int = 48000) -> None:
    n = int(sr * seconds)
    data = np.zeros(n, dtype=np.float32)
    period = 60.0 / bpm
    t, i = 0.0, 0
    while t < seconds:
        pos = int(t * sr)
        dur = int(0.05 * sr)
        env = np.exp(-np.arange(dur) / (0.010 * sr))
        click = np.sin(2 * np.pi * (880 if i % 4 == 0 else 660) * np.arange(dur) / sr) * env * 0.5
        end = min(n, pos + dur)
        data[pos:end] += click[: end - pos]
        t += period
        i += 1
    pcm = np.clip(data * 32767, -32768, 32767).astype(np.int16)
    with wave.open(path, "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(np.repeat(pcm[:, None], 2, axis=1).tobytes())


def main() -> None:
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build")
    os.makedirs(out, exist_ok=True)
    wav = os.path.join(out, "click120.wav")
    make_wav(wav)

    app = QApplication(sys.argv)
    eng = AudioEngine()
    samples, sr = decode_audio(wav)
    s0 = time.perf_counter()
    pk = peaks(samples, 30000)
    print(f"解码 {samples.shape} sr={sr} 峰值数={len(pk)} 用时 {time.perf_counter() - s0:.2f}s")

    eng.samples = samples
    eng.sr = sr
    eng.duration_ms = samples.shape[0] * 1000.0 / sr
    eng._rebuild()
    eng.set_volume(0.12)

    results: list[str] = []
    fails: list[str] = []

    def check(name: str, cond: bool, extra: str = "") -> None:
        (results if cond else fails).append(name)
        print(("  ok  " if cond else " FAIL ") + name + (f"  [{extra}]" if extra else ""))

    check("时长≈5000ms", abs(eng.duration_ms - 5000) < 50, f"{eng.duration_ms:.0f}")

    state = {"t0": 0.0, "pos": 0.0}

    def diag(tag: str) -> None:
        print(f"  · {tag}: 位置={eng.position_ms():.0f}ms  播放中={eng.playing}  "
              f"欠载={eng.underruns}", flush=True)

    def start() -> None:
        try:
            eng.set_beats([0, 500, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500])
            eng.set_metronome(True)
            eng.play(1000.0)
        except Exception as e:  # noqa: BLE001
            print("播放失败：", e)
            app.quit()
            return
        check("进入播放状态", eng.playing)
        state["t0"] = time.perf_counter()
        diag("刚启动")
        QTimer.singleShot(400, lambda: diag("400ms"))
        QTimer.singleShot(900, lambda: diag("900ms"))
        QTimer.singleShot(1500, mid)

    def mid() -> None:
        pos = eng.position_ms()
        state["pos"] = pos
        print(f"  · 播放 1.5s 后位置 = {pos:.0f} ms（起点 1000）")
        check("位置随播放推进", pos > 1800, f"{pos:.0f}")
        check("位置不过冲", pos < 3200, f"{pos:.0f}")
        eng.pause()
        check("暂停后仍在 1000ms+", abs(eng.position_ms() - pos) < 1, f"{eng.position_ms():.0f}")
        QTimer.singleShot(120, tail)

    def tail() -> None:
        eng.set_rate(0.5)
        check("变速后位置仍在同一时间轴", abs(eng.duration_ms - 5000) < 50, f"{eng.duration_ms:.0f}")
        check("变速后 PCM 时长加倍", abs(len(eng._pcm) / 48000 * 1000 - 10000) < 400,
              f"{len(eng._pcm) / 48:.0f}ms")
        eng.set_rate(1.0)
        eng.seek(4000.0)
        eng.play()
        QTimer.singleShot(3000, ended)

    def ended() -> None:
        pos = eng.position_ms()
        check("播到结尾自动停止（或已到末尾）", (not eng.playing) or pos > 4900,
              f"playing={eng.playing} pos={pos:.0f}")
        check("没有音频欠载", eng.underruns == 0, f"{eng.underruns} 次")
        eng.shutdown()
        print()
        if fails:
            print("失败项：", fails)
            app.exit(1)
        else:
            print("音频链路全部通过")
            app.exit(0)

    QTimer.singleShot(300, start)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
