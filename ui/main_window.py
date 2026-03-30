"""
主窗口
布局：菜单栏 | 工具栏 | [轨道面板 | Piano Roll] | 状态栏
"""
from __future__ import annotations
import os
import copy
import queue
import threading
from typing import Optional, List

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QHBoxLayout, QVBoxLayout,
    QToolBar, QLabel, QSpinBox, QFileDialog, QMessageBox,
    QProgressDialog, QComboBox, QSlider, QPushButton, QWidgetAction, QInputDialog,
    QDialog, QDialogButtonBox, QFormLayout, QTextBrowser, QTabWidget,
)
from PyQt6.QtCore  import Qt, QThread, pyqtSignal, QObject, QTimer, QEvent
from PyQt6.QtGui     import QAction, QIcon, QKeySequence, QShortcut
from ui.keyboard_input import KeyboardInputWidget

from core.note_model import Project, Track
from ui.piano_roll   import PianoRollWidget, EditMode
from ui.track_panel  import TrackPanel
from ui.omr_preview_dialog import OmrPreviewDialog
from utils.audio_player import AudioPlayer
from core.midi.exporter import export_midi
from core.midi.importer import import_midi


# ─── 帮助对话框 ──────────────────────────────────────────────

_HELP_STYLE = """
<style>
  body  { background:#1E1E2E; color:#CCCCDD; font-family:微软雅黑,Arial; font-size:13px; }
  h2    { color:#88AAFF; border-bottom:1px solid #3A3A5A; padding-bottom:4px; margin-top:14px; }
  table { width:100%; border-collapse:collapse; margin:6px 0; }
  th    { background:#16162A; color:#8899BB; font-weight:normal;
          text-align:left; padding:4px 8px; }
  td    { padding:4px 8px; border-bottom:1px solid #2A2A3A; }
  td:first-child { color:#FFD080; font-family:Consolas,monospace;
                   white-space:nowrap; width:160px; }
  tr:hover td { background:#252540; }
  .tip  { color:#6688AA; font-size:11px; }
</style>
"""

_TAB_SHORTCUTS = _HELP_STYLE + """
<h2>🎹 钢琴卷帘编辑</h2>
<table>
<tr><th>快捷键</th><th>功能</th></tr>
<tr><td>E / 1</td><td>切换到铅笔模式（绘制音符）</td></tr>
<tr><td>S / 2</td><td>切换到选择模式（框选/移动）</td></tr>
<tr><td>3</td><td>切换到橡皮模式（点击删除）</td></tr>
<tr><td>Ctrl + Z</td><td>撤销</td></tr>
<tr><td>Ctrl + Y / Ctrl+Shift+Z</td><td>重做</td></tr>
<tr><td>Ctrl + A</td><td>全选</td></tr>
<tr><td>Ctrl + C</td><td>复制所选音符</td></tr>
<tr><td>Ctrl + X</td><td>剪切所选音符</td></tr>
<tr><td>Ctrl + V</td><td>粘贴</td></tr>
<tr><td>Ctrl + D</td><td>就地复制所选音符</td></tr>
<tr><td>Delete</td><td>删除所选音符</td></tr>
<tr><td>↑ / ↓</td><td>音高上移/下移半音</td></tr>
<tr><td>Ctrl + ↑ / ↓</td><td>音高上移/下移八度</td></tr>
<tr><td>← / →</td><td>时间前移/后移 0.5 拍</td></tr>
<tr><td>W</td><td>时值加倍（如八分→四分）</td></tr>
<tr><td>Q</td><td>时值减半（如四分→八分）</td></tr>
<tr><td>T</td><td>切换网格吸附开/关</td></tr>
<tr><td>Ctrl + = / -</td><td>钢琴卷帘横向缩放</td></tr>
<tr><td>Ctrl+Shift+Q</td><td>量化所选音符到当前吸附精度</td></tr>
</table>

<h2>⌨ 键盘弹奏</h2>
<table>
<tr><th>按键</th><th>功能</th></tr>
<tr><td>I</td><td>开启 / 关闭键盘弹奏模式（也可点工具栏 ⌨ 弹奏 按钮）</td></tr>
<tr><td>d r m f s l x</td><td>按当前调号输入 do re mi fa sol la xi（大调音阶各级）</td></tr>
<tr><td>1 / 2 / 3 / 4 / 5</td><td>切换时值：十六 / 八分 / 四分（默认）/ 二分 / 全音</td></tr>
<tr><td>. (句号)</td><td>附点开关（时值 × 1.5）</td></tr>
<tr><td>↑ / ↓</td><td>八度升高 / 降低（do 所在的八度）</td></tr>
<tr><td>Z</td><td>休止符（仅推进输入光标，不插入音符）</td></tr>
<tr><td>Escape</td><td>退出键盘弹奏模式</td></tr>
</table>
<p class="tip">输入光标（绿色竖线）显示下一个音符的插入位置，从当前时间轴位置开始。</p>

<h2>▶ 播放控制</h2>
<table>
<tr><th>快捷键</th><th>功能</th></tr>
<tr><td>Space</td><td>播放 / 暂停</td></tr>
<tr><td>Escape</td><td>停止并归位</td></tr>
<tr><td>Home</td><td>回到起点（上次点击时间轴的位置）</td></tr>
<tr><td>F</td><td>切换播放跟随开/关</td></tr>
<tr><td>↺ 按钮</td><td>重播模式：每次从起点播放（亮）/ 继续模式（暗）</td></tr>
</table>

<h2>🎛 轨道面板</h2>
<table>
<tr><th>操作</th><th>功能</th></tr>
<tr><td>M</td><td>静音/取消静音当前选中轨道</td></tr>
<tr><td>S 按钮</td><td>独奏（只播放本轨道，其他自动静音）</td></tr>
<tr><td>音量滑条</td><td>调整轨道音量 0~100%，导出 MIDI 时同步写入 CC7</td></tr>
<tr><td>双击轨道名</td><td>重命名轨道</td></tr>
<tr><td>颜色块</td><td>点击修改轨道颜色</td></tr>
<tr><td>谱号徽章 G/F/C</td><td>显示轨道谱号（高音/低音/中音），由音域或 OMR 结果自动确定</td></tr>
<tr><td>✕ 按钮</td><td>删除轨道</td></tr>
</table>

<h2>📁 文件操作</h2>
<table>
<tr><th>快捷键</th><th>功能</th></tr>
<tr><td>Ctrl + N</td><td>新建项目（会提示保存）</td></tr>
<tr><td>Ctrl + O</td><td>打开项目文件 (.mep)</td></tr>
<tr><td>Ctrl + S</td><td>保存项目文件 (.mep)</td></tr>
<tr><td>文件 → 导入 MIDI</td><td>从 MIDI 文件导入轨道</td></tr>
<tr><td>文件 → 导出 MIDI</td><td>导出为标准 MIDI 文件</td></tr>
<tr><td>文件 → 导出 WAV</td><td>通过 FluidSynth 渲染为 WAV 音频</td></tr>
</table>
"""

_TAB_OMR = _HELP_STYLE + """
<h2>🎼 五线谱识别</h2>
<table>
<tr><th>项目</th><th>说明</th></tr>
<tr><td>支持格式</td><td>PNG / JPG / BMP / PDF</td></tr>
<tr><td>识别引擎</td><td>优先 Audiveris（高精度），不可用时降级为 oemer</td></tr>
<tr><td>PDF 多页</td><td>自动弹出选页对话框，可选择识别范围；单页直接识别</td></tr>
<tr><td>渲染分辨率</td><td>Audiveris = 300 DPI，oemer = 150 DPI（自动切换）</td></tr>
<tr><td>轨道输出</td><td>高音谱（G）/ 低音谱（F）分开成独立轨道</td></tr>
<tr><td>去重</td><td>自动去除 oemer 输出的重复音符</td></tr>
</table>

<h2>🎵 简谱识别</h2>
<table>
<tr><th>项目</th><th>说明</th></tr>
<tr><td>支持格式</td><td>PNG / JPG / BMP / PDF</td></tr>
<tr><td>识别引擎</td><td>PaddleOCR（首次运行自动下载模型）</td></tr>
<tr><td>识别内容</td><td>调号（1=C/D/…）、拍号、BPM、音符 1-7、休止符 0、延音线 —、附点、八度点、时值横线</td></tr>
<tr><td>声部</td><td>上声部（旋律）+ 下声部（伴奏）各输出独立轨道</td></tr>
<tr><td>PDF 多页</td><td>同五线谱，可选页范围；单页直接识别</td></tr>
<tr><td>歌词行过滤</td><td>自动识别并跳过歌词行，只识别音符行</td></tr>
<tr><td>延音线补偿</td><td>轮廓检测补充 OCR 漏检的延音线；x 间距推断过短的伴奏时值</td></tr>
<tr><td>图像增强</td><td>CLAHE 自适应对比度增强（扫描件效果更好）</td></tr>
</table>

<h2>✍ 手动输入简谱</h2>
<table>
<tr><th>符号</th><th>含义</th></tr>
<tr><td>1=C  4/4  J=120</td><td>调号、拍号、BPM 声明（写在第一行）</td></tr>
<tr><td>1 2 3 4 5 6 7</td><td>音符 do~si，0 = 休止符</td></tr>
<tr><td>-</td><td>延音线（延长一拍）</td></tr>
<tr><td>^1  ^^1</td><td>高八度 / 高两个八度</td></tr>
<tr><td>_1  __1</td><td>低八度 / 低两个八度</td></tr>
<tr><td>#3  b7</td><td>升号 / 降号</td></tr>
<tr><td>1_  1__</td><td>八分音符 / 十六分音符（下划线加在音符后）</td></tr>
<tr><td>1.</td><td>附点（时值 × 1.5）</td></tr>
<tr><td>|</td><td>小节线（可省略）</td></tr>
</table>
"""

_TAB_TRANSPORT = _HELP_STYLE + """
<h2>🎚 传输条说明</h2>
<table>
<tr><th>控件</th><th>功能</th></tr>
<tr><td>⏮ 回到起点</td><td>跳回上次点击时间轴设定的起始位置（默认第 0 拍）</td></tr>
<tr><td>▶ 播放/暂停</td><td>开始或暂停播放</td></tr>
<tr><td>⏹ 停止</td><td>停止并将播放头归位到起点</td></tr>
<tr><td>↺ 播放模式</td><td>亮 = 重播模式（每次从起点）；暗 = 继续模式（从暂停处）</td></tr>
<tr><td>BPM 输入框</td><td>全局基准速度（20~300），可与钢琴卷帘内的变速点叠加</td></tr>
<tr><td>调号标签</td><td>由 OMR 识别自动填入，也可通过菜单手动设置</td></tr>
<tr><td>拍号下拉</td><td>4/4、3/4、2/4、6/8、12/8 可选</td></tr>
<tr><td>小节 : 拍 显示</td><td>实时显示播放位置；Tooltip 显示完整时间码（mm:ss.ms）</td></tr>
</table>

<h2>🔊 音频输出</h2>
<table>
<tr><th>项目</th><th>说明</th></tr>
<tr><td>FluidSynth</td><td>优先使用，需要 SoundFont 文件（config.py 中配置路径）</td></tr>
<tr><td>pygame MIDI</td><td>FluidSynth 不可用时降级，使用系统 MIDI 合成器</td></tr>
<tr><td>长音渐弱</td><td>超过 1.5 拍的音符末尾自动渐弱（0.45 拍），模拟真实钢琴</td></tr>
<tr><td>预览音符</td><td>在钢琴卷帘中绘制或点击音符时实时发声；点击左侧钢琴键盘也可试音</td></tr>
<tr><td>力度编辑条</td><td>钢琴卷帘下方的 VEL 条，点击/拖拽可调整每个音符的力度（0-127）</td></tr>
</table>

<h2>📤 MIDI 导出规范</h2>
<table>
<tr><th>项目</th><th>说明</th></tr>
<tr><td>格式</td><td>Type 1 MIDI（多轨道），480 ticks/拍</td></tr>
<tr><td>全局轨道</td><td>Tempo / 拍号 / 曲名 / 变速点（支持多段变速）</td></tr>
<tr><td>通道分配</td><td>自动分配 0-15，跳过 Channel 9（保留给 GM 鼓组）</td></tr>
<tr><td>音量</td><td>每轨道输出 CC7 控制器消息，与轨道面板音量滑条对应</td></tr>
<tr><td>乐器</td><td>Program Change（默认 0 = 大钢琴）</td></tr>
</table>
"""


class _HelpDialog(QDialog):
    """功能大全帮助对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("功能大全")
        self.setMinimumSize(680, 560)
        self.resize(720, 620)
        self.setModal(False)   # 非模态，可与主窗口同时操作

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        tabs = QTabWidget()
        tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #3A3A5A; background: #1E1E2E; }
            QTabBar::tab {
                background: #16162A; color: #8899BB;
                padding: 6px 16px; border: 1px solid #3A3A5A;
                border-bottom: none; border-radius: 4px 4px 0 0;
            }
            QTabBar::tab:selected { background: #2A2A4A; color: #CCCCDD; }
            QTabBar::tab:hover    { background: #222240; color: #AABBFF; }
        """)

        def _make_browser(html: str) -> QTextBrowser:
            b = QTextBrowser()
            b.setOpenExternalLinks(False)
            b.setStyleSheet("QTextBrowser { background:#1E1E2E; border:none; }")
            b.setHtml(html)
            return b

        tabs.addTab(_make_browser(_TAB_SHORTCUTS), "⌨ 快捷键 & 操作")
        tabs.addTab(_make_browser(_TAB_OMR),       "📷 乐谱识别")
        tabs.addTab(_make_browser(_TAB_TRANSPORT), "🎚 传输 & 导出")

        layout.addWidget(tabs)

        close_btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn.rejected.connect(self.close)
        close_btn.setStyleSheet("""
            QPushButton { background:#252538; color:#CCCCDD;
                          border:1px solid #3A3A5A; border-radius:4px;
                          padding:4px 20px; }
            QPushButton:hover { background:#333358; }
        """)
        layout.addWidget(close_btn)

        self.setStyleSheet("QDialog { background:#1E1E2E; }")


# ─── 选页对话框 ──────────────────────────────────────────────

class _PageRangeDialog(QDialog):
    """PDF 多页选页对话框"""

    def __init__(self, total_pages: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择识别页面范围")
        self.setModal(True)

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self._spin_from = QSpinBox()
        self._spin_from.setRange(1, total_pages)
        self._spin_from.setValue(1)
        self._spin_to = QSpinBox()
        self._spin_to.setRange(1, total_pages)
        self._spin_to.setValue(total_pages)
        form.addRow(f"共 {total_pages} 页，从第", self._spin_from)
        form.addRow("页到第", self._spin_to)
        layout.addLayout(form)

        self._spin_from.valueChanged.connect(self._validate)
        self._spin_to.valueChanged.connect(self._validate)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _validate(self) -> None:
        if self._spin_from.value() > self._spin_to.value():
            self._spin_to.setValue(self._spin_from.value())

    @property
    def page_from(self) -> int:
        return self._spin_from.value()

    @property
    def page_to(self) -> int:
        return self._spin_to.value()


# ─── 后台 OMR 工作线程 ────────────────────────────────────────

class _JianpuOcrWorker(QObject):
    """仅执行 OCR，返回识别文本（不解析音符）"""
    finished = pyqtSignal(str)
    error    = pyqtSignal(str)

    def __init__(self, image_path: str):
        super().__init__()
        self.image_path = image_path

    def run(self) -> None:
        try:
            print("[OCR] 开始预处理...")
            from utils.image_utils import prepare_images
            from core.omr.jianpu_omr import ocr_jianpu_text
            pages = prepare_images(self.image_path)
            print(f"[OCR] 预处理完成: {pages[0]}")
            text  = ocr_jianpu_text(pages[0])
            print(f"[OCR] 识别完成，文本长度: {len(text)}")
            self.finished.emit(text)
            print("[OCR] 信号已发送")
        except Exception as e:
            print(f"[OCR] 错误: {e}")
            self.error.emit(str(e))


class OmrWorker(QObject):
    finished = pyqtSignal(list, object, int, int)  # tracks, time_sig, bpm, key_sharps
    error    = pyqtSignal(str)

    def __init__(self, image_path: str, score_type: str,
                 page_from: int = 1, page_to: int = -1):
        super().__init__()
        self.image_path = image_path
        self.score_type = score_type  # "staff" | "jianpu"
        self.page_from  = page_from   # 1-indexed 起始页
        self.page_to    = page_to     # 1-indexed 结束页，-1 = 最后一页

    def run(self) -> None:
        try:
            if self.score_type == "staff":
                from core.omr.staff_omr import recognize_staff
                tracks, time_sig, bpm, key_sharps = recognize_staff(
                    self.image_path,
                    page_from=self.page_from,
                    page_to=self.page_to)
            else:
                from core.omr.jianpu_omr import recognize_jianpu
                tracks, time_sig, bpm, key_sharps = recognize_jianpu(
                    self.image_path,
                    page_from=self.page_from,
                    page_to=self.page_to)

            self.finished.emit(tracks, time_sig, bpm, key_sharps)
        except Exception as e:
            self.error.emit(str(e))


# ─── 主窗口 ──────────────────────────────────────────────────

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self._project        = Project()
        self._modified       = False
        self._current_file   = ""
        self._audio_player   = AudioPlayer()
        self._omr_thread: Optional[QThread] = None

        # 撤销/重做栈（每项为所有轨道音符的深拷贝）
        self._undo_stack: List = []
        self._redo_stack: List = []
        self._MAX_UNDO = 500

        # 跨线程节拍更新队列（后台线程写入，主线程定时器消费）
        self._beat_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._beat_poll_timer = QTimer(self)
        self._beat_poll_timer.setInterval(16)   # ~60 fps
        self._beat_poll_timer.timeout.connect(self._drain_beat_queue)
        self._beat_poll_timer.start()

        # 播放起始位置（点击时间轴时更新，用于"重播"模式）
        self._play_start_beat: float = 0.0
        self._last_beat:       float = 0.0   # 最近一次收到的播放位置，供回退动画使用

        # 重播模式"倒带"动画
        self._return_anim_from:        float = 0.0
        self._return_anim_to:          float = 0.0
        self._return_anim_step:        int   = 0
        self._return_anim_steps:       int   = 1
        self._return_anim_on_complete        = None   # 动画结束后的回调
        self._return_timer = QTimer(self)
        self._return_timer.setInterval(14)            # ~70 fps
        self._return_timer.timeout.connect(self._on_return_anim_tick)

        self._build_ui()
        self._build_menu()
        self._build_toolbar()
        self._connect_signals()

        # 新建默认轨道
        self._project.add_track("主旋律")
        self._piano_roll.set_project(self._project)
        self._track_panel.set_project(self._project)
        self._audio_player.load_project(self._project)

        self._update_title()
        self.setMinimumSize(900, 600)
        self.resize(1280, 720)

    # ── UI 构建 ───────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setStyleSheet("""
            QMainWindow { background: #1E1E2E; }
            QMenuBar { background: #16162A; color: #CCCCDD; }
            QMenuBar::item:selected { background: #2A2A4A; }
            QMenu { background: #1E1E2E; color: #CCCCDD; border: 1px solid #3A3A5A; }
            QMenu::item:selected { background: #2A306A; }
            QToolBar { background: #16162A; border: none; spacing: 4px; padding: 2px; }
            QToolBar QToolButton {
                color: #DDDDEE;
                background: #252538;
                border: 1px solid #3A3A5A;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 12px;
            }
            QToolBar QToolButton:hover   { background: #333358; border-color: #5555AA; }
            QToolBar QToolButton:checked { background: #1A4FA0; color: #FFFFFF;
                                           border: 1px solid #4488FF; font-weight: bold; }
            QToolBar QToolButton:pressed { background: #0F3070; }
            QStatusBar { background: #16162A; color: #888899; font-size: 11px; }
            QSplitter::handle { background: #2A2A4A; }
        """)

        # 中心控件
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 水平分割：轨道面板 | Piano Roll
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(3)

        self._track_panel = TrackPanel()
        self._track_panel.setMinimumWidth(240)
        self._track_panel.setMaximumWidth(360)
        splitter.addWidget(self._track_panel)

        self._piano_roll = PianoRollWidget()
        splitter.addWidget(self._piano_roll)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 900])   # 初始宽度：轨道面板 260，钢琴卷帘其余空间

        main_layout.addWidget(splitter)

        # 键盘弹奏面板（默认折叠，I 键展开）
        self._keyboard_input = KeyboardInputWidget()
        main_layout.addWidget(self._keyboard_input)

        # 底部传输控制条
        transport = self._build_transport()
        main_layout.addWidget(transport)

        self.statusBar().showMessage("就绪")

    def _build_transport(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(44)
        bar.setStyleSheet("background: #16162A; border-top: 1px solid #2A2A4A;")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        btn_style = """
            QPushButton { color: #CCCCDD; background: #222235; border: 1px solid #3A3A5A;
                          border-radius: 4px; font-size: 14px; min-width: 32px; min-height: 28px; }
            QPushButton:hover   { background: #2A306A; }
            QPushButton:pressed { background: #111125; }
        """

        # 回到起点
        self._btn_rewind = QPushButton("⏮")
        self._btn_rewind.setToolTip("回到起点 (Home)")
        self._btn_rewind.setStyleSheet(btn_style)
        layout.addWidget(self._btn_rewind)

        # 播放 / 暂停
        self._btn_play = QPushButton("▶")
        self._btn_play.setToolTip("播放 / 暂停 (Space)")
        self._btn_play.setStyleSheet(btn_style)
        layout.addWidget(self._btn_play)

        # 停止
        self._btn_stop = QPushButton("⏹")
        self._btn_stop.setToolTip("停止 (Esc)")
        self._btn_stop.setStyleSheet(btn_style)
        layout.addWidget(self._btn_stop)

        layout.addSpacing(6)

        # 播放模式切换：↺ = 重播（从起点），▶| = 继续（从暂停处）
        self._btn_restart_mode = QPushButton("↺")
        self._btn_restart_mode.setCheckable(True)
        self._btn_restart_mode.setChecked(False)
        self._btn_restart_mode.setFixedSize(32, 28)
        self._btn_restart_mode.setToolTip(
            "播放模式（点击切换）\n"
            "↺ 亮起 = 重播模式：每次播放都从起点开始\n"
            "↺ 熄灭 = 继续模式：从上次暂停处继续\n\n"
            "起点 = 上次点击时间轴的位置（默认第 0 拍）"
        )
        self._btn_restart_mode.setStyleSheet(btn_style + """
            QPushButton:checked { background: #1A4FA0; color: #FFFFFF;
                                  border: 1px solid #4488FF; }
        """)
        layout.addWidget(self._btn_restart_mode)

        layout.addSpacing(12)

        # BPM
        bpm_label = QLabel("BPM")
        bpm_label.setStyleSheet("color: #8888AA; font-size: 11px;")
        layout.addWidget(bpm_label)

        self._bpm_spin = QSpinBox()
        self._bpm_spin.setRange(20, 300)
        self._bpm_spin.setValue(self._project.tempo)
        self._bpm_spin.setFixedWidth(82)
        self._bpm_spin.setStyleSheet("""
            QSpinBox { background: #222235; color: #CCCCDD; border: 1px solid #3A3A5A;
                       border-radius: 3px; font-size: 13px; padding-right: 2px; }
        """)
        layout.addWidget(self._bpm_spin)

        layout.addSpacing(12)

        # 调号
        key_title = QLabel("调号")
        key_title.setStyleSheet("color: #8888AA; font-size: 11px;")
        layout.addWidget(key_title)

        self._key_label = QLabel("C 大调")
        self._key_label.setStyleSheet(
            "color: #CCCCDD; font-size: 12px; background: #222235;"
            "border: 1px solid #3A3A5A; border-radius: 3px; padding: 2px 8px;"
        )
        layout.addWidget(self._key_label)

        layout.addSpacing(12)

        # 拍号
        ts_label = QLabel("拍号")
        ts_label.setStyleSheet("color: #8888AA; font-size: 11px;")
        layout.addWidget(ts_label)

        self._ts_combo = QComboBox()
        for ts in ["4/4", "3/4", "2/4", "6/8", "12/8"]:
            self._ts_combo.addItem(ts)
        self._ts_combo.setStyleSheet("""
            QComboBox { background: #222235; color: #CCCCDD; border: 1px solid #3A3A5A;
                        border-radius: 3px; font-size: 12px; padding: 2px 6px; }
        """)
        layout.addWidget(self._ts_combo)

        layout.addStretch()

        # 当前位置显示（小节:拍，醒目大字）
        self._pos_label = QLabel("1 : 1")
        self._pos_label.setStyleSheet(
            "color: #99CCFF; font-family: monospace; font-size: 16px; font-weight: bold;"
            " background: #111125; border: 1px solid #2A2A5A;"
            " border-radius: 4px; padding: 0 10px; min-width: 72px;"
        )
        self._pos_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pos_label.setToolTip("当前位置（小节 : 拍）")
        layout.addWidget(self._pos_label)

        return bar

    def _build_menu(self) -> None:
        # ── 文件 ──────────────────────────────────────────────
        file_menu = self.menuBar().addMenu("文件(&F)")

        act_new       = QAction("新建(&N)", self, shortcut=QKeySequence.StandardKey.New)
        act_open_proj = QAction("打开项目(&O)...", self, shortcut=QKeySequence.StandardKey.Open)
        act_save_proj = QAction("保存项目(&S)...", self, shortcut=QKeySequence.StandardKey.Save)
        act_open_midi = QAction("导入 MIDI...", self)
        act_export_midi = QAction("导出 MIDI(&E)...", self)
        act_export_wav  = QAction("导出 WAV(&W)...", self)
        act_quit = QAction("退出(&Q)", self, shortcut=QKeySequence.StandardKey.Quit)

        act_new.triggered.connect(self._on_new)
        act_open_proj.triggered.connect(self._on_open_project)
        act_save_proj.triggered.connect(self._on_save_project)
        act_open_midi.triggered.connect(self._on_open_midi)
        act_export_midi.triggered.connect(self._on_export_midi)
        act_export_wav.triggered.connect(self._on_export_wav)
        act_quit.triggered.connect(self.close)

        file_menu.addAction(act_new)
        file_menu.addSeparator()
        file_menu.addAction(act_open_proj)
        file_menu.addAction(act_save_proj)
        file_menu.addSeparator()
        file_menu.addAction(act_open_midi)
        file_menu.addAction(act_export_midi)
        file_menu.addAction(act_export_wav)
        file_menu.addSeparator()
        file_menu.addAction(act_quit)

        # ── 导入 ──────────────────────────────────────────────
        import_menu = self.menuBar().addMenu("导入(&I)")

        act_import_staff  = QAction("导入五线谱图片...", self)
        act_import_jianpu = QAction("导入简谱图片...", self)

        act_import_staff.triggered.connect(lambda: self._on_import_image("staff"))
        act_import_jianpu.triggered.connect(lambda: self._on_import_image("jianpu"))
        act_input_jianpu  = QAction("手动输入简谱(&T)...", self)
        act_input_jianpu.triggered.connect(self._on_input_jianpu_manual)

        import_menu.addAction(act_import_staff)
        import_menu.addAction(act_import_jianpu)
        import_menu.addSeparator()
        import_menu.addAction(act_input_jianpu)

        # ── 编辑 ──────────────────────────────────────────────
        edit_menu = self.menuBar().addMenu("编辑(&E)")

        act_undo = QAction("撤销(&Z)", self, shortcut=QKeySequence.StandardKey.Undo)
        act_redo = QAction("重做(&Y)", self, shortcut=QKeySequence.StandardKey.Redo)
        act_undo.triggered.connect(self._undo)
        act_redo.triggered.connect(self._redo)
        edit_menu.addAction(act_undo)
        edit_menu.addAction(act_redo)
        edit_menu.addSeparator()

        act_sel_all = QAction("全选(&A)", self, shortcut=QKeySequence.StandardKey.SelectAll)
        act_delete  = QAction("删除所选(&D)", self, shortcut=QKeySequence.StandardKey.Delete)
        act_quantize = QAction("量化所选音符(&Q)...", self, shortcut=QKeySequence("Ctrl+Shift+Q"))
        act_sel_all.triggered.connect(self._piano_roll.select_all)
        act_delete.triggered.connect(self._piano_roll.delete_selected)
        act_quantize.triggered.connect(self._on_quantize)
        edit_menu.addAction(act_sel_all)
        edit_menu.addAction(act_delete)
        edit_menu.addSeparator()
        edit_menu.addAction(act_quantize)

        # ── 视图 ──────────────────────────────────────────────
        view_menu = self.menuBar().addMenu("视图(&V)")
        act_zoom_in  = QAction("放大(&+)", self, shortcut=QKeySequence.StandardKey.ZoomIn)
        act_zoom_out = QAction("缩小(&-)", self, shortcut=QKeySequence.StandardKey.ZoomOut)

        act_zoom_in.triggered.connect(self._piano_roll.zoom_in)
        act_zoom_out.triggered.connect(self._piano_roll.zoom_out)

        view_menu.addAction(act_zoom_in)
        view_menu.addAction(act_zoom_out)

        help_menu = self.menuBar().addMenu("帮助(&H)")
        act_help = QAction("功能大全(&F)...", self, shortcut=QKeySequence("F1"))
        act_help.triggered.connect(lambda: _HelpDialog(self).show())
        help_menu.addAction(act_help)

    def _build_toolbar(self) -> None:
        tb = self.addToolBar("工具")
        tb.setMovable(False)

        # 编辑模式（默认选择模式）
        self._act_pen    = QAction("✏ 铅笔", self, checkable=True, checked=False)
        self._act_select = QAction("⬚ 选择", self, checkable=True, checked=True)
        self._act_erase  = QAction("✂ 橡皮", self, checkable=True)
        self._act_pen.setToolTip("铅笔模式 (E)  — 点击/拖动绘制音符")
        self._act_select.setToolTip("选择模式 (S)  — 框选/移动音符")
        self._act_erase.setToolTip("橡皮模式 (3)  — 点击删除音符")

        self._act_pen.triggered.connect(lambda: self._set_mode(EditMode.PEN))
        self._act_select.triggered.connect(lambda: self._set_mode(EditMode.SELECT))
        self._act_erase.triggered.connect(lambda: self._set_mode(EditMode.ERASE))

        for act in (self._act_pen, self._act_select, self._act_erase):
            tb.addAction(act)

        tb.addSeparator()

        # 导入乐谱（合并为单按钮 + 下拉菜单）
        from PyQt6.QtWidgets import QMenu, QToolButton
        btn_import = QToolButton(self)
        btn_import.setText("导入乐谱")
        btn_import.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu_import = QMenu(btn_import)
        menu_import.addAction("🎼 导入五线谱图片...",  lambda: self._on_import_image("staff"))
        menu_import.addAction("🎵 导入简谱图片...",   lambda: self._on_import_image("jianpu"))
        menu_import.addSeparator()
        menu_import.addAction("✍ 手动输入简谱...", self._on_input_jianpu_manual)
        btn_import.setMenu(menu_import)
        tb.addWidget(btn_import)

        tb.addSeparator()

        # 播放跟随（平滑翻页）
        self._act_follow = QAction("⟳ 跟随", self, checkable=True, checked=True)
        self._act_follow.setToolTip("播放跟随 (F)\n开：视图跟随播放位置滚动\n关：视图固定")
        self._act_follow.triggered.connect(
            lambda checked: self._piano_roll.set_follow_playback(checked)
        )
        tb.addAction(self._act_follow)

        tb.addSeparator()

        # 吸附模式
        self._act_snap = QAction("⊞ 吸附", self, checkable=True, checked=True)
        self._act_snap.setToolTip("网格吸附 (T)\n开：音符对齐到右侧吸附精度\n关：自由放置")
        self._act_snap.triggered.connect(self._on_snap_toggle)
        tb.addAction(self._act_snap)

        # 吸附精度
        self._snap_combo = QComboBox()
        self._snap_combo.setFixedWidth(72)
        self._snap_combo.setToolTip("吸附精度")
        for label, val in [("1/1", 4.0), ("1/2", 2.0), ("1/4", 1.0), ("1/8", 0.5), ("1/16", 0.25), ("1/32", 0.125)]:
            self._snap_combo.addItem(label, val)
        self._snap_combo.setCurrentIndex(2)   # 默认 1/4（四分音符=一格）
        self._snap_combo.setStyleSheet("""
            QComboBox { background: #222235; color: #CCCCDD; border: 1px solid #3A3A5A;
                        border-radius: 3px; font-size: 11px; padding: 2px 4px; }
        """)
        self._snap_combo.currentIndexChanged.connect(self._on_snap_grid_changed)
        snap_wa = QWidgetAction(self)
        snap_wa.setDefaultWidget(self._snap_combo)
        tb.addAction(snap_wa)

        tb.addSeparator()

        # 键盘弹奏
        self._act_keyboard = QAction("⌨ 弹奏", self, checkable=True, checked=False)
        self._act_keyboard.setToolTip(
            "键盘弹奏 (I)\n"
            "开启后用 d/r/m/f/s/l/x 按当前调号输入音符\n"
            "1–5：时值  |  .：附点  |  ↑↓：八度  |  Z：休止"
        )
        self._act_keyboard.triggered.connect(self._keyboard_input.toggle_active)
        # 同步面板开关与按钮状态
        self._keyboard_input.active_changed.connect(self._act_keyboard.setChecked)
        tb.addAction(self._act_keyboard)

        tb.addSeparator()

        # 节拍器
        self._act_metronome = QAction("🥁 节拍器", self, checkable=True, checked=False)
        self._act_metronome.setToolTip("节拍器 (K)\n开：播放时发出节拍声\n强拍用木鱼，弱拍用边鼓")
        self._act_metronome.triggered.connect(self._on_metronome_toggle)
        tb.addAction(self._act_metronome)

        tb.addSeparator()

        # 量化
        act_quantize_tb = QAction("⊟ 量化", self)
        act_quantize_tb.setToolTip("量化所选音符 (Ctrl+Shift+Q)\n将选中音符的起始拍和时值对齐到当前吸附精度")
        act_quantize_tb.triggered.connect(self._on_quantize)
        tb.addAction(act_quantize_tb)

        tb.addSeparator()

        # 导出
        act_export = QAction("💾 导出 MIDI", self)
        act_export.triggered.connect(self._on_export_midi)
        tb.addAction(act_export)

        act_export_wav = QAction("🔊 导出 WAV", self)
        act_export_wav.triggered.connect(self._on_export_wav)
        tb.addAction(act_export_wav)

    # ── 信号连接 ──────────────────────────────────────────────

    def _connect_signals(self) -> None:
        # Piano roll
        self._piano_roll.notes_changed.connect(self._on_notes_changed)
        self._piano_roll.beat_clicked.connect(self._on_beat_clicked)
        self._piano_roll.note_preview.connect(self._audio_player.preview_note)
        self._piano_roll.pre_change.connect(self._save_undo_snapshot)
        self._piano_roll.hover_info.connect(lambda msg: self.statusBar().showMessage(msg))
        self._piano_roll.tempo_change_requested.connect(self._on_tempo_change_requested)

        # 轨道面板
        self._track_panel.track_added.connect(self._on_track_changed)
        self._track_panel.track_deleted.connect(lambda _: self._on_track_changed())
        self._track_panel.track_changed.connect(self._on_track_mute_changed)
        self._track_panel.track_selected.connect(self._piano_roll.select_track)

        # 传输
        self._btn_play.clicked.connect(self._on_play_pause)
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_rewind.clicked.connect(self._on_rewind)
        self._bpm_spin.valueChanged.connect(self._on_bpm_changed)
        self._bpm_spin.editingFinished.connect(self._bpm_spin.clearFocus)
        self._ts_combo.currentTextChanged.connect(self._on_ts_changed)

        # 音频播放器回调（从后台线程发出，用 QTimer 安全更新 UI）
        self._audio_player.on_beat_changed = self._on_beat_update
        self._audio_player.on_playback_stopped = self._on_playback_stopped

        # ── 全局快捷键 ────────────────────────────────────────────
        # 注：部分快捷键在键盘弹奏模式下会被临时禁用（_kb_conflict_scs），
        #     弹奏模式结束后自动恢复，以防止与 d/r/m/f/s/l/x/1-5/↑↓/Esc 冲突。

        def sc(key, slot):
            s = QShortcut(QKeySequence(key), self)
            s.activated.connect(slot)
            return s

        # ── 不会冲突，始终有效 ─────────────────────────────────
        sc("Space",        self._on_play_pause)
        sc("Home",         self._on_rewind)
        sc("Ctrl+=",       self._piano_roll.zoom_in)
        sc("Ctrl+-",       self._piano_roll.zoom_out)
        sc("Ctrl+Z",       self._undo)
        sc("Ctrl+Y",       self._redo)
        sc("Ctrl+Shift+Z", self._redo)
        sc("Ctrl+C",       self._piano_roll.copy_selected)
        sc("Ctrl+X",       self._piano_roll.cut_selected)
        sc("Ctrl+V",       self._piano_roll.paste)
        sc("Ctrl+S",       self._on_save_project)
        sc("Ctrl+Shift+Q", self._on_quantize)
        sc("Ctrl+A",       self._piano_roll.select_all)
        sc("Ctrl+D",       self._piano_roll.duplicate_selected)
        sc("Ctrl+Up",      lambda: self._piano_roll.move_selected_pitch(12))
        sc("Ctrl+Down",    lambda: self._piano_roll.move_selected_pitch(-12))
        sc("Left",         lambda: self._piano_roll.move_selected_beat(-0.5))
        sc("Right",        lambda: self._piano_roll.move_selected_beat(0.5))
        sc("W",            lambda: self._piano_roll.change_selected_duration(2.0))
        sc("Q",            lambda: self._piano_roll.change_selected_duration(0.5))
        sc("E",            lambda: self._set_mode(EditMode.PEN))
        sc("T",            self._toggle_snap_shortcut)
        sc("K",            self._toggle_metronome_shortcut)
        sc("I",            self._keyboard_input.toggle_active)

        # ── 与键盘弹奏冲突的快捷键（保存引用，激活时禁用）─────
        # 冲突键：1/2/3 → 时值；S→sol；F→fa；M→mi；↑↓→八度；Esc→退出模式
        self._kb_conflict_scs = [
            sc("1",      lambda: self._set_mode(EditMode.PEN)),
            sc("2",      lambda: self._set_mode(EditMode.SELECT)),
            sc("3",      lambda: self._set_mode(EditMode.ERASE)),
            sc("S",      lambda: self._set_mode(EditMode.SELECT)),
            sc("F",      self._toggle_follow_shortcut),
            sc("M",      self._track_panel.mute_selected),
            sc("Up",     lambda: self._piano_roll.move_selected_pitch(1)),
            sc("Down",   lambda: self._piano_roll.move_selected_pitch(-1)),
            sc("Escape", self._on_stop),
        ]

        # ── 键盘弹奏信号 ──────────────────────────────────────
        self._keyboard_input.note_input.connect(self._on_keyboard_note)
        self._keyboard_input.rest_input.connect(self._piano_roll.advance_rest)
        self._keyboard_input.active_changed.connect(self._on_keyboard_active_changed)

    # ── 菜单动作 ──────────────────────────────────────────────

    def _on_new(self) -> None:
        if self._modified:
            ret = QMessageBox.question(self, "新建", "当前项目未保存，确定新建？",
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                return
        self._audio_player.stop()
        self._project = Project()
        self._project.add_track("主旋律")
        self._piano_roll.set_project(self._project)
        self._track_panel.set_project(self._project)
        self._audio_player.load_project(self._project)
        self._modified = False
        self._update_title()

    def _on_open_midi(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "打开 MIDI 文件", "", "MIDI 文件 (*.mid *.midi)"
        )
        if not path:
            return
        try:
            self._audio_player.stop()
            self._project = import_midi(path)
            self._piano_roll.set_project(self._project)
            self._track_panel.set_project(self._project)
            self._audio_player.load_project(self._project)
            self._bpm_spin.setValue(self._project.tempo)
            self._modified = False
            self._current_file = path
            self._update_title()
        except Exception as e:
            QMessageBox.critical(self, "打开失败", f"无法读取 MIDI 文件：\n{e}")

    def _on_save(self) -> None:
        """旧版 Ctrl+S 保留兼容，转发到项目存档"""
        self._on_save_project()

    def _on_open_project(self) -> None:
        """打开 .mep 项目文件"""
        if self._modified:
            ret = QMessageBox.question(
                self, "打开项目", "当前项目未保存，确定放弃并打开新项目？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                return
        path, _ = QFileDialog.getOpenFileName(
            self, "打开项目", "", "MIDI 编辑器项目 (*.mep);;所有文件 (*)"
        )
        if not path:
            return
        try:
            from core.project_io import load_project
            self._audio_player.stop()
            self._project = load_project(path)
            self._piano_roll.set_project(self._project)
            self._track_panel.set_project(self._project)
            self._audio_player.load_project(self._project)
            self._bpm_spin.setValue(self._project.tempo)
            from core.note_model import sharps_to_key_name
            self._key_label.setText(sharps_to_key_name(self._project.key_sig))
            ts = self._project.time_sig
            ts_str = f"{ts.numerator}/{ts.denominator}"
            idx = self._ts_combo.findText(ts_str)
            if idx >= 0:
                self._ts_combo.setCurrentIndex(idx)
            self._keyboard_input.set_key_sig(self._project.key_sig)
            self._modified      = False
            self._current_file  = path
            self._update_title()
            self.statusBar().showMessage(f"已打开：{path}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "打开失败", f"无法读取项目文件：\n{e}")

    def _on_save_project(self) -> None:
        """保存为 .mep 项目文件（Ctrl+S）"""
        if self._current_file and self._current_file.endswith(".mep"):
            try:
                from core.project_io import save_project
                save_project(self._project, self._current_file)
                self._modified = False
                self._update_title()
                self.statusBar().showMessage(f"已保存：{self._current_file}", 3000)
            except Exception as e:
                QMessageBox.critical(self, "保存失败", str(e))
        else:
            path, _ = QFileDialog.getSaveFileName(
                self, "保存项目",
                (self._project.title or "新建项目") + ".mep",
                "MIDI 编辑器项目 (*.mep)"
            )
            if not path:
                return
            try:
                from core.project_io import save_project
                save_project(self._project, path)
                self._modified     = False
                self._current_file = path
                self._update_title()
                self.statusBar().showMessage(f"已保存：{path}", 3000)
            except Exception as e:
                QMessageBox.critical(self, "保存失败", str(e))

    def _on_export_wav(self) -> None:
        """导出为 WAV 文件（借助 FluidSynth 命令行渲染）"""
        import os, tempfile, subprocess, shutil
        from config import SOUNDFONT_PATH
        from core.midi.exporter import export_midi as _export_midi

        if not os.path.exists(SOUNDFONT_PATH):
            QMessageBox.warning(
                self, "导出 WAV 失败",
                f"找不到 SoundFont 文件：\n{SOUNDFONT_PATH}\n"
                "请在 config.py 中正确配置 SOUNDFONT_PATH。"
            )
            return

        # 查找 FluidSynth 可执行文件
        fs_exe = shutil.which("fluidsynth")
        if fs_exe is None:
            # 尝试项目 piano/ 目录下的 fluidsynth.exe
            candidate = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "piano", "fluidsynth.exe"
            )
            if os.path.isfile(candidate):
                fs_exe = candidate
        if fs_exe is None:
            QMessageBox.warning(
                self, "导出 WAV 失败",
                "找不到 FluidSynth 可执行文件 (fluidsynth.exe)。\n"
                "请将其加入 PATH，或放在 piano/ 目录下。"
            )
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "导出 WAV",
            (self._project.title or "新建项目") + ".wav",
            "WAV 音频 (*.wav)"
        )
        if not path:
            return

        dlg = QProgressDialog("正在渲染 WAV，请稍候…", None, 0, 0, self)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setCancelButton(None)
        dlg.setMinimumWidth(360)
        dlg.show()

        try:
            tmp_fd, tmp_mid = tempfile.mkstemp(suffix=".mid")
            os.close(tmp_fd)
            _export_midi(self._project, tmp_mid)
            result = subprocess.run(
                [fs_exe, "-ni", "-F", path, "-r", "44100", SOUNDFONT_PATH, tmp_mid],
                capture_output=True, timeout=300
            )
            dlg.close()
            os.unlink(tmp_mid)
            if result.returncode != 0:
                err = result.stderr.decode(errors="replace")
                QMessageBox.critical(self, "导出 WAV 失败",
                                     f"FluidSynth 返回错误：\n{err[:600]}")
            else:
                self.statusBar().showMessage(f"WAV 已导出：{path}", 4000)
        except subprocess.TimeoutExpired:
            dlg.close()
            QMessageBox.critical(self, "导出 WAV 失败", "FluidSynth 渲染超时（>5 分钟）。")
        except Exception as e:
            dlg.close()
            QMessageBox.critical(self, "导出 WAV 失败", str(e))

    def _on_quantize(self) -> None:
        """量化所选音符到当前吸附精度"""
        grid = self._snap_combo.currentData()
        self._piano_roll.quantize_selected(grid)
        self.statusBar().showMessage(
            f"量化完成（精度 {self._snap_combo.currentText()}）", 2000
        )

    def _on_export_midi(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 MIDI", self._project.title + ".mid", "MIDI 文件 (*.mid)"
        )
        if not path:
            return
        try:
            export_midi(self._project, path)
            self._modified     = False
            self._current_file = path
            self._update_title()
            self.statusBar().showMessage(f"已保存：{path}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出 MIDI 失败：\n{e}")

    def _on_import_image(self, score_type: str) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择乐谱图片", "",
            "图片 / PDF (*.png *.jpg *.jpeg *.bmp *.pdf)"
        )
        if not path:
            return

        # ── 选页（仅 PDF 且多页时弹框）────────────────────────
        page_from, page_to = 1, -1  # 默认：第1页到最后一页
        if path.lower().endswith('.pdf'):
            try:
                import fitz
                doc = fitz.open(path)
                total = len(doc)
                doc.close()
                if total > 1:
                    page_dlg = _PageRangeDialog(total, self)
                    if not page_dlg.exec():
                        return  # 用户取消
                    page_from = page_dlg.page_from
                    page_to   = page_dlg.page_to
                # 单页 PDF：跳过对话框，page_from=1, page_to=1
                else:
                    page_to = 1
            except ImportError:
                pass  # 缺少 PyMuPDF，后续识别时再报错

        if score_type == "jianpu":
            dlg = QProgressDialog(
                "正在识别简谱图片，请稍候…\n（首次运行需下载模型，可能需要数分钟）",
                None, 0, 0, self)
            dlg.setWindowModality(Qt.WindowModality.WindowModal)
            dlg.setCancelButton(None)
            dlg.setMinimumWidth(400)
            dlg.show()

            self._omr_thread = QThread()
            self._omr_worker = OmrWorker(path, "jianpu", page_from, page_to)
            self._omr_worker.moveToThread(self._omr_thread)
            self._omr_thread.started.connect(self._omr_worker.run)
            self._omr_worker.finished.connect(lambda tracks, ts, bpm, ks, p=path:
                                    self._on_omr_done(tracks, ts, bpm, ks, dlg, p))
            self._omr_worker.error.connect(lambda msg: self._on_omr_error(msg, dlg))
            self._omr_worker.finished.connect(self._omr_thread.quit)
            self._omr_worker.error.connect(self._omr_thread.quit)
            self._omr_thread.start()
        else:
            from core.omr.staff_omr import _audiveris_available
            engine_hint = "Audiveris" if _audiveris_available() else "oemer（精度较低，建议安装 Audiveris）"
            dlg = QProgressDialog(
                f"正在识别五线谱，请稍候…\n当前引擎：{engine_hint}",
                None, 0, 0, self)
            dlg.setWindowModality(Qt.WindowModality.WindowModal)
            dlg.setCancelButton(None)
            dlg.show()

            self._omr_thread = QThread()
            self._omr_worker = OmrWorker(path, score_type, page_from, page_to)
            self._omr_worker.moveToThread(self._omr_thread)
            self._omr_thread.started.connect(self._omr_worker.run)
            self._omr_worker.finished.connect(lambda tracks, ts, bpm, ks, p=path:
                                    self._on_omr_done(tracks, ts, bpm, ks, dlg, p))
            self._omr_worker.error.connect(lambda msg: self._on_omr_error(msg, dlg))
            self._omr_worker.finished.connect(self._omr_thread.quit)
            self._omr_worker.error.connect(self._omr_thread.quit)
            self._omr_thread.start()

    # ── OMR 回调 ──────────────────────────────────────────────

    def _on_omr_done(self, tracks, time_sig, bpm, key_sharps, dlg, source_path: str = "") -> None:
        dlg.close()
        if not tracks:
            QMessageBox.warning(self, "识别结果", "未识别到任何音符，请检查图片质量或手动编辑。")
            return
        self._import_tracks(tracks, time_sig, bpm, key_sharps, source_path=source_path)

    def _on_omr_error(self, msg: str, dlg) -> None:
        dlg.close()
        # 识别失败时重置 OCR 引擎，防止下次识别因引擎状态损坏而无音符
        try:
            from core.omr.jianpu_omr import reset_ocr_engine
            reset_ocr_engine()
        except Exception:
            pass
        QMessageBox.critical(self, "识别失败",
                             f"OMR 识别过程中发生错误：\n{msg}\n\n"
                             "请确认依赖库已正确安装（见 setup_env.bat）。")

    def _on_jianpu_ocr_done(self, text: str, dlg) -> None:
        """OCR 完成 -> 弹预览对话框"""
        # 先断开 canceled，防止 close() 误触发手动输入框
        try:
            dlg.canceled.disconnect()
        except Exception:
            pass
        dlg.close()
        preview = OmrPreviewDialog(text, title="简谱识别预览 - 请检查并修正文本", parent=self)
        if preview.exec() and preview.tracks:
            self._import_tracks(preview.tracks, preview.time_sig, preview.bpm)

    def _on_input_jianpu_manual(self) -> None:
        """手动输入简谱（不需要图片）"""
        preview = OmrPreviewDialog("", title="手动输入简谱", parent=self)
        if preview.exec() and preview.tracks:
            self._import_tracks(preview.tracks, preview.time_sig, preview.bpm)

    def _import_tracks(self, tracks, time_sig, bpm, key_sharps: int = 0,
                       source_path: str = "") -> None:
        """将轨道列表追加到当前项目（新内容从提示线位置开始）"""
        import os as _os
        from core.note_model import sharps_to_key_name
        is_first_import = not bool(self._project.tracks)  # 首次导入才覆盖 BPM

        # 按来源文件名重命名轨道，标注上/下声部
        if source_path:
            base = _os.path.splitext(_os.path.basename(source_path))[0]
            _SUFFIX = ["上声部", "下声部"]
            for i, t in enumerate(tracks):
                if len(tracks) == 1:
                    t.name = base
                elif i < len(_SUFFIX):
                    t.name = f"{base} - {_SUFFIX[i]}"
                else:
                    t.name = f"{base} - 声部{i + 1}"
        offset = self._play_start_beat   # 从当前提示线处拼接
        for track in tracks:
            if offset > 0:
                for note in track.notes:
                    note.start_beat += offset
            self._project.tracks.append(track)
        if is_first_import:
            self._project.tempo    = bpm
            self._project.time_sig = time_sig
            self._project.key_sig  = key_sharps
            self._bpm_spin.setValue(bpm)
            self._key_label.setText(sharps_to_key_name(key_sharps))
        else:
            # 追加导入时只更新调号显示，保留用户已设置的 BPM 和拍号
            self._key_label.setText(sharps_to_key_name(key_sharps))
        self._keyboard_input.set_key_sig(self._project.key_sig)
        self._piano_roll.set_project(self._project, reset_view=False)  # 保持当前视图位置
        self._track_panel.set_project(self._project)
        self._audio_player.load_project(self._project)
        self._modified = True
        self._update_title()
        self.statusBar().showMessage(f"导入完成，共 {len(tracks)} 条轨道", 4000)

    # ── 吸附模式 ──────────────────────────────────────────────

    def _toggle_follow_shortcut(self) -> None:
        """F 键切换播放跟随"""
        checked = not self._act_follow.isChecked()
        self._act_follow.setChecked(checked)
        self._piano_roll.set_follow_playback(checked)
        self.statusBar().showMessage(f"播放跟随{'开启' if checked else '关闭'}", 2000)

    def _toggle_snap_shortcut(self) -> None:
        """T 键切换吸附开关"""
        self._act_snap.setChecked(not self._act_snap.isChecked())
        self._on_snap_toggle()

    def _on_snap_toggle(self) -> None:
        enabled = self._act_snap.isChecked()
        grid = self._snap_combo.currentData()
        self._piano_roll.set_snap(enabled, grid)
        self.statusBar().showMessage(
            f"吸附{'开启' if enabled else '关闭'}（精度 {self._snap_combo.currentText()}）", 2000
        )

    def _on_snap_grid_changed(self) -> None:
        grid = self._snap_combo.currentData()
        self._piano_roll.set_snap(self._act_snap.isChecked(), grid)

    def _toggle_metronome_shortcut(self) -> None:
        self._act_metronome.setChecked(not self._act_metronome.isChecked())
        self._on_metronome_toggle()

    def _on_metronome_toggle(self) -> None:
        enabled = self._act_metronome.isChecked()
        self._audio_player.metronome_enabled = enabled
        self.statusBar().showMessage(f"节拍器{'开启' if enabled else '关闭'}", 2000)

    # ── 编辑模式切换 ──────────────────────────────────────────

    def _set_mode(self, mode: EditMode) -> None:
        self._piano_roll.set_mode(mode)
        self._act_pen.setChecked(mode == EditMode.PEN)
        self._act_select.setChecked(mode == EditMode.SELECT)
        self._act_erase.setChecked(mode == EditMode.ERASE)
        mode_names = {EditMode.PEN: "铅笔", EditMode.SELECT: "选择", EditMode.ERASE: "橡皮"}
        self.statusBar().showMessage(f"当前模式：{mode_names[mode]}", 2000)

    # ── 播放控制 ──────────────────────────────────────────────

    def _on_play_pause(self) -> None:
        if self._audio_player.is_playing:
            self._audio_player.pause()
            self._btn_play.setText("▶")
        elif self._return_timer.isActive():
            # 倒带动画中再次点击：跳过动画，立即执行回调（开始播放）
            self._return_timer.stop()
            self._piano_roll.set_cursor_beat(self._return_anim_to, follow=True)
            cb, self._return_anim_on_complete = self._return_anim_on_complete, None
            if cb:
                cb()
        else:
            if self._btn_restart_mode.isChecked() and self._last_beat > self._play_start_beat + 0.1:
                # 重播模式：先倒带动画回到参考线，动画结束后再开始播放
                self._audio_player.seek(self._play_start_beat)  # 预设音频位置
                self._start_return_animation(
                    self._last_beat, self._play_start_beat,
                    on_complete=self._audio_player.play,
                )
                self._btn_play.setText("⏸")
            else:
                # 继续模式，或光标已在参考线附近：直接播放
                if self._btn_restart_mode.isChecked():
                    self._audio_player.seek(self._play_start_beat)
                    self._piano_roll.set_cursor_beat(self._play_start_beat)
                self._audio_player.play()
                self._btn_play.setText("⏸")

    def _on_stop(self) -> None:
        """停止并回到第 0 拍，同时重置起点"""
        self._return_timer.stop()    # 取消回退动画
        self._play_start_beat = 0.0
        self._audio_player.stop()
        self._btn_play.setText("▶")
        self._piano_roll.set_cursor_beat(0.0)

    def _on_rewind(self) -> None:
        """回到播放起点（点击时间轴所设的位置，默认第 0 拍）"""
        self._return_timer.stop()    # 取消回退动画
        self._audio_player.seek(self._play_start_beat)
        self._audio_player.pause()   # 确保停在起点
        self._btn_play.setText("▶")
        self._piano_roll.set_cursor_beat(self._play_start_beat)

    def _on_beat_clicked(self, beat: float) -> None:
        """点击时间轴：跳转位置，并将此处设为播放起点"""
        self._play_start_beat = beat
        self._audio_player.seek(beat)
        self._piano_roll.set_cursor_beat(beat)

    def _on_beat_update(self, beat: float) -> None:
        """音频播放线程回调（非 UI 线程）-> 写入队列，由主线程定时器消费"""
        self._beat_queue.put(beat)

    def _drain_beat_queue(self) -> None:
        """主线程定时器：取队列中最新的一帧，避免积压"""
        last            = None
        stopped         = False
        ended_naturally = False
        try:
            while True:
                val = self._beat_queue.get_nowait()
                if isinstance(val, tuple) and val[0] == "stopped":
                    stopped         = True
                    ended_naturally = val[1]
                else:
                    last = val
        except queue.Empty:
            pass
        if last is not None:
            self._last_beat = last
            self._update_beat_ui(last)
        if stopped:
            self._btn_play.setText("▶")
            if ended_naturally:
                # 自然播完：光标回到起点（重播模式）或第 0 拍
                end_beat = self._play_start_beat if self._btn_restart_mode.isChecked() else 0.0
                self._piano_roll.set_cursor_beat(end_beat)
            # 若是暂停（ended_naturally=False），光标保持在当前位置，不做任何操作

    def _start_return_animation(self, from_beat: float, to_beat: float, on_complete=None) -> None:
        """倒带动画：光标从 from_beat 平滑滑回 to_beat，结束后执行 on_complete"""
        DURATION_MS = 600
        self._return_anim_from        = from_beat
        self._return_anim_to          = to_beat
        self._return_anim_step        = 0
        self._return_anim_steps       = max(1, DURATION_MS // self._return_timer.interval())
        self._return_anim_on_complete = on_complete
        self._return_timer.start()

    def _on_return_anim_tick(self) -> None:
        """定时器每帧：推进倒带动画（smooth-step 先快后慢）"""
        self._return_anim_step += 1
        progress = self._return_anim_step / self._return_anim_steps

        if progress >= 1.0:
            self._return_timer.stop()
            self._piano_roll.set_cursor_beat(self._return_anim_to, follow=True)
            cb, self._return_anim_on_complete = self._return_anim_on_complete, None
            if cb:
                cb()
            return

        t    = progress * progress * (3.0 - 2.0 * progress)  # smooth-step
        beat = self._return_anim_from + t * (self._return_anim_to - self._return_anim_from)
        self._piano_roll.set_cursor_beat(beat, follow=True)

    @staticmethod
    def _beats_to_ms(beat: float, project) -> int:
        """将拍位转换为毫秒，考虑分段变速"""
        segments = [(0.0, project.tempo)]
        for tc in sorted(project.tempo_changes, key=lambda t: t.beat):
            segments.append((tc.beat, tc.bpm))

        total_ms    = 0.0
        prev_beat   = 0.0
        prev_bpm    = project.tempo
        for seg_beat, seg_bpm in segments[1:] + [(beat, None)]:
            end = min(seg_beat, beat)
            if end > prev_beat:
                total_ms += (end - prev_beat) / (prev_bpm / 60.0) * 1000.0
            prev_beat = seg_beat
            prev_bpm  = seg_bpm
            if seg_beat >= beat:
                break
        return int(total_ms)

    def _update_beat_ui(self, beat: float) -> None:
        self._piano_roll.set_cursor_beat(beat, follow=True)   # 播放时触发自动跟随
        bpm     = self._project.beats_per_measure()
        measure = int(beat // bpm) + 1
        beat_in = beat % bpm
        ms      = self._beats_to_ms(beat, self._project)
        h, rem  = divmod(ms, 3_600_000)
        m, rem  = divmod(rem, 60_000)
        s, ms_r = divmod(rem, 1_000)
        if h > 0:
            time_str = f"{h:02d}:{m:02d}:{s:02d}.{ms_r:03d}"
        else:
            time_str = f"{m:02d}:{s:02d}.{ms_r:03d}"
        self._pos_label.setText(f"{measure} : {beat_in:.0f}")
        self._pos_label.setToolTip(f"小节 {measure} 第 {beat_in:.1f} 拍  |  {time_str}")

    def _on_playback_stopped(self, ended_naturally: bool) -> None:
        self._beat_queue.put(("stopped", ended_naturally))

    def _on_tempo_change_requested(self, beat: float) -> None:
        """右键标尺：添加/编辑/删除 BPM 标记"""
        existing = next((tc for tc in self._project.tempo_changes
                         if abs(tc.beat - beat) < 0.01), None)

        if existing:
            # 编辑或删除已有标记
            new_bpm, ok = QInputDialog.getInt(
                self, "编辑速度标记",
                f"第 {beat:.2f} 拍的速度（输入 0 删除此标记）:",
                existing.bpm, 0, 300
            )
            if ok:
                if new_bpm == 0:
                    self._project.remove_tempo_change(beat)
                    self.statusBar().showMessage(f"已删除第 {beat:.2f} 拍的速度标记", 2000)
                else:
                    self._project.add_tempo_change(beat, new_bpm)
                    self.statusBar().showMessage(f"已更新：第 {beat:.2f} 拍 = {new_bpm} BPM", 2000)
        else:
            # 新增标记
            cur_bpm = self._project.tempo_at_beat(beat)
            new_bpm, ok = QInputDialog.getInt(
                self, "添加速度标记",
                f"在第 {beat:.2f} 拍处设置速度（BPM）:",
                cur_bpm, 20, 300
            )
            if ok:
                self._project.add_tempo_change(beat, new_bpm)
                self.statusBar().showMessage(f"已添加：第 {beat:.2f} 拍 = {new_bpm} BPM", 2000)

        if ok:
            self._piano_roll.viewport().update()
            self._audio_player.load_project(self._project)
            self._modified = True
            self._update_title()

    # ── 项目属性 ──────────────────────────────────────────────

    def _on_bpm_changed(self, val: int) -> None:
        self._project.tempo = val
        self._audio_player._tempo = val
        self._modified = True

    def _on_ts_changed(self, text: str) -> None:
        parts = text.split("/")
        if len(parts) == 2:
            from core.note_model import TimeSignature
            self._project.time_sig = TimeSignature(int(parts[0]), int(parts[1]))
            self._piano_roll.viewport().update()
            self._modified = True

    # ── 撤销 / 重做 ───────────────────────────────────────────

    def _snapshot(self):
        """返回当前所有轨道音符的深拷贝快照"""
        return [copy.deepcopy(t.notes) for t in self._project.tracks]

    def _restore_snapshot(self, snapshot) -> None:
        """将快照恢复到各轨道"""
        for i, track in enumerate(self._project.tracks):
            if i < len(snapshot):
                track.notes = snapshot[i]
        self._piano_roll.viewport().update()

    def _save_undo_snapshot(self) -> None:
        self._undo_stack.append(self._snapshot())
        if len(self._undo_stack) > self._MAX_UNDO:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _undo(self) -> None:
        if not self._undo_stack:
            self.statusBar().showMessage("没有可撤销的操作", 2000)
            return
        self._redo_stack.append(self._snapshot())
        self._restore_snapshot(self._undo_stack.pop())
        self._modified = True
        self._update_title()
        self.statusBar().showMessage("已撤销", 1500)

    def _redo(self) -> None:
        if not self._redo_stack:
            self.statusBar().showMessage("没有可重做的操作", 2000)
            return
        self._undo_stack.append(self._snapshot())
        self._restore_snapshot(self._redo_stack.pop())
        self._modified = True
        self._update_title()
        self.statusBar().showMessage("已重做", 1500)

    def _on_track_mute_changed(self, track) -> None:
        """轨道属性变化（静音/颜色/改名）时调用；静音时立即切断该通道发音"""
        self._piano_roll.viewport().update()
        if track.muted and self._audio_player.is_playing:
            try:
                channel = self._project.tracks.index(track) % 16
                self._audio_player.silence_channel(channel)
            except ValueError:
                pass

    def _on_notes_changed(self) -> None:
        self._modified = True
        self._update_title()

    def _on_track_changed(self) -> None:
        self._audio_player.load_project(self._project)
        self._piano_roll.set_project(self._project)
        self._modified = True
        self._update_title()

    # ── 标题 ──────────────────────────────────────────────────

    def _update_title(self) -> None:
        mark = " *" if self._modified else ""
        self.setWindowTitle(f"{self._project.title}{mark} - 乐谱 MIDI 编辑器")

    # ── 键盘弹奏 ──────────────────────────────────────────────

    def _on_keyboard_note(self, pitch: int, duration: float) -> None:
        self._piano_roll.insert_note_at_cursor(pitch, duration)

    def _on_keyboard_active_changed(self, active: bool) -> None:
        """弹奏模式开关：同步禁用/恢复冲突快捷键，通知 piano roll。"""
        for sc in self._kb_conflict_scs:
            sc.setEnabled(not active)

        if active:
            self._piano_roll.set_note_input_active(True, self._play_start_beat)
            self._keyboard_input.set_key_sig(self._project.key_sig)
            self.statusBar().showMessage(
                "⌨ 键盘弹奏已开启 — d/r/m/f/s/l/x 输入音符  |  "
                "1–5 时值  |  . 附点  |  ↑↓ 八度  |  Z 休止  |  I / Esc 退出",
                6000
            )
        else:
            self._piano_roll.set_note_input_active(False)
            self.statusBar().showMessage("键盘弹奏已关闭", 2000)

    # ── 键盘快捷键 ────────────────────────────────────────────

    def keyPressEvent(self, event) -> None:
        # 键盘弹奏模式：冲突快捷键已禁用，按键从 piano roll 上浮至此，
        # 由 keyboard_input 处理；其余按键走正常逻辑。
        if self._keyboard_input.is_active:
            if self._keyboard_input.handle_key(event.key(), event.modifiers()):
                event.accept()
                return

        key = event.key()
        if key == Qt.Key.Key_Space:
            self._on_play_pause()
        elif key == Qt.Key.Key_Escape:
            self._on_stop()
        elif key == Qt.Key.Key_Home:
            self._on_rewind()
        elif key == Qt.Key.Key_1:
            self._set_mode(EditMode.PEN)
        elif key == Qt.Key.Key_2:
            self._set_mode(EditMode.SELECT)
        elif key == Qt.Key.Key_3:
            self._set_mode(EditMode.ERASE)
        else:
            super().keyPressEvent(event)

    # ── 关闭事件 ──────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        if self._modified:
            ret = QMessageBox.question(
                self, "退出", "项目尚未保存，确定退出？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if ret != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._audio_player.close()
        event.accept()
