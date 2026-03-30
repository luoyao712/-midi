"""
左侧钢琴键盘组件
垂直排列，与 Piano Roll 的音高轴对齐
支持随 Piano Roll 垂直滚动同步
"""
from __future__ import annotations
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore    import Qt, QRect, QSize
from PyQt6.QtGui     import QPainter, QColor, QPen, QFont, QBrush

from config import (
    NOTE_HEIGHT, PIANO_KEY_WIDTH, MIDI_MIN_PITCH, MIDI_MAX_PITCH,
    COLOR_WHITE_KEY_BG, COLOR_BLACK_KEY_BG, COLOR_C_KEY_MARK,
)
from core.note_model import is_black_key, pitch_to_name


# 白键 / 黑键颜色
_WHITE = QColor(COLOR_WHITE_KEY_BG)
_BLACK = QColor(COLOR_BLACK_KEY_BG)
_C_KEY = QColor(COLOR_C_KEY_MARK)
_LABEL = QColor(80, 80, 80)
_BLACK_LABEL = QColor(180, 180, 180)
_HOVER = QColor(200, 220, 255, 180)
_PRESS = QColor(120, 180, 255)


class PianoKeyboardWidget(QWidget):
    """
    钢琴键盘控件

    外部通过调用 set_scroll_offset(y) 使其随 Piano Roll 同步滚动。
    高亮接口：set_active_pitches([60, 64, 67]) 在播放时点亮按键。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scroll_y:      int        = 0
        self._active_pitches: set[int]  = set()
        self._hover_pitch:   int | None = None

        self.setFixedWidth(PIANO_KEY_WIDTH)
        self.setMinimumHeight(300)
        self.setMouseTracking(True)

    # ── 公开接口 ──────────────────────────────────────────────

    def set_scroll_offset(self, y: int) -> None:
        """同步 Piano Roll 的垂直滚动偏移"""
        if self._scroll_y != y:
            self._scroll_y = y
            self.update()

    def set_active_pitches(self, pitches: list[int]) -> None:
        """播放时高亮显示当前音符对应的琴键"""
        new_set = set(pitches)
        if new_set != self._active_pitches:
            self._active_pitches = new_set
            self.update()

    # ── 坐标换算 ──────────────────────────────────────────────

    def pitch_to_y(self, pitch: int) -> int:
        """音高 -> 组件内 y 坐标（顶部为高音）"""
        return (MIDI_MAX_PITCH - pitch) * NOTE_HEIGHT - self._scroll_y

    def y_to_pitch(self, y: int) -> int:
        """组件内 y 坐标 -> 音高"""
        pitch = MIDI_MAX_PITCH - (y + self._scroll_y) // NOTE_HEIGHT
        return max(MIDI_MIN_PITCH, min(MIDI_MAX_PITCH, pitch))

    # ── 鼠标事件 ──────────────────────────────────────────────

    def mouseMoveEvent(self, event) -> None:
        pitch = self.y_to_pitch(int(event.position().y()))
        if pitch != self._hover_pitch:
            self._hover_pitch = pitch
            self.update()

    def leaveEvent(self, event) -> None:
        self._hover_pitch = None
        self.update()

    # ── 绘制 ──────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        h = self.height()
        w = self.width()

        # 背景
        painter.fillRect(0, 0, w, h, QColor(30, 30, 40))

        # 遍历可见范围内的所有音高
        top_pitch    = self.y_to_pitch(0)
        bottom_pitch = self.y_to_pitch(h)

        for pitch in range(top_pitch, bottom_pitch - 1, -1):
            if not (MIDI_MIN_PITCH <= pitch <= MIDI_MAX_PITCH):
                continue
            self._draw_key(painter, pitch, w)

        # 右侧分隔线
        painter.setPen(QPen(QColor(60, 60, 80), 1))
        painter.drawLine(w - 1, 0, w - 1, h)

    def _draw_key(self, painter: QPainter, pitch: int, w: int) -> None:
        y   = self.pitch_to_y(pitch)
        bk  = is_black_key(pitch)
        note_name = pitch_to_name(pitch)

        if bk:
            # 黑键：宽度为白键的 60%，靠右
            key_w = int(w * 0.60)
            key_x = w - key_w
            rect  = QRect(key_x, y, key_w, NOTE_HEIGHT)
            color = _PRESS if pitch in self._active_pitches else _BLACK
        else:
            # 白键：全宽
            rect  = QRect(0, y, w - 1, NOTE_HEIGHT)
            if note_name.startswith('C') and not bk:
                color = _C_KEY
            else:
                color = _WHITE
            if pitch in self._active_pitches:
                color = _PRESS
            elif pitch == self._hover_pitch:
                color = _HOVER

        painter.fillRect(rect, color)

        # 白键底部分隔线
        if not bk:
            painter.setPen(QPen(QColor(160, 160, 160), 1))
            painter.drawLine(0, y + NOTE_HEIGHT - 1, w - 1, y + NOTE_HEIGHT - 1)

        # C 键标签（如 C4）
        if not bk and note_name.startswith('C'):
            painter.setPen(QPen(_LABEL, 1))
            font = QFont("Arial", 7)
            painter.setFont(font)
            painter.drawText(2, y, w - 4, NOTE_HEIGHT, Qt.AlignmentFlag.AlignVCenter, note_name)
