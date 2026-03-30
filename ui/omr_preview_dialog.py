"""
OMR 识别结果预览 / 手动输入对话框

用途：
  1. 简谱图片 OCR 后，展示识别文本供用户修正，再解析导入
  2. 手动输入简谱文字，直接解析导入（不需要图片）

简谱格式说明（显示在对话框中帮助用户输入）：
  1=C 4/4          调号 调式
  1 2 3 4 |        音符 1-7，0=休止符，| 小节线
  ^1 _2            ^ 高八度，_ 低八度
  #3 b7            # 升，b 降
  1_ 1__           一条下划线=八分，两条=十六分
  1.               附点（时值×1.5）
  1 -              延音线（延长一拍）
"""
from __future__ import annotations
from typing import Optional, List, Tuple

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QSplitter, QFrame, QWidget, QScrollArea,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui  import QFont, QColor, QPalette

from core.note_model import Track, TimeSignature


_DARK = "#1E1E2E"
_MID  = "#2A2A3E"
_BORDER = "#3A3A5A"
_TEXT   = "#CCCCDD"
_ACCENT = "#4A9EFF"

_HELP_TEXT = """\
── 简谱格式说明 ──────────────────
调号：1=C  1=D  1=G  ...
拍号：4/4  3/4  6/8  ...
速度：J=120  ♩=90

音符：1 2 3 4 5 6 7（do~si）
      0 = 休止符
      - = 延音（延长一拍）
      | = 小节线（可省略）

八度：^1 高八度   ^^1 高两个八度
      _1 低八度   __1 低两个八度

升降：#3 升三度   b7 降七度

时值：无标记 = 四分音符（1拍）
      1_  = 八分音符（0.5拍）
      1__ = 十六分音符（0.25拍）
      1.  = 附点四分（1.5拍）

── 示例 ────────────────────────
1=C 4/4 J=120
1 2 3 4 | 5 6 ^1 - |
_1 _2 _3 _4 | 3 2 1 0 |
"""


# ── 后台解析线程 ──────────────────────────────────────────────

class _ParseWorker(QObject):
    done  = pyqtSignal(list, object, int)   # tracks, time_sig, bpm
    error = pyqtSignal(str)

    def __init__(self, text: str):
        super().__init__()
        self.text = text

    def run(self) -> None:
        try:
            from core.omr.jianpu_omr import parse_jianpu_text
            tracks, time_sig, bpm = parse_jianpu_text(self.text)
            self.done.emit(tracks, time_sig, bpm)
        except Exception as e:
            self.error.emit(str(e))


# ── 对话框主体 ────────────────────────────────────────────────

class OmrPreviewDialog(QDialog):
    """
    Parameters
    ----------
    initial_text : str
        OCR 识别到的原始文本（为空则为手动输入模式）
    title : str
        对话框标题
    """

    def __init__(self, initial_text: str = "", title: str = "简谱识别预览",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(720, 520)
        self.resize(820, 580)

        self._tracks:   List[Track]     = []
        self._time_sig: TimeSignature   = TimeSignature(4, 4)
        self._bpm:      int             = 120
        self._parse_thread: Optional[QThread] = None

        self._build_ui(initial_text)
        self._apply_style()

        # 若有初始文本，自动触发一次解析预览
        if initial_text.strip():
            self._on_parse()

    # ── UI 构建 ───────────────────────────────────────────────

    def _build_ui(self, initial_text: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # 标题说明
        hint = QLabel("在下方编辑简谱文本，点击「解析预览」确认音符数量，再点击「导入」")
        hint.setStyleSheet(f"color: #8888AA; font-size: 11px;")
        root.addWidget(hint)

        # 主区域：左=编辑，右=说明+预览
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(3)

        # 左：文本编辑
        left = QWidget()
        lv   = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(4)

        lv.addWidget(QLabel("简谱文本："))
        self._text_edit = QTextEdit()
        self._text_edit.setPlaceholderText(
            "在此输入或编辑简谱...\n例：\n1=C 4/4\n1 2 3 4 | 5 6 ^1 - |"
        )
        self._text_edit.setFont(QFont("Consolas", 12))
        self._text_edit.setPlainText(initial_text)
        lv.addWidget(self._text_edit, 1)

        # 解析按钮
        self._btn_parse = QPushButton("▶ 解析预览")
        self._btn_parse.clicked.connect(self._on_parse)
        lv.addWidget(self._btn_parse)

        # 解析结果提示
        self._result_label = QLabel("尚未解析")
        self._result_label.setStyleSheet("color: #6688AA; font-size: 11px;")
        lv.addWidget(self._result_label)

        splitter.addWidget(left)

        # 右：格式说明
        right = QWidget()
        rv    = QVBoxLayout(right)
        rv.setContentsMargins(4, 0, 0, 0)
        rv.setSpacing(4)
        rv.addWidget(QLabel("格式参考："))
        help_box = QTextEdit()
        help_box.setReadOnly(True)
        help_box.setPlainText(_HELP_TEXT)
        help_box.setFont(QFont("Consolas", 10))
        help_box.setStyleSheet(f"background: #16162A; color: #7788AA; border: 1px solid {_BORDER};")
        rv.addWidget(help_box, 1)
        splitter.addWidget(right)

        splitter.setSizes([480, 280])
        root.addWidget(splitter, 1)

        # 底部按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_cancel = QPushButton("取消")
        self._btn_import = QPushButton("✔ 导入")
        self._btn_import.setEnabled(False)
        self._btn_cancel.clicked.connect(self.reject)
        self._btn_import.clicked.connect(self.accept)
        btn_row.addWidget(self._btn_cancel)
        btn_row.addWidget(self._btn_import)
        root.addLayout(btn_row)

    def _apply_style(self) -> None:
        self.setStyleSheet(f"""
            QDialog    {{ background: {_DARK}; color: {_TEXT}; }}
            QLabel     {{ color: {_TEXT}; font-size: 12px; }}
            QTextEdit  {{ background: {_MID}; color: {_TEXT};
                          border: 1px solid {_BORDER}; border-radius: 4px;
                          font-size: 13px; }}
            QPushButton {{ background: #252538; color: {_TEXT};
                           border: 1px solid {_BORDER}; border-radius: 4px;
                           padding: 5px 16px; font-size: 12px; }}
            QPushButton:hover   {{ background: #333358; }}
            QPushButton:pressed {{ background: #111125; }}
            QPushButton#import_btn {{ background: #1A4FA0; color: #FFFFFF;
                                      border-color: #4488FF; font-weight: bold; }}
            QSplitter::handle {{ background: {_BORDER}; }}
        """)
        self._btn_import.setObjectName("import_btn")
        self._btn_import.setStyleSheet(
            "background: #1A4FA0; color: #FFFFFF; border: 1px solid #4488FF;"
            "border-radius: 4px; padding: 5px 20px; font-size: 12px; font-weight: bold;"
        )

    # ── 解析逻辑 ──────────────────────────────────────────────

    def _on_parse(self) -> None:
        text = self._text_edit.toPlainText().strip()
        if not text:
            self._result_label.setText("⚠ 请先输入简谱文本")
            return

        self._btn_parse.setEnabled(False)
        self._btn_import.setEnabled(False)
        self._result_label.setText("解析中...")

        self._parse_thread = QThread(self)
        self._parse_worker = _ParseWorker(text)   # 保持引用防止 GC
        worker = self._parse_worker
        worker.moveToThread(self._parse_thread)

        self._parse_thread.started.connect(worker.run)
        worker.done.connect(self._on_parse_done)
        worker.error.connect(self._on_parse_error)
        worker.done.connect(self._parse_thread.quit)
        worker.error.connect(self._parse_thread.quit)

        self._parse_thread.start()

    def _on_parse_done(self, tracks, time_sig, bpm) -> None:
        self._tracks   = tracks
        self._time_sig = time_sig
        self._bpm      = bpm
        self._btn_parse.setEnabled(True)

        total_notes = sum(len(t.notes) for t in tracks)
        if total_notes == 0:
            self._result_label.setText("⚠ 未识别到音符，请检查格式")
            self._btn_import.setEnabled(False)
        else:
            self._result_label.setText(
                f"✔ 识别到 {total_notes} 个音符，{len(tracks)} 条轨道，"
                f"拍号 {time_sig.numerator}/{time_sig.denominator}，BPM {bpm}"
            )
            self._result_label.setStyleSheet("color: #4ECDC4; font-size: 11px;")
            self._btn_import.setEnabled(True)

    def _on_parse_error(self, msg: str) -> None:
        self._btn_parse.setEnabled(True)
        self._result_label.setText(f"✘ 解析错误：{msg}")
        self._result_label.setStyleSheet("color: #FF6B6B; font-size: 11px;")

    # ── 公开结果 ──────────────────────────────────────────────

    @property
    def tracks(self) -> List[Track]:
        return self._tracks

    @property
    def time_sig(self) -> TimeSignature:
        return self._time_sig

    @property
    def bpm(self) -> int:
        return self._bpm
