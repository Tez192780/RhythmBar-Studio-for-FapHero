"""音频：ffmpeg 解码 -> numpy；Qt 低延迟播放（含节拍器/变速/延迟校准）；BPM 与起音检测。"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
from typing import Callable

import numpy as np
from PySide6.QtCore import QIODevice, QObject, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

DEFAULT_SR = 48000


# --------------------------------------------------------------------- ffmpeg
def find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    candidates = [
        r"C:\Program Files\FFmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\FFmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        os.path.join(os.path.dirname(sys.executable), "ffmpeg.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return ""


def find_ffprobe() -> str:
    ff = find_ffmpeg()
    if ff:
        p = os.path.join(os.path.dirname(ff), "ffprobe.exe")
        if os.path.isfile(p):
            return p
    return shutil.which("ffprobe") or ""


def decode_audio(path: str, sr: int = DEFAULT_SR, ffmpeg: str | None = None) -> tuple[np.ndarray, int]:
    """解码成 float32。返回 (samples, sr)，samples 形状 (n,) 单声道或 (n,2) 立体声。"""
    ff = ffmpeg or find_ffmpeg()
    if not ff:
        raise RuntimeError("找不到 ffmpeg.exe，请在「设置」里指定路径")
    cmd = [
        ff, "-v", "error", "-nostdin", "-i", path,
        "-vn", "-sn", "-dn",
        "-f", "f32le", "-acodec", "pcm_f32le", "-ac", "2", "-ar", str(sr), "-",
    ]
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
    if proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", "ignore").strip().splitlines()
        raise RuntimeError("音频解码失败：" + (msg[-1] if msg else "未知错误"))
    data = np.frombuffer(proc.stdout, dtype=np.float32)
    if data.size % 2:
        data = data[:-1]
    return data.reshape(-1, 2), sr


def audio_duration_ms(path: str, ffprobe: str | None = None) -> float:
    fp = ffprobe or find_ffprobe()
    if not fp:
        return 0.0
    try:
        out = subprocess.run(
            [fp, "-v", "error", "-show_entries", "format=duration", "-of",
             "default=nw=1:nk=1", path],
            capture_output=True, creationflags=0x08000000 if os.name == "nt" else 0,
        ).stdout.decode("utf-8", "ignore").strip()
        return float(out) * 1000.0
    except Exception:
        return 0.0


# ----------------------------------------------------------------- 波形峰值
def mono_view(samples: np.ndarray) -> np.ndarray:
    return samples.mean(axis=1) if samples.ndim > 1 else samples


def peaks(samples: np.ndarray, buckets: int) -> np.ndarray:
    """按桶取绝对值峰值，用于画波形。"""
    mono = np.abs(mono_view(samples)).astype(np.float32, copy=False)
    n = mono.size
    buckets = max(1, int(buckets))
    if n == 0:
        return np.zeros(buckets, dtype=np.float32)
    if buckets >= n:
        return mono.copy()
    edges = np.linspace(0, n, buckets + 1).astype(np.int64)
    edges[-1] = n
    starts = edges[:-1]
    np.maximum(starts, 0, out=starts)
    starts = np.minimum(starts, n - 1)
    out = np.maximum.reduceat(mono, starts)
    # 空桶（starts 重复）用 0 兜底
    empty = np.diff(edges) <= 0
    if empty.any():
        out[empty] = 0.0
    return out.astype(np.float32)


def rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


# ------------------------------------------------------------------ 频谱图
def _spectrum_colormap() -> list[int]:
    """深蓝 -> 青 -> 绿 -> 黄 -> 白的调色板（256 级，ARGB）。"""
    stops = [
        (0.00, (8, 10, 24)),
        (0.22, (24, 44, 120)),
        (0.42, (16, 130, 160)),
        (0.62, (40, 190, 120)),
        (0.80, (240, 210, 70)),
        (1.00, (255, 255, 245)),
    ]
    out: list[int] = []
    for i in range(256):
        t = i / 255.0
        for k in range(len(stops) - 1):
            t0, c0 = stops[k]
            t1, c1 = stops[k + 1]
            if t <= t1 or k == len(stops) - 2:
                f = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
                f = max(0.0, min(1.0, f))
                r = int(c0[0] + (c1[0] - c0[0]) * f)
                g = int(c0[1] + (c1[1] - c0[1]) * f)
                b = int(c0[2] + (c1[2] - c0[2]) * f)
                out.append(0xFF000000 | (r << 16) | (g << 8) | b)
                break
    return out


class Spectrogram:
    """整首歌的频谱图（行=频率，列=时间），用来对音/找音头。

    行序：第 0 行是最高频，直接贴到画面上就是「低频在下」。
    """

    def __init__(self, data: np.ndarray, frame_ms: float, f_min: float, f_max: float):
        self.data = data               # (bins, frames) uint8
        self.frame_ms = float(frame_ms)
        self.bins = int(data.shape[0])
        self.frames = int(data.shape[1])
        self.f_min = float(f_min)
        self.f_max = float(f_max)
        self._img = None
        self._ct = _spectrum_colormap()

    @property
    def duration_ms(self) -> float:
        return self.frames * self.frame_ms

    def image(self):
        """带调色板的 QImage（缓存，一次构建）。"""
        if self._img is None:
            d = np.ascontiguousarray(self.data)
            img = QImage(d.data, self.frames, self.bins, self.frames, QImage.Format_Indexed8)
            img.setColorTable(self._ct)
            self._img = (img, d)       # 保住 numpy 内存
        return self._img[0]

    def freq_labels(self) -> list[tuple[float, int]]:
        """给界面画刻度用：[(频率, 行号), ...]"""
        out = []
        f = 100.0
        while f < self.f_max:
            k = math.log(self.f_max / f) / math.log(self.f_max / self.f_min)
            row = int(round(k * (self.bins - 1)))
            if 0 <= row < self.bins:
                out.append((f, row))
            f *= 10 if f < 1000 else 10
        return out


def spectrogram(
    samples: np.ndarray,
    sr: int = DEFAULT_SR,
    n_fft: int = 2048,
    hop: int = 512,
    bins: int = 192,
    f_min: float = 35.0,
    f_max: float = 16000.0,
    dynamic_db: float = 72.0,
    progress: Callable[[int, int], None] | None = None,
) -> Spectrogram:
    """算整首歌的频谱图（对数频率轴）。大文件要一两秒，放到后台线程跑。"""
    mono = mono_view(samples).astype(np.float32, copy=False)
    if mono.size < n_fft:
        return Spectrogram(np.zeros((bins, 1), dtype=np.uint8), hop * 1000.0 / sr, f_min, f_max)
    win = np.hanning(n_fft).astype(np.float32)
    n_frames = 1 + (mono.size - n_fft) // hop
    nyq = sr / 2.0
    f_max = min(float(f_max), nyq * 0.98)
    f_min = max(5.0, min(float(f_min), f_max * 0.5))
    # 每个输出行覆盖的 FFT bin 区间（对数分布）
    k = np.arange(bins, dtype=np.float64)
    freqs = f_max * np.power(f_min / f_max, k / max(1, bins - 1))    # 行 0 = 最高频
    idx = np.clip(np.round(freqs / nyq * (n_fft // 2)).astype(np.int64), 0, n_fft // 2)
    order = np.argsort(idx)          # reduceat 需要递增索引，排序后记得放回原行序
    edges = idx[order]
    frames_db: list[np.ndarray] = []
    peak = 1e-9
    chunk = 2048
    offs = np.arange(n_fft)[None, :]
    for s in range(0, n_frames, chunk):
        e = min(n_frames, s + chunk)
        base = np.arange(s, e) * hop
        frames = mono[base[:, None] + offs] * win
        spec = np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)
        np.maximum(spec, 1e-9, out=spec)
        vals = np.maximum.reduceat(spec, edges, axis=1)      # (frames, bins)
        agg = np.empty_like(vals)
        agg[:, order] = vals                                 # 还原成「行 0 = 高频」
        db = 20.0 * np.log10(agg)
        frames_db.append(db)
        peak = max(peak, float(db.max()))
        if progress is not None:
            progress(e, n_frames)
    all_db = np.concatenate(frames_db, axis=0).T      # (frames,bins) -> (bins,frames)
    all_db -= (peak - dynamic_db)
    np.clip(all_db, 0.0, dynamic_db, out=all_db)
    all_db *= (255.0 / dynamic_db)
    return Spectrogram(np.ascontiguousarray(all_db.astype(np.uint8)),
                       hop * 1000.0 / sr, f_min, f_max)


# --------------------------------------------------------------- 起音 / BPM
def onset_envelope(samples: np.ndarray, sr: int = DEFAULT_SR, hop: int = 512, n_fft: int = 1024) -> np.ndarray:
    """谱通量起音包络（每 hop 一个值）。"""
    mono = mono_view(samples).astype(np.float32, copy=False)
    if mono.size < n_fft:
        return np.zeros(1, dtype=np.float32)
    win = np.hanning(n_fft).astype(np.float32)
    n_frames = 1 + (mono.size - n_fft) // hop
    idx = np.arange(n_frames) * hop
    flux = np.zeros(n_frames, dtype=np.float32)
    prev = None
    chunk = 4096
    offset = np.arange(n_fft)[None, :]
    for s in range(0, n_frames, chunk):
        e = min(n_frames, s + chunk)
        frames = mono[idx[s:e, None] + offset] * win
        spec = np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)
        if prev is None:
            # 第一帧没有前一帧可比，通量记 0
            d = np.diff(spec, axis=0)
            if d.size:
                flux[s + 1: e] = d.sum(axis=1)
        else:
            spec = np.vstack([prev[None, :], spec])
            d = np.diff(spec, axis=0)
            np.maximum(d, 0, out=d)
            flux[s:e] = d.sum(axis=1)
        prev = spec[-1]
    if flux.size:
        flux -= flux.min()
        m = flux.max()
        if m > 0:
            flux /= m
    return flux


def envelope_times(flux: np.ndarray, sr: int, hop: int) -> np.ndarray:
    return np.arange(flux.size, dtype=np.float64) * hop * 1000.0 / sr


def estimate_bpm(samples: np.ndarray, sr: int = DEFAULT_SR, hop: int = 512) -> tuple[float, float]:
    """返回 (bpm, 置信度 0..1)。自相关 + 梳状加权。"""
    flux = onset_envelope(samples, sr, hop)
    if flux.size < 16:
        return 0.0, 0.0
    x = flux - flux.mean()
    ac = np.correlate(x, x, mode="full")[x.size - 1:]
    if ac[0] <= 0:
        return 0.0, 0.0
    ac = ac / ac[0]
    fps = sr / hop
    best_bpm, best_score = 0.0, -1.0
    for bpm in np.arange(60.0, 200.01, 0.1):
        lag = 60.0 / bpm * fps
        if lag >= ac.size - 1:
            continue
        i = int(round(lag))
        frac = lag - i
        v = ac[i] * (1 - frac) + ac[min(i + 1, ac.size - 1)] * frac
        # 梳状：把 2、3、4 倍周期的自相关也加权进来，抑制倍速歧义
        comb = v
        for mult in (2, 3, 4):
            j = int(round(lag * mult))
            if j < ac.size:
                comb += ac[j] / mult
        if comb > best_score:
            best_score, best_bpm = comb, bpm
    conf = max(0.0, min(1.0, best_score / 1.6))
    return float(best_bpm), float(conf)


def estimate_offset(samples: np.ndarray, bpm: float, sr: int = DEFAULT_SR, hop: int = 512) -> float:
    """给定期望 BPM，找一个让网格最贴合起音的相位偏移（毫秒）。"""
    flux = onset_envelope(samples, sr, hop)
    if flux.size < 16 or bpm <= 0:
        return 0.0
    t = envelope_times(flux, sr, hop)
    period = 60000.0 / bpm
    best_off, best = 0.0, -1.0
    for off in np.arange(0.0, period, max(1.0, period / 200.0)):
        phase = np.mod(t - off, period)
        w = np.exp(-np.square(phase / (period * 0.10)))
        score = float((flux * w).sum())
        if score > best:
            best, best_off = score, off
    return float(best_off)


def detect_onsets(
    samples: np.ndarray,
    sr: int = DEFAULT_SR,
    hop: int = 256,
    sensitivity: float = 1.0,
    min_gap_ms: float = 60.0,
) -> list[float]:
    """起音点（毫秒）列表，用于「自动铺点」。"""
    flux = onset_envelope(samples, sr, hop)
    if flux.size < 8:
        return []
    times = envelope_times(flux, sr, hop)
    # 自适应阈值：局部均值 + k*标准差
    win = max(4, int(0.15 * sr / hop))
    kernel = np.ones(win, dtype=np.float32) / win
    local_mean = np.convolve(flux, kernel, mode="same")
    local_sq = np.convolve(flux * flux, kernel, mode="same")
    local_std = np.sqrt(np.maximum(local_sq - local_mean * local_mean, 0.0))
    k = max(0.6, 2.2 - 1.4 * float(sensitivity))
    thresh = local_mean + k * local_std + 0.02

    out: list[float] = []
    last = -1e9
    for i in range(1, flux.size - 1):
        if flux[i] < thresh[i]:
            continue
        if flux[i] < flux[i - 1] or flux[i] < flux[i + 1]:
            continue
        t = float(times[i])
        if t - last < min_gap_ms:
            if out and flux[i] > flux[max(0, i - 1)]:
                out[-1] = t
                last = t
            continue
        out.append(t)
        last = t
    return out


# ------------------------------------------------------------------- 播放
class _PCMDevice(QIODevice):
    """把预渲染好的 int16 PCM 喂给 QAudioSink。

    注意：readData 的 maxlen 单位是**字节**，所以内部用扁平字节视图切片，
    否则会把「帧数」当「字节数」，一帧 4 字节 -> 实际吐出 4 倍数据。
    """

    def __init__(self, buf: np.ndarray, parent=None):
        super().__init__(parent)
        self._buf = np.ascontiguousarray(buf)
        self._data = memoryview(self._buf).cast("B")     # 扁平字节视图（零拷贝）
        self._nbytes = len(self._data)
        self._pos = 0

    def set_pos(self, byte_pos: int) -> None:
        self._pos = max(0, min(self._nbytes, int(byte_pos)))

    def readData(self, maxlen: int) -> bytes:
        maxlen = int(maxlen)
        if maxlen <= 0:
            return b""
        maxlen -= maxlen % 4
        end = min(self._nbytes, self._pos + maxlen)
        chunk = bytes(self._data[self._pos:end])
        self._pos = end
        return chunk

    def writeData(self, data) -> int:  # noqa: N802
        return 0

    def bytesAvailable(self) -> int:  # noqa: N802
        return self._nbytes - self._pos + super().bytesAvailable()

    def isSequential(self) -> bool:  # noqa: N802
        return True

    def atEnd(self) -> bool:  # noqa: N802
        return self._pos >= self._nbytes


class AudioEngine(QObject):
    """音频播放：位置精确、可变速、可加节拍器、可校准延迟。"""

    stateChanged = Signal(bool)     # 是否正在播放
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sr = DEFAULT_SR
        self.samples: np.ndarray | None = None      # float32 (n,2)
        self.path = ""
        self.duration_ms = 0.0
        self._pcm = np.zeros((0, 2), dtype=np.int16)   # 预渲染（含变速/节拍器）
        self._device: _PCMDevice | None = None
        self._sink: QAudioSink | None = None
        self._rate = 1.0
        self._metronome = False
        self._beats_ms: list[float] = []
        self._volume = 0.85
        self._playing = False
        self._seek_ms = 0.0
        self._start_us = 0.0
        self._latency_ms = 0.0
        self._auto_latency_ms = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------- 载入
    def load(self, path: str, ffmpeg: str = "") -> None:
        self.stop()
        samples, sr = decode_audio(path, DEFAULT_SR, ffmpeg or None)
        self.samples = samples
        self.sr = sr
        self.path = path
        self.duration_ms = samples.shape[0] * 1000.0 / sr
        self._rebuild()

    def clear(self) -> None:
        self.stop()
        self.samples = None
        self.path = ""
        self.duration_ms = 0.0
        self._pcm = np.zeros((0, 2), dtype=np.int16)

    # ------------------------------------------------------------- 预渲染
    def _resample(self) -> np.ndarray:
        assert self.samples is not None
        src = self.samples
        if abs(self._rate - 1.0) < 1e-6:
            out = src
        else:
            n_out = max(1, int(src.shape[0] / self._rate))
            idx = np.arange(n_out, dtype=np.float64) * self._rate
            i0 = np.floor(idx).astype(np.int64)
            i0 = np.clip(i0, 0, src.shape[0] - 1)
            i1 = np.clip(i0 + 1, 0, src.shape[0] - 1)
            frac = (idx - i0)[:, None]
            out = (src[i0] * (1 - frac) + src[i1] * frac).astype(np.float32)
        pcm = np.clip(out * 32767.0, -32768, 32767).astype(np.int16)
        return np.ascontiguousarray(pcm)

    def _rebuild(self) -> None:
        if self.samples is None:
            self._pcm = np.zeros((0, 2), dtype=np.int16)
            return
        pcm = self._resample()
        if self._metronome and self._beats_ms:
            self._mix_metronome(pcm)
        self._pcm = pcm

    def _mix_metronome(self, pcm: np.ndarray) -> None:
        sr = self.sr / max(0.01, self._rate)
        dur = int(sr * 0.012)
        t = np.arange(dur, dtype=np.float32) / sr
        env = np.exp(-t * 320.0).astype(np.float32)
        click_hi = (np.sin(2 * np.pi * 1600.0 * t) * env * 0.5 * 32767).astype(np.int16)
        click_lo = (np.sin(2 * np.pi * 1000.0 * t) * env * 0.34 * 32767).astype(np.int16)
        n = pcm.shape[0]
        for i, ms in enumerate(self._beats_ms):
            pos = int(ms / 1000.0 * sr / max(0.01, self._rate))
            if pos < 0 or pos >= n:
                continue
            end = min(n, pos + dur)
            click = click_hi if i % 4 == 0 else click_lo
            seg = pcm[pos:end].astype(np.int32)
            seg += click[: end - pos, None]
            pcm[pos:end] = np.clip(seg, -32768, 32767).astype(np.int16)

    # ------------------------------------------------------------- 设置
    def set_beats(self, beats_ms: list[float]) -> None:
        self._beats_ms = list(beats_ms)
        if self._metronome:
            self._rebuild()

    def set_metronome(self, on: bool) -> None:
        if on == self._metronome:
            return
        self._metronome = bool(on)
        was = self._playing
        pos = self.position_ms()
        self._rebuild()
        if was:
            self.play(pos)

    def set_rate(self, rate: float) -> None:
        rate = max(0.25, min(2.0, float(rate)))
        if abs(rate - self._rate) < 1e-6:
            return
        was = self._playing
        pos = self.position_ms()
        self._rate = rate
        self._rebuild()
        if was:
            self.play(pos)

    @property
    def rate(self) -> float:
        return self._rate

    def set_volume(self, v: float) -> None:
        self._volume = max(0.0, min(1.0, float(v)))
        if self._sink is not None:
            self._sink.setVolume(self._volume)

    def set_latency_ms(self, ms: float) -> None:
        self._latency_ms = float(ms)

    # ------------------------------------------------------------- 传输
    @property
    def playing(self) -> bool:
        return self._playing

    def _ensure_sink(self) -> QAudioSink:
        dev = QMediaDevices.defaultAudioOutput()
        if dev is None or dev.isNull():
            raise RuntimeError("没有可用的音频输出设备")
        fmt = QAudioFormat()
        fmt.setSampleRate(self.sr)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.Int16)
        sink = QAudioSink(dev, fmt, self)
        bytes_per_sec = self.sr * 4
        # 100ms 缓冲：窗口偶尔卡一下也不会让声卡饿死；缓冲带来的延迟由下面自动补偿
        buf_bytes = int(bytes_per_sec * 0.10)
        sink.setBufferSize(buf_bytes)
        sink.setVolume(self._volume)
        # 输出缓冲本身带来的固定延迟，自动补偿掉，用户校准值再叠加
        self._auto_latency_ms = buf_bytes / bytes_per_sec * 1000.0
        return sink

    def play(self, start_ms: float | None = None) -> None:
        if self._pcm.shape[0] == 0:
            return
        if start_ms is not None:
            self._seek_ms = max(0.0, min(self.duration_ms, float(start_ms)))
        self.stop(emit=False)
        self._device = _PCMDevice(self._pcm, self)
        pos_ms = self._seek_ms
        byte = int(pos_ms / 1000.0 * (self.sr / max(0.01, self._rate)) * 4)
        byte -= byte % 4
        self._device.set_pos(byte)
        self._device.open(QIODevice.ReadOnly)
        self._sink = self._ensure_sink()
        self._sink.start(self._device)
        self._playing = True
        self._timer.start()
        self.stateChanged.emit(True)

    def pause(self) -> None:
        if not self._playing:
            return
        self._seek_ms = self.position_ms()
        self.stop()

    def stop(self, emit: bool = True) -> None:
        was = self._playing
        self._playing = False
        self._timer.stop()
        if self._sink is not None:
            try:
                self._sink.stop()
            except Exception:
                pass
            self._sink.deleteLater()
            self._sink = None
        if self._device is not None:
            try:
                self._device.close()
            except Exception:
                pass
            self._device.deleteLater()
            self._device = None
        if was and emit:
            self.stateChanged.emit(False)

    def seek(self, ms: float) -> None:
        ms = max(0.0, float(ms))
        if self._playing:
            self.play(ms)
        else:
            self._seek_ms = ms

    def position_ms(self) -> float:
        if not self._playing or self._sink is None:
            return self._seek_ms
        elapsed_ms = self._sink.processedUSecs() / 1000.0 - self._auto_latency_ms - self._latency_ms
        pos = self._seek_ms + max(0.0, elapsed_ms) * self._rate
        if pos >= self.duration_ms:
            return self.duration_ms
        return pos

    def _tick(self) -> None:
        if self._playing and self.position_ms() >= self.duration_ms - 1:
            self._seek_ms = self.duration_ms
            self.stop()
            self.finished.emit()

    def make_click(self, accented: bool = False) -> None:
        """预览节拍器点击（不改工程数据）。"""
        try:
            import winsound

            winsound.Beep(1500 if accented else 1000, 28)
        except Exception:
            pass
