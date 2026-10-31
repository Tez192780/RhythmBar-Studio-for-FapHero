"""主窗口：把时间轴、预览、面板、播放与导出串起来。"""

from __future__ import annotations

import copy
import math
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
from ..audio import (
    AudioEngine,
    decode_audio,
    estimate_bpm,
    estimate_offset,
    find_ffprobe,
    peaks,
)
from ..doc import Doc
from ..exporter import compute_range, estimate_size_mb, export_still, export_video
from ..model import FORMATS, ExportSettings, Note, Project
from ..settings import AppSettings
from ..tasks import Task
from ..theme import Theme
from ..timing import BpmSegment
from ..updater import PAGE as RELEASES_PAGE
from .dialogs import BpmSegmentDialog, FillRangeDialog, TapTempoDialog
from .panels import ChartPanel, ExportPanel, NoteTypePanel, SettingsPanel, StylePanel
from .preview import PreviewWidget, RefFrameWidget, SpectrumWidget
from .timeline import TimelineWidget, fmt_time

AUDIO_EXT = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aiff", ".aif")
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".wmv", ".m4v", ".ts", ".mpg", ".mpeg")
PROJECT_EXT = ".rbarproj"
PEAK_BUCKETS = 30000
# 参考画面/时间轴胶片条的采样率。以前只有 4fps，预览明显发顿；12fps 更顺，
# 3000 帧上限让 3 分钟视频仍保持 12fps，更长的视频自动降采样。
FILMSTRIP_FPS = 12.0
FILMSTRIP_MAX_FRAMES = 3000

# 写进 rbar_settings.json 的导出偏好；输出路径、时间范围属于具体工程，不存。
CONFIG_EXPORT_KEYS = (
    "fmt", "fps", "width", "height", "supersample", "quality",
    "padding_ms", "background", "bg_color", "bar_valign", "open_after",
)


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings | None = None, project: Project | None = None):
        super().__init__()
        self.settings = settings or AppSettings()
        initial_project = project is None
        if initial_project:
            project = Project()
            self._apply_config_defaults(project)
        self.doc = Doc(project)
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
        self.spec = None                 # 兼容旧测试：第一段音频的频谱
        self._clip_specs: dict[str, object] = {}   # clip_id -> Spectrogram
        self._spec_task: Task | None = None
        self._update_task: Task | None = None      # 检查更新
        self._media_is_video = False
        self._media_info = None
        self._media_ainfo: dict = {}
        self._last_tap_at = 0.0
        self._last_ui_pos = -1.0
        self._media_busy = False
        self._spec_token = 0
        self._ctrl_tool_prev: str | None = None
        self._last_autosave = 0.0
        self._fps_t = time.perf_counter()
        self._last_status = 0.0
        self._ui_ready = False
        self._cfg_timer = QTimer(self)
        self._cfg_timer.setSingleShot(True)
        self._cfg_timer.setInterval(500)
        self._cfg_timer.timeout.connect(self.settings.save)
        self.silent = False          # True = 不弹模态框（自动测试用）

        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1500, 940)
        self.setAcceptDrops(True)

        self._build_ui()
        self._build_actions()
        self._wire()
        qapp = QApplication.instance()
        if qapp is not None:
            qapp.installEventFilter(self)      # Ctrl 按住临时切放置模式

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self.settings_changed()
        self.refresh_all()
        i18n.translate_tree(self)          # 按当前语言刷新整套界面
        self.statusBar().showMessage(i18n.tr("就绪：拖入音频 → 打点 → 导出透明视频"), 8000)
        self._ui_ready = True
        if initial_project:
            # 首次打开时把默认外观/导出写进当前目录的配置文件
            self._capture_project_defaults()
            self.settings.save()
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
        a_video = act("导入参考视频…（对照画面卡点）", lambda: self.import_video(), "Ctrl+Shift+V")
        a_save = act("保存", self.save_project, "Ctrl+S")
        a_saveas = act("另存为…", self.save_project_as, "Ctrl+Shift+S")
        a_export = act("导出透明视频…", self.export_dialog, "Ctrl+E")
        a_still = act("导出当前帧 PNG…", self.export_still_dialog, "Ctrl+Shift+E")
        a_quit = act("退出", self.close, "Ctrl+Q")
        for a in (a_new, a_open, a_audio, a_video):
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
        a_paste = act("粘贴 (Ctrl+V，落在鼠标处)", lambda: self.paste_notes())
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
        m_tool.addAction(act("区间批量填充音符…", lambda: self.fill_range()))
        m_tool.addAction(act("Tap 测速…", self.tap_tempo_dialog))
        m_tool.addAction(act("自动检测 BPM…", self.auto_bpm))
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
        self.timeline.duplicateRequested.connect(self.duplicate_forward)
        self.timeline.selectAllRequested.connect(self.select_all)
        self.timeline.fillRequested.connect(self.fill_range)
        self.timeline.loopChanged.connect(lambda: self.chk_loop.setChecked(True))
        self.preview.seekRequested.connect(self.seek)
        self.refvideo.seekRequested.connect(self.seek)
        self.panel_types.changed.connect(self.on_visual_changed)
        self.panel_types.orderChanged.connect(self.on_structure_changed)
        self.panel_chart.changed.connect(self.on_chart_changed)
        self.panel_chart.renderChanged.connect(self._capture_export_defaults)
        self.panel_chart.seekRequested.connect(self.seek)
        self.panel_chart.tapRequested.connect(self.tap_tempo_dialog)
        self.panel_chart.autoBpmRequested.connect(self.auto_bpm)
        self.panel_style.changed.connect(self.on_visual_changed)
        self.panel_export.changed.connect(self._on_export_changed)
        self.panel_export.exportRequested.connect(self.export_dialog)
        self.panel_export.stillRequested.connect(self.export_still_dialog)
        self.panel_settings.changed.connect(self.settings_changed)
        self.doc.changed.connect(lambda _label: self.refresh_all())
        self.doc.selectionChanged.connect(self.on_selection_changed)
        self.doc.dirtyChanged.connect(self._on_dirty)
        self.engine.stateChanged.connect(self._on_play_state)
        self.engine.finished.connect(lambda: self._on_play_state(False))
        self.timeline.clipSelected.connect(self._on_clip_selected)
        self.timeline.clipMoveStarted.connect(self.pause)
        self.timeline.clipMoved.connect(self._rebuild_media)
        self.timeline.clipSplitRequested.connect(self.split_selected_clip)
        self.timeline.clipDeleteRequested.connect(self.delete_selected_clip)
        self.doc.audioNeedsReload.connect(self._rebuild_media)

    # --------------------------------------------------------- 片段操作
    def _selected_clip(self):
        cid = getattr(self.doc.state, "selected_clip_id", "")
        return self.doc.project.clip_by_id(cid) if cid else None

    def _target_audio_clip(self):
        c = self._selected_clip()
        if c is None:
            return None
        if c.kind == "audio":
            return c
        for other in self.doc.project.linked_clips(c):
            if other.kind == "audio":
                return other
        return None

    def _on_clip_selected(self, _cid: str = "") -> None:
        self.panel_chart.refresh()
        c = self._selected_clip()
        target = self._target_audio_clip()
        if c is None:
            self.statusBar().showMessage(i18n.tr("未选中媒体片段"), 2500)
        elif target is not None:
            self.statusBar().showMessage(
                i18n.trf("已选中片段：{name}", name=target.label), 3000)
        else:
            self.statusBar().showMessage(
                i18n.trf("已选中片段：{name}（无音频）", name=c.label), 3000)

    def split_selected_clip(self) -> None:
        c = self._selected_clip()
        if c is None:
            self._info("先选片段", "先在视频轨或音频轨点击选中要切分的片段。")
            return
        cut = float(self.position_ms)
        if not c.contains_time(cut) or cut - c.offset_ms < 2.0 or c.end_ms() - cut < 2.0:
            self._info("切分位置无效", "把播放头移到片段内部再切分。")
            return
        import uuid
        group = self.doc.project.linked_clips(c)
        before = self.doc.snapshot(include_clips=True)
        right_link = uuid.uuid4().hex[:10]
        new_clips = []
        for old in group:
            if not old.contains_time(cut):
                continue
            left_dur = cut - old.offset_ms
            right_dur = old.end_ms() - cut
            if left_dur < 2 or right_dur < 2:
                continue
            right = old.clone()
            right.offset_ms = cut
            right.src_start_ms = old.timeline_to_source(cut)
            right.duration_ms = right_dur
            right.link_id = right_link if old.link_id else ""
            old.duration_ms = left_dur
            new_clips.append(right)
            if old.kind == "audio":
                for n in list(self.doc.project.chart.notes):
                    if n.clip_id != old.id:
                        continue
                    if n.t >= cut:
                        n.clip_id = right.id
                    elif n.t + n.dur > cut > n.t:
                        # 长条跨切点：拆成两条
                        right_n = Note(cut, n.type, n.lane, n.end - cut, n.side, right.id)
                        n.dur = cut - n.t
                        self.doc.project.chart.notes.append(right_n)
        if not new_clips:
            self.doc.cancel()
            return
        self.doc.project.clips.extend(new_clips)
        self.doc.project.chart.sort()
        self.doc.commit_external("切分片段", before)
        self._rebuild_media()
        self.statusBar().showMessage(
            i18n.trf("已切分片段：{name}", name=c.label), 3000)

    def delete_selected_clip(self) -> None:
        c = self._selected_clip()
        if c is None:
            return
        group = self.doc.project.linked_clips(c)
        ids = {x.id for x in group}
        before = self.doc.snapshot(include_clips=True)
        self.doc.project.clips = [x for x in self.doc.project.clips if x.id not in ids]
        self.doc.project.chart.notes = [
            n for n in self.doc.project.chart.notes if n.clip_id not in ids]
        self.doc.project.chart.sort()
        self.doc.state.selected_clip_id = ""
        self.doc.clear_selection()
        acs = self.doc.project.audio_clips
        vcs = self.doc.project.video_clips
        self.doc.project.audio_path = acs[0].path if acs else ""
        self.doc.project.video_path = vcs[0].path if vcs else ""
        self.doc.commit_external("删除片段", before)
        self._rebuild_media()
        self.panel_chart.refresh()

    def _apply_config_defaults(self, project: Project) -> None:
        """把 rbar_settings.json 里的外观/导出偏好用到新工程上。"""
        if isinstance(self.settings.theme, dict):
            try:
                project.theme = Theme.from_dict(self.settings.theme)
            except Exception:  # noqa: BLE001
                pass
        if isinstance(self.settings.export, dict):
            try:
                ex = ExportSettings.from_dict(self.settings.export)
                # 输出路径和时间范围是具体工程的，不拿来当默认值
                ex.output = ""
                ex.range_mode = "song"
                ex.t_start = 0.0
                ex.t_end = 0.0
                project.export = ex
            except Exception:  # noqa: BLE001
                pass

    def _schedule_config_save(self) -> None:
        timer = getattr(self, "_cfg_timer", None)
        if timer is not None:
            timer.start()

    def _capture_theme_defaults(self) -> None:
        if not getattr(self, "_ui_ready", False):
            return
        self.settings.theme = self.doc.project.theme.to_dict()
        self._schedule_config_save()

    def _capture_export_defaults(self) -> None:
        if not getattr(self, "_ui_ready", False):
            return
        d = self.doc.project.export.to_dict()
        self.settings.export = {k: d[k] for k in CONFIG_EXPORT_KEYS if k in d}
        self._schedule_config_save()

    def _capture_project_defaults(self) -> None:
        if not getattr(self, "_ui_ready", False):
            return
        self.settings.theme = self.doc.project.theme.to_dict()
        d = self.doc.project.export.to_dict()
        self.settings.export = {k: d[k] for k in CONFIG_EXPORT_KEYS if k in d}
        self._schedule_config_save()

    def _on_export_changed(self) -> None:
        self._capture_export_defaults()
        self.doc.touch("导出设置")

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
        """把各音频片段的节拍拼起来喂给节拍器；空档没有节拍。"""
        if not self.doc.project.audio_clips:
            try:
                beats = self.doc.project.chart.timemap.grid_lines(
                    0.0, self.engine.duration_ms or 60000.0, 1.0, limit=20000)
            except Exception:
                beats = []
            self.engine.set_beats(beats)
            return
        beats: list[float] = []
        for c in self.doc.project.audio_clips:
            try:
                srcs = c.timemap.grid_lines(c.src_start_ms, c.source_end_ms(),
                                            1.0, limit=20000)
            except Exception:
                srcs = []
            beats.extend(c.source_to_timeline(s) for s in srcs)
        self.engine.set_beats(sorted(beats))

    def _sync_view_actions(self) -> None:
        """工程/状态切换后把菜单、工具栏、循环勾选框同步到 EditorState。"""
        st = self.doc.state
        self.a_wave.setChecked(st.show_waveform)
        self.a_spec.setChecked(st.show_spectrum)
        self.a_film.setChecked(st.show_video)
        self.a_safe.setChecked(self.settings.show_safe)
        self.btn_follow.setChecked(st.follow)
        self.chk_loop.setChecked(st.loop_on)
        self.set_tool(st.tool)
        self.spectrum.setVisible(self.a_live.isChecked())

    def _update_counts(self) -> None:
        n = len(self.doc.project.chart)
        s = len(self.doc.selection)
        self.lbl_count.setText(i18n.trf("音符 {n}", n=n)
                               + (i18n.trf("  选中 {n}", n=s) if s else ""))
        self._update_title()

    def _update_title(self) -> None:
        p = self.doc.project.path
        name = os.path.basename(p) if p else i18n.tr("未命名工程")
        self.setWindowTitle(f"{APP_NAME} — {name}{'*' if self.doc.dirty else ''}")

    def _on_dirty(self, _v: bool) -> None:
        self._update_title()

    def on_selection_changed(self) -> None:
        self.timeline.update()
        self._update_counts()

    def on_visual_changed(self) -> None:
        self._capture_theme_defaults()
        self.timeline.update()
        self.preview.update()

    def on_structure_changed(self) -> None:
        self._capture_theme_defaults()
        self.panel_types.refresh()
        self.timeline.update()
        self.preview.update()
        self._update_counts()

    def on_chart_changed(self) -> None:
        self.timeline.update()
        self.preview.update()
        self._push_beats()
        self._update_playhead(self.position_ms, force=True)

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
        self._update_playhead(self.position_ms, force=True)

    def _refresh_recent(self) -> None:
        self.menu_recent.clear()
        items = [p for p in self.settings.recent if os.path.isfile(p)]
        if not items:
            self.menu_recent.addAction(i18n.tr("（空）")).setEnabled(False)
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
            # 用户手动校准量：正值 = 把播放头往声音前方挪
            return max(0.0, self.engine.position_ms() + self.engine.latency_ms)
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
            self.engine._seek_ms = self._virtual_base     # 下次 play() 从暂停处继续
            self._on_play_state(False)

    def seek(self, ms: float) -> None:
        ms = max(0.0, float(ms))
        raw = ms
        if self.engine.playing:
            # position_ms() 会加回 latency_ms，这里先扣掉，避免每次 seek 越跑越偏
            raw = max(0.0, ms - self.engine.latency_ms)
        self.engine.seek(raw)
        if self._virtual_active:
            self._virtual_base = ms
            self._virtual.start()
        self._update_playhead(ms, force=True)

    def _on_play_state(self, playing: bool) -> None:
        self.btn_play.setText(i18n.tr("⏸  暂停") if playing else i18n.tr("▶  播放"))
        self.timeline.set_position(self.position_ms, playing)
        if not playing:
            # 暂停后位置可能不再变化，这里主动刷一次，顺便让参考画面启动精确取帧
            self._update_playhead(self.position_ms, force=True)

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
        self.refvideo.update()             # 这两个是自绘控件，不会走 translate_tree
        self.spectrum.update()
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
        if not on:
            self.doc.state.loop_armed = False
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
        if on and self._clip_specs:
            self.timeline.set_spectrograms(self._clip_specs)
        elif not on:
            self.timeline.clear_spectrograms()
        self._update_spec_state()
        self.timeline.update()

    def _toggle_live(self, on: bool) -> None:
        self.spectrum.setVisible(bool(on))

    def _update_spec_state(self) -> None:
        on = self.a_spec.isChecked()
        if on and self._clip_specs and not self.timeline._specs:
            self.timeline.set_spectrograms(self._clip_specs)
        if on and not self._clip_specs and self.doc.project.audio_clips:
            self.start_spectrogram()
        self.spectrum.setVisible(self.a_live.isChecked())

    # ============================================================== 频谱
    def start_spectrogram(self) -> None:
        """后台按每个音频片段计算频谱图。"""
        clips = self.doc.project.audio_clips
        if not clips or self._spec_task is not None:
            return
        inputs = []
        for c in clips:
            got = self._media_cache.get(c.path)
            if got is None:
                continue
            samples, sr = got
            s0 = int(c.src_start_ms / 1000.0 * sr)
            take = c.duration_ms or (samples.shape[0] / sr * 1000.0 - c.src_start_ms)
            seg = samples[s0: s0 + int(take / 1000.0 * sr)]
            if seg.size:
                inputs.append((c.id, seg, sr))
        if not inputs:
            return
        token = self._spec_token
        t0 = time.perf_counter()

        def work():
            from ..audio import spectrogram

            out = {}
            for cid, seg, sr in inputs:
                out[cid] = spectrogram(seg, sr)
            return out, time.perf_counter() - t0

        self.statusBar().showMessage(i18n.tr("正在计算频谱图…"))
        task = Task(work, parent=self)
        self._spec_task = task

        def on_done(res) -> None:
            if self._spec_task is task:
                self._spec_task = None
            if token != self._spec_token:
                if self.a_spec.isChecked() and self.doc.project.audio_clips:
                    QTimer.singleShot(120, self.start_spectrogram)
                return
            specs, sec = res
            self._clip_specs = specs
            self.spec = next(iter(specs.values()), None)
            self.timeline.set_spectrograms(specs if self.a_spec.isChecked() else {})
            frames = sum(getattr(s, "frames", 0) for s in specs.values())
            self.statusBar().showMessage(
                i18n.trf("频谱图完成（{sec:.1f}s，{frames} 列）", sec=sec, frames=frames), 4000)

        def on_fail(msg: str) -> None:
            if self._spec_task is task:
                self._spec_task = None
            if token == self._spec_token and self.silent:
                print("[频谱失败]", msg, flush=True)

        task.done.connect(on_done)
        task.failed.connect(on_fail)
        task.start()

    # ============================================================ 参考视频
    def _video_len(self) -> float:
        """媒体总长度（取所有片段末端）。

        旧工程/异常片段的 duration_ms 可能是 0；这时用已解码的缓存长度兜底，
        否则整条时间轴会被压成 1 秒。
        """
        ends: list[float] = []
        for c in self.doc.project.clips:
            dur = float(c.duration_ms)
            if dur <= 0:
                got = self._media_cache.get(c.path)
                if got is not None:
                    samples, sr = got
                    dur = samples.shape[0] * 1000.0 / max(1, sr)
            if dur > 0:
                ends.append(float(c.offset_ms) + dur)
        return max(ends) if ends else 0.0

    def _update_duration(self) -> None:
        self.timeline.set_duration(max(self.engine.duration_ms, self._video_len()))

    def import_video(self, path: str | None = None) -> None:
        """导入参考视频（也可以直接拖进窗口）。"""
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, i18n.tr("导入音视频"), self.settings.last_dir,
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
        box.setWindowTitle(i18n.tr("导入媒体"))
        box.setIcon(QMessageBox.Question)
        kind = i18n.tr("视频" if is_video else "音频")
        box.setText(i18n.trf("要导入的{kind}：{name}",
                             kind=kind, name=os.path.basename(path)))
        box.setInformativeText(i18n.trf(
            "当前时间轴上已有 {n} 段媒体（总长 {sec:.1f}s）。\n\n"
            "· 追加：接在最后一段之后（保持现有内容）\n"
            "· 替换：清空现有媒体，只留这一段\n"
            "· 插入：放到播放头位置，其余片段往后顺延",
            n=len(clips), sec=self._video_len() / 1000.0))
        b_append = box.addButton(i18n.tr("追加到末尾"), QMessageBox.AcceptRole)
        b_insert = box.addButton(i18n.tr("插入到播放头"), QMessageBox.ActionRole)
        b_replace = box.addButton(i18n.tr("替换全部"), QMessageBox.DestructiveRole)
        box.addButton(i18n.tr("取消"), QMessageBox.RejectRole)
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

    def import_media(self, path: str, mode: str | None = None, force_audio: bool = False) -> None:
        """导入一段音频或视频；音频会解码进缓存，视频还会抽参考画面。

        force_audio=True：文件即使带视频扩展名，也只把它当音频导入
        （内部提取音轨用；文件菜单不再单独提供这个入口）。
        """
        if not path or not os.path.isfile(path):
            return
        if self._media_busy:
            self._info("正在导入", "上一段媒体还在解码，请稍等几秒再试。")
            return
        ff = self.settings.ffmpeg
        if not ff or not os.path.isfile(ff):
            self._warn("缺少 ffmpeg", "没找到 ffmpeg.exe，请在「设置」标签里指定路径。")
            return
        is_video = path.lower().endswith(VIDEO_EXT) and not force_audio
        from ..video import probe_audio_stream, probe_video

        if mode is None:
            mode = self._ask_media_mode(path, is_video)
        if mode is None:
            return
        ffprobe = find_ffprobe(ff)
        info = probe_video(path, ffprobe) if is_video else None
        ainfo = probe_audio_stream(path, ffprobe) if (is_video or force_audio) else {"has": True}
        if is_video and info is not None and info.duration_ms <= 0:
            info.duration_ms = 60000.0
        if not ainfo.get("has") and not is_video:
            self._warn("这个文件没有声音", "请换一个音频文件。")
            return
        self.pause()                       # 解码/混音期间不要继续放旧音频
        self._media_is_video = is_video
        self._media_info = info
        self._media_ainfo = ainfo
        self.statusBar().showMessage(
            f"正在{'抽取参考画面与解码音频' if is_video else '解码音频'}：{os.path.basename(path)} …")
        QApplication.setOverrideCursor(Qt.BusyCursor)
        self._media_busy = True
        task = Task(self._media_worker, path, bool(is_video), ff, bool(ainfo.get("has")), parent=self)
        task.done.connect(lambda res: self._media_ready(path, mode, res))
        task.failed.connect(self._media_failed)
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
            info = probe_video(path, find_ffprobe(ffmpeg))
            film = extract_filmstrip(path, info, ffmpeg,
                                     target_fps=FILMSTRIP_FPS, thumb_h=72,
                                     max_frames=FILMSTRIP_MAX_FRAMES)
        return samples, sr, pk, film

    def _media_failed(self, msg: str) -> None:
        self._media_busy = False
        self._task_failed(msg)

    def _media_ready(self, path: str, mode: str, res) -> None:
        from ..model import MediaClip
        import uuid

        self._media_busy = False
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
        group_dur = max(audio_ms, video_ms, 1000.0)

        clips = self.doc.project.clips
        if mode == "replace":
            clips.clear()
            start = 0.0
        elif mode == "insert":
            start = self.position_ms
            for c in clips:
                if c.offset_ms >= start:
                    c.offset_ms += group_dur
        else:                                   # append
            start = self.doc.project.media_end_ms()

        link = uuid.uuid4().hex[:10] if is_video and samples is not None else ""
        made: list[MediaClip] = []
        if is_video:
            made.append(MediaClip(path=path, kind="video", offset_ms=start,
                                  duration_ms=max(video_ms, 1000.0),
                                  name=os.path.basename(path), has_audio=False,
                                  link_id=link))
        if samples is not None:
            made.append(MediaClip(path=path, kind="audio", offset_ms=start,
                                  duration_ms=max(audio_ms, 1000.0),
                                  name=(os.path.basename(path) + "（音频）") if is_video
                                  else os.path.basename(path),
                                  has_audio=True, link_id=link))
        clips.extend(made)

        if is_video:
            self.doc.project.video_path = path
        if samples is not None:
            self.doc.project.audio_path = path
        self.doc.touch("导入媒体")
        self._rebuild_media()
        if made:
            target = next((c for c in made if c.kind == "audio"), made[0])
            self.doc.state.selected_clip_id = target.id
            self.timeline.update()
            self.panel_chart.refresh()
        if mode == "replace":
            self.seek(0.0)
        self.settings.remember_dir(path)
        self.settings.save()
        self.refvideo.setVisible(self.a_ref.isChecked() and bool(self._films))
        mode_txt = {"replace": "替换并载入", "append": "追加到末尾", "insert": "插入到播放头"}[mode]
        kind_txt = ("视频+分离音频" if is_video and samples is not None
                    else ("视频（无声）" if is_video else "音频"))
        self.statusBar().showMessage(
            f"已{mode_txt}：{os.path.basename(path)}（{group_dur / 1000:.1f}s，{kind_txt}）"
            f"　时间轴共 {len(clips)} 段 / {self._video_len() / 1000:.1f}s", 9000)
        if not getattr(self, "_zoomed_once", False):
            self.timeline.zoom_to_fit()
            self._zoomed_once = True

    def _rebuild_media(self) -> None:
        """把音频轨片段混成一条播放音频；每条音频仍有自己的波形/BPM/频谱。"""
        clips = self.doc.project.clips
        sr = 48000
        ends = []
        for c in clips:
            dur = float(c.duration_ms)
            if dur <= 0:
                got = self._media_cache.get(c.path)
                if got is not None:
                    dur = got[0].shape[0] * 1000.0 / max(1, got[1])
            ends.append(c.offset_ms + max(0.0, dur))
        total = max(1000.0, max(ends, default=0.0))
        n = int(total / 1000.0 * sr) + 1
        mix = np.zeros((n, 2), dtype=np.float32)
        any_audio = False
        for c in self.doc.project.audio_clips:
            if not c.has_audio:
                continue
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
        self.engine.path = self.doc.project.audio_clips[0].path if self.doc.project.audio_clips else ""
        self.engine.duration_ms = mix.shape[0] * 1000.0 / sr
        self.engine._rebuild()
        self._peaks = peaks(mix, PEAK_BUCKETS) if any_audio else None
        self.timeline.set_audio(self._peaks, max(self.engine.duration_ms,
                                                 self.doc.project.media_end_ms()))

        # 每个音频片段准备自己的缩略波形；视频片段带胶片条
        clip_items = []
        video_items = []
        for c in clips:
            if c.kind == "video":
                fs = self._films.get(c.path)
                clip_items.append((c, fs))
                video_items.append((c, fs))
            else:
                got = self._media_cache.get(c.path)
                pk = None
                if got is not None:
                    samples, csr = got
                    s0 = int(c.src_start_ms / 1000.0 * csr)
                    take = c.duration_ms or (samples.shape[0] / csr * 1000.0 - c.src_start_ms)
                    seg = samples[s0: s0 + int(take / 1000.0 * csr)]
                    if seg.size:
                        pk = peaks(seg, 3000)
                clip_items.append((c, pk))
        self.timeline.set_clips(clip_items)
        self.spectrum.set_audio(mix if any_audio else None, sr)
        self.refvideo.set_clips(video_items, self.settings.ffmpeg)
        self._clip_specs = {}
        self.spec = None
        self._spec_token += 1
        self.timeline.clear_spectrograms()
        if any_audio and self.a_spec.isChecked():
            QTimer.singleShot(120, self.start_spectrogram)
        self._update_duration()

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
        # 暂停且位置没变时不要每 16ms 重绘预览、做频谱 FFT（空闲 CPU 大头）
        if playing or pos != self._last_ui_pos:
            self._update_playhead(pos, force=False)
        if self.doc.dirty and time.time() - self._last_autosave > 90:
            self._last_autosave = time.time()
            self._autosave()

    def _update_playhead(self, pos: float, force: bool) -> None:
        self._last_ui_pos = float(pos)
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
            clip = self.doc.project.audio_clip_at(pos)
            if clip is None and self.doc.project.audio_clips:
                self.lbl_beat.setText(i18n.tr("空档（无节拍）"))
                self.lbl_bpm.setText("—")
            else:
                tm = (clip.timemap if clip is not None
                      else self.doc.project.chart.timemap)
                src = clip.timeline_to_source(pos) if clip is not None else pos
                b = tm.beat(src)
                self.lbl_beat.setText(i18n.trf("拍 {b:.2f}（第 {n} 拍）", b=b, n=int(b // 1) + 1))
                self.lbl_bpm.setText(f"{tm.bpm_at(src):g} BPM")
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
            self, i18n.tr("导入音频"), self.settings.last_dir,
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
            self.statusBar().showMessage(i18n.tr("操作失败（详情见控制台）"), 5000)
            return
        QMessageBox.critical(self, i18n.tr("出错"), msg)
        self.statusBar().showMessage(i18n.tr("操作失败"), 5000)

    def _warn(self, title: str, text: str) -> None:
        title, text = i18n.tr(title), i18n.tr(text)
        if self.silent:
            print(f"[提示] {title}: {text}", flush=True)
        else:
            QMessageBox.warning(self, title, text)

    def _info(self, title: str, text: str) -> None:
        title, text = i18n.tr(title), i18n.tr(text)
        if self.silent:
            print(f"[提示] {title}: {text}", flush=True)
        else:
            QMessageBox.information(self, title, text)

    # ============================================================== 打点/编辑
    def tap_note(self) -> None:
        """在播放位置打一个点（卡点核心操作）。"""
        now = time.perf_counter()
        gap_ms = float(self.settings.tap_min_ms)
        if gap_ms > 0 and self._last_tap_at and (now - self._last_tap_at) * 1000.0 < gap_ms:
            self.statusBar().showMessage(
                i18n.trf("打点太快（最小间隔 {ms:g} ms）", ms=gap_ms), 700)
            return
        self._last_tap_at = now
        pos = self.position_ms
        clip = self.doc.project.audio_clip_at(pos)
        if clip is None and self.doc.project.audio_clips:
            self.statusBar().showMessage(i18n.tr("空档不能打点：先放到某段音频上"), 1500)
            return
        st = self.doc.state
        if st.tap_quantize and st.snap_on:
            if clip is not None:
                src = clip.timeline_to_source(pos)
                pos = clip.source_to_timeline(clip.timemap.snap(src, st.snap_divisor))
            else:
                pos = self.doc.project.chart.timemap.snap(pos, st.snap_divisor)
        rows = self.doc.project.theme.active_rows()
        if not rows:
            return
        lane = max(0, min(len(rows) - 1, st.current_lane))
        nn = Note(pos, rows[lane], lane, clip_id=clip.id if clip else "")
        self.doc.edit("打点", lambda: self.doc.project.chart.add(nn))
        self.doc.set_selection([nn])
        self.statusBar().showMessage(i18n.trf("打点 @ {t}", t=fmt_time(pos)), 1200)

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
        self.statusBar().showMessage(i18n.trf("已复制 {n} 个音符", n=len(sel)), 2000)

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
                owner = self.doc.project.audio_clip_at(n.t)
                if owner is None and self.doc.project.audio_clips:
                    continue
                n.clip_id = owner.id if owner is not None else ""
                if at_row is not None and rows:
                    n.lane = max(0, min(len(rows) - 1, crow + d_row))
                    n.type = rows[n.lane]
                added.append(n)

        fn()
        if not added:
            self.statusBar().showMessage(i18n.tr("空档不能粘贴：目标位置没有音频片段"), 2000)
            return
        self.doc.project.chart.add_many(added)      # 一次排序，别逐个 add
        self.doc.commit_external("粘贴", before)
        self.doc.set_selection(added)
        self.on_notes_changed()
        self.statusBar().showMessage(i18n.trf("粘贴 {n} 个音符", n=len(added)), 2000)

    def duplicate_forward(self) -> None:
        sel = sorted(self.doc.selection, key=lambda n: n.t)
        if not sel:
            return
        clip = self.doc.project.clip_by_id(sel[0].clip_id) or self.doc.project.audio_clip_at(sel[0].t)
        if clip is None and self.doc.project.audio_clips:
            return
        if clip is not None:
            tm = clip.timemap
            b0 = tm.beat(clip.timeline_to_source(sel[0].t))
            b1 = tm.beat(clip.timeline_to_source(max(n.end for n in sel)))
        else:
            tm = self.doc.project.chart.timemap
            b0, b1 = tm.beat(sel[0].t), tm.beat(max(n.end for n in sel))
        bar = 4.0
        span = max(1.0, b1 - b0)                 # 至少一个整拍
        if span > bar:
            # 向上取整到整小节：向下取整会让长选区复制后和原选区重叠
            span = math.ceil(span / bar - 1e-9) * bar
        if clip is not None:
            delta = (clip.source_to_timeline(tm.ms(b0 + span))
                     - clip.source_to_timeline(tm.ms(b0)))
        else:
            delta = tm.ms(b0 + span) - tm.ms(b0)
        added = []
        before = self.doc.snapshot()

        def fn():
            for n in sel:
                c = copy.deepcopy(n)
                c.t += delta
                owner = self.doc.project.audio_clip_at(c.t)
                if owner is None:
                    continue
                c.clip_id = owner.id
                added.append(c)

        fn()
        if not added:
            return
        self.doc.project.chart.add_many(added)
        self.doc.commit_external("向后复制", before)
        self.doc.set_selection(added)
        self.on_notes_changed()

    def quantize_selection(self) -> None:
        if not self.doc.selection:
            return
        st = self.doc.state

        def fn():
            for n in self.doc.selection:
                clip = (self.doc.project.clip_by_id(n.clip_id)
                        or self.doc.project.audio_clip_at(n.t))
                if clip is not None:
                    src = clip.timeline_to_source(n.t)
                    n.t = clip.source_to_timeline(clip.timemap.snap(src, st.snap_divisor))
                elif not self.doc.project.audio_clips:
                    n.t = self.doc.project.chart.timemap.snap(n.t, st.snap_divisor)

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
                if self.doc.project.audio_clips:
                    owner = self.doc.project.audio_clip_at(n.t)
                    if owner is not None:
                        n.clip_id = owner.id

        self.doc.edit("镜像", fn)
        self.on_notes_changed()

    def set_loop_here(self) -> None:
        st = self.doc.state
        pos = self.position_ms
        if st.loop_armed:
            # 第二次按 L：设终点并启用循环
            st.loop_b = max(pos, st.loop_a + 100.0)
            st.loop_armed = False
            st.loop_on = True
            self.chk_loop.setChecked(True)
            self.statusBar().showMessage(
                i18n.trf("循环区间 {a} → {b}",
                         a=fmt_time(st.loop_a), b=fmt_time(st.loop_b)), 3000)
        elif st.loop_b > st.loop_a:
            # 已有循环区间：单独按 L 把终点挪到播放头
            st.loop_b = max(pos, st.loop_a + 100.0)
            st.loop_on = True
            self.chk_loop.setChecked(True)
            self.statusBar().showMessage(
                i18n.trf("循环终点改为 {t}（要重设起点请先清除循环）",
                         t=fmt_time(st.loop_b)), 3000)
        else:
            st.loop_a = pos
            st.loop_b = pos
            st.loop_on = False
            st.loop_armed = True
            self.chk_loop.setChecked(False)
            self.statusBar().showMessage(
                i18n.tr("已设置循环起点，再按一次 L 设置终点"), 3000)
        self.timeline.update()

    def clear_loop(self) -> None:
        st = self.doc.state
        st.loop_on = False
        st.loop_armed = False
        st.loop_a = st.loop_b = 0.0
        self.chk_loop.setChecked(False)
        self.timeline.update()

    def _on_row_changed(self, row: int) -> None:
        self.panel_types.list.setCurrentRow(row)

    # ========================================================== 区间批量填充
    def fill_range(self, start: float | None = None, end: float | None = None) -> None:
        """在区间内按自定义间隔填满音符。

        区间优先用时间轴上的**框选**（在标尺/波形处横向拖一下），其次循环区间。
        start/end 同时给出时表示“就是这个框选区间”（右键菜单用）。
        """
        chart = self.doc.project.chart
        st = self.doc.state
        clip = self._target_audio_clip()
        # 填充以框选/播放头所在的片段为准；选中的如果不是这一段，就让位
        probe = st.sel_t0 if st.has_range() else self.position_ms
        at_probe = self.doc.project.audio_clip_at(probe)
        if at_probe is not None and (clip is None or not clip.contains_time(probe)):
            clip = at_probe
        if clip is None and self.doc.project.audio_clips:
            self._info("先选片段", "先在音频轨点击选中一段音频，再填充音符。")
            return
        dur = clip.end_ms() if clip is not None else (self.engine.duration_ms or 0.0)
        locked = False
        if start is not None:
            t0 = float(start)
            if end is not None and float(end) > t0:
                t1, locked = float(end), True
            else:
                t1 = st.loop_b if (st.loop_on and st.loop_b > t0) else (dur or t0 + 8000.0)
        elif st.has_range():
            t0, t1, locked = st.sel_t0, st.sel_t1, True
        elif st.loop_on and st.loop_b > st.loop_a:
            t0, t1 = st.loop_a, st.loop_b
        elif len(chart):
            t0, t1 = chart.bounds()
        else:
            t0, t1 = 0.0, (dur or 8000.0)
        if clip is not None:
            t0 = max(t0, clip.offset_ms)
            t1 = min(t1, clip.end_ms())
        if t1 - t0 < 20.0:
            self._info("区间不在片段内", "框选/播放头要落在选中的音频片段里。")
            return
        rows = [(k, self.doc.project.theme.style(k).name) for k in self.doc.project.theme.active_rows()]
        if not rows:
            return
        # 对话框用“时间轴时间”展示；片段 timemap 平移到总时间轴
        from ..timing import BpmSegment, TimeMap
        if clip is not None:
            tm_ui = TimeMap(
                [BpmSegment(clip.source_to_timeline(s.time_ms), s.bpm)
                 for s in clip.timemap.segments],
                clip.source_to_timeline(clip.timemap.offset_ms))
        else:
            tm_ui = chart.timemap
        dlg = FillRangeDialog(self, tm_ui, t0, t1, rows, st.current_lane,
                              st.snap_on, st.snap_divisor, dur or t1,
                              locked=locked, state=st)
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
        new_notes: list[Note] = []
        seen: set[tuple[float, int]] = set()
        for i, t in enumerate(times):
            owner = self.doc.project.audio_clip_at(t)
            if clip is not None:
                if not clip.contains_time(t):
                    continue
                owner = clip
            elif self.doc.project.audio_clips and owner is None:
                continue
            k = keys[i % len(keys)]
            lane = lane_of.get(k, 0)
            key = (round(float(t), 3), lane)
            if key in seen:
                continue
            seen.add(key)
            if chart.find_at(t, lane, 0.5) is not None:
                continue
            new_notes.append(Note(t, k, lane, clip_id=owner.id if owner else ""))
        chart.add_many(new_notes)
        self.doc.commit_external("区间填充", before)
        self.on_notes_changed()
        self.statusBar().showMessage(i18n.trf("已填充 {n} 个音符", n=len(chart) - base), 5000)

    # ============================================================ 鼠标模式
    TOOLS = ("select", "draw", "range")

    def set_tool(self, name: str, announce: bool = True) -> None:
        st = self.doc.state
        st.tool = name if name in self.TOOLS else "select"
        self.a_select_tool.setChecked(st.tool == "select")
        self.a_draw_tool.setChecked(st.tool == "draw")
        self.a_range_tool.setChecked(st.tool == "range")
        cur = {"draw": Qt.CrossCursor, "range": Qt.SizeHorCursor}.get(st.tool, Qt.ArrowCursor)
        self.timeline.setCursor(cur)
        self.timeline.update()
        if announce:
            msg = {
                "draw": "鼠标模式：放置音符（按住拖动可连续刷；右键仍可改类型/删除）",
                "range": "鼠标模式：框选区间（在任意位置横向拖动选出时间区间，右键可填充/设为循环）",
            }.get(st.tool, "鼠标模式：选择 / 拖拽（空白拖动=框选音符，标尺/波形拖动=拖播放头）")
            self.statusBar().showMessage(i18n.tr(msg), 5000)

    def _restore_ctrl_tool(self) -> None:
        prev = self._ctrl_tool_prev
        if prev is None:
            return
        self._ctrl_tool_prev = None
        self.set_tool(prev, announce=False)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        """按住 Ctrl 临时进入放置模式，松开恢复原模式。

        Ctrl+滚轮缩放、Ctrl+Z 等快捷键仍照常工作；在输入框里按 Ctrl 不抢。
        """
        try:
            et = ev.type()
            if et == QEvent.KeyPress and ev.key() == Qt.Key_Control and not ev.isAutoRepeat():
                if self.isVisible() and QApplication.activeModalWidget() is None:
                    fw = QApplication.focusWidget()
                    if not isinstance(fw, (QLineEdit, QAbstractSpinBox, QComboBox,
                                           QTextEdit, QPlainTextEdit)):
                        if self._ctrl_tool_prev is None:
                            self._ctrl_tool_prev = self.doc.state.tool
                            self.set_tool("draw", announce=False)
                            self.statusBar().showMessage(
                                i18n.tr("按住 Ctrl：临时放置音符，松开恢复"), 1200)
            elif et == QEvent.KeyRelease and ev.key() == Qt.Key_Control:
                self._restore_ctrl_tool()
            elif et == QEvent.ApplicationDeactivate or et == QEvent.WindowDeactivate:
                self._restore_ctrl_tool()
        except Exception:
            pass
        return super().eventFilter(obj, ev)

    def toggle_tool(self) -> None:
        i = self.TOOLS.index(self.doc.state.tool) if self.doc.state.tool in self.TOOLS else 0
        self.set_tool(self.TOOLS[(i + 1) % len(self.TOOLS)])

    # ================================================================ BPM
    def edit_bpm_segment(self, idx: int) -> None:
        clip = self._target_audio_clip()
        if clip is None:
            self._info("先选片段", "先在音频轨点击选中一段音频。")
            return
        segs = clip.timemap.segments
        if not (0 <= idx < len(segs)):
            return
        abs_t = clip.source_to_timeline(segs[idx].time_ms)
        dlg = BpmSegmentDialog(self, abs_t, segs[idx].bpm, first=(idx == 0))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        t, bpm = dlg.values()
        before = self.doc.snapshot(include_clips=True)
        segs = list(clip.timemap.segments)
        src = clip.timeline_to_source(t)
        segs[idx] = BpmSegment(src if idx > 0 else segs[0].time_ms, bpm)
        clip.timemap.set_segments(segs, clip.timemap.offset_ms)
        self.doc.commit_external("修改 BPM 段", before)
        self.on_chart_changed()
        self.panel_chart.refresh()

    def add_bpm_segment_at(self, ms: float) -> None:
        clip = self._target_audio_clip()
        if clip is None:
            self._info("先选片段", "先在音频轨点击选中一段音频。")
            return
        if not clip.contains_time(ms):
            self._info("位置不在片段内", "先把播放头放到选中的音频片段里。")
            return
        segs = clip.timemap.segments
        bpm = segs[-1].bpm if segs else 120.0
        dlg = BpmSegmentDialog(self, ms, bpm)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        t, b = dlg.values()
        before = self.doc.snapshot(include_clips=True)
        src = clip.timeline_to_source(t)
        segs = [s for s in list(clip.timemap.segments)
                if abs(s.time_ms - src) > 1e-6]
        segs.append(BpmSegment(src, b))
        clip.timemap.set_segments(segs, clip.timemap.offset_ms)
        self.doc.commit_external("添加 BPM 段", before)
        self.on_chart_changed()
        self.panel_chart.refresh()

    def tap_tempo_dialog(self) -> None:
        clip = self._target_audio_clip()
        if clip is None:
            self._info("先选片段", "先在音频轨点击选中一段音频。")
            return
        src = clip.timeline_to_source(self.position_ms)
        cur = clip.timemap.bpm_at(src)
        if not clip.contains_time(self.position_ms):
            self._info("位置不在片段内", "先把播放头放到选中的音频片段里。")
            return
        dlg = TapTempoDialog(self, cur)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        bpm = round(dlg.bpm, 3)
        before = self.doc.snapshot(include_clips=True)
        segs = [s for s in list(clip.timemap.segments)
                if abs(s.time_ms - src) > 1e-6]
        segs.append(BpmSegment(src, bpm))
        clip.timemap.set_segments(segs, clip.timemap.offset_ms)
        self.doc.commit_external("Tap 测速", before)
        self.on_chart_changed()
        self.panel_chart.refresh()
        self.statusBar().showMessage(i18n.trf("已在片段内插入 {bpm:g} BPM 段", bpm=bpm), 4000)

    def auto_bpm(self) -> None:
        clip = self._target_audio_clip()
        if clip is None:
            self._info("先选片段", "先在音频轨点击选中一段音频。")
            return
        got = self._media_cache.get(clip.path)
        if got is None:
            self._info("没有音频", "这段音频还没有解码，等媒体加载完再试。")
            return
        samples, sr = got
        s0 = int(clip.src_start_ms / 1000.0 * sr)
        take = clip.duration_ms or (samples.shape[0] / sr * 1000.0 - clip.src_start_ms)
        seg = samples[s0: s0 + int(take / 1000.0 * sr)]
        if seg.size == 0:
            self._info("没有音频", "这段片段里没有可分析的音频。")
            return
        self._auto_target_clip_id = clip.id
        self.statusBar().showMessage(i18n.tr("正在分析选中片段的 BPM…"))

        def work():
            bpm, conf = estimate_bpm(seg, sr)
            off = estimate_offset(seg, bpm, sr) if bpm else 0.0
            return bpm, conf, off

        task = Task(work, parent=self)
        task.done.connect(self._auto_bpm_done)
        task.failed.connect(self._task_failed)
        task.start()

    def _auto_bpm_done(self, res) -> None:
        bpm, conf, off = res
        clip = self.doc.project.clip_by_id(getattr(self, "_auto_target_clip_id", ""))
        if clip is None:
            return
        if bpm <= 0:
            self._info("没测出来", "没能估计出 BPM，可以用 Tap 测速手动打。")
            return
        msg = i18n.trf(
            "估计 BPM：{bpm:.2f}（置信度 {conf:.0f}%）\n建议节拍偏移：{off:.1f} ms\n\n"
            "「是」= 这一段统一用这个 BPM 并设好偏移\n「否」= 只在播放头插入这个 BPM 段",
            bpm=bpm, conf=conf * 100, off=off)
        r = QMessageBox.question(self, i18n.tr("自动检测 BPM"), msg,
                                 QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel) if not self.silent \
            else QMessageBox.Yes
        if r == QMessageBox.Cancel:
            return
        if r == QMessageBox.No and not clip.contains_time(self.position_ms):
            self._info("位置不在片段内", "先把播放头放到选中的音频片段里。")
            return
        before = self.doc.snapshot(include_clips=True)
        base_src = clip.src_start_ms
        if r == QMessageBox.Yes:
            src_off = base_src + off
            clip.timemap.set_segments([BpmSegment(src_off, bpm)], src_off)
        else:
            src = clip.timeline_to_source(self.position_ms)
            segs = [s for s in list(clip.timemap.segments)
                    if abs(s.time_ms - src) > 1e-6]
            segs.append(BpmSegment(src, bpm))
            clip.timemap.set_segments(segs, clip.timemap.offset_ms)
        self.doc.commit_external("自动 BPM", before)
        self.on_chart_changed()
        self.panel_chart.refresh()

    # ================================================================ 工程
    def new_project(self) -> None:
        if not self._confirm_discard():
            return
        p = Project()
        self._apply_config_defaults(p)
        self.set_project(p)
        self.statusBar().showMessage(i18n.tr("新工程"), 3000)

    def set_project(self, project: Project, audio_path: str = "") -> None:
        self.pause()
        self.engine.clear()
        self._peaks = None
        self.spec = None
        self._clip_specs = {}
        self._spec_task = None
        self._spec_token += 1
        self._media_busy = False
        self.doc.state.selected_clip_id = ""
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
        self.timeline.set_audio(None, 0.0)
        self.timeline.set_duration(0.0)
        self.refresh_all()
        self._sync_view_actions()
        # 恢复工程里的媒体片段：逐个解码进缓存，最后统一混音
        from ..model import MediaClip

        restore = [c for c in project.clips if c.path and os.path.isfile(c.path)]
        if audio_path and os.path.isfile(audio_path) and not any(c.path == audio_path for c in restore):
            clip = MediaClip(path=audio_path, kind="audio",
                             name=os.path.basename(audio_path), has_audio=True)
            project.clips.append(clip)      # 必须进工程数据，否则 _rebuild_media 看不到它
            project.audio_path = audio_path
            restore.append(clip)
        gone = len(project.clips) - len(restore)
        if gone:
            self.statusBar().showMessage(
                i18n.trf("有 {n} 段媒体文件找不到了，已跳过", n=gone), 7000)
        if restore:
            self._restore_queue = list(restore)
            QTimer.singleShot(200, self._restore_next)
        elif len(project.chart) == 0:
            self.statusBar().showMessage(
                i18n.tr("空工程：先导入音频/视频"), 6000)
        self._refresh_recent()
        # 换工程后播放头必须回到开头，不能沿用旧工程的 _seek_ms
        self.seek(0.0)

    def _restore_next(self) -> None:
        """依次把工程里的媒体片段解码回缓存。"""
        q = getattr(self, "_restore_queue", [])
        if not q:
            self._rebuild_media()
            self.timeline.zoom_to_fit()
            if self._films:
                self.refvideo.setVisible(self.a_ref.isChecked())
            self.statusBar().showMessage(
                i18n.trf("工程已就绪：{n} 段媒体 / {sec:.1f}s",
                         n=len(self.doc.project.clips), sec=self._video_len() / 1000.0), 6000)
            return
        clip = q.pop(0)
        ff = self.settings.ffmpeg
        if not ff or not os.path.isfile(ff):
            self.statusBar().showMessage(
                i18n.tr("找不到 ffmpeg，工程里的媒体没有恢复；请在「设置」里指定 ffmpeg 后重新打开"), 9000)
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
        if clip.duration_ms <= 0:
            # v1 工程/异常片段没有 duration：用解码结果补上，否则会被当成 1 秒
            dur = samples.shape[0] * 1000.0 / max(1, sr) if samples is not None else 0.0
            if film is not None and getattr(film, "count", 0):
                dur = max(dur, float(film.times[-1]) + float(film.step_ms))
            if dur > 0:
                clip.duration_ms = dur
        self._restore_next()

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, i18n.tr("打开工程"), self.settings.last_dir, f"节奏条工程 (*{PROJECT_EXT});;所有文件 (*)")
        if path:
            self.open_path(path)

    def open_path(self, path: str) -> None:
        if not self._confirm_discard():
            return
        try:
            if path.lower().endswith(AUDIO_EXT):
                p = Project()
                self._apply_config_defaults(p)
                self.set_project(p, audio_path=path)
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
                self.statusBar().showMessage(i18n.trf("已打开 {name}", name=os.path.basename(path)), 5000)
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
            self.statusBar().showMessage(i18n.tr("已保存"), 3000)
            return True
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, i18n.tr("保存失败"), str(e))
            return False

    def save_project_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(
            self, i18n.tr("保存工程"), os.path.join(self.settings.last_dir, "我的节奏条" + PROJECT_EXT),
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
            self._info("先选输出位置", "请在「导出」面板里选一个输出文件。")
            self.tabs.setCurrentWidget(self.panel_export)
            return
        ff = self.settings.ffmpeg
        if not ff or not os.path.isfile(ff):
            self._warn("缺少 ffmpeg", "没找到 ffmpeg.exe，请在「设置」标签里指定路径。")
            self.tabs.setCurrentWidget(self.panel_settings)
            return
        t0, t1 = compute_range(self.doc.project, self.engine.duration_ms)
        frames = max(1, int(round((t1 - t0) / 1000.0 * ex.fps)))
        report = self._estimate_report(frames)
        est = estimate_size_mb(ex.fmt, ex.width, ex.height, frames)
        msg = i18n.trf(
            "格式：{fmt}\n尺寸：{w}×{h} @ {fps}fps（{ss}× 超采样）\n"
            "帧数：{frames}（{dur}）\n预计体积：约 {size:.0f} MB（内容不同会有变化）\n"
            "预计用时：{time}\n\n开始导出？",
            fmt=i18n.tr(FORMATS.get(ex.fmt, ex.fmt)), w=ex.width, h=ex.height,
            fps=ex.fps, ss=ex.supersample,
            frames=frames, dur=fmt_time(t1 - t0), size=est, time=report)
        r = QMessageBox.question(
            self, i18n.tr("开始导出"), msg,
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
            return i18n.trf("约 {sec:.0f} 秒", sec=sec)
        return i18n.trf("约 {sec:.1f} 分钟", sec=sec / 60)

    def _run_export(self, t0: float, t1: float, frames: int) -> None:
        ex = self.doc.project.export
        dlg = QProgressDialog(i18n.tr("正在导出…"), i18n.tr("取消"), 0, frames, self)
        dlg.setWindowTitle(i18n.tr("导出视频"))
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
            dlg.setLabelText(i18n.trf("导出中… {done}/{total} 帧　剩余约 {eta:.0f}s",
                                      done=done, total=total, eta=eta))

        def on_done(res) -> None:
            dlg.close()
            QApplication.restoreOverrideCursor()
            if getattr(res, "cancelled", False):
                self.statusBar().showMessage(i18n.tr("导出已取消"), 5000)
                return
            dur = time.perf_counter() - started
            self.statusBar().showMessage(i18n.trf("导出完成：{path}", path=res.path), 15000)
            size = ""
            if os.path.isfile(res.path):
                size = i18n.trf("\n文件大小：{size:.1f} MB",
                                size=os.path.getsize(res.path) / 1048576)
            if self.silent:
                print(f"[导出完成] {res.frames} 帧，{dur:.1f}s，{res.path}{size}", flush=True)
            else:
                QMessageBox.information(
                    self, i18n.tr("导出完成"),
                    i18n.trf("已导出 {frames} 帧，用时 {dur:.1f}s\n{path}{size}\n\n"
                             "导入剪辑软件后，把它放在原视频上层即可（透明通道已带）。",
                             frames=res.frames, dur=dur, path=res.path, size=size))
            if ex.open_after and not self.silent:
                self._open_folder(os.path.dirname(os.path.abspath(res.path)))

        def on_failed(msg: str) -> None:
            dlg.close()
            self._task_failed(msg)

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
        path, _ = QFileDialog.getSaveFileName(self, i18n.tr("导出当前帧"),
                                              os.path.join(self.settings.last_dir, name),
                                              "PNG 图片 (*.png)")
        if not path:
            return
        try:
            export_still(self.doc.project, self.position_ms, path, ex.width, ex.height, ex.supersample)
            self.statusBar().showMessage(i18n.trf("已导出图片：{path}", path=path), 8000)
            if ex.open_after:
                self._open_folder(os.path.dirname(os.path.abspath(path)))
        except Exception as e:  # noqa: BLE001
            self._task_failed(str(e))

    # ============================================================ 检查更新
    def check_updates(self, auto: bool = False) -> None:
        """查 GitHub Releases 有没有新版本；auto=True 时静默失败、只在有更新时弹窗。"""
        if auto and not getattr(self.settings, "check_updates", True):
            return
        import time as _t

        now = _t.time()
        if auto:
            if now < float(getattr(self.settings, "update_snooze_until", 0.0) or 0.0):
                return
            last_ok = float(getattr(self.settings, "update_last_success", 0.0) or 0.0)
            if last_ok and now - last_ok < 6 * 3600:
                return
            fails = int(getattr(self.settings, "update_fail_count", 0) or 0)
            if fails:
                # 失败退避：15 分钟、30 分钟、1 小时……最多 6 小时，避免一直打 GitHub
                delay = min(6 * 3600, 15 * 60 * (2 ** min(fails - 1, 4)))
                last_try = float(getattr(self.settings, "update_last_attempt", 0.0) or 0.0)
                if now - last_try < delay:
                    return
        if self._update_task is not None:
            return
        from ..updater import fetch_latest, is_newer

        task = Task(fetch_latest, parent=self,
                    current=__version__,
                    etag=str(getattr(self.settings, "update_etag", "") or ""))
        self._update_task = task
        if not auto:
            self.statusBar().showMessage(i18n.tr("正在检查更新…"))

        def mark(success: bool, res: dict | None = None) -> None:
            stamp = _t.time()
            if success:
                self.settings.update_last_success = stamp
                self.settings.update_last_attempt = stamp
                self.settings.update_fail_count = 0
                if res and res.get("etag"):
                    self.settings.update_etag = str(res["etag"])
            else:
                self.settings.update_last_attempt = stamp
                self.settings.update_fail_count = int(
                    getattr(self.settings, "update_fail_count", 0) or 0) + 1
            self.settings.last_update_check = self.settings.update_last_success
            self.settings.save()

        def done(res) -> None:
            self._update_task = None
            mark(True, res)
            if not res or res.get("none"):
                if not auto:
                    self._info("检查更新", "作者还没有发布任何 Release。\n（仓库一直在更新代码，可以关注 commit）")
                return
            if res.get("not_modified"):
                if not auto:
                    self._info("检查更新", i18n.trf(
                        "已是最新版（v{ver}）。", ver=__version__))
                return
            tag = str(res.get("tag", ""))
            if is_newer(tag, __version__):
                if auto and tag == str(getattr(self.settings, "update_skip_version", "")
                                       or ""):
                    return
                self._show_update_dialog(res)
            elif not auto:
                self._info("检查更新", i18n.trf(
                    "已是最新版（v{ver}）。\n最新 Release：{tag}",
                    ver=__version__, tag=tag or "—"))

        def failed(msg: str) -> None:
            self._update_task = None
            mark(False)
            # 自动检查失败不打扰用户，但必须留下日志；否则会被误认为“检查更新坏了”
            print(f"[检查更新失败] {msg}", flush=True)
            if auto:
                return
            if self.silent:
                return
            box = QMessageBox(self)
            box.setWindowTitle(i18n.tr("检查更新失败"))
            box.setIcon(QMessageBox.Warning)
            box.setText(str(msg))
            box.setInformativeText(i18n.tr("也可以直接打开 Releases 页面手动看看。"))
            b_open = box.addButton(i18n.tr("打开 Releases 页面"), QMessageBox.AcceptRole)
            box.addButton(i18n.tr("好"), QMessageBox.RejectRole)
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
        asset = next((a for a in assets
                      if a.get("url") and str(a.get("name", "")).lower().endswith(
                          (".zip", ".exe"))), None)
        if asset is None:
            asset = next((a for a in assets if a.get("url")), None)
        download_url = (asset or {}).get("url") or res.get("download_url") or ""
        names = "、".join(a["name"] for a in assets[:5] if a.get("name"))
        if names:
            body += "\n\n" + i18n.trf("发布附件：{names}", names=names)
        if self.silent:
            print(f"[发现新版本] {tag}\n{body}", flush=True)
            return
        box = QMessageBox(self)
        box.setWindowTitle(i18n.tr("发现新版本"))
        box.setIcon(QMessageBox.Information)
        box.setText(i18n.trf("有新版本可用：{tag}（当前 v{ver}）",
                             tag=tag, ver=__version__))
        box.setInformativeText(body or i18n.tr("点「下载更新」到 GitHub 下载。"))
        b_download = box.addButton(
            i18n.tr("下载更新") if download_url else i18n.tr("打开下载页"),
            QMessageBox.AcceptRole)
        b_skip = box.addButton(i18n.tr("跳过此版本"), QMessageBox.ActionRole)
        b_page = None
        if download_url:
            b_page = box.addButton(i18n.tr("打开发布页"), QMessageBox.ActionRole)
        b_later = box.addButton(i18n.tr("稍后提醒"), QMessageBox.RejectRole)
        box.setDefaultButton(b_download)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_download:
            import webbrowser

            webbrowser.open(download_url or res.get("url") or RELEASES_PAGE)
        elif clicked is b_page:
            import webbrowser

            webbrowser.open(res.get("url") or RELEASES_PAGE)
        elif clicked is b_skip:
            self.settings.update_skip_version = tag
            self.settings.update_snooze_until = 0.0
            self.settings.save()
        elif clicked is b_later:
            import time as _t

            self.settings.update_snooze_until = _t.time() + 24 * 3600
            self.settings.save()

    # ================================================================ 帮助
    def show_help(self) -> None:
        QMessageBox.information(
            self, "用法与快捷键",
            "【最快上手流程】\n"
            "1. 把 mp3/wav 拖进窗口（或 Ctrl+Shift+O）\n"
            "2. 在音频轨点击一段，选中后空格播放，跟着音乐按 F 打点；或点「自动检测 BPM」\n"
            "3. 区间铺点用「工具 → 区间批量填充音符」，只填当前片段/框选所在的片段\n"
            "4. 有画面要卡：Ctrl+Shift+V 导入参考视频（画面进视频轨，声音自动拆到音频轨并链接）；\n"
            "   时间轴会多出视频轨、音频波形轨和该片段的频谱图\n"
            "5. Ctrl+E 导出透明视频，拖进剪辑软件放在原视频上层\n\n"
            "【多段媒体（剪辑式）】\n"
            "  视频轨 / 音频轨各自拖动，片段之间可以留空；视频和分离音频默认链接。\n"
            "  拖动片段自动吸附播放头和相邻片段首尾，按住 Alt 临时关吸附。\n"
            "  右键片段：在播放头切分 / 删除；Ctrl+K 切分，Del 删除（没有音符选中时）。\n"
            "  片段之间空档没有网格、没有音符，也不能打点/添加音符。\n\n"
            "【判定点】\n"
            "  中间常驻一枚 45° 灰白菱形；音符打到它时按该音符颜色闪一下。\n"
            "  颜色/大小/闪烁时长都在「外观 → 判定点」里调。\n\n"
            "【对音：波形 + 频谱】\n"
            "  时间轴从上到下是：音符行 / 视频轨 / 音频轨（波形）/ 频谱图。\n"
            "  每段音频有自己的 BPM、偏移、网格、波形和频谱；先选中片段再对音。\n"
            "  频谱图是对音主力：鼓点、音头都是频谱里的一条竖纹，\n"
            "  把该片段的节拍网格线跟竖纹对齐就准了。\n"
            "  左下角还有实时频谱条，播放时能看当前频段能量。\n"
            "  视图菜单里可分别开关频谱图 / 实时频谱 / 视频轨。\n\n"
            "【三种鼠标模式（V / B / R / Tab 切换）】\n"
            "  选择拖拽：空白拖动=框选音符；拖音符=移动；拖右侧把手=变长条\n"
            "  放置音符：单击=加点；按住拖动=沿网格连续刷（一次撤销）\n"
            "  框选区间：在任意位置横向拖动=选出时间区间（Shift+拖动同效）\n"
            "  另外：按住 Ctrl 会临时切到放置模式，松开恢复原模式\n"
            "  注意：标尺/波形处**直接拖动是拖播放头**，框选区间用 Shift 或 R 模式\n\n"
            "【框选区间 → 填充（不用手打时间）】\n"
            "  框出区间后，右键「在这个区间填充音符…」或 工具菜单，\n"
            "  区间会自动采用当前框选，选好间隔和类型直接点「填充」即可。\n"
            "  右键还能「把区间设为循环区间」「清除区间选择」「清除循环区间」。\n\n"
            "【时间轴】\n"
            "  右键 = 改类型/量化/删除/从这一点填充（有框选时隐藏“从这一点”）\n"
            "  Ctrl+滚轮 = 缩放；Shift+滚轮 = 平移；中键拖动 = 平移\n\n"
            "【快捷键】\n"
            "  空格 播放/暂停　F 打点　V/B/R 鼠标模式　Tab 切换模式\n"
            "  Ctrl+Z/Y 撤销/重做　Del 删除音符/片段　Ctrl+K 切分片段\n"
            "  Ctrl+A 全选　Ctrl+C/V 复制/粘贴到鼠标位置　Ctrl+D 向后复制\n"
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
            if self.doc.selection:
                self.delete_selection()
            elif self._selected_clip() is not None:
                self.delete_selected_clip()
            return
        if k == Qt.Key_K and mods & Qt.ControlModifier:
            self.split_selected_clip()
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
            self.statusBar().showMessage(
                i18n.tr("吸附：开") if st.snap_on else i18n.tr("吸附：关"), 1500)
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
        self.settings.geometry = self.saveGeometry()
        self._cfg_timer.stop()
        self.settings.save()
        self.engine.shutdown()             # 音频线程必须在这里收掉
        ev.accept()
