r"""开发用：端到端联调（真实窗口/音频设备）。

载入音频 -> 波形 -> 自动 BPM -> 自动铺点 -> 从界面导出透明视频 -> ffprobe 校验。

    cmd /c "python -X utf8 -u tools\e2e_check.py > build\e2e.log 2>&1"
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("QT_QPA_PLATFORM", None)

import numpy as np  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rbar.app import QSS, build_palette  # noqa: E402
from rbar.audio import detect_onsets, estimate_bpm, estimate_offset, find_ffmpeg, find_ffprobe  # noqa: E402
from rbar.model import Note, Project  # noqa: E402
from rbar.settings import AppSettings  # noqa: E402
from rbar.timing import BpmSegment  # noqa: E402
from rbar.ui.mainwindow import MainWindow  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
FAILS: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  ok  " if cond else " FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)
    if not cond:
        FAILS.append(name)


def make_wav(path: str, bpm: float = 120.0, seconds: float = 8.0, sr: int = 48000) -> None:
    n = int(sr * seconds)
    data = np.zeros(n, dtype=np.float32)
    period = 60.0 / bpm
    t, i = 0.0, 0
    while t < seconds:
        pos = int(t * sr)
        dur = int(0.06 * sr)
        env = np.exp(-np.arange(dur) / (0.012 * sr))
        f = 900 if i % 4 == 0 else 600
        click = np.sin(2 * np.pi * f * np.arange(dur) / sr) * env * 0.55
        end = min(n, pos + dur)
        data[pos:end] += click[: end - pos]
        t += period
        i += 1
    pcm = np.clip(data * 32767, -32768, 32767).astype(np.int16)
    with wave.open(path, "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(np.repeat(pcm[:, None], 2, axis=1).tobytes())


def make_video(path: str, seconds: float = 8.0, with_audio: bool = False) -> None:
    """4 段不同颜色的画面，每 2 秒硬切一次（可选带 120BPM 的滴答声）。"""
    ff = find_ffmpeg()
    colors = ["red", "green", "blue", "yellow"]
    cmd = [ff, "-v", "error", "-y"]
    for c in colors:
        cmd += ["-f", "lavfi", "-i", f"color=c={c}:s=320x180:d={seconds / 4}:r=30"]
    filt = "".join(f"[{i}:v]" for i in range(len(colors)))
    if with_audio:
        cmd += ["-f", "lavfi", "-i",
                f"aevalsrc=0.6*sin(2*PI*880*t)*exp(-6*mod(t\\,0.5)):s=48000:d={seconds}"]
        cmd += ["-filter_complex", f"{filt}concat=n={len(colors)}:v=1:a=0[out]",
                "-map", "[out]", "-map", f"{len(colors)}:a", "-c:a", "aac", "-b:a", "128k"]
    else:
        cmd += ["-filter_complex", f"{filt}concat=n={len(colors)}:v=1:a=0[out]", "-map", "[out]"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "30", path]
    subprocess.run(cmd, check=True, creationflags=0x08000000)


def probe(path: str) -> str:
    fp = find_ffprobe()
    if not fp:
        return "(无 ffprobe)"
    out = subprocess.run(
        [fp, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=codec_name,pix_fmt,width,height,nb_frames", "-of", "default=nw=1", path],
        capture_output=True,
    ).stdout.decode("utf-8", "ignore").strip().replace("\n", " ")
    return out


def alpha_of(path: str, w: int, h: int) -> tuple[int, int]:
    ff = find_ffmpeg()
    out = subprocess.run(
        [ff, "-v", "error", "-ss", "0.5", "-i", path, "-frames:v", "1", "-vf", "format=rgba",
         "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        capture_output=True, creationflags=0x08000000,
    ).stdout
    if len(out) < w * h * 4:
        return (-1, -1)
    a = np.frombuffer(out[: w * h * 4], dtype=np.uint8).reshape(h, w, 4)[:, :, 3]
    return int(a.min()), int(a.max())


def main() -> None:
    os.makedirs(BUILD, exist_ok=True)
    wav = os.path.join(BUILD, "click120.wav")
    make_wav(wav)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(build_palette())
    app.setStyleSheet(QSS)

    win = MainWindow(AppSettings(), Project())
    win.silent = True                # 自动测试：不弹模态框
    win.resize(1600, 960)
    win.show()
    print("[1] 载入音频", flush=True)
    win.load_audio(wav)

    steps: list = []

    def wait_audio(elapsed: int = 0) -> None:
        if win.engine.samples is None and elapsed < 15000:
            QTimer.singleShot(200, lambda: wait_audio(elapsed + 200))
            return
        step2()

    def step2() -> None:
        check("音频已解码", win.engine.samples is not None and win.engine.duration_ms > 7000,
              f"{win.engine.duration_ms:.0f}ms")
        win.timeline.zoom_to_fit()
        win.seek(1500.0)
        app.processEvents()
        p = os.path.join(BUILD, "shot_wave.png")
        win.grab().save(p)
        print("  截图:", p, flush=True)
        QTimer.singleShot(200, step3)

    def step3() -> None:
        print("[2] 自动 BPM / 自动铺点", flush=True)
        s = win.engine.samples
        sr = win.engine.sr
        bpm, conf = estimate_bpm(s, sr)
        off = estimate_offset(s, bpm, sr)
        check("BPM≈120", abs(bpm - 120) < 3, f"{bpm:.2f} conf={conf:.2f}")
        check("偏移在 0~500ms", 0 <= off < 500, f"{off:.0f}ms")
        win.doc.project.chart.set_segments([BpmSegment(off, bpm)], off)
        onsets = detect_onsets(s, sr, sensitivity=0.5, min_gap_ms=140)
        tm = win.doc.project.chart.timemap
        snaps = sorted({round(tm.snap(t, 4), 3) for t in onsets})
        rows = win.doc.project.theme.active_rows()
        win.doc.edit("自动铺点", lambda: [win.doc.project.chart.add(Note(t, rows[0], 0)) for t in snaps])
        check("铺出的音符数≈16", 12 <= len(snaps) <= 18, f"{len(snaps)}")
        check("网格对齐后间隔≈500ms",
              abs(float(np.median(np.diff(snaps))) - 500) < 30 if len(snaps) > 2 else False,
              f"{float(np.median(np.diff(snaps))):.0f}ms" if len(snaps) > 2 else "n/a")
        win.timeline.zoom_to_fit()
        win.seek(2000.0)
        app.processEvents()
        p = os.path.join(BUILD, "shot_notes.png")
        win.grab().save(p)
        print("  截图:", p, flush=True)
        QTimer.singleShot(200, step4)

    def step4() -> None:
        print("[3] 播放 1.2 秒验证走带", flush=True)
        win.seek(1000.0)
        win.play()
        QTimer.singleShot(1200, step5)

    def step5() -> None:
        pos = win.position_ms
        check("播放位置推进", pos > 1900, f"{pos:.0f}ms")
        win.pause()
        p = os.path.join(BUILD, "shot_playing.png")
        win.preview.grab().save(p)
        QTimer.singleShot(150, step_video)

    def step6() -> None:
        print("[4] 从界面导出透明视频", flush=True)
        ex = win.doc.project.export
        ex.fmt = "mov_qtrle"
        ex.fps = 60
        ex.width, ex.height = 1920, 120
        ex.supersample = 2
        ex.quality = "fast"
        ex.range_mode = "custom"
        ex.t_start, ex.t_end = 0.0, 1500.0
        ex.output = os.path.join(BUILD, "e2e_bar.mov")
        ex.open_after = False
        if os.path.isfile(ex.output):
            os.remove(ex.output)
        win._run_export(0.0, 1500.0, 90)
        task = win._export_task
        task.done.connect(on_export_done)
        task.failed.connect(lambda m: (check("导出无异常", False, m[:120]), finish()))

    # ---------------- 参考视频 ----------------
    def step_video() -> None:
        print("[5] 导入参考视频（带声音）", flush=True)
        vid = os.path.join(BUILD, "test_video_av.mp4")
        if not os.path.isfile(vid):
            make_video(vid, 8.0, with_audio=True)
        # 先清掉已载入的音频，验证「视频自带声音自动成为工作音频」
        win.engine.clear()
        win._peaks = None
        win.spectrum.set_audio(None, 48000)
        win.import_video(vid)
        QTimer.singleShot(400, wait_video)

    def wait_video(elapsed: int = 0) -> None:
        if (win.filmstrip is None or win.engine.samples is None) and elapsed < 25000:
            QTimer.singleShot(200, lambda: wait_video(elapsed + 200))
            return
        fs = win.filmstrip
        check("胶片条已生成", fs is not None and fs.count > 20, f"{fs.count if fs else 0}")
        if fs is None:
            finish()
            return
        from rbar.video import probe_audio_stream, scene_cuts
        ai = probe_audio_stream(vid := fs.path)
        check("视频有音轨", bool(ai.get("has")), f"{ai}")
        check("视频声音已作为工作音频", win.engine.samples is not None and win.engine.duration_ms > 7000,
              f"{win.engine.duration_ms:.0f}ms")
        cuts = scene_cuts(fs, 0.35, min_gap_ms=300)
        check("镜头切换检出 3 次", len(cuts) == 3, f"{[round(c) for c in cuts]}")
        check("时间轴时长跟随视频", win.timeline._dur_ms >= 7000, f"{win.timeline._dur_ms:.0f}")
        # 频谱图：等后台算完
        QTimer.singleShot(300, wait_spec)

    def wait_spec(elapsed: int = 0) -> None:
        if win.spec is None and elapsed < 25000:
            QTimer.singleShot(200, lambda: wait_spec(elapsed + 200))
            return
        sp = win.spec
        check("频谱图已生成", sp is not None and sp.frames > 100,
              f"{sp.data.shape if sp is not None else None}")
        if sp is not None:
            col = sp.data[:, sp.frames // 4].astype(np.int32)
            check("频谱有内容（不是全黑）", int(col.max()) > 60, f"max={int(col.max())}")
        win.seek(4500.0)
        win.timeline.zoom_to_fit()
        win.doc.project.theme.judge.idle = True
        app.processEvents()
        p = os.path.join(BUILD, "shot_video.png")
        win.grab().save(p)
        print("  截图:", p, flush=True)
        QTimer.singleShot(400, step_fill)

    # ---------------- 区间填充 ----------------
    def step_fill() -> None:
        print("[6] 区间批量填充", flush=True)
        from PySide6.QtWidgets import QDialog

        from rbar.ui.dialogs import FillRangeDialog

        self_test = FillRangeDialog(win, win.doc.project.chart.timemap, 0.0, 2000.0,
                                    [("cyan", "青菱形"), ("magenta", "品红菱形")], 0, True, 4.0)
        self_test.cb_mode.setCurrentIndex(1)     # 按毫秒
        self_test.sp_ms.setValue(250)
        self_test.sp_start.setValue(0)
        self_test.sp_end.setValue(2000)
        ts = self_test.times()
        check("对话框算出 9 个位置", len(ts) == 9, f"{len(ts)}")
        self_test.cb_mode.setCurrentIndex(0)     # 按节拍
        self_test.sp_beats.setValue(1.0)
        check("按节拍模式可用", len(self_test.times()) > 1, f"{len(self_test.times())}")
        self_test.chk_alt.setChecked(True)
        check("交替类型返回两组", len(self_test.type_keys()) == 2)
        self_test.close()

        orig_exec = FillRangeDialog.exec

        def fake_exec(self):
            self.cb_mode.setCurrentIndex(1)
            self.sp_ms.setValue(250)
            self.sp_start.setValue(3000)
            self.sp_end.setValue(5000)
            return QDialog.DialogCode.Accepted

        FillRangeDialog.exec = fake_exec
        try:
            n0 = len(win.doc.project.chart)
            win.fill_range()
            added = len(win.doc.project.chart) - n0
            check("实际填入 9 个音符", added == 9, f"{added}")
            win.doc.undo()
            check("填充可撤销", len(win.doc.project.chart) == n0, f"{len(win.doc.project.chart)}")
            win.doc.redo()
        finally:
            FillRangeDialog.exec = orig_exec
        app.processEvents()
        QTimer.singleShot(200, step6)

    def on_export_done(res) -> None:
        check("导出完成未取消", not getattr(res, "cancelled", False))
        check("输出文件存在", os.path.isfile(res.path), res.path)
        info = probe(res.path)
        print("  ffprobe:", info, flush=True)
        check("编码=qtrle 且帧数=90", "qtrle" in info and "nb_frames=90" in info, info)
        lo, hi = alpha_of(res.path, 1920, 120)
        check("alpha 通道有效（既有透明也有不透明）", lo >= 0 and lo < 200 and hi > 200, f"min={lo} max={hi}")
        QTimer.singleShot(150, finish)

    def finish() -> None:
        print(flush=True)
        if FAILS:
            print("失败项：", FAILS, flush=True)
        else:
            print("端到端全部通过", flush=True)
        win.doc.mark_clean()
        win.close()
        app.exit(1 if FAILS else 0)

    QTimer.singleShot(400, wait_audio)
    code = app.exec()
    sys.exit(code)


if __name__ == "__main__":
    main()
