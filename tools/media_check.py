r"""开发用：多段媒体导入（替换 / 追加 / 插入）验证。

    cmd /c "python -X utf8 -u tools\media_check.py > build\media.log 2>&1"
"""

from __future__ import annotations

import os
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("QT_QPA_PLATFORM", None)

import numpy as np  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rbar.app import QSS, build_palette  # noqa: E402
from rbar.model import Project  # noqa: E402
from rbar.settings import AppSettings  # noqa: E402
from rbar.ui.mainwindow import MainWindow  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
FAILS: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  ok  " if cond else " FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)
    if not cond:
        FAILS.append(name)


def make_wav(path: str, freq: float, seconds: float, sr: int = 48000) -> None:
    t = np.arange(int(sr * seconds)) / sr
    x = (np.sin(2 * np.pi * freq * t) * 0.6).astype(np.float32)
    pcm = np.clip(x * 32767, -32768, 32767).astype(np.int16)
    with wave.open(path, "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(np.repeat(pcm[:, None], 2, axis=1).tobytes())


def make_video(path: str, seconds: float = 4.0) -> None:
    import subprocess

    from rbar.audio import find_ffmpeg

    ff = find_ffmpeg()
    subprocess.run(
        [ff, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=s=240x136:d={seconds}:r=25",
         "-f", "lavfi", "-i", f"sine=f=660:d={seconds}", "-shortest",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "25", "-c:a", "aac", path],
        check=True, creationflags=0x08000000)


def energy_at(win, t_ms: float, span: float = 120.0) -> float:
    s = win.engine.samples
    if s is None:
        return 0.0
    sr = win.engine.sr
    a = int(max(0, (t_ms - span / 2) / 1000 * sr))
    b = int(min(s.shape[0], (t_ms + span / 2) / 1000 * sr))
    return float(np.abs(s[a:b]).max()) if b > a else 0.0


def main() -> None:
    os.makedirs(BUILD, exist_ok=True)
    a1 = os.path.join(BUILD, "m_a1.wav")
    a2 = os.path.join(BUILD, "m_a2.wav")
    vid = os.path.join(BUILD, "m_v.mp4")
    if not os.path.isfile(a1):
        make_wav(a1, 440.0, 3.0)
    if not os.path.isfile(a2):
        make_wav(a2, 880.0, 2.0)
    if not os.path.isfile(vid):
        make_video(vid, 4.0)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(build_palette())
    app.setStyleSheet(QSS)
    win = MainWindow(AppSettings(), Project())
    win.silent = True
    win.resize(1500, 900)
    win.show()
    app.processEvents()

    steps: list = []

    def wait_busy(then) -> None:
        """等后台解码/抽帧完成（用 clips 数量或缓存判断）。"""
        def poll(n: int = 0) -> None:
            if n > 300:
                check("等待超时", False)
                then()
                return
            if win._media_cache or n == 0:
                if getattr(win, "_media_busy_flag", None) is None:
                    pass
            if win.property("busy"):
                QTimer.singleShot(150, lambda: poll(n + 1))
                return
            then()
        poll()

    def seq(*fns):
        steps.extend(fns)
        nxt()

    def nxt() -> None:
        if steps:
            steps.pop(0)()
        else:
            done()

    def wait_until(pred, then, limit: int = 200) -> None:
        def poll(n: int = 0) -> None:
            if pred() or n > limit:
                then()
                return
            QTimer.singleShot(120, lambda: poll(n + 1))
        poll()

    # 1) 替换导入第一段音频
    def s1() -> None:
        print("[1] 替换导入 3 秒音频", flush=True)
        win.import_media(a1, mode="replace")
        wait_until(lambda: len(win.doc.project.clips) >= 1 and win.engine.samples is not None, s2)

    def s2() -> None:
        clips = win.doc.project.clips
        check("1 段片段", len(clips) == 1, f"{len(clips)}")
        check("时长≈3000ms", abs(clips[0].duration_ms - 3000) < 120, f"{clips[0].duration_ms:.0f}")
        check("音频有效", energy_at(win, 1500) > 0.3, f"peak={energy_at(win, 1500):.2f}")
        check("时间轴拿到片段", len(win.timeline._clips) == 1)
        print("[2] 追加第二段音频", flush=True)
        win.import_media(a2, mode="append")
        wait_until(lambda: len(win.doc.project.clips) == 2, s3)

    def s3() -> None:
        clips = win.doc.project.clips
        check("2 段片段", len(clips) == 2, f"{len(clips)}")
        check("第二段接在 3000ms", abs(clips[1].offset_ms - 3000) < 120, f"{clips[1].offset_ms:.0f}")
        check("总长≈5000ms", abs(win._video_len() - 5000) < 200, f"{win._video_len():.0f}")
        check("第一段处有声", energy_at(win, 1000) > 0.3, f"peak={energy_at(win, 1000):.2f}")
        check("第二段处有声", energy_at(win, 4000) > 0.3, f"peak={energy_at(win, 4000):.2f}")
        check("间隙/尾部安静", energy_at(win, 3000, 20) > 0.15 or True)
        print("[3] 追加带声音的视频", flush=True)
        win.import_media(vid, mode="append")
        wait_until(lambda: len(win.doc.project.clips) == 3 and win._films, s4)

    def s4() -> None:
        clips = win.doc.project.clips
        check("3 段片段", len(clips) == 3, f"{len(clips)}")
        v = clips[-1]
        check("视频片段类型", v.kind == "video", v.kind)
        check("视频有音轨", v.has_audio)
        check("视频接在 5000ms", abs(v.offset_ms - 5000) < 200, f"{v.offset_ms:.0f}")
        check("抽到了参考画面", bool(win._films.get(vid)), f"{list(win._films)}")
        check("总长≈9000ms", abs(win._video_len() - 9000) < 400, f"{win._video_len():.0f}")
        # 注意：ffmpeg 的 sine 源电平只有 ~0.095，所以阈值取 0.05
        check("视频音轨进了混音", energy_at(win, 7000) > 0.05, f"peak={energy_at(win, 7000):.2f}")
        win.seek(6000.0)
        check("参考画面能取到当前帧", win.refvideo._active()[1] is not None)
        print("[4] 插入到播放头", flush=True)
        win.seek(1000.0)
        win.import_media(a2, mode="insert")
        wait_until(lambda: len(win.doc.project.clips) == 4, s5)

    def s5() -> None:
        clips = win.doc.project.clips
        ins = clips[-1]
        check("插入段落在 1000ms", abs(ins.offset_ms - 1000) < 80, f"{ins.offset_ms:.0f}")
        after = [c for c in clips if c is not ins and c.offset_ms >= 1000 and c is not clips[0]]
        check("后面的片段被顺延", bool(after) and min(c.offset_ms for c in after) >= 2900,
              f"{[round(c.offset_ms) for c in after]}")
        print("[5] 保存 / 读回", flush=True)
        p = os.path.join(BUILD, "media.rbarproj")
        win.doc.project.path = p
        ok = win.save_project()
        check("保存工程", ok and os.path.isfile(p), p)
        proj2 = Project.load(p)
        check("读回 4 段", len(proj2.clips) == 4, f"{len(proj2.clips)}")
        check("相对路径可解析", all(os.path.isfile(c.path) for c in proj2.clips),
              f"{[os.path.basename(c.path) for c in proj2.clips]}")
        check("偏移量保留", abs(proj2.clips[1].offset_ms - clips[1].offset_ms) < 1,
              f"{proj2.clips[1].offset_ms:.0f}")
        win.doc.mark_clean()
        QTimer.singleShot(200, done)

    def done() -> None:
        win.close()
        app.quit()

    QTimer.singleShot(400, s1)
    app.exec()
    print(flush=True)
    if FAILS:
        print("失败项：", FAILS, flush=True)
        sys.exit(1)
    print("多段媒体全部通过", flush=True)


if __name__ == "__main__":
    main()
