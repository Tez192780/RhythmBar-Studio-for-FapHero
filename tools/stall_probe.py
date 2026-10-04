r"""开发用：给主线程装「栈采样探头」，抓出 180ms 停顿到底卡在哪。

思路：后台线程每 4ms 采一次主线程调用栈；主线程自己用 10ms 定时器测停顿，
发现 >30ms 就把这段时间内的栈样本全部打出来。

    cmd /c "python -X utf8 -u tools\stall_probe.py > build\stall.log 2>&1"
"""

from __future__ import annotations

import os
import sys
import threading
import time
import wave
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("QT_QPA_PLATFORM", None)

import numpy as np  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rbar.app import QSS, build_palette, tune_gc  # noqa: E402
from rbar.model import Note, Project  # noqa: E402
from rbar.settings import AppSettings  # noqa: E402
from rbar.ui.mainwindow import MainWindow  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
SECONDS = 30.0
STALL_MS = 30.0
MAIN_ID = threading.main_thread().ident
SAMPLES: deque = deque(maxlen=6000)     # (时刻, 栈摘要)
STALLS: list[dict] = []
READS: list[tuple[float, int, float]] = []   # (时刻, maxlen, 耗时ms)
MARK = {"t0": 0.0}


def now() -> float:
    return time.perf_counter() - MARK["t0"]


def make_audio(path: str, seconds: float, sr: int = 48000) -> None:
    n = int(sr * seconds)
    t = np.arange(n) / sr
    x = 0.25 * np.sin(2 * np.pi * 55 * t) + 0.15 * np.sin(2 * np.pi * 220 * t)
    pcm = np.clip(x * 32767 / max(1e-6, np.abs(x).max()), -32768, 32767).astype(np.int16)
    with wave.open(path, "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(np.repeat(pcm[:, None], 2, axis=1).tobytes())


def stack_summary(frame) -> str:
    """把栈压成 "a < b < c" 形式（最内层在最前）。"""
    names = []
    f = frame
    while f is not None and len(names) < 6:
        names.append(f.f_code.co_name)
        f = f.f_back
    return " < ".join(names)


def watchdog() -> None:
    """后台线程：定时采样主线程栈。"""
    frames = sys._current_frames
    while True:
        f = frames().get(MAIN_ID)
        if f is not None:
            SAMPLES.append((now(), stack_summary(f)))
        time.sleep(0.004)


def main() -> None:
    os.makedirs(BUILD, exist_ok=True)
    wav = os.path.join(BUILD, "stall.wav")
    if not os.path.isfile(wav):
        make_audio(wav, SECONDS + 5)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(build_palette())
    app.setStyleSheet(QSS)

    # 记录 QAudioSink 每次拉数据的耗时
    from rbar.audio import _PCMDevice

    orig_read = _PCMDevice.readData

    def timed_read(self, maxlen):
        t0 = time.perf_counter()
        out = orig_read(self, maxlen)
        READS.append((now(), int(maxlen), (time.perf_counter() - t0) * 1000.0))
        return out

    _PCMDevice.readData = timed_read

    win = MainWindow(AppSettings(), Project())
    win.silent = True
    win.resize(1600, 960)
    win.show()
    pr = win.doc.project
    for i in range(600):
        pr.chart.add(Note(i * 62.0, ["cyan", "magenta", "gray"][i % 3], i % 3))
    win.on_notes_changed()
    win.load_audio(wav)
    app.processEvents()

    state = {"last": 0.0}

    def wait_ready(elapsed: int = 0) -> None:
        if (win.engine.samples is None or win.spec is None) and elapsed < 30000:
            QTimer.singleShot(200, lambda: wait_ready(elapsed + 200))
            return
        print(f"就绪：音频 {win.engine.duration_ms:.0f}ms", flush=True)
        MARK["t0"] = time.perf_counter()
        threading.Thread(target=watchdog, daemon=True).start()
        win.seek(0.0)
        win.play()
        resp = QTimer()
        resp.setInterval(10)

        def on_resp() -> None:
            t = now()
            if state["last"] and (t - state["last"]) * 1000.0 > STALL_MS:
                STALLS.append({"t0": state["last"] - 0.02, "t1": t,
                               "ms": (t - state["last"]) * 1000.0})
            state["last"] = t

        resp.timeout.connect(on_resp)
        resp.start()
        state["resp"] = resp
        QTimer.singleShot(int(SECONDS * 1000), finish)

    def finish() -> None:
        try:
            report()
        except Exception as e:  # noqa: BLE001
            print("[报告出错]", e, flush=True)
        finally:
            win.doc.mark_clean()
            win.close()
            app.quit()

    def report() -> None:
        state["resp"].stop()
        win.pause()
        snap = list(SAMPLES)          # 先快照，采样线程还在写这个 deque
        reads = list(READS)
        print(flush=True)
        print(f"=== {SECONDS:.0f} 秒内 {len(STALLS)} 次停顿 ===", flush=True)
        for st in sorted(STALLS, key=lambda x: -x["ms"])[:6]:
            print(f"\n--- 停顿 {st['ms']:.0f}ms  @ {st['t0']:.2f}s ~ {st['t1']:.2f}s ---", flush=True)
            seen: dict[str, int] = {}
            hits = [s for s in snap if st["t0"] <= s[0] <= st["t1"]]
            for _t, name in hits:
                seen[name] = seen.get(name, 0) + 1
            if not hits:
                print("    （没采到样本）", flush=True)
            for name, cnt in sorted(seen.items(), key=lambda x: -x[1])[:5]:
                print(f"    {cnt:3d}×  {name}", flush=True)
            rd = [r for r in reads if st["t0"] <= r[0] <= st["t1"]]
            if rd:
                print(f"    期间 readData 调用 {len(rd)} 次，最大 {max(r[1] for r in rd)} 字节，"
                      f"最慢 {max(r[2] for r in rd):.2f}ms", flush=True)
        if reads:
            tot = len(reads) / SECONDS
            slow = sorted(reads, key=lambda x: -x[2])[:5]
            print(flush=True)
            print(f"readData：{len(reads)} 次（{tot:.1f} 次/秒），"
                  f"最大块 {max(r[1] for r in reads)} 字节，"
                  f"平均耗时 {np.mean([r[2] for r in reads]):.3f}ms", flush=True)
            print("最慢的几次：" + "；".join(f"{t:.1f}s {ms:.2f}ms({n}B)" for t, n, ms in slow),
                  flush=True)

    QTimer.singleShot(400, wait_ready)
    tune_gc()
    app.exec()


if __name__ == "__main__":
    sys.exit(main())
