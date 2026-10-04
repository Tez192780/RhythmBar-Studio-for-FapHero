"""导出：离屏渲染 -> BGRA 原始帧 -> ffmpeg 管道 -> 带透明通道的视频。

支持：
  * MOV / ProRes 4444      (yuva444p10le)  —— PR/AE/达芬奇
  * MOV / QuickTime RLE    (argb)          —— 剪映等，纯色图形体积很小
  * WebM / VP9 alpha       (yuva420p)
  * WebM / VP8 alpha       (yuva420p)
  * PNG 序列               (rgba)
  * MP4 / 黑底 H.264       (无透明，剪辑里用滤色模式)
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from .audio import find_ffmpeg
from .model import Project
from .render import BarRenderer

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ------------------------------------------------------------------ 命令构建
def _quality_preset(fmt: str, quality: str) -> list[str]:
    q = quality if quality in ("fast", "balanced", "high") else "high"
    if fmt == "mov_prores":
        qs = {"fast": 10, "balanced": 7, "high": 4}[q]
        return ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
                "-alpha_bits", "16", "-vendor", "apl0", "-qscale:v", str(qs)]
    if fmt == "mov_qtrle":
        return ["-c:v", "qtrle", "-pix_fmt", "argb"]
    if fmt == "webm_vp9":
        cu = {"fast": 5, "balanced": 3, "high": 2}[q]
        crf = {"fast": 36, "balanced": 30, "high": 24}[q]
        return ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0",
                "-lag-in-frames", "0", "-b:v", "0", "-crf", str(crf), "-cpu-used", str(cu),
                "-row-mt", "1", "-tile-columns", "2", "-deadline", "good"]
    if fmt == "webm_vp8":
        cu = {"fast": 5, "balanced": 3, "high": 1}[q]
        crf = {"fast": 16, "balanced": 12, "high": 8}[q]
        return ["-c:v", "libvpx", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0",
                "-b:v", "0", "-crf", str(crf), "-qmin", "0", "-qmax", "50",
                "-cpu-used", str(cu), "-deadline", "good"]
    if fmt == "png_seq":
        lvl = {"fast": 1, "balanced": 4, "high": 6}[q]
        return ["-c:v", "png", "-pix_fmt", "rgba", "-compression_level", str(lvl)]
    if fmt == "mp4_black":
        preset = {"fast": "veryfast", "balanced": "medium", "high": "slow"}[q]
        crf = {"fast": 20, "balanced": 17, "high": 14}[q]
        return ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", preset, "-crf", str(crf),
                "-movflags", "+faststart"]
    raise ValueError(f"未知导出格式：{fmt}")


def has_alpha(fmt: str) -> bool:
    return fmt != "mp4_black"


def build_command(fmt: str, w: int, h: int, fps: int, quality: str, out_path: str, ffmpeg: str) -> list[str]:
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgra",
        "-s", f"{w}x{h}", "-r", str(int(fps)), "-i", "-",
        "-an",
    ]
    cmd += _quality_preset(fmt, quality)
    cmd += [out_path]
    return cmd


# ------------------------------------------------------------------ 时间范围
def compute_range(project: Project, audio_duration_ms: float = 0.0) -> tuple[float, float]:
    ex = project.export
    if ex.range_mode == "custom" and ex.t_end > ex.t_start:
        return float(ex.t_start), float(ex.t_end)
    lo, hi = project.chart.bounds()
    lead = float(project.render.approach_s) * 1000.0 + 150.0
    if ex.range_mode == "song":
        end = audio_duration_ms or (hi + ex.padding_ms)
        return 0.0, max(100.0, end)
    if ex.range_mode == "notes":
        if len(project.chart) == 0:
            return 0.0, max(1000.0, audio_duration_ms)
        return max(0.0, lo - lead), min(audio_duration_ms or 1e12, hi + float(ex.padding_ms))
    return 0.0, max(1000.0, audio_duration_ms or hi + 1000.0)


# ------------------------------------------------------------------ 画布布局
def bar_rect(project: Project, w: float, h: float) -> tuple[float, float, float, float]:
    """把节奏条按它自己的宽高比放进导出画布（整帧导出时不会把音符拉成巨型）。

    返回 (x, y, 宽, 高)。
    """
    rs = project.render
    ar = max(0.2, float(rs.width) / max(1.0, float(rs.height)))
    bw = float(w)
    bh = bw / ar
    if bh > h:
        bh = float(h)
        bw = bh * ar
    x = (w - bw) * 0.5
    va = getattr(project.export, "bar_valign", "center")
    if va == "top":
        y = 0.0
    elif va == "bottom":
        y = h - bh
    else:
        y = (h - bh) * 0.5
    return (x, y, bw, bh)


def draw_canvas(p, project: Project, w: int, h: int, t_ms: float, ss: int,
                solid: QColor | None) -> None:
    """在 w×h 逻辑画布上画一帧（ss 为超采样倍数）。"""
    p.save()
    if solid is not None:
        p.fillRect(QRectF(0, 0, float(w) * ss, float(h) * ss), solid)
    x, y, bw, bh = bar_rect(project, float(w), float(h))
    p.translate(x * ss, y * ss)
    BarRenderer(project).render(p, bw * ss, bh * ss, t_ms)
    p.restore()


def _solid_for(project: Project, fmt: str) -> QColor | None:
    ex = project.export
    if ex.background == "color":
        return QColor(ex.bg_color)
    if ex.background == "black":
        return QColor(0, 0, 0)
    if ex.background == "white":
        return QColor(255, 255, 255)
    if not has_alpha(fmt):
        return QColor(0, 0, 0)
    return None


# ------------------------------------------------------------------ 单帧渲染
def render_frame_image(project: Project, t_ms: float, w: int, h: int, supersample: int = 1,
                       background: str = "transparent", bg_color: str = "#000000") -> QImage:
    ss = max(1, int(supersample))
    old_bg, old_color = project.export.background, project.export.bg_color
    project.export.background, project.export.bg_color = background, bg_color
    img = QImage(int(w) * ss, int(h) * ss, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    draw_canvas(p, project, int(w), int(h), t_ms, ss, _solid_for(project, "still"))
    p.end()
    project.export.background, project.export.bg_color = old_bg, old_color
    if ss > 1:
        img = img.scaled(int(w), int(h), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return img.convertToFormat(QImage.Format_ARGB32)


def export_still(project: Project, t_ms: float, path: str, w: int | None = None, h: int | None = None,
                 supersample: int | None = None) -> str:
    ex = project.export
    img = render_frame_image(
        project, t_ms, w or ex.width, h or ex.height,
        supersample if supersample is not None else ex.supersample,
        ex.background, ex.bg_color,
    )
    if not img.save(path):
        raise RuntimeError(f"无法写入图片：{path}")
    return path


# ------------------------------------------------------------------ 主导出
@dataclass
class ExportResult:
    path: str
    frames: int
    seconds: float
    cancelled: bool = False


def export_video(
    project: Project,
    out_path: str,
    *,
    ffmpeg: str = "",
    progress: Callable[[int, int, float], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    audio_duration_ms: float = 0.0,
) -> ExportResult:
    """把工程导出成视频。progress(已完成帧, 总帧数, 预计剩余秒)。"""
    ex = project.export
    ff = ffmpeg or find_ffmpeg()
    if not ff:
        raise RuntimeError("找不到 ffmpeg.exe，请在「设置」里指定路径")

    w = max(2, int(ex.width) - int(ex.width) % 2)
    h = max(2, int(ex.height) - int(ex.height) % 2)
    fps = max(1, int(ex.fps))
    t0, t1 = compute_range(project, audio_duration_ms)
    total = max(1, int(round((t1 - t0) / 1000.0 * fps)))

    if ex.fmt == "png_seq":
        # 没写扩展名就当成目录，帧文件放进去
        if os.path.splitext(out_path)[1].lower() in (".png", ""):
            if os.path.splitext(out_path)[1]:
                folder = os.path.dirname(os.path.abspath(out_path))
                base = os.path.splitext(os.path.basename(out_path))[0]
            else:
                folder = os.path.abspath(out_path)
                base = os.path.basename(os.path.normpath(out_path)) or "frame"
        else:
            folder = os.path.dirname(os.path.abspath(out_path))
            base = os.path.splitext(os.path.basename(out_path))[0]
        os.makedirs(folder, exist_ok=True)
        out_path = os.path.join(folder, f"{base}_%05d.png")

    target_dir = os.path.dirname(os.path.abspath(out_path))
    if target_dir:
        os.makedirs(target_dir, exist_ok=True)
    cmd = build_command(ex.fmt, w, h, fps, ex.quality, out_path, ff)

    errors: list[str] = []

    def _read_stderr(pipe) -> None:
        try:
            for line in iter(pipe.readline, b""):
                if line:
                    errors.append(line.decode("utf-8", "ignore").rstrip())
                    del errors[:-12]
        except Exception:
            pass

    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=CREATE_NO_WINDOW,
    )
    assert proc.stdin is not None and proc.stderr is not None
    th = threading.Thread(target=_read_stderr, args=(proc.stderr,), daemon=True)
    th.start()

    ss = max(1, int(ex.supersample))
    img = QImage(w * ss, h * ss, QImage.Format_ARGB32_Premultiplied)
    solid = _solid_for(project, ex.fmt)
    renderer = BarRenderer(project)
    frame_bytes = w * h * 4
    started = time.time()
    done = 0
    cancelled = False

    try:
        for i in range(total):
            if is_cancelled is not None and is_cancelled():
                cancelled = True
                break
            t_ms = t0 + i * 1000.0 / fps
            img.fill(Qt.transparent)
            p = QPainter(img)
            draw_canvas(p, project, w, h, t_ms, ss, solid)
            p.end()
            if ss > 1:
                out = img.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            else:
                out = img
            if out.format() != QImage.Format_ARGB32:
                out = out.convertToFormat(QImage.Format_ARGB32)
            buf = out.constBits().tobytes()
            if len(buf) != frame_bytes:  # 有行对齐时按行裁剪
                stride = out.bytesPerLine()
                buf = b"".join(buf[y * stride: y * stride + w * 4] for y in range(h))
            proc.stdin.write(buf)
            done += 1
            if progress is not None and (done % 5 == 0 or done == total):
                elapsed = time.time() - started
                eta = (elapsed / done) * (total - done) if done else 0.0
                progress(done, total, eta)
        proc.stdin.close()
        code = proc.wait()
        if cancelled:
            return ExportResult(out_path, done, time.time() - started, True)
        if code != 0:
            tail = "\n".join(errors[-6:]) or f"ffmpeg 退出码 {code}"
            raise RuntimeError("导出失败：\n" + tail)
    except BrokenPipeError:
        tail = "\n".join(errors[-6:])
        raise RuntimeError("ffmpeg 提前退出：\n" + tail)
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
        th.join(timeout=1.0)

    if progress is not None:
        progress(done, total, 0.0)
    return ExportResult(out_path, done, time.time() - started, False)
