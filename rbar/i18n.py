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
    "用视频里的声音作为音频": ("動画の音声を音源にする", "Use Video Audio"),
    "载入示例工程（没音频也能试）": ("サンプルを読み込む", "Load Demo Project"),
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
    "粘贴到播放头 (Ctrl+V)": ("再生位置に貼り付け (Ctrl+V)", "Paste at Playhead (Ctrl+V)"),
    "向后复制一份 (Ctrl+D)": ("後ろに複製 (Ctrl+D)", "Duplicate Forward (Ctrl+D)"),
    "量化到网格 (Ctrl+Q)": ("グリッドに量子化 (Ctrl+Q)", "Quantize (Ctrl+Q)"),
    "镜像选中区间 (Ctrl+M)": ("選択範囲を反転 (Ctrl+M)", "Mirror Selection (Ctrl+M)"),
    "删除选中 (Del)": ("選択を削除 (Del)", "Delete Selected (Del)"),
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
    "自动铺点（音频起音 / 视频镜头）…": ("自動配置（音の立ち上がり / 映像カット）…",
                              "Auto Notes (Onsets / Scene Cuts)…"),
    "在播放头插入 BPM 段…": ("再生位置に BPM 区間を挿入…", "Insert BPM Segment…"),
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
    "自动铺点": ("自動配置", "Auto Notes"),
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
    "提示：在时间轴上单击=加点，拖拽=移动，\n拖尾部=长条，右键=更多操作。": (
        "ヒント：タイムラインをクリック＝配置、ドラッグ＝移動、\n右端をドラッグ＝長押し、右クリック＝その他。",
        "Tip: click = place, drag = move,\ndrag the right edge = hold, right click = more."),
    "谱面 / BPM": ("譜面 / BPM", "Chart / BPM"),
    "外观": ("外観", "Appearance"),
    "导出": ("書き出し", "Export"),
    "设置": ("設定", "Settings"),
    "BPM 变速": ("BPM 変化", "BPM Changes"),
    "起点 (ms)": ("開始 (ms)", "Start (ms)"),
    "在播放头添加段": ("再生位置に追加", "Add at Playhead"),
    "删除段": ("区間を削除", "Delete Segment"),
    "整段统一 BPM": ("全体を同じ BPM に", "Unify BPM"),
    "Tap 测速（连点）": ("タップで測定（連打）", "Tap Tempo (tap)"),
    "自动检测 BPM": ("BPM を自動検出", "Detect BPM"),
    "自动铺点（起音）": ("自動配置（立ち上がり）", "Auto Notes"),
    "对齐": ("タイミング合わせ", "Alignment"),
    "节拍偏移": ("拍のオフセット", "Beat Offset"),
    "取播放头": ("再生位置を採用", "Use Playhead"),
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
    "自动铺点": ("自動配置", "Auto Notes"),
    "依据": ("基準", "Source"),
    "灵敏度": ("感度", "Sensitivity"),
    "最小间隔": ("最小間隔", "Min Interval"),
    "镜头阈值": ("カット閾値", "Cut Threshold"),
    "试检测": ("テスト検出", "Test Detect"),
    "生成音符": ("ノーツを生成", "Generate Notes"),
    "音频起音（听到的鼓点/音头）": ("音の立ち上がり（ドラム/アタック）", "Audio onsets (drums/attacks)"),
    "视频镜头切换（画面剪切点）": ("映像カット（シーンチェンジ）", "Scene cuts (video)"),
    "吸附到当前网格": ("グリッドにスナップ", "Snap to grid"),
    "取消": ("キャンセル", "Cancel"),
    "确定": ("OK", "OK"),
    # ------------------------------------------------------------ 动态文案
    "就绪：拖入音频 → 打点 → 导出透明视频": (
        "準備完了：音声をドロップ → 打点 → 透過動画を書き出し",
        "Ready: drop media → tap notes → export transparent video"),
    "空工程：先导入音频/视频，或用「示例工程」练手": (
        "空のプロジェクト：音声/動画を読み込むか、サンプルを使ってみてください",
        "Empty project: import media, or load the demo project"),
    "已载入示例工程：可以直接改音符、换外观、试导出": (
        "サンプルを読み込みました：ノーツ編集・外観変更・書き出しを試せます",
        "Demo project loaded: edit notes, tweak the look, try exporting"),
    "没有可用的音频输出设备": ("利用可能な音声出力デバイスがありません", "No audio output device"),
    "没有找到 ffmpeg": ("ffmpeg が見つかりません", "ffmpeg not found"),
    "找不到 ffmpeg.exe，请在「设置」标签里指定路径。": (
        "ffmpeg.exe が見つかりません。「設定」タブでパスを指定してください。",
        "ffmpeg.exe not found. Set its path in the Settings tab."),
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
