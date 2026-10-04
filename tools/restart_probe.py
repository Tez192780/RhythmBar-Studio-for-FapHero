r"""开发用：查「暂停后再次播放」时 QAudioSink 的真实状态变化。

    cmd /c "python -X utf8 -u tools\restart_probe.py > build\restart.log 2>&1"
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

from rbar.audio import AudioEngine  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG: list[str] = []
T0 = time.perf_counter()


def stamp() -> str:
    return f"{(time.perf_counter() - T0) * 1000:7.0f}ms"


def make_wav(path: str, seconds: float = 6.0, sr: int = 48000) -> None:
    n = int(sr * seconds)
    t = np.arange(n) / sr
    x = 0.3 * np.sin(2 * np.pi * 440 * t)
    pcm = np.clip(x * 32767, -32768, 32767).astype(np.int16)
    with wave.open(path, "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(np.repeat(pcm[:, None], 2, axis=1).tobytes())


def main() -> None:
    wav = os.path.join(ROOT, "build", "restart.wav")
    make_wav(wav)
    app = QApplication(sys.argv)
    eng = AudioEngine()
    eng.load(wav)
    print(f"载入 {eng.duration_ms:.0f}ms", flush=True)

    # 给 worker 的 tick 打探针
    from rbar.audio import _PlaybackWorker

    orig_tick = _PlaybackWorker._on_tick

    def probed(self) -> None:
        s = self._sink
        d = self._device
        if s is not None:
            try:
                LOG.append(
                    f"{stamp()}  worker: state={s.state()} processed={s.processedUSecs()/1000:7.1f}ms "
                    f"free={s.bytesFree():6d} atEnd={d.atEnd() if d else '-'} "
                    f"devpos={d._pos if d else '-'} playing={self._playing}")
            except Exception as e:  # noqa: BLE001
                LOG.append(f"{stamp()}  worker: 读取状态异常 {e}")
        orig_tick(self)

    _PlaybackWorker._on_tick = probed

    steps = []

    def step1() -> None:
        LOG.append(f"{stamp()}  == 第一次播放（从 1000ms）==")
        eng.play(1000.0)
        QTimer.singleShot(1500, step2)

    def step2() -> None:
        LOG.append(f"{stamp()}  == 暂停（位置 {eng.position_ms():.0f}）==")
        eng.pause()
        QTimer.singleShot(300, step3)

    def step3() -> None:
        LOG.append(f"{stamp()}  == 变速 0.5 再回 1.0（复现 audio_check 的序列）==")
        eng.set_rate(0.5)
        LOG.append(f"{stamp()}  set_rate(0.5) 后 pcm={len(eng._pcm)} 帧，playing={eng.playing}")
        eng.set_rate(1.0)
        LOG.append(f"{stamp()}  set_rate(1.0) 后 pcm={len(eng._pcm)} 帧，playing={eng.playing}")
        eng.seek(3000.0)
        LOG.append(f"{stamp()}  seek(3000) 后 _seek={eng._seek_ms:.0f} playing={eng.playing}")
        eng.play()
        LOG.append(f"{stamp()}  play() 后 _seek={eng._seek_ms:.0f} playing={eng.playing}")
        for i in range(1, 9):
            QTimer.singleShot(i * 250, lambda i=i: LOG.append(
                f"{stamp()}  主线程看到：pos={eng.position_ms():7.0f} "
                f"playing={eng.playing} _seek={eng._seek_ms:.0f} "
                f"_pos_at={eng._pos_at:.0f}"))
        QTimer.singleShot(2500, step4)

    def step4() -> None:
        LOG.append(f"{stamp()}  == 只播末尾 500ms，观察结束判定 ==")
        eng.finished.connect(lambda: LOG.append(f"{stamp()}  >>> finished 信号收到"))
        eng.play(4500.0)
        QTimer.singleShot(3000, step5)

    def step5() -> None:
        LOG.append(f"{stamp()}  == 结束（位置 {eng.position_ms():.0f}，"
                   f"playing={eng.playing}，欠载={eng.underruns}）==")
        for line in LOG:
            print(line, flush=True)
        eng.shutdown()
        app.quit()

    QTimer.singleShot(300, step1)
    app.exec()


if __name__ == "__main__":
    main()
