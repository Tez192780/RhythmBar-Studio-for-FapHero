r"""开发用：量化播放时的界面开销 / 音频欠载情况（先测量，再优化）。

    cmd /c "python -X utf8 -u tools\perf_check.py > build\perf.log 2>&1"
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
from PySide6.QtMultimedia import QAudio  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rbar.app import QSS, build_palette  # noqa: E402
from rbar.model import Note, Project  # noqa: E402
from rbar.render import BarRenderer  # noqa: E402
from rbar.settings import AppSettings  # noqa: E402
from rbar.ui.mainwindow import MainWindow  # noqa: E402
from rbar.ui.preview import PreviewWidget  # noqa: E402
from rbar.ui.timeline import TimelineWidget  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
REPORT: dict[str, list[float]] = {"preview": [], "timeline": [], "tick_gap": []}
UNDERRUNS = {"n": 0, "idle": 0}
VERBOSE = "-v" in sys.argv


def make_wav(path: str, bpm: float = 128.0, seconds: float = 20.0, sr: int = 48000) -> None:
    n = int(sr * seconds)
    data = np.zeros(n, dtype=np.float32)
    period = 60.0 / bpm
    t, i = 0.0, 0
    while t < seconds:
        pos = int(t * sr)
        dur = int(0.10 * sr)
        env = np.exp(-np.arange(dur) / (0.02 * sr))
        f = 90 if i % 4 == 0 else 140
        tone = (np.sin(2 * np.pi * f * np.arange(dur) / sr)
                + 0.4 * np.sin(2 * np.pi * f * 2 * np.arange(dur) / sr)) * env * 0.5
        end = min(n, pos + dur)
        data[pos:end] += tone[: end - pos]
        t += period
        i += 1
    pcm = np.clip(data * 32767, -32768, 32767).astype(np.int16)
    with wave.open(path, "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(np.repeat(pcm[:, None], 2, axis=1).tobytes())


def patch_paint(cls, key: str) -> None:
    orig = cls.paintEvent

    def timed(self, ev):
        t0 = time.perf_counter()
        orig(self, ev)
        dt = (time.perf_counter() - t0) * 1000.0
        REPORT[key].append(dt)
        if VERBOSE and dt > 12:
            print(f"    [慢帧] {key}: {dt:.1f}ms", flush=True)

    cls.paintEvent = timed


def patch_method(cls, name: str, key: str) -> None:
    orig = getattr(cls, name)

    def timed(self, *a, **kw):
        t0 = time.perf_counter()
        r = orig(self, *a, **kw)
        REPORT.setdefault(key, []).append((time.perf_counter() - t0) * 1000.0)
        return r

    setattr(cls, name, timed)


def stats(name: str, xs: list[float]) -> str:
    if not xs:
        return f"{name}: 无数据"
    a = np.array(xs)
    return (f"{name}: n={len(a):4d} 平均={a.mean():6.2f}ms "
            f"p50={np.percentile(a, 50):6.2f} p95={np.percentile(a, 95):6.2f} "
            f"max={a.max():6.2f}ms  合计={a.sum():7.1f}ms")


def main() -> None:
    os.makedirs(BUILD, exist_ok=True)
    wav = os.path.join(BUILD, "perf_click.wav")
    make_wav(wav)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(build_palette())
    app.setStyleSheet(QSS)
    patch_paint(PreviewWidget, "preview")
    patch_paint(TimelineWidget, "timeline")
    patch_method(TimelineWidget, "_ensure_bg", "bg渲染")
    patch_method(TimelineWidget, "_draw_notes", "音符层")
    patch_method(TimelineWidget, "_draw_grid", "网格层")
    patch_method(BarRenderer, "_draw_plate", "预览-底板")
    patch_method(BarRenderer, "_draw_judge", "预览-判定点")
    patch_method(BarRenderer, "_draw_notes", "预览-音符")

    win = MainWindow(AppSettings(), Project())
    win.silent = True
    win.resize(1600, 960)
    win.show()

    # 造 400 个音符，让渲染有真实负载
    pr = win.doc.project
    for i in range(400):
        pr.chart.add(Note(i * 62.0, ["cyan", "magenta", "gray"][i % 3], i % 3))
    win.on_notes_changed()
    win.timeline.zoom_to_fit()
    win.doc.project.audio_path = wav
    win.load_audio(wav)
    app.processEvents()

    state = {"t0": 0.0, "last_tick": 0.0, "n": 0}

    def wait_audio(elapsed: int = 0) -> None:
        if (win.engine.samples is None or win.spec is None) and elapsed < 15000:
            QTimer.singleShot(150, lambda: wait_audio(elapsed + 150))
            return
        print(f"音频载入：{win.engine.duration_ms:.0f}ms; 音符 {len(pr.chart)} 个; "
              f"频谱 {win.spec.data.shape if win.spec else None}", flush=True)
        win.seek(0.0)
        win.play()
        # 主线程响应性：10ms 定时器，量实际间隔
        resp = QTimer()
        resp.setInterval(10)

        def on_resp() -> None:
            now = time.perf_counter()
            if state["last_tick"]:
                gap = (now - state["last_tick"]) * 1000.0
                REPORT["tick_gap"].append(gap)
                if gap > 40:
                    print(f"    [停顿] {gap:.0f}ms  在播放 {now - state['t0']:.2f}s 处", flush=True)
            state["last_tick"] = now

        resp.timeout.connect(on_resp)
        resp.start()
        state["resp"] = resp
        state["t0"] = time.perf_counter()
        QTimer.singleShot(6000, finish)

    def finish() -> None:
        state["resp"].stop()
        win.pause()
        print(flush=True)
        print(f"_tick 调用 {TICKS['n']} 次 / 6 秒（≈{TICKS['n'] / 6:.0f}/s）", flush=True)
        print(stats("预览条绘制", REPORT["preview"]), flush=True)
        print(stats("时间轴绘制", REPORT["timeline"]), flush=True)
        print(stats("  └ 背景缓存 _ensure_bg", REPORT.get("bg渲染", [])), flush=True)
        print(stats("  └ 音符层 _draw_notes", REPORT.get("音符层", [])), flush=True)
        print(stats("  └ 网格层 _draw_grid（仅背景重画时）", REPORT.get("网格层", [])), flush=True)
        print(stats("  └ 预览-底板", REPORT.get("预览-底板", [])), flush=True)
        print(stats("  └ 预览-判定点", REPORT.get("预览-判定点", [])), flush=True)
        print(stats("  └ 预览-音符", REPORT.get("预览-音符", [])), flush=True)
        print(stats("主线程 10ms 定时器实际间隔", REPORT["tick_gap"]), flush=True)
        print(f"音频欠载采样：{UNDERRUNS['idle']}/{UNDERRUNS['n']} 次处于 Idle（播放中不该出现）", flush=True)
        gaps = np.array(REPORT["tick_gap"]) if REPORT["tick_gap"] else np.zeros(1)
        worst = gaps.max() if gaps.size else 0
        print(f"最长一次主线程停顿：{worst:.1f}ms", flush=True)
        win.doc.mark_clean()
        win.close()
        app.quit()

    def poll_audio() -> None:
        s = win.engine._sink
        if s is not None and win.engine.playing:
            UNDERRUNS["n"] += 1
            if s.state() == QAudio.State.IdleState:   # 播放中不该出现 Idle
                UNDERRUNS["idle"] += 1

    poll = QTimer()
    poll.setInterval(20)
    poll.timeout.connect(poll_audio)
    poll.start()
    state["poll"] = poll

    # 统计 _tick 调用次数，看看是否存在「重绘放大」
    orig_tick = win._tick
    TICKS = {"n": 0}

    def tick_counted() -> None:
        TICKS["n"] += 1
        orig_tick()

    win._tick = tick_counted
    win._timer.timeout.disconnect()
    win._timer.timeout.connect(tick_counted)

    QTimer.singleShot(300, wait_audio)
    app.exec()


if __name__ == "__main__":
    main()
