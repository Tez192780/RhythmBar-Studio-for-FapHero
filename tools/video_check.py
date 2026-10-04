r"""开发用：验证参考视频功能（抽帧 / 精确取帧 / 镜头切换检测）。

会先用 ffmpeg 生成一段带明显镜头切换的测试视频。

    cmd /c "python -X utf8 -u tools\video_check.py > build\video.log 2>&1"
"""

from __future__ import annotations

import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rbar.audio import find_ffmpeg  # noqa: E402
from rbar.video import decode_frame, extract_filmstrip, probe_video, scene_cuts, scene_scores  # noqa: E402

BUILD = os.path.join(ROOT, "build")
FAILS: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  ok  " if cond else " FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)
    if not cond:
        FAILS.append(name)


def make_video(path: str, seconds: float = 8.0) -> None:
    """4 段不同颜色的画面，每 2 秒硬切一次。"""
    ff = find_ffmpeg()
    parts = []
    colors = ["red", "green", "blue", "yellow"]
    for i, c in enumerate(colors):
        parts.append(f"color=c={c}:s=320x180:d={seconds / 4}:r=30")
    filt = "".join(f"[{i}:v]" for i in range(len(colors)))
    cmd = [ff, "-v", "error", "-y"]
    for p in parts:
        cmd += ["-f", "lavfi", "-i", p]
    cmd += ["-filter_complex", f"{filt}concat=n={len(colors)}:v=1:a=0[out]", "-map", "[out]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "30", path]
    subprocess.run(cmd, check=True, creationflags=0x08000000)


def main() -> None:
    os.makedirs(BUILD, exist_ok=True)
    vid = os.path.join(BUILD, "test_video.mp4")
    if not os.path.isfile(vid):
        print("生成测试视频…", flush=True)
        make_video(vid, 8.0)

    print("[视频信息]", flush=True)
    info = probe_video(vid)
    check("时长≈8s", abs(info.duration_ms - 8000) < 300, f"{info.duration_ms:.0f}ms")
    check("分辨率 320x180", (info.width, info.height) == (320, 180), f"{info.width}x{info.height}")
    check("帧率≈30", abs(info.fps - 30) < 0.5, f"{info.fps:.2f}")

    print("[胶片条]", flush=True)
    fs = extract_filmstrip(vid, info, target_fps=4.0, thumb_h=72, max_frames=200)
    check("抽到约 32 张", abs(fs.count - 32) <= 3, f"{fs.count}")
    check("缩略图尺寸等比", (fs.thumb_w, fs.thumb_h) == (128, 72), f"{fs.thumb_w}x{fs.thumb_h}")
    check("时间轴覆盖整段", abs(float(fs.times[-1]) - 8000) < 400, f"{float(fs.times[-1]):.0f}ms")
    img = fs.image(3)
    check("能取出 QImage", img is not None and not img.isNull())

    print("[镜头切换]", flush=True)
    scores = scene_scores(fs)
    check("变化量数组长度", scores.size == fs.count - 1, f"{scores.size}")
    cuts = scene_cuts(fs, threshold=0.25, min_gap_ms=300)
    check("检出 3 次切换", len(cuts) == 3, f"{len(cuts)}: {[round(c) for c in cuts]}")
    expect = [2000, 4000, 6000]
    if len(cuts) == 3:
        ok = all(abs(a - b) < 320 for a, b in zip(cuts, expect))
        check("切换时间≈2/4/6s", ok, f"{[round(c) for c in cuts]}")

    print("[精确取帧]", flush=True)
    arr, w, h = decode_frame(vid, 1000.0, info, 90)
    check("取帧尺寸", (w, h) == (160, 90), f"{w}x{h}")
    check("第 1 秒是红色", int(arr[..., 0].mean()) > 180 and int(arr[..., 1].mean()) < 90,
          f"R={int(arr[..., 0].mean())} G={int(arr[..., 1].mean())}")
    arr2, _, _ = decode_frame(vid, 5000.0, info, 90)
    check("第 5 秒是蓝色", int(arr2[..., 2].mean()) > 180 and int(arr2[..., 0].mean()) < 90,
          f"B={int(arr2[..., 2].mean())} R={int(arr2[..., 0].mean())}")

    print(flush=True)
    if FAILS:
        print("失败项：", FAILS, flush=True)
        sys.exit(1)
    print("参考视频功能全部通过", flush=True)


if __name__ == "__main__":
    main()
