"""主窗口：把时间轴、预览、面板、播放与导出串起来。"""

from __future__ import annotations

import copy
import os
import threading
import time

import numpy as np

from PySide6.QtCore import QElapsedTimer, QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QSlider,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from .. import i18n
from ..audio import AudioEngine, decode_audio, detect_onsets, estimate_bpm, estimate_offset, peaks
from ..demo import make_demo_project
from ..doc import Doc
from ..exporter import compute_range, export_still, export_video
from ..model import Note, Project
from ..settings import AppSettings
from ..tasks import Task
from ..timing import BpmSegment
from ..updater import PAGE as RELEASES_PAGE
from .dialogs import AutoNotesDialog, BpmSegmentDialog, FillRangeDialog, TapTempoDialog
from .panels import ChartPanel, ExportPanel, NoteTypePanel, SettingsPanel, StylePanel
from .preview import PreviewWidget, RefFrameWidget, SpectrumWidget
from .timeline import TimelineWidget, fmt_time

AUDIO_EXT = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aiff", ".aif")
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".wmv", ".m4v", ".ts", ".mpg", ".mpeg")
PROJECT_EXT = ".rbarproj"
PEAK_BUCKETS = 30000


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings | None = None, project: Project | None = None):
        super().__init__()
        self.settings = settings or AppSettings()
        self.doc = Doc(project or Project())
        self.engine = AudioEngine(self)
        self._clipboard: list[dict] = []
        self._clip_anchor = 0.0
        self._dragging_pos = False
        self._virtual = QElapsedTimer()
        self._virtual_active = False
        self._virtual_base = 0.0
        self._export_cancel = threading.Event()
        self._export_task: Task | None = None
        self._media_cache: dict[str, tuple] = {}   # 路径 -> (samples, sr)
        self._films: dict[str, object] = {}        # 路径 -> Filmstrip
        self._peaks = None
        self._zoomed_once = False
        self.spec = None                 # 频谱图
        self._spec_task: Task | None = None
        self._update_task: Task | None = None      # 检查更新
        self._media_mode = "replace"
        self._media_path = ""
        self._media_is_video = False
        self._media_info = None
        self._media_ainfo: dict = {}
        self._tap_times: list[float] = []
        self._last_autosave = 0.0
        self._fps_t = time.perf_counter()
        self._last_status = 0.0
        self.silent = False          # True = 不弹模态框（自动测试用）

        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1500, 940)
        self.setAcceptDrops(True)

        self._build_ui()
        self._build_actions()
        self._wire()

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self.settings_changed()
        self.refresh_all()
        i18n.translate_tree(self)          # 按当前语言刷新整套界面
        self.statusBar().showMessage(i18n.tr("就绪：拖入音频 → 打点 → 导出透明视频"), 8000)
        # 启动后延迟自动查一次更新（设置里可关；失败静默）
        QTimer.singleShot(2500, lambda: self.check_updates(auto=True))
        if self.doc.project.path:
            self.setWindowTitle(f"{APP_NAME} — {os.path.basename(self.doc.project.path)}")

    # ==================================================================== UI
    def _build_ui(self) -> None:
        self.timeline = TimelineWidget(self.doc)
        self.preview = PreviewWidget(self.doc)
        self.refvideo = RefFrameWidget(self.doc)
        self.spectrum = SpectrumWidget(self.doc)
        self.refvideo.setVisible(False)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        bl.addWidget(self._build_transport())
        bl.addWidget(self.spectrum)
        hsplit = QSplitter(Qt.Horizontal)
        hsplit.addWidget(self.refvideo)
        hsplit.addWidget(self.preview)
        hsplit.setStretchFactor(0, 0)
        hsplit.setStretchFactor(1, 1)
        hsplit.setSizes([300, 1000])
        self.bottom_split = hsplit
        bl.addWidget(hsplit, 1)
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.timeline)
        split.addWidget(bottom)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([740, 200])
        self.split = split
        self.setCentralWidget(split)
        self.preview.setMinimumHeight(104)
        self.preview.setMaximumHeight(150)

        # 左侧：音符类型
        self.panel_types = NoteTypePanel(self.doc)
        dock = QDockWidget("音符类型", self)
        dock.setWidget(self.panel_types)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        self.dock_types = dock

        # 右侧：谱面 / 外观 / 导出 / 设置
        self.panel_chart = ChartPanel(self.doc)
        self.panel_style = StylePanel(self.doc)
        self.panel_export = ExportPanel(self.doc)
        self.panel_settings = SettingsPanel(self.doc, self.settings)
        tabs = QTabWidget()
        tabs.addTab(self.panel_chart, "谱面 / BPM")
        tabs.addTab(self.panel_style, "外观")
        tabs.addTab(self.panel_export, "导出")
        tabs.addTab(self.panel_settings, "设置")
        dock2 = QDockWidget("工程设置", self)
        dock2.setWidget(tabs)
        dock2.setMinimumWidth(330)
        self.addDockWidget(Qt.RightDockWidgetArea, dock2)
        self.dock_settings = dock2
        self.tabs = tabs

        # 状态栏
        self.lbl_pos = QLabel("0:00.000")
        self.lbl_beat = QLabel("拍 1")
        self.lbl_bpm = QLabel("120 BPM")
        self.lbl_count = QLabel("音符 0")
        self.lbl_fps = QLabel("")
        for w in (self.lbl_pos, self.lbl_beat, self.lbl_bpm, self.lbl_count, self.lbl_fps):
            w.setMinimumWidth(72)
            self.statusBar().addPermanentWidget(w)

    def _build_transport(self) -> QWidget:
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 4, 8, 2)
        lay.setSpacing(8)
        self.btn_play = QPushButton("▶  播放")
        self.btn_play.setMinimumWidth(96)
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_home = QPushButton("⏮")
        self.btn_home.setFixedWidth(34)
        self.btn_home.clicked.connect(lambda: self.seek(0.0))
        self.slider_pos = QSlider(Qt.Horizontal)
        self.slider_pos.setRange(0, 1000)
        self.slider_pos.sliderPressed.connect(lambda: setattr(self, "_dragging_pos", True))
        self.slider_pos.sliderReleased.connect(self._on_slider_released)
        self.slider_pos.sliderMoved.connect(self._on_slider_moved)
        self.chk_loop = QCheckBox("循环")
        self.chk_loop.toggled.connect(self._on_loop_toggle)
        self.chk_meta = QCheckBox("节拍器")
        self.chk_meta.toggled.connect(self._on_metronome)
        self.cb_rate = QComboBox()
        for r in (0.5, 0.75, 1.0, 1.25, 1.5):
            self.cb_rate.addItem(f"{r:g}×", r)
        self.cb_rate.currentIndexChanged.connect(self._on_rate)
        self.btn_follow = QPushButton("跟随")
        self.btn_follow.setCheckable(True)
        self.btn_follow.setChecked(True)
        self.btn_follow.toggled.connect(self._on_follow)
        lay.addWidget(self.btn_home)
        lay.addWidget(self.btn_play)
        lay.addWidget(self.slider_pos, 1)
        lay.addWidget(self.chk_loop)
        lay.addWidget(self.chk_meta)
        lay.addWidget(QLabel("速度"))
        lay.addWidget(self.cb_rate)
        lay.addWidget(self.btn_follow)
        return bar

    def _build_actions(self) -> None:
        tb = QToolBar("主工具栏")
        tb.setMovable(False)
        self.addToolBar(tb)
        self.toolbar = tb

        def act(text: str, slot, shortcut: str = "", tip: str = "", icon_text: str = "") -> QAction:
            a = QAction(icon_text or text, self)
            a.setToolTip(tip or text)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
                a.setShortcutContext(Qt.WindowShortcut)
            return a

        m_file = self.menuBar().addMenu("文件")
        a_new = act("新建工程", self.new_project, "Ctrl+N")
        a_open = act("打开工程…", self.open_project, "Ctrl+O")
        a_audio = act("打开音频…", self.open_audio, "Ctrl+Shift+O")
        a_video = act("导入参考视频…（对照画面卡点）", self.import_video, "Ctrl+Shift+V")
        a_vaudio = act("用视频里的声音作为音频", self.use_video_audio)
        a_demo = act("载入示例工程（没音频也能试）", self.load_demo)
        a_save = act("保存", self.save_project, "Ctrl+S")
        a_saveas = act("另存为…", self.save_project_as, "Ctrl+Shift+S")
        a_export = act("导出透明视频…", self.export_dialog, "Ctrl+E")
        a_still = act("导出当前帧 PNG…", self.export_still_dialog, "Ctrl+Shift+E")
        a_quit = act("退出", self.close, "Ctrl+Q")
        for a in (a_new, a_open, a_audio, a_video, a_vaudio, a_demo):
            m_file.addAction(a)
        m_file.addSeparator()
        for a in (a_save, a_saveas):
            m_file.addAction(a)
        m_file.addSeparator()
        m_file.addAction(a_export)
        m_file.addAction(a_still)
        m_file.addSeparator()
        self.menu_recent = m_file.addMenu("最近打开")
        m_file.addSeparator()
        m_file.addAction(a_quit)

        m_edit = self.menuBar().addMenu("编辑")
        self.a_undo = act("撤销", self.doc.undo, "Ctrl+Z")
        self.a_redo = act("重做", self.doc.redo, "Ctrl+Y")
        self.a_select_tool = act("选择 / 拖拽模式（V）", lambda: self.set_tool("select"))
        self.a_select_tool.setCheckable(True)
        self.a_select_tool.setChecked(True)
        self.a_draw_tool = act("放置音符模式（B，按住可连续刷）", lambda: self.set_tool("draw"))
        self.a_draw_tool.setCheckable(True)
        self.a_range_tool = act("框选区间模式（R，Shift+拖动同效）",
                                lambda: self.set_tool("range"))
        self.a_range_tool.setCheckable(True)
        grp = QActionGroup(self)
        grp.setExclusive(True)
        grp.addAction(self.a_select_tool)
        grp.addAction(self.a_draw_tool)
        grp.addAction(self.a_range_tool)
        self.tool_group = grp
        a_selall = act("全选", self.select_all, "Ctrl+A")
        a_copy = act("复制 (Ctrl+C)", self.copy_notes)
        a_paste = act("粘贴 (Ctrl+V，落在鼠标处)", self.paste_notes)
        a_dup = act("向后复制一份 (Ctrl+D)", self.duplicate_forward)
        a_quant = act("量化到网格 (Ctrl+Q)", self.quantize_selection)
        a_mirror = act("镜像选中区间 (Ctrl+M)", self.mirror_selection)
        a_del = act("删除选中 (Del)", self.delete_selection)
        for a in (self.a_undo, self.a_redo):
            m_edit.addAction(a)
        m_edit.addSeparator()
        m_edit.addAction(self.a_select_tool)
        m_edit.addAction(self.a_draw_tool)
        m_edit.addAction(self.a_range_tool)
        m_edit.addSeparator()
        for a in (a_selall, a_copy, a_paste, a_dup, a_quant, a_mirror, a_del):
            m_edit.addAction(a)

        m_view = self.menuBar().addMenu("视图")
        a_fit = act("缩放适应 (Ctrl+0)", self.timeline.zoom_to_fit, "Ctrl+0")
        a_zin = act("放大 (Ctrl+=)", lambda: self.timeline.zoom_at(self.timeline.width() / 2, 1.25))
        a_zout = act("缩小 (Ctrl+-)", lambda: self.timeline.zoom_at(self.timeline.width() / 2, 1 / 1.25))
        self.a_wave = act("显示波形", self._toggle_wave)
        self.a_wave.setCheckable(True)
        self.a_wave.setChecked(True)
        self.a_safe = act("预览安全框", self._toggle_safe)
        self.a_safe.setCheckable(True)
        self.a_safe.setChecked(True)
        self.a_ref = act("显示参考视频画面", self._toggle_ref)
        self.a_ref.setCheckable(True)
        self.a_ref.setChecked(True)
        self.a_film = act("时间轴显示视频胶片条", self._toggle_film)
        self.a_film.setCheckable(True)
        self.a_film.setChecked(True)
        self.a_spec = act("时间轴显示频谱图", self._toggle_spec)
        self.a_spec.setCheckable(True)
        self.a_spec.setChecked(True)
        self.a_live = act("实时频谱条", self._toggle_live)
        self.a_live.setCheckable(True)
        self.a_live.setChecked(True)
        for a in (a_fit, a_zin, a_zout):
            m_view.addAction(a)
        m_view.addSeparator()
        m_view.addAction(self.a_wave)
        m_view.addAction(self.a_spec)
        m_view.addAction(self.a_live)
        m_view.addAction(self.a_safe)
        m_view.addAction(self.a_ref)
        m_view.addAction(self.a_film)
        m_view.addSeparator()
        m_view.addAction(self.dock_types.toggleViewAction())
        m_view.addAction(self.dock_settings.toggleViewAction())

        m_play = self.menuBar().addMenu("播放")
        m_play.addAction(act("播放 / 暂停（空格）", self.toggle_play))
        m_play.addAction(act("回到开头 (Home)", lambda: self.seek(0.0), "Home"))
        m_play.addAction(act("设置循环 A / B（L）", self.set_loop_here))
        m_play.addAction(act("清除循环", self.clear_loop))
        m_play.addAction(act("节拍器（M）", lambda: self.chk_meta.toggle()))

        m_tool = self.menuBar().addMenu("工具")
        m_tool.addAction(act("区间批量填充音符…", self.fill_range))
        m_tool.addAction(act("Tap 测速…", self.tap_tempo_dialog))
        m_tool.addAction(act("自动检测 BPM…", self.auto_bpm))
        m_tool.addAction(act("自动铺点（音频起音 / 视频镜头）…", self.auto_notes))
        m_tool.addAction(act("在播放头插入 BPM 段…", lambda: self.panel_chart.add_segment()))

        m_help = self.menuBar().addMenu("帮助")
        m_help.addAction(act("快捷键与用法", self.show_help))
        m_help.addAction(act("关于", self.show_about))

        # 语言（三个选项互斥）
        m_lang = self.menuBar().addMenu("语言")
        grp_lang = QActionGroup(self)
        grp_lang.setExclusive(True)
        self.lang_actions: dict[str, QAction] = {}
        for code, label in i18n.LANGS:
            a = QAction(label, self)
            a.setCheckable(True)
            a.setChecked(i18n.current_language() == code)
            a.triggered.connect(lambda _=False, c=code: self.set_language(c))
            grp_lang.addAction(a)
            m_lang.addAction(a)
            self.lang_actions[code] = a
        self.menu_lang = m_lang

        # 「检查更新」单独作菜单栏顶层一项，点一下直接查
        self.a_check_update = act("检查更新", lambda: self.check_updates(auto=False),
                                  "Ctrl+U", "检查 GitHub 上有没有新版本")
        self.menuBar().addAction(self.a_check_update)

        # 工具栏按钮
        tb.addAction(a_audio)
        tb.addAction(a_video)
        tb.addAction(a_open)
        tb.addAction(a_save)
        tb.addSeparator()
        tb.addAction(self.a_undo)
        tb.addAction(self.a_redo)
        tb.addSeparator()
        tb.addAction(act("播放/暂停", self.toggle_play, icon_text="▶"))
        tb.addAction(act("循环", self.set_loop_here, icon_text="↻"))
        tb.addAction(act("打点(F)", lambda: self.tap_note()))
        tb.addSeparator()
        tb.addAction(self.a_select_tool)
        tb.addAction(self.a_draw_tool)
        tb.addAction(self.a_range_tool)
        tb.addSeparator()
        tb.addAction(act("自动BPM", self.auto_bpm))
        tb.addAction(act("自动铺点", self.auto_notes))
        tb.addSeparator()
        tb.addAction(a_export)

        self.a_undo.setEnabled(False)
        self.a_redo.setEnabled(False)
        self._refresh_recent()

    def _wire(self) -> None:
        self.timeline.seekRequested.connect(self.seek)
        self.timeline.statusMessage.connect(lambda s: self.statusBar().showMessage(s, 2500))
        self.timeline.notesChanged.connect(self.on_notes_changed)
        self.timeline.rowChanged.connect(self._on_row_changed)
        self.timeline.bpmEditRequested.connect(self.edit_bpm_segment)
        self.timeline.bpmAddRequested.connect(self.add_bpm_segment_at)
        self.timeline.copyRequested.connect(self.copy_notes)
        self.timeline.pasteRequested.connect(
            lambda ms, row: self.paste_notes(ms, None if row < 0 else row))
        self.timeline.pasteAtPlayheadRequested.connect(self.paste_notes)
        self.timeline.duplicateRequested.connect(self.duplicate_forward)
        self.timeline.selectAllRequested.connect(self.select_all)
        self.timeline.fillRequested.connect(self.fill_range)
        self.timeline.loopChanged.connect(lambda: self.chk_loop.setChecked(True))
        self.preview.seekRequested.connect(self.seek)
        self.refvideo.seekRequested.connect(self.seek)
        self.panel_types.changed.connect(self.on_visual_changed)
        self.panel_types.orderChanged.connect(self.on_structure_changed)
        self.panel_chart.changed.connect(self.on_chart_changed)
        self.panel_chart.seekRequested.connect(self.seek)
        self.panel_chart.tapRequested.connect(self.tap_tempo_dialog)
        self.panel_chart.autoBpmRequested.connect(self.auto_bpm)
        self.panel_chart.autoNotesRequested.connect(self.auto_notes)
        self.panel_style.changed.connect(self.on_visual_changed)
        self.panel_export.changed.connect(lambda: self.doc.touch("导出设置"))
        self.panel_export.exportRequested.connect(self.export_dialog)
        self.panel_export.stillRequested.connect(self.export_still_dialog)
        self.panel_settings.changed.connect(self.settings_changed)
        self.doc.changed.connect(lambda _label: self.refresh_all())
        self.doc.selectionChanged.connect(self.on_selection_changed)
        self.doc.dirtyChanged.connect(self._on_dirty)
        self.engine.stateChanged.connect(self._on_play_state)
        self.engine.finished.connect(lambda: self._on_play_state(False))

    # ============================================================== 状态刷新
    def refresh_all(self) -> None:
        self.panel_types.refresh()
        self.panel_chart.refresh()
        self.panel_style.refresh()
        self.panel_export.refresh()
        self.a_undo.setEnabled(self.doc.can_undo())
        self.a_redo.setEnabled(self.doc.can_redo())
        self.timeline.update()
        self.preview.update()
        self._update_counts()
        self._push_beats()

    def _push_beats(self) -> None:
        """把节拍时间喂给节拍器。"""
        tm = self.doc.project.chart.timemap
        dur = self.engine.duration_ms or 60000.0
        try:
            beats = tm.grid_lines(0.0, dur, 1.0, limit=20000)
        except Exception:
            beats = []
        self.engine.set_beats(beats)

    def _update_counts(self) -> None:
        n = len(self.doc.project.chart)
        s = len(self.doc.selection)
        self.lbl_count.setText(i18n.trf("音符 {n}", n=n)
                               + (i18n.trf("  选中 {n}", n=s) if s else ""))
        self._update_title()

    def _update_title(self) -> None:
        p = self.doc.project.path
        name = os.path.basename(p) if p else "未命名工程"
        self.setWindowTitle(f"{APP_NAME} — {name}{'*' if self.doc.dirty else ''}")

    def _on_dirty(self, _v: bool) -> None:
        self._update_title()

    def on_selection_changed(self) -> None:
        self.timeline.update()
        self._update_counts()

    def on_visual_changed(self) -> None:
        self.timeline.update()
        self.preview.update()

    def on_structure_changed(self) -> None:
        self.panel_types.refresh()
        self.timeline.update()
        self.preview.update()
        self._update_counts()

    def on_chart_changed(self) -> None:
        self.timeline.update()
        self.preview.update()
        self._push_beats()

    def on_notes_changed(self) -> None:
        self.timeline.update()
        self.preview.update()
        self._update_counts()
        self._push_beats()

    def settings_changed(self) -> None:
        self.engine.set_latency_ms(self.settings.latency_ms)
        self.engine.set_volume(self.settings.volume)
        self.engine.set_rate(self.settings.rate)
        i = self.cb_rate.findData(self.settings.rate)
        self.cb_rate.blockSignals(True)
        self.cb_rate.setCurrentIndex(max(0, i))
        self.cb_rate.blockSignals(False)
        self.preview.set_safe_area(self.settings.show_safe)

    def _refresh_recent(self) -> None:
        self.menu_recent.clear()
        items = [p for p in self.settings.recent if os.path.isfile(p)]
        if not items:
            self.menu_recent.addAction("（空）").setEnabled(False)
            return
        for p in items:
            a = QAction(os.path.basename(p), self)
            a.setToolTip(p)
            a.triggered.connect(lambda _=False, path=p: self.open_path(path))
            self.menu_recent.addAction(a)

    # ================================================================ 播放
    @property
    def position_ms(self) -> float:
        if self.engine.playing:
            return self.engine.position_ms()
        if self._virtual_active:
            return self._virtual_base + self._virtual.elapsed() * self.engine.rate
        return self.engine._seek_ms

    def toggle_play(self) -> None:
        if self.engine.playing or self._virtual_active:
            self.pause()
        else:
            self.play()

    def play(self) -> None:
        pos = self.engine._seek_ms
        st = self.doc.state
        if st.loop_on and not (st.loop_a <= pos < st.loop_b):
            pos = st.loop_a
        if self.engine.samples is not None:
            self.engine.play(pos)
        else:
            self._virtual_base = pos
            self._virtual.start()
            self._virtual_active = True
            self._on_play_state(True)

    def pause(self) -> None:
        if self.engine.playing:
            self.engine.pause()
        if self._virtual_active:
            self._virtual_base = self.position_ms
            self._virtual_active = False
            self._on_play_state(False)

    def seek(self, ms: float) -> None:
        ms = max(0.0, float(ms))
        self.engine.seek(ms)
        if self._virtual_active:
            self._virtual_base = ms
            self._virtual.start()
        self._update_playhead(ms, force=True)

    def _on_play_state(self, playing: bool) -> None:
        self.btn_play.setText(i18n.tr("⏸  暂停") if playing else i18n.tr("▶  播放"))
        self.timeline.set_position(self.position_ms, playing)

    # ============================================================ 界面语言
    def set_language(self, code: str) -> None:
        i18n.set_language(code)
        self.settings.language = code
        self.settings.save()
        for c, a in self.lang_actions.items():
            a.setChecked(c == code)
        i18n.translate_tree(self)          # 控件原文都记在 _zh 属性里，直接重译
        self._on_play_state(self.engine.playing or self._virtual_active)
        self.refresh_all()
        self.timeline.update()
        self.preview.update()
        self.statusBar().showMessage(
            {"zh": "界面语言：中文", "ja": "表示言語：日本語", "en": "UI language: English"}[code],
            5000)

    def _on_slider_moved(self, v: int) -> None:
        if self.engine.duration_ms <= 0:
            return
        ms = v / 1000.0 * self.engine.duration_ms
        self.lbl_pos.setText(fmt_time(ms))

    def _on_slider_released(self) -> None:
        self._dragging_pos = False
        if self.engine.duration_ms > 0:
            self.seek(self.slider_pos.value() / 1000.0 * self.engine.duration_ms)

    def _on_loop_toggle(self, on: bool) -> None:
        self.doc.state.loop_on = bool(on)
        self.timeline.update()

    def _on_metronome(self, on: bool) -> None:
        self.engine.set_metronome(bool(on))

    def _on_rate(self, _i: int) -> None:
        r = float(self.cb_rate.currentData())
        was = self.engine.playing or self._virtual_active
        pos = self.position_ms
        self.settings.rate = r
        self.settings.save()
        self.engine.set_rate(r)
        self.seek(pos)
        if was:
            self.play()

    def _on_follow(self, on: bool) -> None:
        self.doc.state.follow = bool(on)

    def _toggle_wave(self, on: bool) -> None:
        self.doc.state.show_waveform = bool(on)
        self.timeline.update()

    def _toggle_safe(self, on: bool) -> None:
        self.settings.show_safe = bool(on)
        self.settings.save()
        self.preview.set_safe_area(bool(on))

    def _toggle_ref(self, on: bool) -> None:
        self.refvideo.setVisible(bool(on) and bool(self._films))

    def _toggle_film(self, on: bool) -> None:
        self.doc.state.show_video = bool(on)
        self.timeline.update()

    def _toggle_spec(self, on: bool) -> None:
        self.doc.state.show_spectrum = bool(on)
        self._update_spec_state()
        self.timeline.update()

    def _toggle_live(self, on: bool) -> None:
        self.spectrum.setVisible(bool(on))

    def _update_spec_state(self) -> None:
        on = self.a_spec.isChecked()
        if on and self.spec is None and self.engine.samples is not None:
            self.start_spectrogram()
        self.spectrum.setVisible(self.a_live.isChecked())

    # ============================================================== 频谱
    def start_spectrogram(self) -> None:
        """后台算整首歌的频谱图（算完给时间轴用）。"""
        if self.engine.samples is None or self._spec_task is not None:
            return
        samples = self.engine.samples
        sr = self.engine.sr
        t0 = time.perf_counter()

        def work():
            from ..audio import spectrogram

            sp = spectrogram(samples, sr)
            return sp, time.perf_counter() - t0

        self.statusBar().showMessage("正在计算频谱图…")
        task = Task(work, parent=self)
        self._spec_task = task

        def on_done(res) -> None:
            self._spec_task = None
            sp, sec = res
            self.spec = sp
            self.timeline.set_spectrogram(sp if self.a_spec.isChecked() else None)
            self.statusBar().showMessage(f"频谱图完成（{sec:.1f}s，{sp.frames} 列）", 4000)

        def on_fail(msg: str) -> None:
            self._spec_task = None
            if self.silent:
                print("[频谱失败]", msg, flush=True)

        task.done.connect(on_done)
        task.failed.connect(on_fail)
        task.start()

    # ============================================================ 参考视频
    def _video_len(self) -> float:
        """媒体总长度（取所有片段末端）。"""
        ends = [c.end_ms() for c in self.doc.project.clips if c.duration_ms > 0]
        return max(ends) if ends else 0.0

    def _video_path(self) -> str:
        for c in self.doc.project.clips:
            if c.kind == "video":
                return c.path
        return ""

    def _update_duration(self) -> None:
        self.timeline.set_duration(max(self.engine.duration_ms, self._video_len()))

    def import_video(self, path: str | None = None) -> None:
        """导入参考视频（也可以直接拖进窗口）。"""
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "导入音视频", self.settings.last_dir,
                "音视频 (*.mp3 *.wav *.flac *.m4a *.mp4 *.mov *.mkv *.avi *.webm *.flv *.wmv);;"
                "所有文件 (*)")
        if path:
            self.import_media(path)

    # ========================================================== 多段媒体
    def _ask_media_mode(self, path: str, is_video: bool) -> str | None:
        """每次导入都问：替换 / 追加 / 取消。"""
        clips = self.doc.project.clips
        if not clips or self.silent:
            return "replace" if not clips else "append"
        box = QMessageBox(self)
        box.setWindowTitle("导入媒体")
        box.setIcon(QMessageBox.Question)
        kind = "视频" if is_video else "音频"
        box.setText(f"要导入的{kind}：{os.path.basename(path)}")
        box.setInformativeText(
            f"当前时间轴上已有 {len(clips)} 段媒体（总长 {self._video_len() / 1000:.1f}s）。\n\n"
            f"· 追加：接在最后一段之后（保持现有内容）\n"
            f"· 替换：清空现有媒体，只留这一段\n"
            f"· 插入：放到播放头位置，其余片段往后顺延")
        b_append = box.addButton("追加到末尾", QMessageBox.AcceptRole)
        b_insert = box.addButton("插入到播放头", QMessageBox.ActionRole)
        b_replace = box.addButton("替换全部", QMessageBox.DestructiveRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(b_append)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_append:
            return "append"
        if clicked is b_insert:
            return "insert"
        if clicked is b_replace:
            return "replace"
        return None

    def import_media(self, path: str, mode: str | None = None) -> None:
        """导入一段音频或视频；音频会解码进缓存，视频还会抽参考画面。"""
        if not path or not os.path.isfile(path):
            return
        ff = self.settings.ffmpeg
        if not ff or not os.path.isfile(ff):
            self._warn("缺少 ffmpeg", "没找到 ffmpeg.exe，请在「设置」标签里指定路径。")
            return
        is_video = path.lower().endswith(VIDEO_EXT)
        from ..video import probe_audio_stream, probe_video

        if mode is None:
            mode = self._ask_media_mode(path, is_video)
        if mode is None:
            return
        info = probe_video(path) if is_video else None
        ainfo = probe_audio_stream(path) if is_video else {"has": True}
        if is_video and info is not None and info.duration_ms <= 0:
            info.duration_ms = 60000.0
        if not ainfo.get("has") and not is_video:
            self._warn("这个文件没有声音", "请换一个音频文件。")
            return
        self._media_mode = mode
        self._media_path = path
        self._media_is_video = is_video
        self._media_info = info
        self._media_ainfo = ainfo
        self.statusBar().showMessage(
            f"正在{'抽取参考画面与解码音频' if is_video else '解码音频'}：{os.path.basename(path)} …")
        QApplication.setOverrideCursor(Qt.BusyCursor)
        task = Task(self._media_worker, path, bool(is_video), ff, bool(ainfo.get("has")), parent=self)
        task.done.connect(lambda res: self._media_ready(path, mode, res))
        task.failed.connect(self._task_failed)
        task.start()

    @staticmethod
    def _media_worker(path: str, is_video: bool, ffmpeg: str, has_audio: bool):
        from ..video import extract_filmstrip, probe_video

        samples = None
        sr = 48000
        pk = None
        if has_audio:
            samples, sr = decode_audio(path, ffmpeg=ffmpeg)
            if samples.size == 0:
                samples = None
        if samples is not None:
            pk = peaks(samples, PEAK_BUCKETS)
        film = None
        if is_video:
            info = probe_video(path)
            film = extract_filmstrip(path, info, ffmpeg, target_fps=4.0, thumb_h=72,
                                     max_frames=1500)
        return samples, sr, pk, film

    def _media_ready(self, path: str, mode: str, res) -> None:
        from ..model import MediaClip

        QApplication.restoreOverrideCursor()
        samples, sr, pk, film = res
        info = self._media_info
        ainfo = self._media_ainfo or {}
        is_video = bool(self._media_is_video)
        if samples is not None:
            self._media_cache[path] = (samples, sr)
        if film is not None:
            self._films[path] = film

        audio_ms = (samples.shape[0] * 1000.0 / sr) if samples is not None else 0.0
        video_ms = 0.0
        if is_video and film is not None and getattr(film, "count", 0):
            video_ms = float(film.times[-1]) + float(film.step_ms)
        if info is not None:
            video_ms = max(video_ms, float(info.duration_ms or 0.0))
        dur = max(audio_ms, video_ms, 1000.0)

        clip = MediaClip(path=path, kind="video" if is_video else "audio",
                         duration_ms=dur, name=os.path.basename(path),
                         has_audio=samples is not None)
        clips = self.doc.project.clips
        if mode == "replace":
            clips.clear()
            clip.offset_ms = 0.0
        elif mode == "insert":
            at = self.position_ms
            clip.offset_ms = at
            for c in clips:
                if c.offset_ms >= at:
                    c.offset_ms += dur
        else:                                   # append
            clip.offset_ms = self._video_len()
        clips.append(clip)

        if is_video:
            self.doc.project.video_path = path
        if clip.has_audio:
            self.doc.project.audio_path = path
        self.doc.touch("导入媒体")
        self._rebuild_media()
        self.settings.remember_dir(path)
        self.settings.save()
        self.refvideo.setVisible(self.a_ref.isChecked() and bool(self._films))
        mode_txt = {"replace": "替换并载入", "append": "追加到末尾", "insert": "插入到播放头"}[mode]
        self.statusBar().showMessage(
            f"已{mode_txt}：{os.path.basename(path)}　"
            f"（{dur / 1000:.1f}s，{'视频+音频' if is_video and clip.has_audio else ('视频（无声）' if is_video else '音频')}）"
            f"　时间轴共 {len(clips)} 段 / {self._video_len() / 1000:.1f}s", 9000)
        if not getattr(self, "_zoomed_once", False):
            self.timeline.zoom_to_fit()
            self._zoomed_once = True

    def _rebuild_media(self) -> None:
        """把所有片段的音频混成一条时间轴音频 —— 波形/频谱/BPM/播放全都基于它。"""
        clips = self.doc.project.clips
        sr = 48000
        total = max(1000.0, self._video_len())
        n = int(total / 1000.0 * sr) + 1
        mix = np.zeros((n, 2), dtype=np.float32)
        any_audio = False
        for c in clips:
            got = self._media_cache.get(c.path)
            if got is None:
                continue
            samples, csr = got
            s0 = int(c.src_start_ms / 1000.0 * csr)
            take = c.duration_ms or (samples.shape[0] / csr * 1000.0 - c.src_start_ms)
            seg = samples[s0: s0 + int(take / 1000.0 * csr)]
            if seg.size == 0:
                continue
            d0 = int(c.offset_ms / 1000.0 * sr)
            e = min(n, d0 + seg.shape[0])
            if e > d0:
                mix[d0:e] += seg[: e - d0]
                any_audio = True
        if any_audio:
            np.clip(mix, -1.0, 1.0, out=mix)
        self.engine.samples = mix if any_audio else None
        self.engine.sr = sr
        self.engine.path = clips[0].path if clips else ""
        self.engine.duration_ms = mix.shape[0] * 1000.0 / sr
        self.engine._rebuild()
        self._peaks = peaks(mix, PEAK_BUCKETS) if any_audio else None
        self.timeline.set_audio(self._peaks, max(self.engine.duration_ms, self._video_len()))
        self.timeline.set_clips([(c, self._films.get(c.path)) for c in clips])
        self.spectrum.set_audio(mix if any_audio else None, sr)
        self.refvideo.set_clips([(c, self._films.get(c.path)) for c in clips], self.settings.ffmpeg)
        self.spec = None
        self.timeline.set_spectrogram(None)
        if any_audio and self.a_spec.isChecked():
            QTimer.singleShot(120, self.start_spectrogram)
        self._update_duration()

    def use_video_audio(self) -> None:
        """把某个视频片段里的声音单独提出来当音频（多加一段）。"""
        path = self._video_path()
        if not path:
            self._info("还没有参考视频", "先导入一个带声音的视频。")
            return
        from ..video import probe_audio_stream

        if not probe_audio_stream(path).get("has"):
            self._info("这个视频没有声音", "视频里没有音轨，请另外载入音频文件。")
            return
        self.import_media(path, mode="append")

    # ================================================================ 时钟
    def _tick(self) -> None:
        playing = self.engine.playing or self._virtual_active
        pos = self.position_ms
        st = self.doc.state
        dur = self.engine.duration_ms
        if playing and dur > 0 and pos >= dur:
            if st.loop_on and st.loop_b > st.loop_a:
                self.seek(st.loop_a)
                self.play()
            else:
                self.pause()
                self.seek(dur)
                pos = dur
        if playing and st.loop_on and st.loop_b > st.loop_a and pos >= st.loop_b:
            self.seek(st.loop_a)
            self.play()
            pos = st.loop_a
        self._update_playhead(pos, force=False)
        if self.doc.dirty and time.time() - self._last_autosave > 90:
            self._last_autosave = time.time()
            self._autosave()

    def _update_playhead(self, pos: float, force: bool) -> None:
        playing = self.engine.playing or self._virtual_active
        self.timeline.set_position(pos, playing)
        self.preview.set_position(pos)
        self.refvideo.set_playhead(pos, playing)
        if self.spectrum.isVisible():
            self.spectrum.set_position(pos, playing)
        self.panel_chart.set_playhead(pos)
        now = time.perf_counter()
        # 状态栏/进度条不需要 60fps，否则每帧都在触发布局重排
        if force or now - self._last_status > 0.06:
            self._last_status = now
            self.lbl_pos.setText(fmt_time(pos))
            tm = self.doc.project.chart.timemap
            b = tm.beat(pos)
            self.lbl_beat.setText(i18n.trf("拍 {b:.2f}（第 {n} 拍）", b=b, n=int(b // 1) + 1))
            self.lbl_bpm.setText(f"{tm.bpm_at(pos):g} BPM")
            dur = self.engine.duration_ms
            if dur > 0 and not self._dragging_pos:
                self.slider_pos.blockSignals(True)
                self.slider_pos.setValue(int(pos / dur * 1000))
                self.slider_pos.blockSignals(False)
        if force:
            self.update()
        if now - self._fps_t > 0.5:
            self._fps_t = now
            self.lbl_fps.setText(i18n.trf("界面 {a:.0f}/{b:.0f} fps",
                                          a=self.timeline.fps(), b=self.preview.fps()))

    # ================================================================ 音频
    def open_audio(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入音频", self.settings.last_dir,
            "音频 (*.mp3 *.wav *.flac *.m4a *.aac *.ogg *.opus *.wma *.aiff);;所有文件 (*)")
        if path:
            self.import_media(path)

    def load_audio(self, path: str) -> None:
        """兼容旧调用：当作「导入一段媒体」。"""
        self.import_media(path)

    @staticmethod
    def _decode_worker(path: str, ffmpeg: str):
        samples, sr = decode_audio(path, ffmpeg=ffmpeg)
        pk = peaks(samples, PEAK_BUCKETS)
        return samples, sr, pk

    def _task_failed(self, msg: str) -> None:
        QApplication.restoreOverrideCursor()
        if self.silent:
            print("[错误]", msg, flush=True)
            self.statusBar().showMessage("操作失败（详情见控制台）", 5000)
            return
        QMessageBox.critical(self, "出错", msg)
        self.statusBar().showMessage("操作失败", 5000)

    def _warn(self, title: str, text: str) -> None:
        if self.silent:
            print(f"[提示] {title}: {text}", flush=True)
        else:
            QMessageBox.warning(self, title, text)

    def _info(self, title: str, text: str) -> None:
        if self.silent:
            print(f"[提示] {title}: {text}", flush=True)
        else:
            QMessageBox.information(self, title, text)

    # ============================================================== 打点/编辑
    def tap_note(self) -> None:
        """在播放位置打一个点（卡点核心操作）。"""
        pos = self.position_ms
        st = self.doc.state
        if st.tap_quantize and st.snap_on:
            pos = self.doc.project.chart.timemap.snap(pos, st.snap_divisor)
        rows = self.doc.project.theme.active_rows()
        lane = max(0, min(len(rows) - 1, st.current_lane))
        nn = Note(pos, rows[lane], lane)
        self.doc.edit("打点", lambda: self.doc.project.chart.add(nn))
        self.doc.set_selection([nn])
        self.statusBar().showMessage(f"打点 @ {fmt_time(pos)}", 1200)

    def select_all(self) -> None:
        self.doc.set_selection(list(self.doc.project.chart.notes))

    def delete_selection(self) -> None:
        self.timeline.delete_selected()

    def copy_notes(self) -> None:
        sel = sorted(self.doc.selection, key=lambda n: n.t)
        if not sel:
            return
        self._clipboard = [n.to_dict() for n in sel]
        self._clip_anchor = sel[0].t
        self.timeline.set_clipboard_state(True)
        self.statusBar().showMessage(f"已复制 {len(sel)} 个音符", 2000)

    def paste_notes(self, at_ms: float | None = None, at_row: int | None = None) -> None:
        """粘贴：默认落在鼠标处（没有鼠标位置就落播放头），可整块挪到别的音符行。

        at_ms  目标时间（该时间对应复制块最早那个音符）
        at_row 目标行号；给了就把整块音符挪到这一行（跨音符种类粘贴）
        """
        if not self._clipboard:
            return
        rows = self.doc.project.theme.active_rows()
        clip_rows = []
        for d in self._clipboard:
            t = str(d.get("type", ""))
            clip_rows.append(rows.index(t) if t in rows else int(d.get("lane", 0)))
        top = min(clip_rows) if clip_rows else 0
        if at_ms is None:
            tl = self.timeline
            at_ms = getattr(tl, "_mouse_ms", None) or self.position_ms
            if at_row is None:
                r = getattr(tl, "_mouse_row", -1)
                at_row = None if r < 0 else r
        d_row = 0 if at_row is None else (int(at_row) - top)
        base = float(at_ms)
        added = []
        before = self.doc.snapshot()

        def fn():
            for d, crow in zip(self._clipboard, clip_rows):
                n = Note.from_dict(d)
                n.t = max(0.0, base + (n.t - self._clip_anchor))
                if at_row is not None and rows:
                    n.lane = max(0, min(len(rows) - 1, crow + d_row))
                    n.type = rows[n.lane]
                self.doc.project.chart.add(n)
                added.append(n)

        fn()
        self.doc.undo_stack.push("粘贴", before, self.doc.snapshot())
        self.doc._mark_dirty(True)
        self.doc.set_selection(added)
        self.on_notes_changed()
        self.statusBar().showMessage(f"粘贴 {len(added)} 个音符", 2000)

    def duplicate_forward(self) -> None:
        sel = sorted(self.doc.selection, key=lambda n: n.t)
        if not sel:
            return
        tm = self.doc.project.chart.timemap
        b0 = tm.beat(sel[0].t)
        b1 = tm.beat(max(n.end for n in sel))
        span = max(1.0, round(b1 - b0))          # 至少一个整拍
        bar = 4.0
        span = max(bar, round(span / bar) * bar) if span > bar else span
        delta = tm.ms(b0 + span) - tm.ms(b0)
        added = []
        before = self.doc.snapshot()

        def fn():
            for n in sel:
                c = copy.deepcopy(n)
                c.t += delta
                self.doc.project.chart.add(c)
                added.append(c)

        fn()
        self.doc.undo_stack.push("向后复制", before, self.doc.snapshot())
        self.doc._mark_dirty(True)
        self.doc.set_selection(added)
        self.on_notes_changed()

    def quantize_selection(self) -> None:
        if not self.doc.selection:
            return
        st = self.doc.state
        tm = self.doc.project.chart.timemap

        def fn():
            for n in self.doc.selection:
                n.t = tm.snap(n.t, st.snap_divisor)

        self.doc.edit("量化", fn)
        self.on_notes_changed()

    def mirror_selection(self) -> None:
        sel = sorted(self.doc.selection, key=lambda n: n.t)
        if len(sel) < 2:
            return
        t0, t1 = sel[0].t, max(n.end for n in sel)

        def fn():
            for n in self.doc.selection:
                n.t = t0 + (t1 - n.end)

        self.doc.edit("镜像", fn)
        self.on_notes_changed()

    def set_loop_here(self) -> None:
        st = self.doc.state
        pos = self.position_ms
        if not st.loop_on or st.loop_b > st.loop_a:
            st.loop_a, st.loop_b = pos, pos
            st.loop_on = False
            self.chk_loop.setChecked(False)
            self.statusBar().showMessage("已设置循环起点，再按一次设置终点", 3000)
        else:
            st.loop_b = max(pos, st.loop_a + 100.0)
            st.loop_on = True
            self.chk_loop.setChecked(True)
            self.statusBar().showMessage(
                f"循环区间 {fmt_time(st.loop_a)} → {fmt_time(st.loop_b)}", 3000)
        self.timeline.update()

    def clear_loop(self) -> None:
        st = self.doc.state
        st.loop_on = False
        st.loop_a = st.loop_b = 0.0
        self.chk_loop.setChecked(False)
        self.timeline.update()

    def _on_row_changed(self, row: int) -> None:
        self.panel_types.list.setCurrentRow(row)

    # ========================================================== 区间批量填充
    def fill_range(self, start: float | None = None) -> None:
        """在区间内按自定义间隔填满音符。

        区间优先用时间轴上的**框选**（在标尺/波形处横向拖一下），其次循环区间。
        """
        chart = self.doc.project.chart
        st = self.doc.state
        dur = self.engine.duration_ms
        locked = False
        if start is not None:
            t0 = float(start)
            t1 = st.loop_b if (st.loop_on and st.loop_b > t0) else (dur or t0 + 8000.0)
        elif st.has_range():
            t0, t1, locked = st.sel_t0, st.sel_t1, True
        elif st.loop_on and st.loop_b > st.loop_a:
            t0, t1 = st.loop_a, st.loop_b
        elif len(chart):
            t0, t1 = chart.bounds()
        else:
            t0, t1 = 0.0, (dur or 8000.0)
        rows = [(k, self.doc.project.theme.style(k).name) for k in self.doc.project.theme.active_rows()]
        if not rows:
            return
        dlg = FillRangeDialog(self, chart.timemap, t0, t1, rows, st.current_lane,
                              st.snap_on, st.snap_divisor, dur, locked=locked, state=st)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        dlg.remember()
        times = dlg.times()
        if not times:
            self._info("没有可填充的位置", "先在时间轴上框选一段，或点「手动输入区间」。")
            return
        keys = dlg.type_keys()
        lane_of = {k: i for i, (k, _n) in enumerate(rows)}
        before = self.doc.snapshot()
        base = len(chart)
        for i, t in enumerate(times):
            k = keys[i % len(keys)]
            chart.add(Note(t, k, lane_of.get(k, 0)))
        self.doc.commit_external("区间填充", before)
        self.on_notes_changed()
        self.statusBar().showMessage(f"已填充 {len(chart) - base} 个音符", 5000)

    # ============================================================ 鼠标模式
    TOOLS = ("select", "draw", "range")

    def set_tool(self, name: str) -> None:
        st = self.doc.state
        st.tool = name if name in self.TOOLS else "select"
        self.a_select_tool.setChecked(st.tool == "select")
        self.a_draw_tool.setChecked(st.tool == "draw")
        self.a_range_tool.setChecked(st.tool == "range")
        cur = {"draw": Qt.CrossCursor, "range": Qt.SizeHorCursor}.get(st.tool, Qt.ArrowCursor)
        self.timeline.setCursor(cur)
        self.timeline.update()
        self.statusBar().showMessage({
            "draw": "鼠标模式：放置音符（按住拖动可连续刷；右键仍可改类型/删除）",
            "range": "鼠标模式：框选区间（在任意位置横向拖动选出时间区间，右键可填充/设为循环）",
        }.get(st.tool, "鼠标模式：选择 / 拖拽（空白拖动=框选音符，标尺/波形拖动=拖播放头）"), 5000)

    def toggle_tool(self) -> None:
        i = self.TOOLS.index(self.doc.state.tool) if self.doc.state.tool in self.TOOLS else 0
        self.set_tool(self.TOOLS[(i + 1) % len(self.TOOLS)])

    # ================================================================ BPM
    def edit_bpm_segment(self, idx: int) -> None:
        segs = self.doc.project.chart.timemap.segments
        if not (0 <= idx < len(segs)):
            return
        dlg = BpmSegmentDialog(self, segs[idx].time_ms, segs[idx].bpm, first=(idx == 0))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        t, bpm = dlg.values()
        before = self.doc.snapshot()
        segs = list(self.doc.project.chart.timemap.segments)
        segs[idx] = BpmSegment(t if idx > 0 else segs[0].time_ms, bpm)
        self.doc.project.chart.set_segments(segs, self.doc.project.chart.offset_ms)
        self.doc.undo_stack.push("修改 BPM 段", before, self.doc.snapshot())
        self.on_chart_changed()
        self.panel_chart.refresh()

    def add_bpm_segment_at(self, ms: float) -> None:
        segs = self.doc.project.chart.timemap.segments
        bpm = segs[-1].bpm if segs else 120.0
        dlg = BpmSegmentDialog(self, ms, bpm)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        t, b = dlg.values()
        before = self.doc.snapshot()
        self.doc.project.chart.add_segment(t, b)
        self.doc.undo_stack.push("添加 BPM 段", before, self.doc.snapshot())
        self.on_chart_changed()
        self.panel_chart.refresh()

    def tap_tempo_dialog(self) -> None:
        cur = self.doc.project.chart.timemap.bpm_at(self.position_ms)
        dlg = TapTempoDialog(self, cur)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        bpm = round(dlg.bpm, 3)
        before = self.doc.snapshot()
        self.doc.project.chart.add_segment(self.position_ms, bpm)
        self.doc.undo_stack.push("Tap 测速", before, self.doc.snapshot())
        self.on_chart_changed()
        self.panel_chart.refresh()
        self.statusBar().showMessage(f"已在播放头插入 {bpm:g} BPM 段", 4000)

    def auto_bpm(self) -> None:
        if self.engine.samples is None:
            self._info("没有音频", "请先载入音频。")
            return
        self.statusBar().showMessage("正在分析 BPM…")

        def work():
            bpm, conf = estimate_bpm(self.engine.samples, self.engine.sr)
            off = estimate_offset(self.engine.samples, bpm, self.engine.sr) if bpm else 0.0
            return bpm, conf, off

        task = Task(work, parent=self)
        task.done.connect(self._auto_bpm_done)
        task.failed.connect(self._task_failed)
        task.start()

    def _auto_bpm_done(self, res) -> None:
        bpm, conf, off = res
        if bpm <= 0:
            QMessageBox.information(self, "没测出来", "没能估计出 BPM，可以用 Tap 测速手动打。")
            return
        msg = (f"估计 BPM：{bpm:.2f}（置信度 {conf * 100:.0f}%）\n"
               f"建议节拍偏移：{off:.1f} ms\n\n"
               f"「是」= 整首统一用这个 BPM 并设好偏移\n"
               f"「否」= 只在播放头插入这个 BPM 段")
        r = QMessageBox.question(self, "自动检测 BPM", msg,
                                 QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel) if not self.silent \
            else QMessageBox.Yes
        if r == QMessageBox.Cancel:
            return
        before = self.doc.snapshot()
        if r == QMessageBox.Yes:
            self.doc.project.chart.set_segments([BpmSegment(off, bpm)], off)
        else:
            self.doc.project.chart.add_segment(self.position_ms, bpm)
        self.doc.undo_stack.push("自动 BPM", before, self.doc.snapshot())
        self.on_chart_changed()
        self.panel_chart.refresh()

    def auto_notes(self) -> None:
        if self.engine.samples is None and not self._video_path():
            self._info("没有素材", "请先载入音频，或导入参考视频。")
            return
        rows = [(k, self.doc.project.theme.style(k).name) for k in self.doc.project.theme.active_rows()]
        dlg = AutoNotesDialog(self, rows, has_video=bool(self._video_path()))
        dlg.detectRequested.connect(self._auto_notes_detect)
        while True:
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            if dlg.times:
                break
            self._info("先试检测", "请先点「试检测」生成结果。")
        times = list(dlg.times)
        key = dlg.params()["type"]
        rows_list = self.doc.project.theme.active_rows()
        lane = rows_list.index(key) if key in rows_list else 0
        before = self.doc.snapshot()

        def fn():
            for t in times:
                self.doc.project.chart.add(Note(t, key, lane))

        fn()
        self.doc.commit_external("自动铺点", before)
        self.on_notes_changed()
        self.statusBar().showMessage(f"已生成 {len(times)} 个音符", 5000)

    def _auto_notes_detect(self, params: dict) -> None:
        sens = params["sensitivity"]
        gap = params["min_gap_ms"]
        t0 = time.perf_counter()
        source = params.get("source", "audio")
        thr = float(params.get("scene_threshold", 0.35))

        def work():
            if source == "video":
                from ..video import scene_cuts

                times: list[float] = []
                for c in list(self.doc.project.clips):
                    fs = self._films.get(c.path)
                    if fs is None or not getattr(fs, "count", 0):
                        continue
                    for t in scene_cuts(fs, thr, min_gap_ms=gap):
                        times.append(c.offset_ms + t - c.src_start_ms)
                times = sorted(t for t in times if t >= 0)
            else:
                times = detect_onsets(self.engine.samples, self.engine.sr,
                                      sensitivity=sens, min_gap_ms=gap)
            if params.get("quantize") and self.doc.state.snap_on and times:
                tm = self.doc.project.chart.timemap
                d = self.doc.state.snap_divisor
                times = sorted({round(tm.snap(t, d), 3) for t in times})
            return times, time.perf_counter() - t0

        self._auto_dlg = self.sender()
        task = Task(work, parent=self)
        task.done.connect(self._auto_notes_done)
        task.failed.connect(self._task_failed)
        task.start()

    def _auto_notes_done(self, res) -> None:
        times, elapsed = res
        dlg = getattr(self, "_auto_dlg", None)
        if dlg is not None:
            dlg.set_result(times, elapsed)

    # ================================================================ 工程
    def new_project(self) -> None:
        if not self._confirm_discard():
            return
        self.set_project(Project())
        self.statusBar().showMessage("新工程", 3000)

    def load_demo(self) -> None:
        """示例工程：没音频也能立刻看到/试用效果。"""
        if not self._confirm_discard():
            return
        self.set_project(make_demo_project(bars=16))
        self.statusBar().showMessage("已载入示例工程：可以直接改音符、换外观、试导出", 8000)

    def set_project(self, project: Project, audio_path: str = "") -> None:
        self.pause()
        self.engine.clear()
        self._peaks = None
        self.spec = None
        self._spec_task = None
        self.spectrum.set_audio(None, 48000)
        self.timeline.set_spectrogram(None)
        self._media_cache.clear()
        self._films.clear()
        self.timeline.set_clips([])
        self.refvideo.set_clips([])
        self.refvideo.setVisible(False)
        self.doc.project = project
        self.doc.undo_stack.clear()
        self.doc.selection.clear()
        self.doc.mark_clean()
        self.doc.state = type(self.doc.state)()
        self.selection_loaded = True
        self.timeline.set_audio(None, 0.0)
        self.timeline.set_duration(0.0)
        self.refresh_all()
        # 恢复工程里的媒体片段：逐个解码进缓存，最后统一混音
        from ..model import MediaClip

        restore = [c for c in project.clips if c.path and os.path.isfile(c.path)]
        gone = len(project.clips) - len(restore)
        if audio_path and os.path.isfile(audio_path) and not any(c.path == audio_path for c in restore):
            restore.append(MediaClip(path=audio_path, kind="audio",
                                     name=os.path.basename(audio_path)))
        if gone:
            self.statusBar().showMessage(f"有 {gone} 段媒体文件找不到了，已跳过", 7000)
        if restore:
            self._restore_queue = list(restore)
            QTimer.singleShot(200, self._restore_next)
        elif len(project.chart) == 0:
            self.statusBar().showMessage("空工程：先导入音频/视频，或用「示例工程」练手", 6000)
        self._refresh_recent()

    def _restore_next(self) -> None:
        """依次把工程里的媒体片段解码回缓存。"""
        q = getattr(self, "_restore_queue", [])
        if not q:
            self._rebuild_media()
            self.timeline.zoom_to_fit()
            if self._films:
                self.refvideo.setVisible(self.a_ref.isChecked())
            self.statusBar().showMessage(
                f"工程已就绪：{len(self.doc.project.clips)} 段媒体 / "
                f"{self._video_len() / 1000:.1f}s", 6000)
            return
        clip = q.pop(0)
        ff = self.settings.ffmpeg
        if not ff or not os.path.isfile(ff):
            return
        task = Task(self._media_worker, clip.path, clip.kind == "video", ff,
                    bool(clip.has_audio), parent=self)
        task.done.connect(lambda res, c=clip: self._restore_done(c, res))
        task.failed.connect(lambda _m: self._restore_next())
        task.start()

    def _restore_done(self, clip, res) -> None:
        samples, sr, _pk, film = res
        if samples is not None:
            self._media_cache[clip.path] = (samples, sr)
        if film is not None:
            self._films[clip.path] = film
        self._restore_next()

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "打开工程", self.settings.last_dir, f"节奏条工程 (*{PROJECT_EXT});;所有文件 (*)")
        if path:
            self.open_path(path)

    def open_path(self, path: str) -> None:
        if not self._confirm_discard():
            return
        try:
            if path.lower().endswith(AUDIO_EXT):
                self.set_project(Project(), audio_path=path)
            else:
                proj = Project.load(path)
                auto = path + ".autosave" + PROJECT_EXT
                if os.path.isfile(auto) and os.path.getmtime(auto) > os.path.getmtime(path):
                    if not self.silent and QMessageBox.question(
                            self, "发现自动保存",
                            f"这个工程有一份更新的自动保存：\n{os.path.basename(auto)}\n\n恢复它吗？"
                            "（选「否」就打开原工程）") == QMessageBox.Yes:
                        proj = Project.load(auto)
                        proj.path = os.path.abspath(path)
                self.set_project(proj)
                self.settings.add_recent(path)
                self.settings.save()
                self._refresh_recent()
                self.statusBar().showMessage(f"已打开 {os.path.basename(path)}", 5000)
        except Exception as e:  # noqa: BLE001
            self._task_failed(f"打不开 {path}：{e}")

    def save_project(self) -> bool:
        if not self.doc.project.path:
            return self.save_project_as()
        try:
            self.doc.project.save(self.doc.project.path)
            self.doc.mark_clean()
            self.settings.add_recent(self.doc.project.path)
            self.settings.save()
            self._refresh_recent()
            self.statusBar().showMessage("已保存", 3000)
            return True
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(e))
            return False

    def save_project_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(
            self, "保存工程", os.path.join(self.settings.last_dir, "我的节奏条" + PROJECT_EXT),
            f"节奏条工程 (*{PROJECT_EXT})")
        if not path:
            return False
        self.doc.project.path = path
        return self.save_project()

    def _autosave(self) -> None:
        p = self.doc.project.path
        try:
            if p:
                self.doc.project.save(p + ".autosave")
            else:
                d = os.path.join(os.path.expanduser("~"), ".rbar")
                os.makedirs(d, exist_ok=True)
                self.doc.project.path = ""
                tmp = Project()
                tmp.load_dict(self.doc.project.to_dict())
                tmp.save(os.path.join(d, "autosave" + PROJECT_EXT))
        except Exception:
            pass

    def _confirm_discard(self) -> bool:
        if not self.doc.dirty or self.silent:
            return True
        r = QMessageBox.question(self, "还没保存", "当前工程有未保存的改动，要保存吗？",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Cancel:
            return False
        if r == QMessageBox.Save:
            return self.save_project()
        return True

    # ================================================================ 导出
    def export_dialog(self) -> None:
        ex = self.doc.project.export
        if not ex.output:
            base = os.path.splitext(os.path.basename(self.doc.project.path or "节奏条"))[0]
            ext = {"mov_prores": ".mov", "mov_qtrle": ".mov", "webm_vp9": ".webm",
                   "webm_vp8": ".webm", "mp4_black": ".mp4", "png_seq": ""}.get(ex.fmt, ".mov")
            out = os.path.join(self.settings.last_dir, base + "_bar" + ext)
            ex.output = out
            self.panel_export.refresh()
        if not ex.output:
            QMessageBox.information(self, "先选输出位置", "请在「导出」面板里选一个输出文件。")
            self.tabs.setCurrentWidget(self.panel_export)
            return
        ff = self.settings.ffmpeg
        if not ff or not os.path.isfile(ff):
            QMessageBox.warning(self, "缺少 ffmpeg", "没找到 ffmpeg.exe，请在「设置」标签里指定路径。")
            self.tabs.setCurrentWidget(self.panel_settings)
            return
        t0, t1 = compute_range(self.doc.project, self.engine.duration_ms)
        frames = max(1, int(round((t1 - t0) / 1000.0 * ex.fps)))
        report = self._estimate_report(frames)
        r = QMessageBox.question(
            self, "开始导出",
            f"格式：{ex.fmt}\n尺寸：{ex.width}×{ex.height} @ {ex.fps}fps（{ex.supersample}× 超采样）\n"
            f"帧数：{frames}（{fmt_time(t1 - t0)}）\n预计：{report}\n\n开始导出？",
            QMessageBox.Ok | QMessageBox.Cancel) if not self.silent else QMessageBox.Ok
        if r != QMessageBox.Ok:
            return
        self._run_export(t0, t1, frames)

    def _estimate_report(self, frames: int) -> str:
        fmt = self.doc.project.export.fmt
        speed = {"mov_qtrle": 900, "mov_prores": 400, "png_seq": 500,
                 "mp4_black": 300, "webm_vp8": 120, "webm_vp9": 60}.get(fmt, 200)
        sec = frames / speed
        if sec < 60:
            return f"约 {sec:.0f} 秒"
        return f"约 {sec / 60:.1f} 分钟"

    def _run_export(self, t0: float, t1: float, frames: int) -> None:
        ex = self.doc.project.export
        dlg = QProgressDialog("正在导出…", "取消", 0, frames, self)
        dlg.setWindowTitle("导出视频")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        self._export_cancel.clear()
        dlg.canceled.connect(self._export_cancel.set)

        started = time.perf_counter()

        def progress(done: int, total: int, eta: float) -> None:
            task.progress.emit(done, total, eta)

        task = Task(export_video, self.doc.project, ex.output, ffmpeg=self.settings.ffmpeg,
                    progress=progress, is_cancelled=self._export_cancel.is_set,
                    audio_duration_ms=self.engine.duration_ms, parent=self)
        self._export_task = task

        def on_progress(done: int, total: int, eta: float) -> None:
            dlg.setMaximum(total)
            dlg.setValue(done)
            dlg.setLabelText(f"导出中… {done}/{total} 帧　剩余约 {eta:.0f}s")

        def on_done(res) -> None:
            dlg.close()
            QApplication.restoreOverrideCursor()
            if getattr(res, "cancelled", False):
                self.statusBar().showMessage("导出已取消", 5000)
                return
            dur = time.perf_counter() - started
            self.statusBar().showMessage(f"导出完成：{res.path}", 15000)
            size = ""
            if os.path.isfile(res.path):
                size = f"\n文件大小：{os.path.getsize(res.path) / 1048576:.1f} MB"
            QMessageBox.information(
                self, "导出完成",
                f"已导出 {res.frames} 帧，用时 {dur:.1f}s\n{res.path}{size}\n\n"
                f"导入剪辑软件后，把它放在原视频上层即可（透明通道已带）。")
            if ex.open_after:
                self._open_folder(os.path.dirname(os.path.abspath(res.path)))

        def on_failed(msg: str) -> None:
            dlg.close()
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "导出失败", msg)

        task.progress.connect(on_progress)
        task.done.connect(on_done)
        task.failed.connect(on_failed)
        QApplication.setOverrideCursor(Qt.BusyCursor)
        task.start()

    @staticmethod
    def _open_folder(path: str) -> None:
        try:
            os.startfile(path)  # noqa: S606
        except Exception:
            pass

    def export_still_dialog(self) -> None:
        ex = self.doc.project.export
        base = os.path.splitext(os.path.basename(self.doc.project.path or "节奏条"))[0]
        name = f"{base}_{int(self.position_ms)}ms.png"
        path, _ = QFileDialog.getSaveFileName(self, "导出当前帧", os.path.join(self.settings.last_dir, name),
                                              "PNG 图片 (*.png)")
        if not path:
            return
        try:
            export_still(self.doc.project, self.position_ms, path, ex.width, ex.height, ex.supersample)
            self.statusBar().showMessage(f"已导出图片：{path}", 8000)
            if ex.open_after:
                self._open_folder(os.path.dirname(os.path.abspath(path)))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "导出失败", str(e))

    # ============================================================ 检查更新
    def check_updates(self, auto: bool = False) -> None:
        """查 GitHub Releases 有没有新版本；auto=True 时静默失败、只在有更新时弹窗。"""
        if auto and not getattr(self.settings, "check_updates", True):
            return
        if auto:
            import time as _t

            last = float(getattr(self.settings, "last_update_check", 0.0) or 0.0)
            if _t.time() - last < 6 * 3600:       # 6 小时内不重复打扰 GitHub
                return
        if self._update_task is not None:
            return
        from ..updater import fetch_latest, is_newer

        task = Task(fetch_latest, parent=self)
        self._update_task = task
        if not auto:
            self.statusBar().showMessage("正在检查更新…")

        def stamp() -> None:
            import time as _t

            self.settings.last_update_check = _t.time()
            self.settings.save()

        def done(res) -> None:
            self._update_task = None
            stamp()
            if not res or res.get("none"):
                if not auto:
                    self._info("检查更新", "作者还没有发布任何 Release。\n（仓库一直在更新代码，可以关注 commit）")
                return
            tag = str(res.get("tag", ""))
            if is_newer(tag, __version__):
                self._show_update_dialog(res)
            elif not auto:
                self._info("检查更新", f"已是最新版（v{__version__}）。\n最新 Release：{tag or '—'}")

        def failed(msg: str) -> None:
            self._update_task = None
            stamp()                           # 失败也记时间，别一直重试
            if auto:
                return                        # 自动检查失败就静默，不打扰
            box = QMessageBox(self)
            box.setWindowTitle("检查更新失败")
            box.setIcon(QMessageBox.Warning)
            box.setText(str(msg))
            box.setInformativeText("也可以直接打开 Releases 页面手动看看。")
            b_open = box.addButton("打开 Releases 页面", QMessageBox.AcceptRole)
            box.addButton("好", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is b_open:
                import webbrowser

                webbrowser.open(RELEASES_PAGE)

        task.done.connect(done)
        task.failed.connect(failed)
        task.start()

    def _show_update_dialog(self, res: dict) -> None:
        tag = res.get("tag") or res.get("name") or "?"
        body = (res.get("body") or "").strip()
        if len(body) > 1000:
            body = body[:1000] + "…"
        assets = res.get("assets") or []
        names = "、".join(a["name"] for a in assets[:5] if a.get("name"))
        if names:
            body += f"\n\n发布附件：{names}"
        box = QMessageBox(self)
        box.setWindowTitle("发现新版本")
        box.setIcon(QMessageBox.Information)
        box.setText(f"有新版本可用：{tag}（当前 v{__version__}）")
        box.setInformativeText(body or "点「打开下载页」到 GitHub 查看。")
        b_open = box.addButton("打开下载页", QMessageBox.AcceptRole)
        box.addButton("以后再说", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is b_open:
            import webbrowser

            webbrowser.open(res.get("url") or RELEASES_PAGE)

    # ================================================================ 帮助
    def show_help(self) -> None:
        QMessageBox.information(
            self, "用法与快捷键",
            "【最快上手流程】\n"
            "1. 把 mp3/wav 拖进窗口（或 Ctrl+Shift+O）\n"
            "2. 空格播放，跟着音乐按 F 打点；或点「自动检测 BPM」+「自动铺点」\n"
            "3. 整段铺点也可以用「工具 → 区间批量填充音符」按固定间隔一次填满\n"
            "4. 有画面要卡：Ctrl+Shift+V 导入参考视频（视频自带声音的话会自动拿来当音频，\n"
            "   波形/频谱/自动 BPM 都基于它）；时间轴会多出胶片条和频谱图\n"
            "5. Ctrl+E 导出透明视频，拖进剪辑软件放在原视频上层\n\n"
            "【判定点】\n"
            "  中间常驻一枚 45° 灰白菱形；音符打到它时按该音符颜色闪一下。\n"
            "  颜色/大小/闪烁时长都在「外观 → 判定点」里调。\n\n"
            "【对音：波形 + 频谱】\n"
            "  时间轴从下到上是：音符行 / 视频胶片条 / 频谱图 / 波形。\n"
            "  频谱图是对音主力：鼓点、音头都是频谱里的一条竖纹，\n"
            "  把节拍网格线（可拖 BPM 段边界）跟竖纹对齐就准了。\n"
            "  左下角还有实时频谱条，播放时能看当前频段能量。\n"
            "  视图菜单里可分别开关频谱图 / 实时频谱 / 胶片条。\n\n"
            "【两种鼠标模式（V / B / R / Tab 切换）】\n"
            "  选择拖拽：空白拖动=框选音符；拖音符=移动；拖右侧把手=变长条\n"
            "  放置音符：单击=加点；按住拖动=沿网格连续刷（一次撤销）\n"
            "  框选区间：在任意位置横向拖动=选出时间区间（Shift+拖动同效）\n"
            "  注意：标尺/波形处**直接拖动是拖播放头**，框选区间用 Shift 或 R 模式\n\n"
            "【框选区间 → 填充（不用手打时间）】\n"
            "  框出区间后，右键「在这个区间填充音符…」或 工具菜单，选好间隔和类型即可。\n"
            "  右键还能「把区间设为循环区间」「清除区间选择」「清除循环区间」。\n\n"
            "【时间轴】\n"
            "  右键 = 改类型/量化/删除/从这一点填充；Ctrl+拖 = 加选\n"
            "  Ctrl+滚轮 = 缩放；Shift+滚轮 = 平移；中键拖动 = 平移\n\n"
            "【快捷键】\n"
            "  空格 播放/暂停　F 打点　V/B/R 鼠标模式　Tab 切换模式\n"
            "  Ctrl+Z/Y 撤销/重做　Del 删除\n"
            "  Ctrl+A 全选　Ctrl+C/V 复制/粘贴到播放头　Ctrl+D 向后复制\n"
            "  数字 1~9 切换音符类型　Q/W 上下换行　L 设循环　M 节拍器\n"
            "  Ctrl+0 缩放适应　Ctrl+E 导出视频　Ctrl+Shift+E 导出当前帧 PNG\n"
            "  Ctrl+Shift+V 导入参考视频　Ctrl+U 检查更新（菜单栏顶上那一项）")

    def show_about(self) -> None:
        QMessageBox.about(
            self, "关于",
            f"<b>{APP_NAME}</b> v{__version__}<br>"
            "给视频卡点用的节奏条编辑器：实时编辑 + BPM 变速 + 导出背景透明视频。<br><br>"
            "Python + PySide6 + ffmpeg")

    # ================================================================ 事件
    def event(self, ev):
        """Tab 切鼠标模式（输入框里仍然让 Tab 正常跳焦点）。

        单字母快捷键（V/B/F/数字）故意不做成 QAction 快捷键：
        QAction 会在焦点控件之前吃掉按键，导致在输入框里打不出这些字母。
        """
        if ev.type() == QEvent.KeyPress and ev.key() == Qt.Key_Tab and ev.modifiers() == Qt.NoModifier:
            fw = QApplication.focusWidget()
            if not isinstance(fw, (QLineEdit, QAbstractSpinBox, QComboBox, QTextEdit, QPlainTextEdit)):
                self.toggle_tool()
                return True
        return super().event(ev)

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        k = ev.key()
        mods = ev.modifiers()
        st = self.doc.state
        if k == Qt.Key_Space:
            self.toggle_play()
            return
        if k == Qt.Key_F and not mods:
            self.tap_note()
            return
        if k == Qt.Key_V and not mods:
            self.set_tool("select")
            return
        if k == Qt.Key_B and not mods:
            self.set_tool("draw")
            return
        if k == Qt.Key_R and not mods:
            self.set_tool("range")
            return
        if k == Qt.Key_Delete or k == Qt.Key_Backspace:
            self.delete_selection()
            return
        if Qt.Key_1 <= k <= Qt.Key_9 and not mods:
            idx = k - Qt.Key_1
            rows = self.doc.project.theme.active_rows()
            if idx < len(rows):
                st.current_lane = idx
                self.panel_types.list.setCurrentRow(idx)
                self.timeline.update()
            return
        if k == Qt.Key_Q and not mods:
            st.current_lane = max(0, st.current_lane - 1)
            self.panel_types.list.setCurrentRow(st.current_lane)
            self.timeline.update()
            return
        if k == Qt.Key_W and not mods:
            rows = self.doc.project.theme.active_rows()
            st.current_lane = min(len(rows) - 1, st.current_lane + 1)
            self.panel_types.list.setCurrentRow(st.current_lane)
            self.timeline.update()
            return
        if k == Qt.Key_L and not mods:
            self.set_loop_here()
            return
        if k == Qt.Key_M and not mods:
            self.chk_meta.toggle()
            return
        if k == Qt.Key_E and not mods:
            self.export_dialog()
            return
        if k in (Qt.Key_Left, Qt.Key_Right):
            step = 1000.0 if mods & Qt.ShiftModifier else 50.0
            if mods & Qt.ControlModifier:
                self.nudge_selection(-step if k == Qt.Key_Left else step)
            else:
                self.seek(self.position_ms + (-step if k == Qt.Key_Left else step))
            return
        if k == Qt.Key_Up and self.doc.selection:
            self.nudge_selection_row(-1)
            return
        if k == Qt.Key_Down and self.doc.selection:
            self.nudge_selection_row(1)
            return
        if k == Qt.Key_Home:
            self.seek(0.0)
            return
        if k == Qt.Key_S and not mods:
            st.snap_on = not st.snap_on
            self.panel_chart.refresh()
            self.statusBar().showMessage("吸附：" + ("开" if st.snap_on else "关"), 1500)
            return
        super().keyPressEvent(ev)

    def nudge_selection(self, delta_ms: float) -> None:
        if not self.doc.selection:
            return
        for n in self.doc.selection:
            n.t = max(0.0, n.t + delta_ms)
        self.doc.project.chart.sort()
        self.doc.touch("微调位置")
        self.on_notes_changed()

    def nudge_selection_row(self, d: int) -> None:
        rows = self.doc.project.theme.active_rows()
        for n in self.doc.selection:
            n.lane = max(0, min(len(rows) - 1, n.lane + d))
            n.type = rows[n.lane]
        self.doc.touch("换行")
        self.on_notes_changed()

    def dragEnterEvent(self, ev) -> None:  # noqa: N802
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:  # noqa: N802
        for url in ev.mimeData().urls():
            p = url.toLocalFile()
            if not p:
                continue
            low = p.lower()
            if low.endswith(AUDIO_EXT):
                self.load_audio(p)
            elif low.endswith(VIDEO_EXT):
                self.import_video(p)
            elif low.endswith(PROJECT_EXT):
                self.open_path(p)
            break

    def shutdown(self) -> None:
        """退出前收尾：停播放、收音频线程（不收的话关窗口会弹崩溃框）。"""
        try:
            self.pause()
        except Exception:
            pass
        try:
            self.engine.shutdown()
        except Exception:
            pass

    def closeEvent(self, ev) -> None:  # noqa: N802
        self.pause()
        if not self._confirm_discard():
            ev.ignore()
            return
        self.settings.save()
        self.engine.shutdown()             # 音频线程必须在这里收掉
        ev.accept()
