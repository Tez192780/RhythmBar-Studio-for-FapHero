r"""开发用：分离测试「卡顿」到底来自哪里。

三种模式各跑 30 秒，比较主线程停顿分布：
    python tools\jitter_probe.py pure    # 纯 Python 10ms 循环（无 Qt 无音频）→ 系统底噪
    python tools\jitter_probe.py qt      # Qt 界面 + 虚拟播放（不开声卡）
    python tools\jitter_probe.py audio   # Qt 界面 + 真实音频播放
"""

from __future__ import annotations

import os
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

SECONDS = 30.0
STEP = 0.010


def measure(seconds: float = SECONDS) -> list[float]:
    """以 10ms 为周期跑一段，返回每次实际间隔（毫秒）。"""
    gaps: list[float] = []
    last = time.perf_counter()
    t_end = last + seconds
    while True:
        time.sleep(STEP)
        now = time.perf_counter()
        gaps.append((now - last) * 1000.0)
        last = now
        if now >= t_end:
            break
    return gaps


def report(name: str, gaps: list[float]) -> None:
    a = np.array(gaps)
    over = a[a > 30]
    print(f"\n=== {name} ===", flush=True)
    print(f"样本 {a.size}  平均 {a.mean():.2f}ms  p50 {np.percentile(a,50):.1f}  "
          f"p99 {np.percentile(a,99):.1f}  最大 {a.max():.1f}ms", flush=True)
    print(f"超过 30ms 的停顿：{over.size} 次", flush=True)
    if over.size:
        top = np.sort(over)[::-1][:10]
        print("  最大的几次：" + "、".join(f"{x:.0f}ms" for x in top), flush=True)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "pure"
    print(f"模式：{mode}，时长 {SECONDS:.0f}s", flush=True)

    if mode == "pure":
        report("纯 Python 10ms 循环（系统底噪）", measure())
        return

    os.environ.pop("QT_QPA_PLATFORM", None)
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from rbar.app import QSS, build_palette, tune_gc
    from rbar.model import Note, Project
    from rbar.settings import AppSettings
    from rbar.ui.mainwindow import MainWindow

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    wav = os.path.join(root, "build", "stall.wav")
    if mode == "audio" and not os.path.isfile(wav):
        sr = 48000
        n = int(sr * 35)
        t = np.arange(n) / sr
        x = 0.3 * np.sin(2 * np.pi * 220 * t)
        pcm = np.clip(x * 32767, -32768, 32767).astype(np.int16)
        with wave.open(wav, "wb") as f:
            f.setnchannels(2)
            f.setsampwidth(2)
            f.setframerate(sr)
            f.writeframes(np.repeat(pcm[:, None], 2, axis=1).tobytes())

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setPalette(build_palette())
    app.setStyleSheet(QSS)
    win = MainWindow(AppSettings(), Project())
    win.silent = True
    win.resize(1600, 960)
    win.show()
    for i in range(600):
        win.doc.project.chart.add(Note(i * 62.0, ["cyan", "magenta", "gray"][i % 3], i % 3))
    win.on_notes_changed()

    gaps: list[float] = []
    state = {"last": 0.0}

    def start() -> None:
        if mode == "audio":
            win.load_audio(wav)

            def wait(el: int = 0) -> None:
                if win.engine.samples is None and el < 20000:
                    QTimer.singleShot(150, lambda: wait(el + 150))
                    return
                win.seek(0.0)
                win.play()
                begin()

            QTimer.singleShot(300, wait)
        else:
            win.seek(0.0)
            win.play()          # 没音频 → 虚拟播放，界面照样动
            begin()

    def begin() -> None:
        t = QTimer()
        t.setInterval(int(STEP * 1000))

        def on_tick() -> None:
            now = time.perf_counter()
            if state["last"]:
                gaps.append((now - state["last"]) * 1000.0)
            state["last"] = now

        t.timeout.connect(on_tick)
        t.start()
        state["timer"] = t
        state["t_end"] = time.perf_counter() + SECONDS
        QTimer.singleShot(int(SECONDS * 1000) + 200, done)

    def done() -> None:
        state["timer"].stop()
        win.pause()
        label = "Qt 界面 + 真实音频播放" if mode == "audio" else "Qt 界面 + 虚拟播放（无声卡）"
        report(label, gaps[5:])
        win.doc.mark_clean()
        win.close()
        app.quit()

    QTimer.singleShot(300, start)
    tune_gc()
    app.exec()


if __name__ == "__main__":
    main()
