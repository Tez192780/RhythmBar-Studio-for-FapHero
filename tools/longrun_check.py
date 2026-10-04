r"""开发用：40 秒长测，专抓「隔几秒一小卡」。

同时监控：主线程定时器间隔、GC 各代回收（时间/次数）、绘制耗时、
音频欠载（Idle 次数）、窗口/控件尺寸变化（布局重排）、音频位置漂移。

    cmd /c "python -X utf8 -u tools\longrun_check.py > build\longrun.log 2>&1"
"""

from __future__ import annotations

import gc
import os
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("QT_QPA_PLATFORM", None)

import numpy as np  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rbar.app import QSS, build_palette, tune_gc  # noqa: E402
from rbar.model import Note, Project  # noqa: E402
from rbar.settings import AppSettings  # noqa: E402
from rbar.ui.mainwindow import MainWindow  # noqa: E402
from rbar.ui.preview import PreviewWidget  # noqa: E402
from rbar.ui.timeline import TimelineWidget  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
SECONDS = 40.0
STALL_MS = 30.0        # 超过这个就算一次「卡」

REPORT: dict[str, list] = {"paint_tl": [], "paint_pv": [], "gap": []}
STALLS: list[tuple[float, float, str]] = []
GCS: list[tuple[float, float, int, int]] = []      # (时刻, 耗时ms, 代, 回收数)
UNDERRUN = {"idle": 0, "samples": 0}
SIZES: list[tuple[float, str]] = []
MARK: dict[str, float] = {}


def make_audio(path: str, seconds: float, sr: int = 48000, bpm: float = 128.0) -> None:
    """有点低频+高频内容的 40 秒素材，方便观察频谱/波形。"""
    n = int(sr * seconds)
    t = np.arange(n) / sr
    x = 0.25 * np.sin(2 * np.pi * 55 * t) + 0.15 * np.sin(2 * np.pi * 220 * t)
    beat = 60.0 / bpm
    k = 0
    while k * beat < seconds:
        p = int(k * beat * sr)
        d = int(0.05 * sr)
        env = np.exp(-np.arange(d) / (0.01 * sr))
        seg = np.sin(2 * np.pi * 1800 * np.arange(d) / sr) * env * 0.5
        e = min(n, p + d)
        x[p:e] += seg[: e - p]
        k += 1
    pcm = np.clip(x * 32767 / max(1e-6, np.abs(x).max()), -32768, 32767).astype(np.int16)
    with wave.open(path, "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(np.repeat(pcm[:, None], 2, axis=1).tobytes())


def now() -> float:
    return time.perf_counter() - MARK["t0"]


def patch_paint(cls, key: str) -> None:
    orig = cls.paintEvent

    def timed(self, ev):
        t0 = time.perf_counter()
        orig(self, ev)
        REPORT[key].append((time.perf_counter() - t0) * 1000.0)

    cls.paintEvent = timed


def patch_method(cls, name: str, key: str) -> None:
    orig = getattr(cls, name)

    def timed(self, *a, **kw):
        t0 = time.perf_counter()
        r = orig(self, *a, **kw)
        REPORT.setdefault(key, []).append((time.perf_counter() - t0) * 1000.0)
        return r

    setattr(cls, name, timed)


def gc_callback(phase: str, info: dict) -> None:
    if phase == "start":
        MARK["gc"] = time.perf_counter()
    else:
        t0 = MARK.pop("gc", None)
        if t0 is not None:
            GCS.append((now(), (time.perf_counter() - t0) * 1000.0,
                        int(info.get("generation", -1)), int(info.get("collected", 0))))


def main() -> None:
    os.makedirs(BUILD, exist_ok=True)
    wav = os.path.join(BUILD, "longrun.wav")
    if not os.path.isfile(wav):
        print("生成 40 秒测试音频…", flush=True)
        make_audio(wav, SECONDS + 5)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(build_palette())
    app.setStyleSheet(QSS)
    patch_paint(TimelineWidget, "paint_tl")
    patch_paint(PreviewWidget, "paint_pv")
    patch_method(TimelineWidget, "_ensure_bg", "ensure_bg")

    win = MainWindow(AppSettings(), Project())
    win.silent = True
    win.resize(1600, 960)
    win.show()

    lite = "--lite" in sys.argv          # 对照实验：关掉实时频谱与频谱图
    if lite:
        win.a_live.setChecked(False)
        win.a_spec.setChecked(False)
        win.spectrum.setVisible(False)

    pr = win.doc.project
    for i in range(600):                     # 密集音符，制造真实绘制负载
        pr.chart.add(Note(i * 62.0, ["cyan", "magenta", "gray"][i % 3], i % 3))
    win.on_notes_changed()
    win.load_audio(wav)
    app.processEvents()

    state = {"last": 0.0, "last_size": (0, 0), "t0": 0.0}

    def wait_ready(elapsed: int = 0) -> None:
        need_spec = not lite
        if (win.engine.samples is None or (need_spec and win.spec is None)) and elapsed < 30000:
            QTimer.singleShot(200, lambda: wait_ready(elapsed + 200))
            return
        print(f"就绪：音频 {win.engine.duration_ms:.0f}ms，频谱 "
              f"{win.spec.data.shape if win.spec else None}，音符 {len(pr.chart)}", flush=True)
        MARK["t0"] = time.perf_counter()
        state["t0"] = MARK["t0"]
        if "--zoom" in sys.argv:            # 放大编辑（背景缓存会周期性重画）
            win.doc.state.px_per_ms = 0.2
            win.seek(2000.0)
        win.play()

        resp = QTimer()
        resp.setInterval(10)

        def on_resp() -> None:
            t = now()
            if state["last"]:
                gap = (t - state["last"]) * 1000.0
                REPORT["gap"].append(gap)
                if gap > STALL_MS:
                    STALLS.append((t, gap, "?"))
            state["last"] = t
            # 布局/尺寸变化
            sz = (win.timeline.width(), win.timeline.height(),
                  win.preview.width(), win.preview.height())
            if sz != state["last_size"]:
                if state["last_size"] != (0, 0, 0, 0):
                    SIZES.append((t, str(sz)))
                state["last_size"] = sz

        resp.timeout.connect(on_resp)
        resp.start()
        state["resp"] = resp

        poll = QTimer()
        poll.setInterval(10)

        def on_poll() -> None:
            if win.engine.playing:
                UNDERRUN["samples"] += 1
                UNDERRUN["idle"] = max(UNDERRUN["idle"], win.engine.underruns)

        poll.timeout.connect(on_poll)
        poll.start()
        state["poll"] = poll
        gc.callbacks.append(gc_callback)
        QTimer.singleShot(int(SECONDS * 1000), finish)

    def finish() -> None:
        state["resp"].stop()
        state["poll"].stop()
        try:
            gc.callbacks.remove(gc_callback)
        except ValueError:
            pass
        win.pause()
        gaps = np.array(REPORT["gap"]) if REPORT["gap"] else np.zeros(1)
        tl = np.array(REPORT["paint_tl"]) if REPORT["paint_tl"] else np.zeros(1)
        pv = np.array(REPORT["paint_pv"]) if REPORT["paint_pv"] else np.zeros(1)
        print(flush=True)
        print(f"=== {SECONDS:.0f} 秒长测结果 ===", flush=True)
        print(f"主线程定时器：平均 {gaps.mean():.2f}ms  p99 {np.percentile(gaps, 99):.1f}ms  "
              f"最大 {gaps.max():.1f}ms", flush=True)
        print(f"超过 {STALL_MS:.0f}ms 的停顿：{len(STALLS)} 次", flush=True)
        for t, g, _c in sorted(STALLS, key=lambda x: -x[1])[:12]:
            near = [x for x in GCS if abs(x[0] - t) < 0.05]
            tag = f"  ← GC(第{x[2]}代 {x[1]:.1f}ms)" if near else ""
            print(f"    {t:7.2f}s  {g:7.1f}ms{tag}", flush=True)
        print(f"时间轴绘制：平均 {tl.mean():.2f}ms  p99 {np.percentile(tl, 99):.2f}ms  "
              f"最大 {tl.max():.2f}ms  次数 {tl.size}", flush=True)
        print(f"预览条绘制：平均 {pv.mean():.2f}ms  p99 {np.percentile(pv, 99):.2f}ms  "
              f"最大 {pv.max():.2f}ms  次数 {pv.size}", flush=True)
        bg = np.array(REPORT["ensure_bg"]) if REPORT.get("ensure_bg") else np.zeros(1)
        heavy = bg[bg > 0.5]
        print(f"背景缓存 _ensure_bg：调用 {bg.size} 次，其中重画 {heavy.size} 次；"
              f"重画平均 {heavy.mean() if heavy.size else 0:.2f}ms  "
              f"最大 {bg.max():.2f}ms", flush=True)
        if GCS:
            gen = {}
            for _, ms, g, n in GCS:
                d = gen.setdefault(g, [0, 0.0, 0])
                d[0] += 1
                d[1] += ms
                d[2] += n
            print(f"GC 共 {len(GCS)} 次：" + "；".join(
                f"第{g}代 {v[0]}次/合计{v[1]:.1f}ms/回收{v[2]}个" for g, v in sorted(gen.items())),
                flush=True)
            worst = sorted(GCS, key=lambda x: -x[1])[:5]
            print("最慢的 GC：" + "；".join(f"{t:.1f}s 第{g}代 {ms:.1f}ms" for t, ms, g, _ in worst),
                  flush=True)
        else:
            print("GC 一次都没触发", flush=True)
        print(f"音频欠载：{UNDERRUN['idle']}/{UNDERRUN['samples']} 次采样处于 Idle", flush=True)
        print(f"控件尺寸变化：{len(SIZES)} 次" + (f"（{SIZES[:3]}）" if SIZES else ""), flush=True)
        win.doc.mark_clean()
        win.close()
        app.quit()

    QTimer.singleShot(400, wait_ready)
    tune_gc()
    app.exec()


if __name__ == "__main__":
    sys.exit(main())
