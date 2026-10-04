"""开发用：把每种导出格式都跑一遍，用 ffprobe 校验透明通道是否真的在。"""

from __future__ import annotations

import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from rbar.audio import find_ffmpeg, find_ffprobe  # noqa: E402
from rbar.demo import make_demo_project  # noqa: E402
from rbar.exporter import export_video  # noqa: E402
from rbar.model import FORMATS  # noqa: E402


def probe(path: str) -> str:
    fp = find_ffprobe()
    if not fp:
        return "(无 ffprobe)"
    out = subprocess.run(
        [fp, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=codec_name,pix_fmt,width,height,nb_frames,duration", "-of",
         "default=nw=1", path],
        capture_output=True,
    ).stdout.decode("utf-8", "ignore").strip().replace("\n", " ")
    return out


def alpha_frames(path: str, w: int, h: int, ss: float = 0.45) -> np.ndarray | None:
    """解码一帧，返回 alpha 二维数组。"""
    ff = find_ffmpeg()
    if not ff:
        return None
    extra = []
    if path.lower().endswith(".webm"):
        extra = ["-c:v", "libvpx-vp9" if "vp9" in path.lower() else "libvpx"]
    seek = [] if path.lower().endswith(".png") else ["-ss", str(ss)]
    cmd = [ff, "-v", "error"] + extra + seek + ["-i", path, "-frames:v", "1",
           "-vf", "format=rgba", "-f", "rawvideo", "-pix_fmt", "rgba", "-"]
    out = subprocess.run(cmd, capture_output=True,
                         creationflags=0x08000000 if os.name == "nt" else 0).stdout
    need = w * h * 4
    if len(out) < need:
        return None
    return np.frombuffer(out[:need], dtype=np.uint8).reshape(h, w, 4)[:, :, 3]


def alpha_probe(path: str, w: int, h: int, ss: float = 0.45) -> str:
    """真正解码一帧看 alpha 分布：全透明 / 半透明 / 不透明 占比。

    注意：ffmpeg 自带的 vp8/vp9 解码器不还原 alpha 层，必须显式用 libvpx 解码器。
    """
    a = alpha_frames(path, w, h, ss)
    if a is None:
        return "alpha? 解码失败"
    return (f"alpha 全透={100.0*(a==0).mean():5.1f}% 半透={100.0*((a>0)&(a<250)).mean():5.1f}% "
            f"不透={100.0*(a>=250).mean():5.1f}% min={int(a.min())} max={int(a.max())}")


def alpha_rows(path: str, w: int, h: int) -> tuple[str, bool]:
    """整帧导出时检查条带是否只在中间那一条（上下应该全透明）。"""
    a = alpha_frames(path, w, h, 0.2)
    if a is None:
        return ("alpha? 解码失败", False)
    top = int(a[: h // 4].max())
    mid = int(a[h // 3: 2 * h // 3].max())
    bottom = int(a[3 * h // 4:].max())
    ok = top == 0 and mid > 100 and bottom == 0
    return (f"alpha 行分布: 上={top} 中={mid} 下={bottom}（期望 上=0 中>0 下=0）", ok)


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    out_dir = os.path.join(ROOT, "build", "export_test")
    os.makedirs(out_dir, exist_ok=True)

    ok = True
    for fmt in FORMATS:
        proj = make_demo_project(bars=4)
        ex = proj.export
        ex.fmt = fmt
        ex.fps = 30
        ex.width, ex.height = 480, 60
        ex.supersample = 2
        ex.quality = "fast"
        ex.range_mode = "custom"
        ex.t_start, ex.t_end = 0.0, 1500.0
        ext = {"mov_prores": ".mov", "mov_qtrle": ".mov", "webm_vp9": ".webm",
               "webm_vp8": ".webm", "mp4_black": ".mp4", "png_seq": ""}[fmt]
        out = os.path.join(out_dir, f"test_{fmt}{ext}")
        try:
            res = export_video(proj, out, progress=None, audio_duration_ms=0.0)
            size = os.path.getsize(res.path) if os.path.isfile(res.path) else -1
            extra = ""
            if fmt == "png_seq":
                folder = os.path.join(out_dir, "test_png_seq")
                files = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
                size = sum(os.path.getsize(os.path.join(folder, f)) for f in files)
                extra = f"  files={len(files)}"
                res_path = os.path.join(folder, files[0]) if files else ""
            else:
                res_path = res.path
            print(f"[OK] {fmt:11s} frames={res.frames:4d} {res.seconds:5.1f}s "
                  f"{size/1024:8.1f}KB{extra}\n     {probe(res_path)}\n     {alpha_probe(res_path, 480, 60)}")
        except Exception as e:
            ok = False
            print(f"[FAIL] {fmt}: {e}")
    # 整帧画布（1920×1080 里塞 1920×120 的条带）
    if ok:
        proj = make_demo_project(bars=4)
        ex = proj.export
        ex.fmt = "mov_qtrle"
        ex.fps = 30
        ex.width, ex.height = 640, 360
        ex.supersample = 1
        ex.quality = "fast"
        ex.range_mode = "custom"
        ex.t_start, ex.t_end = 0.0, 500.0
        out = os.path.join(out_dir, "test_fullframe.mov")
        try:
            res = export_video(proj, out, audio_duration_ms=0.0)
            lo, hi = alpha_rows(out, 640, 360)
            print(f"[OK] 整帧画布 frames={res.frames} {probe(out)}\n     {lo}")
            ok = hi
        except Exception as e:
            ok = False
            print(f"[FAIL] 整帧画布: {e}")
    app.quit()
    print("ALL OK" if ok else "有失败项")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
