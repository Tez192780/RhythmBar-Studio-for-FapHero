"""参考视频：抽帧做胶片条、按需取精确帧、按画面变化找镜头切换。

只依赖 ffmpeg/ffprobe + numpy，不引入解码库。
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Callable

import numpy as np
from PySide6.QtGui import QImage

from .audio import find_ffmpeg, find_ffprobe

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@dataclass
class VideoInfo:
    path: str = ""
    duration_ms: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0


def probe_video(path: str, ffprobe: str = "") -> VideoInfo:
    fp = ffprobe or find_ffprobe()
    info = VideoInfo(path=path)
    if not fp:
        return info
    try:
        out = subprocess.run(
            [fp, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,r_frame_rate,duration", "-show_entries", "format=duration",
             "-of", "json", path],
            capture_output=True, creationflags=CREATE_NO_WINDOW,
        ).stdout.decode("utf-8", "ignore")
        d = json.loads(out or "{}")
        st = (d.get("streams") or [{}])[0]
        info.width = int(st.get("width") or 0)
        info.height = int(st.get("height") or 0)
        fr = str(st.get("r_frame_rate") or "0/1")
        if "/" in fr:
            a, b = fr.split("/")
            info.fps = float(a) / float(b) if float(b) else 0.0
        dur = st.get("duration") or (d.get("format") or {}).get("duration") or 0
        info.duration_ms = float(dur or 0) * 1000.0
    except Exception:
        pass
    return info


def probe_audio_stream(path: str, ffprobe: str = "") -> dict:
    """视频里有没有声音轨；返回 {has, channels, sample_rate, codec}。"""
    fp = ffprobe or find_ffprobe()
    out_info = {"has": False, "channels": 0, "sample_rate": 0, "codec": ""}
    if not fp:
        return out_info
    try:
        out = subprocess.run(
            [fp, "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=codec_name,channels,sample_rate", "-of", "json", path],
            capture_output=True, creationflags=CREATE_NO_WINDOW,
        ).stdout.decode("utf-8", "ignore")
        d = json.loads(out or "{}")
        st = (d.get("streams") or [{}])[0] if d.get("streams") else {}
        if st:
            out_info["has"] = True
            out_info["channels"] = int(st.get("channels") or 0)
            out_info["sample_rate"] = int(float(st.get("sample_rate") or 0))
            out_info["codec"] = str(st.get("codec_name") or "")
    except Exception:
        pass
    return out_info


def _thumb_size(info: VideoInfo, thumb_h: int) -> tuple[int, int]:
    src_w = max(1, info.width or 16)
    src_h = max(1, info.height or 9)
    tw = int(round(src_w * thumb_h / src_h))
    tw -= tw % 2
    return max(2, tw), thumb_h


# ------------------------------------------------------------------ 胶片条
class Filmstrip:
    """一张“胶片”：等间隔抽出来的小图 + 每张的时间。"""

    def __init__(self, path: str, times: np.ndarray, frames: np.ndarray,
                 src_w: int, src_h: int, step_ms: float):
        self.path = path
        self.times = times                  # (N,) float32 毫秒
        self.frames = frames                # (N, h, w, 4) uint8 RGBA
        self.src_w = src_w
        self.src_h = src_h
        self.step_ms = step_ms
        self._qimages: dict[int, tuple[QImage, np.ndarray]] = {}

    @property
    def count(self) -> int:
        return int(self.frames.shape[0])

    @property
    def thumb_h(self) -> int:
        return int(self.frames.shape[1])

    @property
    def thumb_w(self) -> int:
        return int(self.frames.shape[2])

    def image(self, i: int) -> QImage | None:
        if i < 0 or i >= self.count:
            return None
        got = self._qimages.get(i)
        if got is None:
            arr = np.ascontiguousarray(self.frames[i])
            img = QImage(arr.data, self.thumb_w, self.thumb_h,
                         self.thumb_w * 4, QImage.Format_RGBA8888)
            got = (img, arr)                 # 保住底层内存，QImage 不拷贝
            if len(self._qimages) > 2000:
                self._qimages.clear()
            self._qimages[i] = got
        return got[0]

    def index_at(self, t_ms: float) -> int:
        if self.count == 0 or self.step_ms <= 0:
            return 0
        i = int(round((float(t_ms) - float(self.times[0])) / self.step_ms))
        return max(0, min(self.count - 1, i))

    def time_of(self, i: int) -> float:
        i = max(0, min(self.count - 1, int(i)))
        return float(self.times[i])


def extract_filmstrip(
    path: str,
    info: VideoInfo | None = None,
    ffmpeg: str = "",
    target_fps: float = 4.0,
    thumb_h: int = 72,
    max_frames: int = 1500,
    progress: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> Filmstrip:
    """一次 ffmpeg 解码出等间隔缩略图（大视频会花几秒，放到后台线程跑）。"""
    ff = ffmpeg or find_ffmpeg()
    if not ff:
        raise RuntimeError("找不到 ffmpeg.exe")
    info = info or probe_video(path)
    dur_s = max(0.2, (info.duration_ms or 1000.0) / 1000.0)
    n_want = int(max(1, min(max_frames, round(dur_s * max(0.2, target_fps)))))
    step_s = dur_s / n_want
    tw, th = _thumb_size(info, thumb_h)
    cmd = [
        ff, "-v", "error", "-nostdin", "-i", path,
        "-an", "-sn",
        "-vf", f"fps={1.0 / step_s:.6f},scale={tw}:{th}",
        "-f", "rawvideo", "-pix_fmt", "rgba", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=CREATE_NO_WINDOW)
    assert proc.stdout is not None
    frame_bytes = tw * th * 4
    chunks: list[bytes] = []
    n = 0
    try:
        while True:
            if is_cancelled is not None and is_cancelled():
                proc.kill()
                break
            buf = proc.stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            chunks.append(buf)
            n += 1
            if progress is not None and n % 4 == 0:
                progress(n, n_want)
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.stdout.close()
            proc.stderr.close()
        except Exception:
            pass
    if is_cancelled is not None and is_cancelled():
        raise RuntimeError("已取消")
    if not chunks:
        raise RuntimeError("这个视频抽不出画面（格式不支持？）")
    data = b"".join(chunks)
    frames = np.frombuffer(data, dtype=np.uint8).reshape(-1, th, tw, 4).copy()
    times = (np.arange(frames.shape[0], dtype=np.float32)) * step_s * 1000.0
    return Filmstrip(path, times, frames, info.width, info.height, step_s * 1000.0)


# ------------------------------------------------------------------ 精确取帧
def decode_frame(path: str, t_ms: float, info: VideoInfo | None = None,
                 height: int = 270, ffmpeg: str = "") -> tuple[np.ndarray, int, int]:
    """解码指定时刻的单帧（输入 seek，很快）。返回 (RGBA 数组, w, h)。"""
    ff = ffmpeg or find_ffmpeg()
    if not ff:
        raise RuntimeError("找不到 ffmpeg.exe")
    info = info or probe_video(path)
    th = height
    src_w = max(1, info.width or 16)
    src_h = max(1, info.height or 9)
    tw = int(round(src_w * th / src_h))
    tw -= tw % 2
    tw = max(2, tw)
    cmd = [
        ff, "-v", "error", "-nostdin",
        "-ss", f"{max(0.0, float(t_ms)) / 1000.0:.3f}", "-i", path,
        "-frames:v", "1", "-an", "-sn",
        "-vf", f"scale={tw}:{th}",
        "-f", "rawvideo", "-pix_fmt", "rgba", "-",
    ]
    out = subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW).stdout
    need = tw * th * 4
    if len(out) < need:
        raise RuntimeError("取帧失败")
    return np.frombuffer(out[:need], dtype=np.uint8).reshape(th, tw, 4), tw, th


# ------------------------------------------------------------- 镜头切换检测
def scene_scores(filmstrip: Filmstrip, chunk: int = 128) -> np.ndarray:
    """相邻两帧的画面变化量（0~1，按 RGB 三通道平均）。

    注意：不能用灰度差 —— 颜色不同但亮度接近的硬切（比如 ffmpeg 的 green #008000
    切到 red）灰度差几乎为 0，会整段漏检。
    """
    if filmstrip is None or filmstrip.count < 2:
        return np.zeros(0, dtype=np.float32)
    f = filmstrip.frames
    n = filmstrip.count
    out = np.empty(n - 1, dtype=np.float32)
    for s in range(0, n - 1, chunk):
        e = min(n - 1, s + chunk)
        a = f[s:e, :, :, :3].astype(np.float32)
        b = f[s + 1: e + 1, :, :, :3].astype(np.float32)
        np.subtract(b, a, out=b)
        np.abs(b, out=b)
        out[s:e] = b.mean(axis=(1, 2, 3)) / 255.0
    return out


def scene_cuts(filmstrip: Filmstrip, threshold: float = 0.35,
               min_gap_ms: float = 300.0) -> list[float]:
    """画面变化超过阈值就算一次镜头切换（相邻太近的只留最剧烈的一次）。"""
    scores = scene_scores(filmstrip)
    if scores.size == 0:
        return []
    times = filmstrip.times
    cuts: list[float] = []
    best = -1.0
    for i in range(scores.size):
        if scores[i] < threshold:
            continue
        t = float(times[min(i + 1, len(times) - 1)])
        if cuts and t - cuts[-1] < min_gap_ms:
            if scores[i] > best:
                cuts[-1] = t
                best = float(scores[i])
            continue
        cuts.append(t)
        best = float(scores[i])
    return cuts
