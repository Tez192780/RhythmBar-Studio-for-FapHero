r"""开发用：剥掉本项目所有界面代码，只留「Qt + 一个音频播放线程 + 10ms 定时器」。

目的：判断 160ms 停顿是来自我们的代码，还是来自 Windows 音频栈/驱动。

    cmd /c "python -X utf8 -u tools\audio_os_probe.py > build\aos.log 2>&1"
    参数加 --noaudio 则不打开音频设备（对照组）
"""

from __future__ import annotations

import sys
import time

import numpy as np
from PySide6.QtCore import QIODevice, QObject, QThread, QTimer, Slot
from PySide6.QtWidgets import QApplication

SECONDS = 30.0
STEP = 0.010


class Dev(QIODevice):
    def __init__(self, buf: bytes):
        super().__init__()
        self._b = memoryview(buf).cast("B")
        self._pos = 0

    def readData(self, maxlen):
        maxlen -= maxlen % 4
        end = min(len(self._b), self._pos + maxlen)
        out = bytes(self._b[self._pos:end])
        self._pos = end
        return out

    def writeData(self, d):
        return 0

    def bytesAvailable(self):
        return len(self._b) - self._pos + super().bytesAvailable()

    def isSequential(self):
        return True

    def atEnd(self):
        return self._pos >= len(self._b)


class Player(QObject):
    """跟我们项目一样：sink 建在独立线程里。"""

    @Slot()
    def start(self) -> None:
        from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

        sr = 48000
        t = np.arange(sr * 60) / sr
        pcm = (np.sin(2 * np.pi * 220 * t) * 6000).astype(np.int16)
        stereo = np.ascontiguousarray(np.repeat(pcm[:, None], 2, axis=1))
        fmt = QAudioFormat()
        fmt.setSampleRate(sr)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.Int16)
        self._dev = Dev(stereo.tobytes())
        self._dev.open(QIODevice.ReadOnly)
        self._sink = QAudioSink(QMediaDevices.defaultAudioOutput(), fmt)
        self._sink.setBufferSize(sr * 4 // 8)
        self._sink.setVolume(0.15)
        self._sink.start(self._dev)
        print("音频已启动（独立线程），sink 状态：",
              getattr(self._sink.state(), "name", self._sink.state()), flush=True)


def main() -> None:
    no_audio = "--noaudio" in sys.argv
    app = QApplication(sys.argv[:1])
    gaps: list[float] = []
    state = {"last": 0.0}

    th = None
    holder = {}
    if not no_audio:
        th = QThread()
        p = Player()
        p.moveToThread(th)
        holder["p"] = p
        th.started.connect(p.start)
        th.start()
    else:
        print("对照组：不打开音频设备", flush=True)

    def tick() -> None:
        now = time.perf_counter()
        if state["last"]:
            gaps.append((now - state["last"]) * 1000.0)
        state["last"] = now

    timer = QTimer()
    timer.setInterval(int(STEP * 1000))
    timer.timeout.connect(tick)
    timer.start()

    def done() -> None:
        timer.stop()
        a = np.array(gaps[10:]) if len(gaps) > 10 else np.zeros(1)
        over = a[a > 30]
        print(f"\n=== {'（对照组：无音频）' if no_audio else '（Qt + 音频线程）'} ===", flush=True)
        print(f"样本 {a.size}  平均 {a.mean():.2f}ms  p99 {np.percentile(a, 99):.1f}  "
              f"最大 {a.max():.1f}ms", flush=True)
        print(f"超过 30ms 的停顿：{over.size} 次"
              + ("：" + "、".join(f"{x:.0f}ms" for x in np.sort(over)[::-1][:8]) if over.size else ""),
              flush=True)
        if th is not None:
            th.quit()
            th.wait(1000)
        app.quit()

    QTimer.singleShot(int(SECONDS * 1000), done)
    app.exec()


if __name__ == "__main__":
    main()
