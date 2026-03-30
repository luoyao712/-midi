"""
键盘弹奏面板
————————————————————————————————————————
· 字母键 d r m f s l x  →  当前调号的 do re mi fa sol la xi
· 数字键 1–5            →  时值（十六 / 八分 / 四分 / 二分 / 全音）
· 句号 .                →  附点切换（时值 × 1.5）
· ↑ / ↓                →  八度上移 / 下移
· Z                    →  休止符（仅推进光标，不插入音符）
· Escape / I           →  退出键盘输入模式
————————————————————————————————————————
设计参考 MuseScore Musical Typing：
  - 激活后键盘事件由 MainWindow.eventFilter 在 QShortcut 之前拦截，
    避免与已有快捷键冲突。
  - 每次插入音符后自动推进"输入光标"，钢琴卷帘跟随高亮。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui  import QFont

# ─── 乐理常量 ────────────────────────────────────────────────

# 大调音阶各级相对主音的半音数
_MAJOR_INTERVALS = [0, 2, 4, 5, 7, 9, 11]

# 调号（升降号数）→ 主音音级 (0=C … 11=B)
_KEY_SIG_ROOT: dict[int, int] = {
     0:  0,   # C
     1:  7,   # G
     2:  2,   # D
     3:  9,   # A
     4:  4,   # E
     5: 11,   # B
     6:  6,   # F#
     7:  1,   # C#
    -1:  5,   # F
    -2: 10,   # Bb
    -3:  3,   # Eb
    -4:  8,   # Ab
    -5:  1,   # Db
    -6:  6,   # Gb
    -7: 11,   # Cb
}

# 唱名顺序及对应键位
_SOLFEGE_NAMES = ["do", "re", "mi", "fa", "sol", "la", "xi"]
_SOLFEGE_KEYS  = "drmfslx"          # 键位字母（小写）

# 时值选项：(显示名, beats, 键位提示)
_DURATIONS = [
    ("十六",  0.25,  "1"),
    ("八分",  0.50,  "2"),
    ("四分",  1.00,  "3"),
    ("二分",  2.00,  "4"),
    ("全音",  4.00,  "5"),
]

_NOTE_NAMES_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_NOTE_NAMES_FLAT  = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]


def _note_name(pitch: int, use_flat: bool = False) -> str:
    names = _NOTE_NAMES_FLAT if use_flat else _NOTE_NAMES_SHARP
    return f"{names[pitch % 12]}{pitch // 12 - 1}"


# ─── 主控件 ──────────────────────────────────────────────────

class KeyboardInputWidget(QWidget):
    """
    键盘弹奏面板。

    信号
    ----
    note_input(pitch: int, duration: float)
        用户按下音符键，发出 MIDI 音高和时值（拍）。
    rest_input(duration: float)
        用户按下 Z（休止符），发出时值。
    active_changed(bool)
        输入模式开关变化（True=开启）。
    """

    note_input    = pyqtSignal(int, float)
    rest_input    = pyqtSignal(float)
    active_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active  = False
        self._octave  = 4       # do 所在八度（C4 = MIDI 60）
        self._dur_idx = 2       # 默认四分音符（1.0 拍）
        self._dotted  = False
        self._key_sig = 0       # 由主窗口同步

        self._build_ui()
        # 默认折叠（仅在 toggle 后显示）
        self.setMaximumHeight(0)
        self.setVisible(True)
        self._anim_collapsed = True

    # ── 公开接口 ──────────────────────────────────────────────

    def set_key_sig(self, key_sig: int) -> None:
        """同步调号，刷新面板上的音名标签。"""
        self._key_sig = key_sig
        self._refresh_note_labels()

    def toggle_active(self) -> None:
        """开启 / 关闭键盘弹奏模式（由 I 键或按钮触发）。"""
        self._active = not self._active
        self._btn_toggle.setChecked(self._active)
        self._update_toggle_style()
        if self._active:
            self.setMaximumHeight(16777215)
        else:
            self.setMaximumHeight(0)
        self.active_changed.emit(self._active)

    @property
    def is_active(self) -> bool:
        return self._active

    def get_duration(self) -> float:
        """当前时值（拍），含附点。"""
        beats = _DURATIONS[self._dur_idx][1]
        return round(beats * 1.5, 6) if self._dotted else beats

    def handle_key(self, key: int, modifiers: Qt.KeyboardModifier) -> bool:
        """
        处理来自 MainWindow.eventFilter 的按键。
        返回 True 表示已消费（阻止后续处理）。
        """
        if not self._active:
            return False

        # Escape → 退出
        if key == Qt.Key.Key_Escape:
            self.toggle_active()
            return True

        # 数字 1–5 → 时值
        if Qt.Key.Key_1 <= key <= Qt.Key.Key_5:
            self._set_dur(key - Qt.Key.Key_1)
            return True

        # . → 附点
        if key == Qt.Key.Key_Period:
            self._set_dotted(not self._dotted)
            return True

        # ↑ ↓ → 八度
        if key == Qt.Key.Key_Up:
            self._set_octave(self._octave + 1)
            return True
        if key == Qt.Key.Key_Down:
            self._set_octave(self._octave - 1)
            return True

        # Z → 休止符
        if key == Qt.Key.Key_Z:
            self.rest_input.emit(self.get_duration())
            self._flash_key_frame(7)   # 最后一格（休止）
            return True

        # d r m f s l x → 音符
        for i, ch in enumerate(_SOLFEGE_KEYS):
            if key == getattr(Qt.Key, f"Key_{ch.upper()}"):
                pitch = self._degree_to_pitch(i)
                self.note_input.emit(pitch, self.get_duration())
                self._flash_key_frame(i)
                return True

        return False

    # ── 乐理计算 ──────────────────────────────────────────────

    def _degree_to_pitch(self, degree: int) -> int:
        """唱名级（0=do … 6=xi）→ MIDI 音高。"""
        root_pc  = _KEY_SIG_ROOT.get(self._key_sig, 0)
        interval = _MAJOR_INTERVALS[degree]
        return (self._octave + 1) * 12 + root_pc + interval

    def _note_label_text(self, degree: int) -> str:
        pitch    = self._degree_to_pitch(degree)
        use_flat = self._key_sig < 0
        return _note_name(pitch, use_flat)

    def _refresh_note_labels(self) -> None:
        for i, lbl in enumerate(self._note_name_labels):
            lbl.setText(self._note_label_text(i))

    # ── UI 内部操作 ────────────────────────────────────────────

    def _set_dur(self, idx: int) -> None:
        self._dur_idx = idx
        for i, btn in enumerate(self._dur_btns):
            btn.setChecked(i == idx)

    def _set_dotted(self, dotted: bool) -> None:
        self._dotted = dotted
        self._btn_dot.setChecked(dotted)

    def _set_octave(self, oct_: int) -> None:
        self._octave = max(0, min(8, oct_))
        self._lbl_oct.setText(str(self._octave))
        self._refresh_note_labels()

    def _flash_key_frame(self, idx: int) -> None:
        """按下音符键时短暂高亮对应方块（视觉反馈）。"""
        if 0 <= idx < len(self._key_frames):
            frame = self._key_frames[idx]
            orig  = frame.styleSheet()
            frame.setStyleSheet(orig.replace("#1E1E2E", "#2A4A2A").replace("#222235", "#2A4A2A"))
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(80, lambda f=frame, s=orig: f.setStyleSheet(s))

    def _update_toggle_style(self) -> None:
        if self._active:
            self._btn_toggle.setText("关闭 (I)")
            self._btn_toggle.setStyleSheet(
                "QPushButton{background:#1A4FA0;color:#FFF;"
                "border:1px solid #4488FF;border-radius:4px;padding:2px 10px;}"
                "QPushButton:hover{background:#1A5FBF;}"
            )
        else:
            self._btn_toggle.setText("开启 (I)")
            self._btn_toggle.setStyleSheet(
                "QPushButton{background:#252538;color:#CCCCDD;"
                "border:1px solid #3A3A5A;border-radius:4px;padding:2px 10px;}"
                "QPushButton:hover{background:#333350;}"
            )

    # ── UI 构建 ───────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setStyleSheet("QWidget{background:#16162A;color:#CCCCDD;}")
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 6, 10, 6)
        root.setSpacing(5)

        # ── 第一行：标题 + 时值 + 八度 + 开关 ─────────────────
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        title = QLabel("⌨  键盘弹奏")
        title.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.Bold))
        title.setStyleSheet("color:#AABBFF;")
        row1.addWidget(title)

        sep_v = QFrame()
        sep_v.setFrameShape(QFrame.Shape.VLine)
        sep_v.setStyleSheet("color:#2A2A4A;")
        row1.addWidget(sep_v)

        # 时值按钮
        row1.addWidget(self._lbl("时值:"))
        self._dur_btns: list[QPushButton] = []
        for i, (name, _, hint) in enumerate(_DURATIONS):
            btn = QPushButton(f"{name}\n[{hint}]")
            btn.setCheckable(True)
            btn.setFixedSize(52, 36)
            btn.setStyleSheet(self._dur_style())
            btn.setToolTip(f"{name}音符（键盘 {hint}）")
            btn.clicked.connect(lambda _, ix=i: self._set_dur(ix))
            self._dur_btns.append(btn)
            row1.addWidget(btn)

        self._btn_dot = QPushButton("·附点\n[.]")
        self._btn_dot.setCheckable(True)
        self._btn_dot.setFixedSize(52, 36)
        self._btn_dot.setStyleSheet(self._dur_style())
        self._btn_dot.setToolTip("附点（键盘 .）：时值 × 1.5")
        self._btn_dot.clicked.connect(lambda checked: self._set_dotted(checked))
        row1.addWidget(self._btn_dot)

        sep_v2 = QFrame()
        sep_v2.setFrameShape(QFrame.Shape.VLine)
        sep_v2.setStyleSheet("color:#2A2A4A;")
        row1.addWidget(sep_v2)

        # 八度控制
        row1.addWidget(self._lbl("八度:"))
        btn_down = QPushButton("▼")
        btn_down.setFixedSize(26, 26)
        btn_down.setStyleSheet(self._small_btn_style())
        btn_down.setToolTip("八度降低 (↓)")
        btn_down.clicked.connect(lambda: self._set_octave(self._octave - 1))
        row1.addWidget(btn_down)

        self._lbl_oct = QLabel("4")
        self._lbl_oct.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_oct.setFixedWidth(22)
        self._lbl_oct.setStyleSheet(
            "color:#FFD080;font-size:14px;font-weight:bold;"
        )
        row1.addWidget(self._lbl_oct)

        btn_up = QPushButton("▲")
        btn_up.setFixedSize(26, 26)
        btn_up.setStyleSheet(self._small_btn_style())
        btn_up.setToolTip("八度升高 (↑)")
        btn_up.clicked.connect(lambda: self._set_octave(self._octave + 1))
        row1.addWidget(btn_up)

        row1.addStretch()

        hint_lbl = self._lbl(
            "↑↓ 八度  |  1–5 时值  |  . 附点  |  Z 休止  |  Esc 退出", "#445566"
        )
        row1.addWidget(hint_lbl)

        self._btn_toggle = QPushButton("开启 (I)")
        self._btn_toggle.setCheckable(True)
        self._update_toggle_style()
        self._btn_toggle.clicked.connect(
            lambda _: (setattr(self, "_active", not self._active) or True)
            and self._finalize_toggle()
        )
        row1.addWidget(self._btn_toggle)
        root.addLayout(row1)

        # ── 第二行：音符按键图 ─────────────────────────────────
        row2 = QHBoxLayout()
        row2.setSpacing(5)

        self._key_frames:      list[QFrame] = []
        self._note_name_labels: list[QLabel] = []

        for i in range(len(_SOLFEGE_KEYS)):
            frame, name_lbl = self._make_key_frame(
                key_ch   = _SOLFEGE_KEYS[i].upper(),
                sol_name = _SOLFEGE_NAMES[i],
                note_txt = self._note_label_text(i),
                accent   = i in (3, 6),  # fa、xi 半音突出
            )
            self._key_frames.append(frame)
            self._note_name_labels.append(name_lbl)
            row2.addWidget(frame)

        # 休止符（Z）
        rest_frame, _ = self._make_key_frame("Z", "rest", "休止", accent=False, is_rest=True)
        self._key_frames.append(rest_frame)
        row2.addWidget(rest_frame)

        row2.addStretch()
        root.addLayout(row2)

        # 初始化时值选中
        self._set_dur(self._dur_idx)

    def _finalize_toggle(self) -> None:
        """按钮点击时的完整切换（含展开/折叠）。"""
        self._btn_toggle.setChecked(self._active)
        self._update_toggle_style()
        if self._active:
            self.setMaximumHeight(16777215)
        else:
            self.setMaximumHeight(0)
        self.active_changed.emit(self._active)

    def _make_key_frame(
        self,
        key_ch: str,
        sol_name: str,
        note_txt: str,
        accent: bool = False,
        is_rest: bool = False,
    ) -> tuple[QFrame, QLabel]:
        """创建一个音符方块（键位 / 唱名 / 音名）。"""
        bg  = "#1A1A28" if is_rest else "#1E1E2E"
        bdr = "#2A2A4A" if is_rest else "#3A3A5A"
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setFixedSize(62, 58)
        frame.setStyleSheet(
            f"QFrame{{background:{bg};border:1px solid {bdr};"
            "border-radius:6px;}}"
        )

        fl = QVBoxLayout(frame)
        fl.setContentsMargins(4, 4, 4, 4)
        fl.setSpacing(1)

        # 键位字母
        kl = QLabel(key_ch)
        kl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        kl.setStyleSheet(
            "color:#FFD080;font-family:Consolas,monospace;"
            "font-size:16px;font-weight:bold;background:none;border:none;"
        )
        fl.addWidget(kl)

        # 唱名
        sl = QLabel(sol_name)
        sl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sl.setStyleSheet(
            "color:#7799BB;font-size:9px;background:none;border:none;"
        )
        fl.addWidget(sl)

        # 音名（动态更新）
        nl = QLabel(note_txt)
        nl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nl.setStyleSheet(
            "color:#CCCCDD;font-size:10px;background:none;border:none;"
        )
        fl.addWidget(nl)

        return frame, nl

    # ── 样式字符串 ────────────────────────────────────────────

    @staticmethod
    def _dur_style() -> str:
        return (
            "QPushButton{background:#1E1E2E;color:#AABBCC;"
            "border:1px solid #3A3A5A;border-radius:4px;"
            "font-size:9px;line-height:1.2;}"
            "QPushButton:checked{background:#1A4FA0;color:#FFF;"
            "border:1px solid #4488FF;}"
            "QPushButton:hover{background:#252540;}"
        )

    @staticmethod
    def _small_btn_style() -> str:
        return (
            "QPushButton{background:#1E1E2E;color:#AABBCC;"
            "border:1px solid #3A3A5A;border-radius:3px;font-size:11px;}"
            "QPushButton:hover{background:#252540;}"
        )

    @staticmethod
    def _lbl(text: str, color: str = "#8888AA") -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color:{color};font-size:10px;")
        return lbl
