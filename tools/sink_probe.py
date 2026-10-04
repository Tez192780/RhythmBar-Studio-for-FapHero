"""开发用：诊断 QAudioSink 的时间基准（processedUSecs 到底准不准）。"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("QT_QPA_PLATFORM", None)

import numpy as np  # noqa: E402
from PySide6.QtCore import QIODevice, QTimer  # noqa: E402
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


class Dev(QIODevice):
    def __init__(self, buf):
        super().__init__()
        self.data = memoryview(np.ascontiguousarray(buf).data)
        self.pos = 0

    def readData(self, maxlen):
        maxlen -= maxlen % 4
        end = min(len(self.data), self.pos + maxlen)
        out = bytes(self.data[self.pos:end])
        self.pos = end
        return out

    def writeData(self, d):
        return 0

    def bytesAvailable(self):
        return len(self.data) - self.pos + super().bytesAvailable()

    def isSequential(self):
        return True


def main() -> None:
    app = QApplication(sys.argv)
    sr = 48000
    n = sr * 6
    pcm = (np.sin(2 * np.pi * 440 * np.arange(n) / sr) * 8000).astype(np.int16)
    stereo = np.repeat(pcm[:, None], 2, axis=1)

    dev_out = QMediaDevices.defaultAudioOutput()
    print("默认输出设备：", dev_out.description(), "isNull=", dev_out.isNull())
    fmt = QAudioFormat()
    fmt.setSampleRate(sr)
    fmt.setChannelCount(2)
    fmt.setSampleFormat(QAudioFormat.Int16)
    print("格式受支持：", dev_out.isFormatSupported(fmt), " 首选：", dev_out.preferredFormat().sampleRate(),
          dev_out.preferredFormat().channelCount(), dev_out.preferredFormat().sampleFormat())

    sink = QAudioSink(dev_out, fmt)
    sink.setBufferSize(sr * 4 // 20)      # 50ms
    d = Dev(stereo)
    d.open(QIODevice.ReadOnly)
    sink.start(d)
    print("bufferSize =", sink.bufferSize())

    import time

    t0 = time.perf_counter()
    ticks = {"n": 0}

    def tick() -> None:
        ticks["n"] += 1
        el = (time.perf_counter() - t0) * 1000.0
        print(f"  t={el:7.0f}ms  wall_pos={el:7.0f}  processed={sink.processedUSecs()/1000:7.0f} "
              f"elapsed={sink.elapsedUSecs()/1000:7.0f}  state={sink.state()}  "
              f"bytes_read={d.pos}/{len(d.data)}  free={sink.bytesFree()}")
        if ticks["n"] >= 8:
            sink.stop()
            app.quit()
            return
        QTimer.singleShot(400, tick)

    QTimer.singleShot(400, tick)
    app.exec()


if __name__ == "__main__":
    main()
