"""开发用：无显示器冒烟测试（模型 / 音频分析 / 界面构建 / 离屏截图）。"""

from __future__ import annotations

import os
import sys
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rbar.audio import decode_audio, detect_onsets, estimate_bpm, peaks  # noqa: E402
from rbar.demo import make_demo_project  # noqa: E402
from rbar.doc import Doc  # noqa: E402
from rbar.model import Note, Project  # noqa: E402
from rbar.timing import BpmSegment  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  ok  " if cond else " FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not cond:
        FAILS.append(name)


def make_click_wav(path: str, bpm: float = 120.0, seconds: float = 6.0, sr: int = 48000) -> None:
    n = int(sr * seconds)
    data = np.zeros(n, dtype=np.float32)
    period = 60.0 / bpm
    t = 0.0
    i = 0
    while t < seconds:
        pos = int(t * sr)
        dur = int(0.03 * sr)
        env = np.exp(-np.arange(dur) / (0.006 * sr))
        click = np.sin(2 * np.pi * (1500 if i % 4 == 0 else 1000) * np.arange(dur) / sr) * env * 0.7
        end = min(n, pos + dur)
        data[pos:end] += click[: end - pos]
        t += period
        i += 1
    pcm = np.clip(data * 32767, -32768, 32767).astype(np.int16)
    stereo = np.repeat(pcm[:, None], 2, axis=1)
    with wave.open(path, "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(stereo.tobytes())


def test_timing() -> None:
    print("[时间映射]")
    from rbar.timing import TimeMap

    tm = TimeMap([BpmSegment(0.0, 120.0)], 0.0)
    check("120bpm 一拍=500ms", abs(tm.ms(1.0) - 500.0) < 1e-6)
    check("500ms = 1 拍", abs(tm.beat(500.0) - 1.0) < 1e-6)
    tm.set_segments([BpmSegment(0.0, 120.0), BpmSegment(2000.0, 240.0)], 0.0)
    check("变速后 2000ms = 4 拍", abs(tm.beat(2000.0) - 4.0) < 1e-6)
    check("变速后 2250ms = 5 拍", abs(tm.beat(2250.0) - 5.0) < 1e-6, f"{tm.beat(2250.0)}")
    check("往返一致", abs(tm.ms(tm.beat(2345.6)) - 2345.6) < 1e-6)
    s = tm.snap(510.0, 4.0)
    check("吸附到十六分", abs(s - 500.0) < 1e-6, f"{s}")
    check("网格线数量合理", 40 <= len(tm.grid_lines(0, 4000, 4.0)) <= 60)


def test_chart() -> None:
    print("[谱面/撤销]")
    doc = Doc(Project())
    ch = doc.project.chart
    doc.edit("加", lambda: ch.add(Note(1000.0, "cyan", 0)))
    check("加音符", len(ch) == 1)
    doc.edit("加", lambda: ch.add(Note(1500.0, "magenta", 1)))
    check("两个音符", len(ch) == 2)
    doc.undo()
    check("撤销后剩 1 个", len(ch) == 1)
    doc.redo()
    check("重做后 2 个", len(ch) == 2)
    sel = list(ch.notes)
    doc.edit("删", lambda: ch.remove_many(sel))
    check("删除", len(ch) == 0)
    doc.undo()
    check("撤销删除", len(ch) == 2)
    check("范围查询", len(ch.in_range(900, 1600)) == 2)
    check("边界查询", len(ch.in_range(1001, 1600)) == 1)


def test_audio(tmp: str) -> None:
    print("[音频分析]")
    wav = os.path.join(tmp, "click120.wav")
    make_click_wav(wav, bpm=120.0, seconds=8.0)
    samples, sr = decode_audio(wav)
    check("解码帧数", samples.shape[0] > 48000 * 7, f"{samples.shape}")
    pk = peaks(samples, 2000)
    check("峰值数组", pk.shape[0] == 2000 and pk.max() > 0.2, f"max={pk.max():.2f}")
    bpm, conf = estimate_bpm(samples, sr)
    check("BPM≈120", abs(bpm - 120.0) < 3.0, f"{bpm:.2f} conf={conf:.2f}")
    ons = detect_onsets(samples, sr, sensitivity=0.5, min_gap_ms=120)
    expect = 16.0
    check("起音个数≈16", abs(len(ons) - expect) <= 2, f"{len(ons)}")
    if ons:
        gaps = np.diff(ons)
        check("起音间隔≈500ms", abs(float(np.median(gaps)) - 500.0) < 25.0,
              f"median={float(np.median(gaps)):.1f}")


def test_spectrum() -> None:
    print("[频谱图]")
    from rbar.audio import spectrogram

    sr = 48000
    t = np.arange(sr * 2) / sr
    mixed = (np.sin(2 * np.pi * 1000 * t) * 0.5 + np.sin(2 * np.pi * 6000 * t) * 0.5)
    sp = spectrogram(np.repeat(mixed.astype(np.float32)[:, None], 2, axis=1), sr)
    check("形状 (行=频率, 列=时间)", sp.data.shape[0] == 192 and sp.data.shape[1] > 100,
          f"{sp.data.shape}")
    col = sp.data[:, sp.frames // 2].astype(np.int32)
    rows = np.argsort(col)[-6:]
    freqs = [sp.f_max * (sp.f_min / sp.f_max) ** (r / (sp.bins - 1)) for r in rows]
    hit = lambda target: any(abs(f - target) / target < 0.25 for f in freqs)  # noqa: E731
    check("1kHz 在亮行里", hit(1000.0), f"{[round(f) for f in sorted(freqs)]}")
    check("6kHz 在亮行里", hit(6000.0), f"{[round(f) for f in sorted(freqs)]}")
    check("能生成 QImage", not sp.image().isNull())


def test_gui(tmp: str) -> None:
    print("[界面]")
    app = QApplication.instance() or QApplication(sys.argv)
    from rbar.app import QSS, build_palette
    from rbar.settings import AppSettings
    from rbar.ui.mainwindow import MainWindow

    app.setStyle("Fusion")
    app.setPalette(build_palette())
    app.setStyleSheet(QSS)

    proj = make_demo_project(bars=8)
    win = MainWindow(AppSettings(), proj)
    win.resize(1600, 950)
    win.show()
    app.processEvents()

    check("时间轴构件存在", win.timeline is not None)
    win.timeline.zoom_to_fit()
    app.processEvents()

    # 打点
    before = len(win.doc.project.chart)
    win.seek(1234.0)
    win.tap_note()
    check("打点加了 1 个音符", len(win.doc.project.chart) == before + 1)
    win.doc.undo()
    check("撤销打点", len(win.doc.project.chart) == before)

    # 复制粘贴 / 量化 / 镜像
    win.select_all()
    check("全选", len(win.doc.selection) == len(win.doc.project.chart))
    win.copy_notes()
    win.seek(9000.0)
    win.paste_notes()
    check("粘贴后有更多音符", len(win.doc.project.chart) > before)

    # 外观参数
    from rbar.theme import apply_preset

    apply_preset(win.doc.project.theme, "霓虹发光")
    win.on_visual_changed()
    apply_preset(win.doc.project.theme, "图中样式（暖灰半透明）")
    win.on_visual_changed()

    # 截图
    out = os.path.join(tmp, "ui_main.png")
    win.grab().save(out)
    print("  截图:", out)
    tl = os.path.join(tmp, "ui_timeline.png")
    win.timeline.grab().save(tl)
    pv = os.path.join(tmp, "ui_preview.png")
    win.preview.grab().save(pv)

    # 导出范围
    from rbar.exporter import compute_range

    t0, t1 = compute_range(win.doc.project, 0.0)
    check("导出范围合理", t1 > t0, f"{t0:.0f}~{t1:.0f}")

    win.doc.mark_clean()          # 避免关闭时弹模态确认框（无头环境下会卡住）
    win.close()
    app.processEvents()


def main() -> None:
    tmp = os.path.join(ROOT, "build")
    os.makedirs(tmp, exist_ok=True)
    test_timing()
    test_chart()
    test_audio(tmp)
    test_spectrum()
    test_gui(tmp)
    print()
    if FAILS:
        print("失败项：", FAILS)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
