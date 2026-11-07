"""界面多语言：中文 / 日本語 / English。

设计：**用中文原文当 key**，不用给几百个字符串起英文键名。
静态界面文案（菜单、按钮、标签、下拉项）统一靠 `translate_tree()` 遍历控件树替换，
每个控件第一次翻译时会把原文记在 Qt 属性 `_zh` 上，之后切换语言都从原文重译。
动态拼接的文案（状态栏、弹窗）在调用处用 `tr()` 包一层。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QTabWidget,
    QTableWidget,
    QWidget,
)

LANGS = (("zh", "中文"), ("ja", "日本語"), ("en", "English"))
_LANG = "zh"

# 中文原文 -> (日本語, English)
_TABLE: dict[str, tuple[str, str]] = {
    # ---------------------------------------------------------------- 菜单
    "文件": ("ファイル", "File"),
    "编辑": ("編集", "Edit"),
    "视图": ("表示", "View"),
    "播放": ("再生", "Playback"),
    "工具": ("ツール", "Tools"),
    "帮助": ("ヘルプ", "Help"),
    "语言": ("言語", "Language"),
    "新建工程": ("新規プロジェクト", "New Project"),
    "打开工程…": ("プロジェクトを開く…", "Open Project…"),
    "打开音频…": ("音声を開く…", "Open Audio…"),
    "导入参考视频…（对照画面卡点）": ("動画を読み込む…（映像に合わせる）", "Import Video…"),
    "保存": ("保存", "Save"),
    "另存为…": ("名前を付けて保存…", "Save As…"),
    "导出透明视频…": ("透過動画を書き出す…", "Export Transparent Video…"),
    "导出当前帧 PNG…": ("現在のフレームを PNG 保存…", "Export Current Frame as PNG…"),
    "退出": ("終了", "Quit"),
    "最近打开": ("最近使ったファイル", "Recent Files"),
    "撤销": ("元に戻す", "Undo"),
    "重做": ("やり直す", "Redo"),
    "选择 / 拖拽模式（V）": ("選択 / ドラッグ（V）", "Select / Drag (V)"),
    "放置音符模式（B，按住可连续刷）": ("ノーツ配置（B・押しっぱなしで連続）",
                              "Place Notes (B, drag to paint)"),
    "框选区间模式（R，Shift+拖动同效）": ("範囲選択（R・Shift+ドラッグも同じ）",
                              "Select Range (R, or Shift+drag)"),
    "全选": ("すべて選択", "Select All"),
    "复制 (Ctrl+C)": ("コピー (Ctrl+C)", "Copy (Ctrl+C)"),
    "粘贴到当前时间 (Ctrl+V)": ("再生位置に貼り付け (Ctrl+V)", "Paste at current time (Ctrl+V)"),
    "向后复制一份 (Ctrl+D)": ("後ろに複製 (Ctrl+D)", "Duplicate Forward (Ctrl+D)"),
    "量化到网格 (Ctrl+Q)": ("グリッドに量子化 (Ctrl+Q)", "Quantize (Ctrl+Q)"),
    "镜像选中区间 (Ctrl+M)": ("選択範囲を反転 (Ctrl+M)", "Mirror Selection (Ctrl+M)"),
    "删除选中 (Del)": ("選択を削除 (Del)", "Delete Selected (Del)"),
    "删除 (Del)": ("削除 (Del)", "Delete (Del)"),
    "缩放适应 (Ctrl+0)": ("全体を表示 (Ctrl+0)", "Zoom to Fit (Ctrl+0)"),
    "放大 (Ctrl+=)": ("拡大 (Ctrl+=)", "Zoom In (Ctrl+=)"),
    "缩小 (Ctrl+-)": ("縮小 (Ctrl+-)", "Zoom Out (Ctrl+-)"),
    "显示波形": ("波形を表示", "Show Waveform"),
    "时间轴显示频谱图": ("タイムラインにスペクトログラム", "Show Spectrogram"),
    "实时频谱条": ("リアルタイムスペクトル", "Live Spectrum"),
    "预览安全框": ("プレビューのガイド枠", "Preview Guide Frame"),
    "显示参考视频画面": ("参照映像を表示", "Show Reference Video"),
    "时间轴显示视频胶片条": ("タイムラインにフィルムストリップ", "Show Filmstrip"),
    "播放 / 暂停（空格）": ("再生 / 一時停止（スペース）", "Play / Pause (Space)"),
    "回到开头 (Home)": ("先頭へ (Home)", "Go to Start (Home)"),
    "设置循环 A / B（L）": ("A/B リピート設定（L）", "Set Loop A/B (L)"),
    "清除循环": ("リピート解除", "Clear Loop"),
    "节拍器（M）": ("メトロノーム（M）", "Metronome (M)"),
    "区间批量填充音符…": ("範囲にノーツを一括配置…", "Fill Range with Notes…"),
    "Tap 测速…": ("タップで BPM 測定…", "Tap Tempo…"),
    "自动检测 BPM…": ("BPM を自動検出…", "Detect BPM…"),
    "在当前时间插入 BPM 段…": ("再生位置に BPM 区間を挿入…", "Insert BPM Segment…"),
    "快捷键与用法": ("ショートカットと使い方", "Shortcuts & Usage"),
    "关于": ("このアプリについて", "About"),
    # ------------------------------------------------------------ 工具栏/走带
    "打开音频": ("音声を開く", "Open Audio"),
    "导入参考视频": ("動画を読み込む", "Import Video"),
    "打开工程": ("プロジェクトを開く", "Open Project"),
    "播放/暂停": ("再生/一時停止", "Play/Pause"),
    "循环": ("リピート", "Loop"),
    "打点(F)": ("打点(F)", "Tap (F)"),
    "自动BPM": ("自動BPM", "Auto BPM"),
    "导出透明视频": ("透過動画を書き出す", "Export Video"),
    "▶  播放": ("▶  再生", "▶  Play"),
    "⏸  暂停": ("⏸  一時停止", "⏸  Pause"),
    "节拍器": ("メトロノーム", "Metronome"),
    "速度": ("速度", "Speed"),
    "跟随": ("追従", "Follow"),
    # ---------------------------------------------------------------- 面板
    "音符类型": ("ノーツの種類", "Note Types"),
    "工程设置": ("プロジェクト設定", "Project Settings"),
    "音符类型（行）": ("ノーツの種類（行）", "Note Types (rows)"),
    "当前类型外观": ("選択中のノーツ外観", "Current Type Appearance"),
    "名称": ("名前", "Name"),
    "形状": ("形状", "Shape"),
    "颜色": ("色", "Color"),
    "描边色": ("輪郭色", "Outline"),
    "大小": ("サイズ", "Size"),
    "描边宽": ("輪郭の太さ", "Outline Width"),
    "外发光": ("グロー", "Glow"),
    "复制": ("複製", "Copy"),
    "菱形": ("菱形", "Diamond"),
    "方块": ("四角", "Square"),
    "圆形": ("円", "Circle"),
    "长条": ("長押しバー", "Bar"),
    "三角形": ("三角形", "Triangle"),
    "星形": ("星形", "Star"),
    "十字": ("十字", "Cross"),
    "六边形": ("六角形", "Hexagon"),
    "提示：在时间轴上单击=加点，拖拽=移动，\n拖尾部=长条，右键=更多操作。": (
        "ヒント：タイムラインをクリック＝配置、ドラッグ＝移動、\n右端をドラッグ＝長押し、右クリック＝その他。",
        "Tip: click = place, drag = move,\ndrag the right edge = hold, right click = more."),
    "谱面 / BPM": ("譜面 / BPM", "Chart / BPM"),
    "外观": ("外観", "Appearance"),
    "导出": ("書き出し", "Export"),
    "设置": ("設定", "Settings"),
    "BPM 变速": ("BPM 変化", "BPM Changes"),
    "起点 (ms)": ("開始 (ms)", "Start (ms)"),
    "在当前时间切分音视频": ("現在の時間で映像/音声を分割", "Split video/audio at current time"),
    "在当前时间把选中的视频/音频片段切成两段（链接片段一起切）": (
        "現在の時間で選択中の映像/音声クリップを2つに分割します（リンク中のクリップも一緒に分割）。",
        "Split the selected video/audio clip in two at the current time (linked clips split together)."),
    "删除段": ("区間を削除", "Delete Segment"),
    "整段统一 BPM": ("全体を同じ BPM に", "Unify BPM"),
    "Tap 测速（连点）": ("タップで測定（連打）", "Tap Tempo (tap)"),
    "自动检测 BPM": ("BPM を自動検出", "Detect BPM"),
    "对齐": ("タイミング合わせ", "Alignment"),
    "节拍偏移": ("拍のオフセット", "Beat Offset"),
    "取当前时间": ("現在の時間を採用", "Use Current Time"),
    "网格": ("グリッド", "Grid"),
    "吸附": ("スナップ", "Snap"),
    "节奏条": ("リズムバー", "Rhythm Bar"),
    "流向": ("流れる向き", "Direction"),
    "流速(走完全程)": ("速度（端→中央）", "Speed (edge → center)"),
    "判定点位置": ("判定位置", "Judge Position"),
    "音符大小": ("ノーツの大きさ", "Note Size"),
    "多行铺开": ("行を縦に広げる", "Row Spread"),
    "节拍刻度": ("拍の目盛り", "Beat Ticks"),
    "背景": ("背景", "Background"),
    "右 → 左（推荐）": ("右 → 左（推奨）", "Right → Left (recommended)"),
    "左 → 右": ("左 → 右", "Left → Right"),
    "双侧向中心汇聚": ("両側から中央へ", "Both Sides → Center"),
    "不显示": ("非表示", "Hidden"),
    "每拍刻度": ("拍ごと", "Per Beat"),
    "每小节刻度": ("小節ごと", "Per Bar"),
    "四分音符": ("4分音符", "1/4 note"),
    "八分音符": ("8分音符", "1/8 note"),
    "八分三连": ("8分3連", "1/8 triplet"),
    "十六分": ("16分音符", "1/16 note"),
    "十六三连": ("16分3連", "1/16 triplet"),
    "三十二分": ("32分音符", "1/32 note"),
    "六十四分": ("64分音符", "1/64 note"),
    "透明（推荐）": ("透明（推奨）", "Transparent (recommended)"),
    "黑底": ("黒背景", "Black"),
    "白底": ("白背景", "White"),
    "自定义": ("カスタム", "Custom"),
    "预设": ("プリセット", "Presets"),
    "（选择预设…）": ("（プリセットを選択…）", "(choose a preset…)"),
    "底板（那根条）": ("ベースバー", "Base Bar"),
    "模式": ("モード", "Mode"),
    "有底板": ("ベースあり", "With base"),
    "无底板（只有音符）": ("ベースなし（ノーツのみ）", "No base (notes only)"),
    "不透明度": ("不透明度", "Opacity"),
    "顶部提亮": ("上部ハイライト", "Top Highlight"),
    "底部压暗": ("下部シェード", "Bottom Shade"),
    "圆角": ("角丸", "Corner Radius"),
    "内缩": ("内側マージン", "Inset"),
    "边缘高光": ("エッジの光", "Edge Light"),
    "外阴影": ("外側の影", "Drop Shadow"),
    "判定点": ("判定ポイント", "Judge Point"),
    "竖线透明度": ("縦線の不透明度", "Line Opacity"),
    "竖线粗细": ("縦線の太さ", "Line Width"),
    "判定点常驻 45° 菱形": ("判定点に常駐の45°菱形", "Always-on 45° diamond"),
    "判定点常驻": ("判定点に常駐", "Always-on judge marker"),
    "不管有没有音符，判定点上都常驻一枚标记；命中后按音符颜色闪一下": (
        "ノーツがなくても判定点にマークを常駐。ヒット時にノーツ色で光ります。",
        "Always show a marker at the judge point; flash in the note color on hit."),
    "标记颜色": ("マーク色", "Marker Color"),
    "标记大小": ("マークサイズ", "Marker Size"),
    "图像皮肤": ("画像スキン", "Image Skin"),
    "导入图像…": ("画像を読み込む…", "Import Image…"),
    "恢复形状": ("図形に戻す", "Use Shape"),
    "未设置": ("未設定", "Not Set"),
    "裁剪图像皮肤": ("画像スキンをトリミング", "Crop Image Skin"),
    "裁剪大小": ("トリミングサイズ", "Crop Size"),
    "拖动图片调整裁剪位置；裁剪框中心就是判定点/音符中心": (
        "画像をドラッグして位置を調整。切り抜き枠の中心が判定点/ノーツの中心になります。",
        "Drag the image to position the crop; the crop center is the judge/note center."),
    "没有图片": ("画像がありません", "No image"),
    "选择图像皮肤": ("画像スキンを選択", "Choose Image Skin"),
    "菱形颜色": ("菱形の色", "Diamond Color"),
    "菱形大小": ("菱形の大きさ", "Diamond Size"),
    "命中后按音符颜色闪一下": ("ヒット時にノーツ色で発光", "Flash in note color on hit"),
    "染色强度": ("発光の強さ", "Flash Strength"),
    "染色时长": ("発光の長さ", "Flash Duration"),
    "判定窗口": ("判定ウィンドウ", "Hit Window"),
    "随 BPM 脉冲": ("BPM で脈動", "Pulse with BPM"),
    "脉冲强度": ("脈動の強さ", "Pulse Strength"),
    "命中扩散圈": ("ヒットの波紋", "Hit Ripple"),
    "高亮光晕": ("発光のグロー", "Flash Glow"),
    "命中后残影（判定点左侧那串灰块）": ("ヒット後の残像（判定点の左に出る灰ブロック）",
                            "After-hit ghost (gray blocks)"),
    "灰块（还原参考图）": ("灰ブロック（参考画像どおり）", "Gray block (as reference)"),
    "原样式淡出": ("元の見た目でフェード", "Fade in original style"),
    "延迟": ("遅延", "Delay"),
    "过渡时长": ("変化時間", "Morph Time"),
    "从原音符旋转 90° 并渐变成灰块所需时间；0 = 瞬间切换": (
        "元のノーツから 90° 回転しながらグレーのブロックへ変わる時間。0 = 即時切替。",
        "Time to rotate the note 90° and morph into the gray block; 0 = instant."),
    "消失时长": ("消えるまで", "Fade Duration"),
    "格式": ("形式", "Format"),
    "封装 / 编码": ("コンテナ / コーデック", "Container / Codec"),
    "质量": ("品質", "Quality"),
    "帧率": ("フレームレート", "Frame Rate"),
    "画面": ("画面", "Canvas"),
    "尺寸预设": ("サイズのプリセット", "Size Preset"),
    "宽 × 高": ("幅 × 高さ", "Width × Height"),
    "超采样": ("スーパーサンプリング", "Supersampling"),
    "条带位置": ("バーの位置", "Bar Position"),
    "时间范围": ("時間範囲", "Time Range"),
    "范围": ("範囲", "Range"),
    "起点 / 终点": ("開始 / 終了", "Start / End"),
    "末尾留白": ("末尾の余白", "Tail Padding"),
    "输出": ("出力", "Output"),
    "文件": ("ファイル", "File"),
    "完成后打开所在文件夹": ("完了後にフォルダを開く", "Open folder when done"),
    "导出视频": ("動画を書き出す", "Export Video"),
    "导出当前帧 PNG": ("現在のフレームを PNG 保存", "Export Current Frame PNG"),
    "居中": ("中央", "Center"),
    "靠上": ("上寄せ", "Top"),
    "靠下": ("下寄せ", "Bottom"),
    "快速（体积大/快）": ("高速（大きい/速い）", "Fast (bigger file)"),
    "标准": ("標準", "Balanced"),
    "高质量（慢）": ("高品質（遅い）", "High Quality (slow)"),
    "1× 快": ("1× 高速", "1× fast"),
    "2× 抗锯齿（推荐）": ("2× アンチエイリアス（推奨）", "2× anti-aliased (recommended)"),
    "3× 最锐利": ("3× 最もシャープ", "3× sharpest"),
    "整首音频": ("音声全体", "Whole audio"),
    "从第一个音符到最后一个音符": ("最初のノーツから最後まで", "First note to last note"),
    "自定义区间": ("カスタム範囲", "Custom range"),
    "音频 / 播放": ("音声 / 再生", "Audio / Playback"),
    "音画延迟校准": ("音と映像のズレ補正", "A/V Latency Calibration"),
    "音量": ("音量", "Volume"),
    "试听速度": ("試聴速度", "Preview Speed"),
    "打点最小间隔": ("打点の最小間隔", "Min Tap Interval"),
    "启动时检查更新": ("起動時にアップデート確認", "Check for updates on startup"),
    "预览显示安全框和尺寸标注": ("プレビューにガイドとサイズを表示", "Show guide frame & size info"),
    "快捷键（部分）": ("ショートカット（一部）", "Shortcuts (partial)"),
    # ---------------------------------------------------------------- 对话框
    "BPM 段": ("BPM 区間", "BPM Segment"),
    "起点": ("開始", "Start"),
    "第一段的起点 = 节拍偏移（beat 0），请在「对齐」里改。": (
        "最初の区間の開始＝拍オフセット（beat 0）。「タイミング合わせ」で変更してください。",
        "The first segment's start = beat offset. Change it under Alignment."),
    "Tap 测速": ("タップで BPM 測定", "Tap Tempo"),
    "拍！": ("タップ！", "Tap!"),
    "用这个 BPM": ("この BPM を使う", "Use This BPM"),
    "连续点下面的按钮（或按空格/回车）至少 4 次": (
        "下のボタン（またはスペース/Enter）を4回以上押してください",
        "Tap the button (or press Space/Enter) at least 4 times"),
    "区间批量填充音符": ("範囲にノーツを一括配置", "Fill Range with Notes"),
    "手动输入区间": ("範囲を手入力", "Enter range manually"),
    "用当前框选": ("現在の範囲選択を使う", "Use current selection"),
    "用循环区间": ("リピート範囲を使う", "Use loop range"),
    "用全部音符": ("全ノーツの範囲を使う", "Use all notes"),
    "整首": ("全体", "Whole song"),
    "间隔方式": ("間隔の指定方法", "Interval Mode"),
    "间隔": ("間隔", "Interval"),
    "按节拍": ("拍で指定", "By beats"),
    "按毫秒": ("ミリ秒で指定", "By milliseconds"),
    "与下一种类型交替": ("次の種類と交互に", "Alternate with next type"),
    "起点吸附到当前网格": ("開始をグリッドにスナップ", "Snap start to grid"),
    "填充": ("配置", "Fill"),
    "取消": ("キャンセル", "Cancel"),
    "确定": ("OK", "OK"),
    # ------------------------------------------------------------ 动态文案
    "就绪：拖入音频 → 打点 → 导出透明视频": (
        "準備完了：音声をドロップ → 打点 → 透過動画を書き出し",
        "Ready: drop media → tap notes → export transparent video"),
    "空工程：先导入音频/视频": (
        "空のプロジェクト：音声/動画を読み込んでください",
        "Empty project: import media first"),
    "没有可用的音频输出设备": ("利用可能な音声出力デバイスがありません", "No audio output device"),
    "没有找到 ffmpeg": ("ffmpeg が見つかりません", "ffmpeg not found"),
    "找不到 ffmpeg.exe，请在「设置」标签里指定路径。": (
        "ffmpeg.exe が見つかりません。「設定」タブでパスを指定してください。",
        "ffmpeg.exe not found. Set its path in the Settings tab."),
    "找不到 ffmpeg，请在「设置」标签里指定路径。": (
        "ffmpeg が見つかりません。「設定」タブでパスを指定してください。",
        "ffmpeg not found. Set its path in the Settings tab."),
    "当前工程有未保存的改动，要保存吗？": (
        "保存されていない変更があります。保存しますか？",
        "You have unsaved changes. Save them?"),
    "还没保存": ("未保存", "Unsaved"),
    "正在导出…": ("書き出し中…", "Exporting…"),
    "正在导出": ("書き出し", "Exporting"),
    "导出失败": ("書き出し失敗", "Export Failed"),
    "导出完成": ("書き出し完了", "Export Finished"),
    "导出已取消": ("書き出しを中止しました", "Export cancelled"),
    "取消2": ("", ""),
    "正在计算频谱图…": ("スペクトログラムを計算中…", "Computing spectrogram…"),
    # ------------------------------------------------- 画布上直接绘制的文案
    "未加载音频 —— 把 mp3 / wav / flac 拖进窗口，或点工具栏「打开音频」": (
        "音声が未読み込み — mp3 / wav / flac をウィンドウにドロップするか、"
        "ツールバーの「音声を開く」",
        "No audio loaded — drop an mp3 / wav / flac here, or use “Open Audio”"),
    "实时频谱（载入音频后显示）": ("リアルタイムスペクトル（音声を読み込むと表示）",
                        "Live spectrum (load audio to see it)"),
    "参考视频": ("参照映像", "Reference video"),
    "（取帧中…）": ("（フレーム取得中…）", " (fetching frame…)"),
    "没有参考视频\n文件 → 导入音视频": ("参照映像がありません\nファイル → 動画を読み込む",
                          "No reference video\nFile → Import Video"),
    "预览": ("プレビュー", "Preview"),
    "右→左": ("右→左", "R→L"),
    "左→右": ("左→右", "L→R"),
    "双侧汇聚": ("両側から中央", "both sides"),
    "流速": ("速度", "speed"),
    "拍 {b:.2f}（第 {n} 拍）": ("拍 {b:.2f}（{n} 拍目）", "beat {b:.2f} (#{n})"),
    "音符 {n}": ("ノーツ {n}", "{n} notes"),
    "  选中 {n}": ("　選択 {n}", "  ·  {n} selected"),
    "界面 {a:.0f}/{b:.0f} fps": ("描画 {a:.0f}/{b:.0f} fps", "UI {a:.0f}/{b:.0f} fps"),
    "Hz": ("Hz", "Hz"),
    # ------------------------------------------------------------ 右键菜单
    "在这个区间填充音符…": ("この範囲にノーツを配置…", "Fill notes in this range…"),
    "把区间设为循环区间": ("この範囲をリピートに設定", "Set range as loop"),
    "清除区间选择": ("範囲選択を解除", "Clear range selection"),
    "清除循环区间": ("リピートを解除", "Clear loop"),
    "在此处添加 BPM 段…": ("ここに BPM 区間を追加…", "Add BPM segment here…"),
    "编辑此 BPM 段…": ("この BPM 区間を編集…", "Edit this BPM segment…"),
    "删除此 BPM 段": ("この BPM 区間を削除", "Delete this BPM segment"),
    "改成类型": ("種類を変更", "Change type"),
    "量化到网格": ("グリッドに量子化", "Quantize to grid"),
    "在此添加音符": ("ここにノーツを追加", "Add note here"),
    "从这一点开始填充音符…": ("ここからノーツを配置…", "Fill notes from here…"),
    "把循环起点设到这里": ("リピート開始をここに", "Set loop start here"),
    "把循环终点设到这里": ("リピート終了をここに", "Set loop end here"),
    "长条延长一小节": ("長押しを1小節延ばす", "Extend hold by 1 bar"),
    "长条缩短一小节": ("長押しを1小節縮める", "Shorten hold by 1 bar"),
    # ------------------------------------------------- 粘贴 / 检查更新
    "粘贴到这里 (Ctrl+V)": ("ここに貼り付け (Ctrl+V)", "Paste here (Ctrl+V)"),
    "粘贴到当前时间": ("再生位置に貼り付け", "Paste at current time"),
    "粘贴 (Ctrl+V，落在鼠标处)": ("貼り付け (Ctrl+V・マウス位置)", "Paste (Ctrl+V, at cursor)"),
    "检查更新…": ("アップデートを確認…", "Check for Updates…"),
    "正在检查更新…": ("アップデートを確認中…", "Checking for updates…"),
    "检查更新": ("アップデートの確認", "Check for Updates"),
    "检查更新失败": ("アップデート確認に失敗", "Update check failed"),
    "发现新版本": ("新しいバージョンがあります", "New version available"),
    "打开下载页": ("ダウンロードページを開く", "Open download page"),
    "以后再说": ("後で", "Later"),
    "下载更新": ("アップデートをダウンロード", "Download update"),
    "打开发布页": ("リリースページを開く", "Open release page"),
    "跳过此版本": ("このバージョンをスキップ", "Skip this version"),
    "稍后提醒": ("後で通知", "Remind me later"),
    "有新版本可用：{tag}（当前 v{ver}）": (
        "新しいバージョンがあります：{tag}（現在 v{ver}）",
        "New version available: {tag} (current v{ver})"),
    "点「下载更新」到 GitHub 下载。": (
        "「アップデートをダウンロード」から GitHub で取得できます。",
        "Click “Download update” to get it from GitHub."),
    "发布附件：{names}": ("リリース添付：{names}", "Release assets: {names}"),
    "已是最新版（v{ver}）。": ("最新版です（v{ver}）。", "You're up to date (v{ver})."),
    "未选中媒体片段": ("メディアクリップが未選択", "No media clip selected"),
    "已选中片段：{name}": ("選択中のクリップ：{name}", "Selected clip: {name}"),
    "已选中片段：{name}（无音频）": (
        "選択中のクリップ：{name}（音声なし）", "Selected clip: {name} (no audio)"),
    "已切分片段：{name}": ("クリップを分割しました：{name}", "Split clip: {name}"),
    "移动片段：{a} → {b}": ("クリップ移動：{a} → {b}", "Move clip: {a} → {b}"),
    "片段重叠：换个位置（按住 Alt 可临时关吸附）": (
        "クリップが重なっています。位置を変えてください（Alt でスナップ一時解除）。",
        "Clips overlap; choose another position (hold Alt to disable snapping)."),
    "在当前时间切分片段": ("再生位置でクリップを分割", "Split clip at current time"),
    "删除片段": ("クリップを削除", "Delete clip"),
    "空档不能添加音符": ("空白部分にはノーツを追加できません", "Cannot add notes in a gap"),
    "空档不能打点：先放到某段音频上": (
        "空白部分では打点できません。音声クリップの上に移動してください。",
        "Cannot tap in a gap; move over an audio clip."),
    "空档不能粘贴：目标位置没有音频片段": (
        "空白部分には貼り付けできません（音声クリップがありません）。",
        "Cannot paste in a gap (no audio clip there)."),
    "空档（无节拍）": ("空白（拍なし）", "Gap (no beat)"),
    "正在分析选中片段的 BPM…": ("選択クリップの BPM を分析中…", "Analyzing selected clip BPM…"),
    "已在片段内插入 {bpm:g} BPM 段": (
        "クリップ内に {bpm:g} BPM 区間を挿入しました",
        "Inserted a {bpm:g} BPM segment in the clip"),
    "未选中音频片段 —— 先在音频轨点击一段": (
        "音声クリップが未選択 — 音声トラックのクリップをクリックしてください",
        "No audio clip selected — click one on the audio track"),
    "视频轨": ("映像トラック", "Video track"),
    "音频轨": ("音声トラック", "Audio track"),
    "片段：{name}（BPM/偏移只作用于这一段）": (
        "クリップ：{name}（BPM/オフセットはこのクリップだけに適用）",
        "Clip: {name} (BPM/offset applies to this clip only)"),
    "片段：{name}（无音频）": ("クリップ：{name}（音声なし）", "Clip: {name} (no audio)"),
    "把这一段设为同一个 BPM：": (
        "このクリップ全体を同じ BPM に：", "Set this clip to one BPM:"),
    "先选片段": ("先にクリップを選択", "Select a clip first"),
    "先在视频轨或音频轨点击选中要切分的片段。": (
        "分割するクリップを映像/音声トラックで選択してください。",
        "Select the clip to split on the video or audio track."),
    "切分位置无效": ("分割位置が無効", "Invalid split position"),
    "把当前时间移到片段内部再切分。": (
        "再生位置をクリップの内側に移動してから分割してください。",
        "Move the current time inside the clip before splitting."),
    "先在音频轨点击选中一段音频，再填充音符。": (
        "先に音声トラックのクリップを選択してからノーツを配置してください。",
        "Select an audio clip on the audio track before filling notes."),
    "区间不在片段内": ("範囲がクリップ内にありません", "Range is outside the clip"),
    "框选/当前时间要落在选中的音频片段里。": (
        "選択範囲/再生位置を選択中の音声クリップ内に置いてください。",
        "Keep the selection/current time inside the selected audio clip."),
    "先在音频轨点击选中一段音频。": (
        "先に音声トラックのクリップを選択してください。",
        "Select an audio clip on the audio track first."),
    "位置不在片段内": ("位置がクリップ内にありません", "Position is outside the clip"),
    "先把当前时间放到选中的音频片段里。": (
        "再生位置を選択中の音声クリップ内に移動してください。",
        "Move the current time inside the selected audio clip."),
    "这段音频还没有解码，等媒体加载完再试。": (
        "この音声はまだデコードされていません。読み込み完了後にもう一度試してください。",
        "This audio is not decoded yet; try again after loading finishes."),
    "这段片段里没有可分析的音频。": (
        "このクリップには分析できる音声がありません。",
        "This clip has no analyzable audio."),
    "估计 BPM：{bpm:.2f}（置信度 {conf:.0f}%）\n建议节拍偏移：{off:.1f} ms\n\n"
    "「是」= 这一段统一用这个 BPM 并设好偏移\n「否」= 只在当前时间插入这个 BPM 段": (
        "推定 BPM：{bpm:.2f}（信頼度 {conf:.0f}%）\n推奨オフセット：{off:.1f} ms\n\n"
        "「はい」= このクリップ全体をこの BPM に\n「いいえ」= 再生位置に BPM 区間を追加",
        "Estimated BPM: {bpm:.2f} (confidence {conf:.0f}%)\n"
        "Suggested offset: {off:.1f} ms\n\n"
        "“Yes” = use this BPM for the whole clip\n“No” = insert a segment at the current time"),
    "检查 GitHub 上有没有新版本": ("GitHub に新しい版がないか確認", "Check GitHub for a new version"),
    "打开 Releases 页面": ("Releases ページを開く", "Open the Releases page"),
    "也可以直接打开 Releases 页面手动看看。": (
        "Releases ページを直接開いて確認することもできます。",
        "You can also open the Releases page and check manually."),
    "好": ("OK", "OK"),
    # --------------------------------------------- 区间填充 / 动态提示
    "已自动使用当前框选": ("現在の範囲を自動使用", "Using current selection"),
    "右键打开时已经自动采用当前框选的区间，不需要再点": (
        "右クリックで開いた時点で現在の範囲を使用済みです。もう押す必要はありません。",
        "The current selection is already applied; no extra click needed."),
    "在时间轴上方标尺或波形/频谱区域横向拖一下即可框选区间（单击则定位当前时间）": (
        "タイムライン上部のルーラー、または波形/スペクトルを横にドラッグすると範囲を選択できます（クリックのみなら再生位置を移動）。",
        "Drag across the ruler, waveform, or spectrogram to select a range (a click moves the current time)."),
    "区间（{src}）：{a:.3f}s → {b:.3f}s　共 {dur:.3f}s": (
        "範囲（{src}）：{a:.3f}s → {b:.3f}s　計 {dur:.3f}s",
        "Range ({src}): {a:.3f}s → {b:.3f}s  ·  {dur:.3f}s total"),
    "区间无内容：先在时间轴上框选一段，或点「手动输入区间」": (
        "範囲に内容がありません：タイムラインで範囲を選択するか、「範囲を手入力」を押してください。",
        "Empty range: select a range on the timeline, or use “Enter range manually”."),
    "将生成 {n} 个音符　首尾 {a:.0f} → {b:.0f} ms　实际间隔 {gap:.1f} ms（{bpm:g} BPM）": (
        "ノーツを {n} 個生成　先頭/末尾 {a:.0f} → {b:.0f} ms　実際の間隔 {gap:.1f} ms（{bpm:g} BPM）",
        "Will create {n} notes, {a:.0f} → {b:.0f} ms, spacing {gap:.1f} ms ({bpm:g} BPM)"),
    "提示：已存在的重复位置不会重复添加；上限 20000 个音符。": (
        "ヒント：既にある同じ位置には追加しません（上限 20000 ノーツ）。",
        "Tip: existing positions are skipped; max 20,000 notes."),
    "手动输入": ("手入力", "Manual input"),
    "时间轴框选": ("タイムライン選択", "Timeline selection"),
    "当前：{bpm:g} BPM": ("現在：{bpm:g} BPM", "Current: {bpm:g} BPM"),
    "当前：{bpm:.2f} BPM　（已点 {n} 次）": (
        "現在：{bpm:.2f} BPM　（{n} 回タップ）",
        "Current: {bpm:.2f} BPM  ({n} taps)"),
    "按住 Ctrl：临时放置音符，松开恢复": (
        "Ctrl を押している間は一時的にノーツ配置、離すと元に戻ります",
        "Hold Ctrl to place notes temporarily; release to restore"),
    "鼠标模式：放置音符（按住拖动可连续刷；右键仍可改类型/删除）": (
        "マウス：ノーツ配置（ドラッグで連続配置、右クリックで種類変更/削除）",
        "Mouse: place notes (drag to paint; right-click to change type/delete)"),
    "鼠标模式：框选区间（在任意位置横向拖动选出时间区间，右键可填充/设为循环）": (
        "マウス：範囲選択（横ドラッグで範囲を選択、右クリックで配置/リピート設定）",
        "Mouse: select range (drag horizontally; right-click to fill/set loop)"),
    "鼠标模式：选择 / 拖拽（空白拖动=框选音符，标尺/波形拖动=移动当前时间）": (
        "マウス：選択 / ドラッグ（空白ドラッグ=ノーツ選択、ルーラー/波形ドラッグ=再生位置）",
        "Mouse: select/drag (empty drag = box select, ruler/waveform drag = scrub)"),
    # ------------------------------------------------- 导出体积提示 / 格式
    "MOV · QuickTime 动画 RLE（透明，剪映友好，体积中等，推荐）": (
        "MOV · QuickTime アニメーション RLE（透過・CapCut 向け・中容量・推奨）",
        "MOV · QuickTime Animation RLE (alpha, CapCut-friendly, medium size, recommended)"),
    "WebM · VP9 alpha（透明，体积最小，兼容性一般）": (
        "WebM · VP9 alpha（透過・最小容量・互換性は普通）",
        "WebM · VP9 alpha (alpha, smallest, limited compatibility)"),
    "MOV · ProRes 4444（透明，PR/AE/达芬奇最稳，体积很大）": (
        "MOV · ProRes 4444（透過・PR/AE/DaVinci で最も安定・大容量）",
        "MOV · ProRes 4444 (alpha, most reliable in PR/AE/DaVinci, very large)"),
    "WebM · VP8 alpha（兼容老一点的解码器）": (
        "WebM · VP8 alpha（古いデコーダー向け）", "WebM · VP8 alpha (for older decoders)"),
    "PNG 序列（透明，最保险，体积很大）": (
        "PNG 連番（透過・最も安全・大容量）", "PNG sequence (alpha, safest, very large)"),
    "MP4 · 黑底（无透明，剪辑里用「滤色/变亮」混合模式去黑）": (
        "MP4 · 黒背景（透過なし・スクリーン/比較（明）で黒を除去）",
        "MP4 · black background (no alpha; use Screen/Lighten to remove black)"),
    "ProRes 4444：PR / AE / 达芬奇 直接拖进去就有透明通道，体积很大。": (
        "ProRes 4444：PR / AE / DaVinci にそのままドロップで透過。容量は非常に大きいです。",
        "ProRes 4444: drop straight into PR/AE/DaVinci with alpha. Very large files."),
    "QuickTime 动画：剪映等软件兼容好，纯色图形体积中等，推荐先用这个。": (
        "QuickTime アニメーション：CapCut などとの互換性が良く、単色図形なら中容量。まずこれがおすすめ。",
        "QuickTime Animation: good compatibility, medium size for flat graphics. Recommended first."),
    "VP9 alpha：体积最小（约为 qtrle 的 1/15），Chrome / 部分剪辑软件支持；导入前先试一下。": (
        "VP9 alpha：最小容量（qtrle の約 1/15）。Chrome / 一部の編集ソフトのみ対応。先に試してください。",
        "VP9 alpha: smallest (about 1/15 of qtrle). Chrome and some editors only; test before use."),
    "VP8 alpha：兼容老一点的解码器，体积比 VP9 稍大。": (
        "VP8 alpha：古いデコーダー向け。VP9 より少し大きいです。",
        "VP8 alpha: for older decoders; slightly larger than VP9."),
    "PNG 序列：绝对不会翻车，配合「导入序列」使用，体积很大。": (
        "PNG 連番：最も確実。「連番を読み込み」で使用。容量は非常に大きいです。",
        "PNG sequence: safest; use “Import Sequence”. Very large files."),
    "黑底 MP4：没有透明通道，在剪辑软件里把混合模式设为「滤色 / 变亮」即可去掉黑底，体积小。": (
        "黒背景 MP4：透過なし。編集ソフトでスクリーン/比較（明）にすると黒が消えます。容量は小さいです。",
        "Black MP4: no alpha; set Screen/Lighten in the editor to drop the black. Small files."),
    "粗略体积：约 {size:.0f} MB/分钟（{w}×{h}@{fps}fps；内容不同会有差异）": (
        "概算容量：約 {size:.0f} MB/分（{w}×{h}@{fps}fps、内容により変動）",
        "Approx. size: ~{size:.0f} MB/min ({w}×{h}@{fps}fps; varies with content)"),
    "60fps 的文件体积约为 30fps 的 2 倍；节奏条一般 30fps 就够": (
        "60fps の容量は 30fps の約 2 倍。リズムバーは通常 30fps で十分です。",
        "60fps files are about 2× the size of 30fps; 30fps is usually enough."),
    # ------------------------------------------------- 面板提示 / 工具提示
    "选择颜色": ("色を選択", "Choose Color"),
    "新建一种音符类型": ("新しいノーツ種類を作成", "Create a note type"),
    "删除选中的音符类型（其音符会一起删除）": (
        "選択中のノーツ種類を削除（そのノーツも削除されます）",
        "Delete the selected note type (its notes are removed too)"),
    "第 1 拍（beat 0）所在的时间；波形对不上网格时调它": (
        "1拍目（beat 0）の時刻。波形とグリッドが合わない時に調整します。",
        "Time of beat 0; adjust when the waveform doesn't line up with the grid."),
    "音符从画面边缘走到中间判定点所需时间，越小越快": (
        "ノーツが画面端から判定点まで移動する時間。小さいほど速いです。",
        "Time for a note to travel from the edge to the judge point; smaller = faster."),
    "多种音符类型时，纵向铺开，避免叠在一条线上": (
        "複数のノーツ種類を縦に広げ、同じ線上に重ならないようにします。",
        "Spread multiple note types vertically so they don't overlap on one line."),
    "把节拍偏移设为当前时间位置": (
        "拍オフセットを現在の再生位置に設定", "Set the beat offset to the current time"),
    "不管有没有音符，判定点上都常驻一枚菱形；命中后按音符颜色闪一下": (
        "ノーツがなくても判定点に菱形を常駐。ヒット時にノーツ色で光ります。",
        "Always show a diamond at the judge point; flash in the note color on hit."),
    "命中后先保持原样多久，再开始变残影": (
        "ヒット後、残像に変わるまでの保持時間", "How long to keep the normal look before fading to a ghost"),
    "命中后延迟多久开始变形（0 = 命中瞬间开始）": (
        "ヒット後、変形を始めるまでの遅延（0 = ヒット瞬間から）",
        "Delay before morphing starts (0 = at the hit moment)"),
    "导出整帧（比如 1920×1080）时，条带放在画面的哪个位置": (
        "全体（例：1920×1080）を書き出す時、バーを画面のどこに置くか",
        "Where to place the bar when exporting a full frame (e.g. 1920×1080)"),
    "当前时间和声音对不齐时在这校准：画面比声音慢就调小": (
        "再生位置と音がずれる時の補正。映像が音より遅い場合は小さくします。",
        "A/V calibration: if the picture lags the sound, decrease this."),
    "打点时两次点击小于这个间隔就忽略": (
        "この間隔より短い連続タップは無視します", "Ignore taps closer together than this"),
    "启动约 2.5 秒后查一次 GitHub Release；成功后 6 小时内不重复，失败会稍后重试": (
        "起動約 2.5 秒後に GitHub Release を確認。成功後 6 時間は再確認しません（失敗時は後で再試行）。",
        "Check GitHub Releases ~2.5s after startup; after success at most once per 6 hours (failures retry later)."),
    "选择 ffmpeg.exe": ("ffmpeg.exe を選択", "Select ffmpeg.exe"),
    "选择 ffmpeg": ("ffmpeg を選択", "Select ffmpeg"),
    "选择 PNG 序列输出文件夹": ("PNG 連番の出力フォルダを選択", "Choose PNG sequence output folder"),
    "导出为": ("書き出し先", "Export as"),
    "不吸附": ("スナップなし", "No snapping"),
    "把整首曲子设为同一个 BPM：": ("曲全体を同じ BPM にします：", "Set the whole song to one BPM:"),
    # ------------------------------------------------- 时间轴绘制 / 状态
    "循环 {dur:.2f}s": ("リピート {dur:.2f}s", "Loop {dur:.2f}s"),
    "循环区间：{a} → {b}（播放菜单或循环勾选框可取消）": (
        "リピート範囲：{a} → {b}（再生メニューまたはチェックで解除）",
        "Loop range: {a} → {b} (clear from Playback menu or the checkbox)"),
    "已清除循环区间": ("リピート範囲を解除しました", "Loop range cleared"),
    "框选区间：拖动确定范围（右键可填充/设为循环）": (
        "範囲選択：ドラッグで決定（右クリックで配置/リピート設定）",
        "Selecting range: drag to set (right-click to fill/set loop)"),
    "区间 {a} → {b}（{dur:.3f}s）": (
        "範囲 {a} → {b}（{dur:.3f}s）", "Range {a} → {b} ({dur:.3f}s)"),
    "放置音符：已放 {n} 个": ("ノーツ配置：{n} 個", "Placing notes: {n}"),
    "BPM 段 {i} 起点 {t}": ("BPM 区間 {i} 開始 {t}", "BPM segment {i} start {t}"),
    "已放置 {n} 个音符": ("{n} 個のノーツを配置しました", "Placed {n} notes"),
    "已框选区间 {a} → {b}　右键可「在区间填充音符」": (
        "範囲 {a} → {b} を選択。右クリックで「範囲にノーツを配置」",
        "Selected {a} → {b}. Right-click to “Fill notes in this range”"),
    "定位到 {t}": ("{t} に移動", "Moved to {t}"),
    # ------------------------------------------------- 常用状态栏
    "打点 @ {t}": ("打点 @ {t}", "Tap @ {t}"),
    "已复制 {n} 个音符": ("{n} 個のノーツをコピー", "Copied {n} notes"),
    "粘贴 {n} 个音符": ("{n} 個のノーツを貼り付け", "Pasted {n} notes"),
    "已填充 {n} 个音符": ("{n} 個のノーツを配置", "Filled {n} notes"),
    "循环区间 {a} → {b}": ("リピート範囲 {a} → {b}", "Loop range {a} → {b}"),
    "循环终点改为 {t}（要重设起点请先清除循环）": (
        "リピート終了を {t} に変更（開始を変えるには先に解除してください）",
        "Loop end changed to {t} (clear the loop to set a new start)"),
    "已打开 {name}": ("{name} を開きました", "Opened {name}"),
    "已保存": ("保存しました", "Saved"),
    "已导出图片：{path}": ("画像を書き出しました：{path}", "Image exported: {path}"),
    "正在计算频谱图…": ("スペクトログラムを計算中…", "Computing spectrogram…"),
    "频谱图完成（{sec:.1f}s，{frames} 列）": (
        "スペクトログラム完了（{sec:.1f}s、{frames} 列）",
        "Spectrogram ready ({sec:.1f}s, {frames} frames)"),
    "工程已就绪：{n} 段媒体 / {sec:.1f}s": (
        "プロジェクト準備完了：メディア {n} 個 / {sec:.1f}s",
        "Project ready: {n} clips / {sec:.1f}s"),
    "有 {n} 段媒体文件找不到了，已跳过": (
        "{n} 個のメディアが見つからないためスキップしました",
        "{n} media files not found; skipped"),
    # ------------------------------------------------- 导入 / 错误对话框
    "导入媒体": ("メディアを読み込む", "Import Media"),
    "视频": ("映像", "Video"),
    "音频": ("音声", "Audio"),
    "追加到末尾": ("末尾に追加", "Append to end"),
    "插入到当前时间": ("再生位置に挿入", "Insert at current time"),
    "替换全部": ("すべて置き換え", "Replace all"),
    "正在导入": ("読み込み中", "Importing"),
    "上一段媒体还在解码，请稍等几秒再试。": (
        "前のメディアをデコード中です。数秒待ってから再試行してください。",
        "The previous media is still decoding; wait a few seconds and try again."),
    "缺少 ffmpeg": ("ffmpeg がありません", "ffmpeg missing"),
    "没找到 ffmpeg.exe，请在「设置」标签里指定路径。": (
        "ffmpeg.exe が見つかりません。「設定」タブでパスを指定してください。",
        "ffmpeg.exe not found. Set its path in the Settings tab."),
    "没找到 ffmpeg，请在「设置」标签里指定路径。": (
        "ffmpeg が見つかりません。「設定」タブでパスを指定してください。",
        "ffmpeg not found. Set its path in the Settings tab."),
    "这个文件没有声音": ("このファイルには音声がありません", "This file has no audio"),
    "请换一个音频文件。": ("別の音声ファイルを選んでください。", "Choose another audio file."),
    "还没有参考视频": ("参照映像がありません", "No reference video yet"),
    "先导入一个带声音的视频。": ("音声付きの動画を読み込んでください。", "Import a video with audio first."),
    "这个视频没有声音": ("この動画には音声がありません", "This video has no audio"),
    "视频里没有音轨，请另外载入音频文件。": (
        "動画に音声トラックがありません。別途音声ファイルを読み込んでください。",
        "The video has no audio track; load a separate audio file."),
    "视频声音已经在用了": ("動画の音声は使用中です", "Video audio is already in use"),
    "这个视频导入时已经带声音，不需要再单独提取一遍。": (
        "この動画は読み込み時に音声を含んでいます。改めて抽出する必要はありません。",
        "This video already imported with its sound; no need to extract it again."),
    "开始导出": ("書き出し開始", "Start Export"),
    "预计体积：约 {size:.0f} MB（内容不同会有变化）": (
        "推定容量：約 {size:.0f} MB（内容により変動）",
        "Estimated size: ~{size:.0f} MB (varies with content)"),
    "预计用时：{time}": ("推定時間：{time}", "Estimated time: {time}"),
    "没有可填充的位置": ("配置できる位置がありません", "Nothing to fill"),
    "先在时间轴上框选一段，或点「手动输入区间」。": (
        "タイムラインで範囲を選択するか、「範囲を手入力」を押してください。",
        "Select a range on the timeline, or use “Enter range manually”."),
    "没有音频": ("音声がありません", "No audio"),
    "请先载入音频。": ("先に音声を読み込んでください。", "Load audio first."),
    # ------------------------------------------------- 其它动态模板
    "估计 BPM：{bpm:.2f}（置信度 {conf:.0f}%）\n建议节拍偏移：{off:.1f} ms\n\n"
    "「是」= 整首统一用这个 BPM 并设好偏移\n「否」= 只在当前时间插入这个 BPM 段": (
        "推定 BPM：{bpm:.2f}（信頼度 {conf:.0f}%）\n推奨オフセット：{off:.1f} ms\n\n"
        "「はい」= 曲全体をこの BPM に統一\n「いいえ」= 再生位置に BPM 区間を追加",
        "Estimated BPM: {bpm:.2f} (confidence {conf:.0f}%)\n"
        "Suggested offset: {off:.1f} ms\n\n"
        "“Yes” = use this BPM for the whole song\n“No” = insert a segment at the current time"),
    "格式：{fmt}\n尺寸：{w}×{h} @ {fps}fps（{ss}× 超采样）\n"
    "帧数：{frames}（{dur}）\n预计体积：约 {size:.0f} MB（内容不同会有变化）\n"
    "预计用时：{time}\n\n开始导出？": (
        "形式：{fmt}\nサイズ：{w}×{h} @ {fps}fps（{ss}× スーパーサンプリング）\n"
        "フレーム数：{frames}（{dur}）\n推定容量：約 {size:.0f} MB（内容により変動）\n"
        "推定時間：{time}\n\n書き出しますか？",
        "Format: {fmt}\nSize: {w}×{h} @ {fps}fps ({ss}× supersampling)\n"
        "Frames: {frames} ({dur})\nEstimated size: ~{size:.0f} MB (varies with content)\n"
        "Estimated time: {time}\n\nStart export?"),
    "要导入的{kind}：{name}": ("読み込む{kind}：{name}", "Import {kind}: {name}"),
    "时间轴上已有 {n} 段媒体（总长 {sec:.1f}s）。\n\n"
    "· 追加：接在最后一段之后（保持现有内容）\n"
    "· 替换：清空现有媒体，只留这一段\n"
    "· 插入：放到当前时间位置，其余片段往后顺延": (
        "タイムラインには既に {n} 個のメディア（合計 {sec:.1f}s）があります。\n\n"
        "· 末尾に追加：最後の後ろに追加（既存を保持）\n"
        "· 置き換え：既存メディアを消してこの1つだけ\n"
        "· 挿入：再生位置に挿入し、以降を後ろへずらす",
        "There are already {n} clips on the timeline ({sec:.1f}s total).\n\n"
        "· Append: after the last clip (keep existing)\n"
        "· Replace: clear existing clips\n"
        "· Insert: at the current time, shifting later clips"),
    "导入音视频": ("音声/映像を読み込む", "Import Media"),
    "出错": ("エラー", "Error"),
    "操作失败": ("操作に失敗しました", "Operation failed"),
    "操作失败（详情见控制台）": ("操作に失敗（詳細はコンソール）", "Operation failed (see console)"),
    "已在当前时间插入 {bpm:g} BPM 段": (
        "再生位置に {bpm:g} BPM 区間を挿入しました",
        "Inserted a {bpm:g} BPM segment at the current time"),
    "正在分析 BPM…": ("BPM を分析中…", "Analyzing BPM…"),
    "新工程": ("新規プロジェクト", "New project"),
    "打点太快（最小间隔 {ms:g} ms）": (
        "打点が速すぎます（最小間隔 {ms:g} ms）", "Too fast (min interval {ms:g} ms)"),
    "导出中… {done}/{total} 帧　剩余约 {eta:.0f}s": (
        "書き出し中… {done}/{total} フレーム　残り約 {eta:.0f}s",
        "Exporting… {done}/{total} frames, ~{eta:.0f}s left"),
    "导出完成：{path}": ("書き出し完了：{path}", "Export finished: {path}"),
    "\n文件大小：{size:.1f} MB": ("\nファイルサイズ：{size:.1f} MB", "\nFile size: {size:.1f} MB"),
    "已导出 {frames} 帧，用时 {dur:.1f}s\n{path}{size}\n\n"
    "导入剪辑软件后，把它放在原视频上层即可（透明通道已带）。": (
        "{frames} フレームを書き出しました（{dur:.1f}s）\n{path}{size}\n\n"
        "編集ソフトで元動画の上に置いてください（透過済み）。",
        "Exported {frames} frames in {dur:.1f}s\n{path}{size}\n\n"
        "Put it above the original video in your editor (alpha included)."),
    "已设置循环起点，再按一次 L 设置终点": (
        "リピート開始を設定。もう一度 L で終了を設定します",
        "Loop start set; press L again to set the end"),
    "开始导出": ("書き出し開始", "Start Export"),
    "导出视频": ("動画を書き出す", "Export Video"),
    "约 {sec:.0f} 秒": ("約 {sec:.0f} 秒", "~{sec:.0f} s"),
    "约 {sec:.1f} 分钟": ("約 {sec:.1f} 分", "~{sec:.1f} min"),
    "统一 BPM": ("BPM を統一", "Unify BPM"),
    "未命名工程": ("名称未設定", "Untitled"),
    "（空）": ("（なし）", "(empty)"),
    "导入音频": ("音声を読み込む", "Import Audio"),
    "打开工程": ("プロジェクトを開く", "Open Project"),
    "保存工程": ("プロジェクトを保存", "Save Project"),
    "保存失败": ("保存に失敗", "Save failed"),
    "导出当前帧": ("現在のフレームを書き出す", "Export Current Frame"),
    "吸附：开": ("スナップ：オン", "Snap: on"),
    "吸附：关": ("スナップ：オフ", "Snap: off"),
    "先选输出位置": ("出力先を選択", "Choose an output path"),
    "请在「导出」面板里选一个输出文件。": (
        "「書き出し」タブで出力ファイルを指定してください。",
        "Choose an output file in the Export tab."),
    "没测出来": ("測定できませんでした", "Could not detect BPM"),
    "没能估计出 BPM，可以用 Tap 测速手动打。": (
        "BPM を推定できませんでした。Tap で手動測定してください。",
        "Could not estimate BPM; use Tap Tempo to enter it manually."),
    "作者还没有发布任何 Release。\n（仓库一直在更新代码，可以关注 commit）": (
        "まだ Release はありません。\n（リポジトリの commit は更新されています）",
        "No releases yet.\n(The repository is still updated via commits.)"),
    "找不到 ffmpeg，工程里的媒体没有恢复；请在「设置」里指定 ffmpeg 后重新打开": (
        "ffmpeg が見つからないためメディアを復元できません。「設定」で指定してから開き直してください。",
        "ffmpeg not found, so project media was not restored. Set it in Settings and reopen."),
    "已是最新版（v{ver}）。\n最新 Release：{tag}": (
        "最新版です（v{ver}）。\n最新 Release：{tag}",
        "You're up to date (v{ver}).\nLatest release: {tag}"),
    "正在导出…": ("書き出し中…", "Exporting…"),
    " 拍": (" 拍", " beats"),
    "V / B / R / Tab 切换「选择拖拽 / 放置音符 / 框选区间」三种鼠标模式\n"
    "放置模式：单击加点，按住拖动连续刷（一次撤销）\n"
    "选择模式：空白拖动框选音符，拖音符移动，拖右侧把手变长条\n"
    "标尺或波形处直接拖动 = 移动当前时间；Shift+拖动 或 R 模式 = 框选时间区间\n"
    "空格 播放/暂停      F 打点（播放中随时按）\n"
    "Ctrl+Z / Ctrl+Y 撤销/重做      Del 删除选中\n"
    "Ctrl+A 全选      Ctrl+C/V 复制粘贴      Ctrl+D 向后复制\n"
    "数字键 1~9 切类型      Q/W 上下换行\n"
    "Ctrl+滚轮 缩放      Shift+滚轮 平移      中键拖动 平移\n"
    "L 设循环      M 节拍器      E 导出      Ctrl+Shift+V 导入视频": (
        "V / B / R / Tab で「選択ドラッグ / ノーツ配置 / 範囲選択」を切替\n"
        "配置：クリックで追加、ドラッグで連続配置（1回で取消可）\n"
        "選択：空白ドラッグでノーツ選択、ドラッグで移動、右端で長押し化\n"
        "ルーラー/波形のドラッグ＝再生位置移動、Shift+ドラッグまたは R＝範囲選択\n"
        "スペース 再生/一時停止      F 打点（再生中いつでも）\n"
        "Ctrl+Z / Ctrl+Y 元に戻す/やり直し      Del 選択削除\n"
        "Ctrl+A すべて選択      Ctrl+C/V コピー/貼り付け      Ctrl+D 後ろに複製\n"
        "数字 1~9 種類切替      Q/W 行移動\n"
        "Ctrl+ホイール ズーム      Shift+ホイール 平行移動      中ボタンドラッグ 平行移動\n"
        "L リピート      M メトロノーム      E 書き出し      Ctrl+Shift+V 動画読み込み",
        "V / B / R / Tab: Select / Place notes / Select range\n"
        "Place: click to add, drag to paint (one undo)\n"
        "Select: drag empty space to box-select, drag notes to move, drag the right edge for holds\n"
        "Drag the ruler/waveform to scrub; Shift+drag or R selects a time range\n"
        "Space play/pause      F tap (any time while playing)\n"
        "Ctrl+Z / Ctrl+Y undo/redo      Del delete selected\n"
        "Ctrl+A select all      Ctrl+C/V copy/paste      Ctrl+D duplicate forward\n"
        "1–9 switch type      Q/W previous/next row\n"
        "Ctrl+wheel zoom      Shift+wheel pan      middle-drag pan\n"
        "L loop      M metronome      E export      Ctrl+Shift+V import video"),
}


def current_language() -> str:
    return _LANG


def set_language(code: str) -> None:
    global _LANG
    _LANG = code if code in ("zh", "ja", "en") else "zh"


def tr(text: str) -> str:
    """把中文原文翻成当前语言（找不到就原样返回）。"""
    if _LANG == "zh" or not text:
        return text
    hit = _TABLE.get(text)
    if hit is None:
        return text
    return hit[1] if _LANG == "en" else hit[0]


def trf(template: str, **kw) -> str:
    """先翻译模板再套参数（模板里的占位符用 {name}）。"""
    return tr(template).format(**kw)


# ------------------------------------------------------------------ 控件树翻译
def _remember(obj, current: str) -> str:
    """第一次翻译时记下原文，之后都从原文重译。"""
    orig = obj.property("_zh")
    if not isinstance(orig, str) or not orig:
        obj.setProperty("_zh", current)
        return current
    return orig


def _set(obj, getter, setter) -> None:
    try:
        cur = getter()
    except Exception:
        return
    if not isinstance(cur, str) or not cur:
        return
    orig = _remember(obj, cur)
    new = tr(orig)
    if new != cur:
        try:
            setter(new)
        except Exception:
            pass


def translate_tree(root) -> None:
    """把一棵界面（窗口/对话框/菜单）里的文案全部按当前语言刷新。"""
    if root is None:
        return
    widgets = [root] + list(root.findChildren(QWidget))
    for w in widgets:
        if isinstance(w, QGroupBox):
            _set(w, w.title, w.setTitle)
        elif isinstance(w, (QLabel, QAbstractButton)):
            _set(w, w.text, w.setText)
        _set(w, w.windowTitle, w.setWindowTitle)
        _set(w, w.toolTip, w.setToolTip)
        _set(w, w.statusTip, w.setStatusTip)
        if isinstance(w, QLineEdit):
            _set(w, w.placeholderText, w.setPlaceholderText)
        if isinstance(w, QComboBox):
            for i in range(w.count()):
                cur = w.itemText(i)
                orig = w.itemData(i, Qt.UserRole + 7)
                if not isinstance(orig, str) or not orig:
                    orig = cur
                    w.setItemData(i, cur, Qt.UserRole + 7)
                new = tr(orig)
                if new != cur:
                    w.setItemText(i, new)
        if isinstance(w, QListWidget):
            for i in range(w.count()):
                it = w.item(i)
                cur = it.text()
                orig = it.data(Qt.UserRole + 7)
                if not isinstance(orig, str) or not orig:
                    orig = cur
                    it.setData(Qt.UserRole + 7, cur)
                new = tr(orig)
                if new != cur:
                    it.setText(new)
        if isinstance(w, QTabWidget):
            for i in range(w.count()):
                cur = w.tabText(i)
                orig = w.tabBar().tabData(i)
                if not isinstance(orig, str) or not orig:
                    orig = cur
                    w.tabBar().setTabData(i, cur)
                new = tr(orig)
                if new != cur:
                    w.setTabText(i, new)
        if isinstance(w, QTableWidget):
            for i in range(w.columnCount()):
                it = w.horizontalHeaderItem(i)
                if it is None:
                    continue
                cur = it.text()
                orig = it.data(Qt.UserRole + 7)
                if not isinstance(orig, str) or not orig:
                    orig = cur
                    it.setData(Qt.UserRole + 7, cur)
                new = tr(orig)
                if new != cur:
                    it.setText(new)
    for act in root.findChildren(QAction):
        _set(act, act.text, act.setText)
        _set(act, act.toolTip, act.setToolTip)
