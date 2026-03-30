"""
Piano Roll 编辑器核心组件
使用 QAbstractScrollArea 实现，左侧钢琴键盘固定，内容区可双向滚动

功能：
  - 音符显示（按轨道颜色区分）
  - 铅笔模式：左键单击新建音符，右键删除
  - 选择模式：左键单击选中/框选，拖拽移动，右边缘拖拽调整时值
  - 播放光标显示
  - Ctrl+滚轮（内容区）：水平缩放；Ctrl+滚轮（钢琴键盘上）：垂直缩放音符高度
  - 滚轮：垂直滚动；Shift+滚轮：水平滚动
"""
from __future__ import annotations
import math
import copy
from enum import Enum
from typing import Optional, Set, List, Tuple

from PyQt6.QtWidgets import QAbstractScrollArea, QSizePolicy
from PyQt6.QtCore    import Qt, QRect, QRectF, QPointF, QSize, pyqtSignal
from PyQt6.QtGui     import (
    QPainter, QColor, QPen, QBrush, QFont,
    QMouseEvent, QWheelEvent, QKeyEvent, QCursor,
)

from config import (
    PIANO_KEY_WIDTH, NOTE_HEIGHT, HEADER_HEIGHT,
    DEFAULT_PPB, MIN_PPB, MAX_PPB,
    DEFAULT_NOTE_DURATION,
    SNAP_ENABLED, SNAP_GRID,
    COLOR_BG_DARK, COLOR_BG_MID, COLOR_BG_HEADER,
    COLOR_GRID_BAR, COLOR_GRID_BEAT, COLOR_GRID_SEMI,
    COLOR_CURSOR, COLOR_SELECTION,
    MIDI_MIN_PITCH, MIDI_MAX_PITCH,
)
from core.note_model import Project, Track, Note, is_black_key, pitch_to_name, TempoChange


# ─── 编辑模式 ────────────────────────────────────────────────

class EditMode(Enum):
    PEN    = "pen"     # 铅笔：新建音符
    SELECT = "select"  # 选择：移动/调整
    ERASE  = "erase"   # 橡皮：删除


# ─── 拖拽操作类型 ─────────────────────────────────────────────

class DragOp(Enum):
    NONE   = 0
    CREATE = 1   # 正在创建新音符
    MOVE   = 2   # 移动已选音符
    RESIZE = 3   # 调整音符结尾


RESIZE_ZONE    = 6    # 距音符右边缘多少像素内进入 resize 区
_TEMPO_STRIP_H = 14   # 标尺顶部留给 BPM 标记的像素高度
_VEL_LANE_H    = 72   # 力度编辑条高度（像素，固定在视口底部）


# ─── Piano Roll 主控件 ───────────────────────────────────────

class PianoRollWidget(QAbstractScrollArea):
    """
    Piano Roll 主控件
    信号：
        notes_changed()     音符被修改（用于通知主窗口更新标题等）
        beat_clicked(float) 用户点击时间轴时发出（用于跳转播放位置）
    """

    notes_changed          = pyqtSignal()
    beat_clicked           = pyqtSignal(float)
    note_preview           = pyqtSignal(int)        # 发出 MIDI 音高，用于即时预览音
    pre_change             = pyqtSignal()           # 修改前触发，用于保存撤销快照
    hover_info             = pyqtSignal(str)        # 悬停位置描述，发给状态栏
    tempo_change_requested = pyqtSignal(float)      # 右键标尺：请求在该拍位添加/编辑 BPM 标记

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: Optional[Project] = None
        self._mode:    EditMode          = EditMode.SELECT       # 默认选择模式
        self._ppb:     float             = float(DEFAULT_PPB)   # pixels per beat

        # 播放光标
        self._cursor_beat: float = 0.0

        # 选中音符集合
        self._selected: Set[int] = set()  # id(note)

        # 剪贴板（存储相对于最早起始拍的偏移，格式：List[Note]，start_beat 为相对偏移）
        self._clipboard: List[Note] = []

        # 悬停位置（用于绘制白框提示）
        self._hover_beat:  Optional[float] = None
        self._hover_pitch: Optional[int]   = None

        # 拖拽状态
        self._drag_op:        DragOp             = DragOp.NONE
        self._drag_start:     Optional[QPointF]  = None
        self._drag_note:      Optional[Note]     = None
        self._drag_track:     Optional[Track]    = None
        self._drag_orig_beat: float              = 0.0
        self._drag_orig_pit:  int                = 0
        self._drag_orig_dur:  float              = 0.0
        self._new_note:       Optional[Note]     = None

        # 框选矩形
        self._rubber_start: Optional[QPointF] = None
        self._rubber_end:   Optional[QPointF] = None

        # 空白区域点击（短按=创建音符，长拖=框选）
        self._blank_press_pos: Optional[QPointF] = None

        # 多音符同步移动：{id(note): (orig_beat, orig_pitch, orig_dur)}
        self._drag_orig_all: dict = {}

        # 吸附
        self._snap_enabled: bool  = SNAP_ENABLED
        self._snap_grid:    float = SNAP_GRID

        # 播放跟随（平滑翻页）
        self._follow_playback: bool = True

        # 标尺拖动横向平移
        self._ruler_drag_x:      Optional[float] = None
        self._ruler_drag_scroll: int             = 0

        # 键盘弹奏输入光标
        self._input_active: bool  = False
        self._input_beat:   float = 0.0

        # 力度编辑条拖拽状态
        self._vel_drag_note: Optional[Note] = None
        self._vel_drag_v0:   int            = 0

        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.viewport().setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # 垂直音符高度（支持动态缩放）
        self._note_h: float = float(NOTE_HEIGHT)

        # 垂直滚动条初始位置（居中在 C4 = pitch 60 附近）
        self._pending_center_pitch = 60

    # ── 公开接口 ──────────────────────────────────────────────

    def set_project(self, project: Project, reset_view: bool = True) -> None:
        self._project  = project
        self._selected = set()
        self._update_scrollbars()
        self.viewport().update()
        if reset_view:
            self._center_on_pitch(60)

    def set_mode(self, mode: EditMode) -> None:
        self._mode            = mode
        self._selected        = set()
        self._blank_press_pos = None
        self._rubber_start    = None
        self._rubber_end      = None
        # 切换鼠标光标
        if mode == EditMode.SELECT:
            self.viewport().setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        elif mode == EditMode.PEN:
            self.viewport().setCursor(QCursor(Qt.CursorShape.CrossCursor))
        elif mode == EditMode.ERASE:
            self.viewport().setCursor(QCursor(Qt.CursorShape.ForbiddenCursor))
        self.viewport().update()

    def set_follow_playback(self, enabled: bool) -> None:
        """开/关平滑翻页跟随模式"""
        self._follow_playback = enabled

    def set_cursor_beat(self, beat: float, follow: bool = False) -> None:
        """
        更新播放光标位置。
        follow=True 时（由播放线程调用）根据 _follow_playback 决定是否自动滚动。
        """
        self._cursor_beat = beat
        if follow and self._follow_playback:
            # 始终将光标保持在可视内容区左侧约 20%
            hbar     = self.horizontalScrollBar()
            vw       = self.viewport().width()
            content_w = vw - PIANO_KEY_WIDTH
            target   = max(0, int(beat * self._ppb - content_w * 0.2))
            hbar.setValue(target)
        self.viewport().update()

    def set_pixels_per_beat(self, ppb: float) -> None:
        old_ppb = self._ppb
        self._ppb = max(MIN_PPB, min(MAX_PPB, ppb))
        # 保持水平中心不变
        hbar = self.horizontalScrollBar()
        center_beat = (hbar.value() + self.viewport().width() / 2) / old_ppb
        self._update_scrollbars()
        hbar.setValue(int(center_beat * self._ppb - self.viewport().width() / 2))
        self.viewport().update()

    def zoom_in(self) -> None:
        self.set_pixels_per_beat(self._ppb * 1.25)

    def zoom_out(self) -> None:
        self.set_pixels_per_beat(self._ppb / 1.25)

    def select_all(self) -> None:
        if not self._project:
            return
        for track in self._project.tracks:
            for note in track.notes:
                self._selected.add(id(note))
        self.viewport().update()

    def select_track(self, track) -> None:
        """
        选中指定轨道的全部音符，并滚动视图使第一个音符可见。
        若音符已在视口内则只高亮，不强制滚动。
        """
        if not self._project or not track.notes:
            return
        self._selected = {id(n) for n in track.notes}

        first_note = min(track.notes, key=lambda n: n.start_beat)
        # 计算第一个音符在画布上的绝对 x（不减去 scrollbar，用于判断是否可见）
        hbar = self.horizontalScrollBar()
        abs_x = first_note.start_beat * self._ppb          # 相对画布左边
        vp_content_w = self.viewport().width() - PIANO_KEY_WIDTH
        visible_left  = hbar.value()
        visible_right = visible_left + vp_content_w
        if abs_x < visible_left or abs_x > visible_right - 20:
            # 第一个音符不在视口内：滚动使其显示在左侧留 40px 的位置
            hbar.setValue(max(0, int(abs_x) - 40))

        # 垂直居中到该轨道音符的平均音高
        avg_pitch = int(sum(n.pitch for n in track.notes) / len(track.notes))
        self._center_on_pitch(avg_pitch)
        self.viewport().update()

    # ── 键盘弹奏输入 ──────────────────────────────────────────

    def set_note_input_active(self, active: bool, start_beat: float = None) -> None:
        """开启/关闭键盘弹奏模式，可选择设置起始位置。"""
        self._input_active = active
        if active:
            self._input_beat = (
                start_beat if start_beat is not None else self._cursor_beat
            )
        self.viewport().update()

    def set_note_input_beat(self, beat: float) -> None:
        self._input_beat = beat
        self.viewport().update()

    def insert_note_at_cursor(self, pitch: int, duration: float) -> None:
        """在输入光标处插入一个音符，推进光标，滚动视图，预览发声。"""
        if not self._project or not self._project.tracks:
            return

        # 优先插入第一条未静音轨道
        target = next(
            (t for t in self._project.tracks if not t.muted),
            self._project.tracks[0]
        )

        self.pre_change.emit()
        note = Note(
            pitch          = pitch,
            start_beat     = self._input_beat,
            duration_beats = duration,
        )
        target.add_note(note)
        self._selected = {id(note)}
        self._input_beat = round(self._input_beat + duration, 9)

        # 自动滚动：若光标超出右侧可视区则滚动
        hbar       = self.horizontalScrollBar()
        abs_x      = self._input_beat * self._ppb
        content_w  = self.viewport().width() - PIANO_KEY_WIDTH
        vis_right  = hbar.value() + content_w
        if abs_x > vis_right - 20:
            hbar.setValue(max(0, int(abs_x - content_w * 0.25)))

        # 垂直居中到输入音高
        self._center_on_pitch(pitch)
        self.note_preview.emit(pitch)
        self._update_scrollbars()
        self.viewport().update()
        self.notes_changed.emit()

    def advance_rest(self, duration: float) -> None:
        """推进输入光标（休止符，不插入音符）。"""
        self._input_beat = round(self._input_beat + duration, 9)
        # 同步滚动
        hbar      = self.horizontalScrollBar()
        abs_x     = self._input_beat * self._ppb
        content_w = self.viewport().width() - PIANO_KEY_WIDTH
        if abs_x > hbar.value() + content_w - 20:
            hbar.setValue(max(0, int(abs_x - content_w * 0.25)))
        self.viewport().update()

    def delete_selected(self) -> None:
        if not self._project:
            return
        self.pre_change.emit()
        for track in self._project.tracks:
            track.notes = [n for n in track.notes if id(n) not in self._selected]
        self._selected.clear()
        self.viewport().update()
        self.notes_changed.emit()

    def copy_selected(self) -> bool:
        """
        复制选中音符到内部剪贴板
        音符的 start_beat 转为相对于最早起始拍的偏移
        返回是否成功复制
        """
        if not self._project:
            return False
        selected_notes = [
            note for track in self._project.tracks
            for note in track.notes
            if id(note) in self._selected
        ]
        if not selected_notes:
            return False
        min_beat = min(n.start_beat for n in selected_notes)
        self._clipboard = [
            Note(
                pitch          = n.pitch,
                start_beat     = n.start_beat - min_beat,   # 相对偏移
                duration_beats = n.duration_beats,
                velocity       = n.velocity,
            )
            for n in selected_notes
        ]
        return True

    def cut_selected(self) -> None:
        """剪切 = 复制 + 删除"""
        if self.copy_selected():
            self.pre_change.emit()
            if self._project:
                for track in self._project.tracks:
                    track.notes = [n for n in track.notes if id(n) not in self._selected]
            self._selected.clear()
            self.viewport().update()
            self.notes_changed.emit()

    def paste(self) -> None:
        """
        粘贴剪贴板内容到当前播放光标位置
        粘贴后自动选中新音符
        """
        if not self._clipboard or not self._project or not self._project.tracks:
            return
        self.pre_change.emit()
        target_track = self._project.tracks[0]
        self._selected.clear()
        for note in self._clipboard:
            new_note = Note(
                pitch          = note.pitch,
                start_beat     = self._cursor_beat + note.start_beat,
                duration_beats = note.duration_beats,
                velocity       = note.velocity,
            )
            target_track.add_note(new_note)
            self._selected.add(id(new_note))
        self.viewport().update()
        self.notes_changed.emit()

    # ── 坐标换算 ──────────────────────────────────────────────

    def _beat_to_x(self, beat: float) -> float:
        return PIANO_KEY_WIDTH + beat * self._ppb - self.horizontalScrollBar().value()

    def _pitch_to_y(self, pitch: int) -> float:
        return HEADER_HEIGHT + (MIDI_MAX_PITCH - pitch) * self._note_h - self.verticalScrollBar().value()

    def _x_to_beat(self, x: float) -> float:
        raw = (x - PIANO_KEY_WIDTH + self.horizontalScrollBar().value()) / self._ppb
        return max(0.0, raw)

    def _y_to_pitch(self, y: float) -> int:
        raw = MIDI_MAX_PITCH - int((y - HEADER_HEIGHT + self.verticalScrollBar().value()) / self._note_h)
        return max(MIDI_MIN_PITCH, min(MIDI_MAX_PITCH, raw))

    def set_snap(self, enabled: bool, grid: float = None) -> None:
        """设置吸附模式"""
        self._snap_enabled = enabled
        if grid is not None:
            self._snap_grid = grid
        self.viewport().update()

    def _snap_beat(self, beat: float) -> float:
        """根据吸附设置对拍位取整"""
        if not self._snap_enabled:
            return beat
        grid = self._snap_grid
        return round(beat / grid) * grid

    def _min_duration(self) -> float:
        """最小音符时值"""
        if self._snap_enabled:
            return self._snap_grid
        return 0.0625  # 1/32 拍，自由模式下最短

    # ── 滚动条管理 ────────────────────────────────────────────

    def _update_scrollbars(self) -> None:
        if not self._project:
            return
        total_beats  = self._project.total_beats() + 8
        canvas_w     = int(total_beats * self._ppb)
        canvas_h     = int((MIDI_MAX_PITCH - MIDI_MIN_PITCH + 1) * self._note_h)

        vp_w = self.viewport().width()
        vp_h = self.viewport().height() - HEADER_HEIGHT - _VEL_LANE_H

        hbar = self.horizontalScrollBar()
        vbar = self.verticalScrollBar()
        hbar.setRange(0, max(0, canvas_w - (vp_w - PIANO_KEY_WIDTH)))
        hbar.setPageStep(vp_w - PIANO_KEY_WIDTH)
        vbar.setRange(0, max(0, canvas_h - vp_h))
        vbar.setPageStep(vp_h)

    def _center_on_pitch(self, pitch: int) -> None:
        vbar     = self.verticalScrollBar()
        target_y = int((MIDI_MAX_PITCH - pitch) * self._note_h) - (self.viewport().height() // 2)
        vbar.setValue(max(0, min(vbar.maximum(), target_y)))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_scrollbars()

    # ── 绘制 ──────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        vw = self.viewport().width()
        vh = self.viewport().height()

        # 1. 背景
        painter.fillRect(0, 0, vw, vh, QColor(COLOR_BG_DARK))

        # 2. 网格（在 Piano Roll 区域内，不含力度条）
        content_h = vh - HEADER_HEIGHT - _VEL_LANE_H
        painter.setClipRect(PIANO_KEY_WIDTH, HEADER_HEIGHT, vw - PIANO_KEY_WIDTH, content_h)
        self._draw_grid(painter)

        # 3. 音符
        self._draw_notes(painter)
        painter.setClipping(False)

        # 4. 钢琴键盘（固定在左侧，垂直随滚动，不含力度条）
        painter.setClipRect(0, HEADER_HEIGHT, PIANO_KEY_WIDTH, content_h)
        self._draw_keyboard(painter)
        painter.setClipping(False)

        # 5. 时间标尺（固定在顶部）
        painter.setClipRect(PIANO_KEY_WIDTH, 0, vw - PIANO_KEY_WIDTH, HEADER_HEIGHT)
        self._draw_header(painter)
        painter.setClipping(False)

        # 6. 悬停白框（铅笔/橡皮模式下显示即将操作的位置）
        self._draw_hover(painter)

        # 7. 播放光标
        self._draw_cursor(painter)

        # 7b. 键盘弹奏输入光标（绿色）
        if self._input_active:
            self._draw_input_cursor(painter)

        # 7c. 框选矩形
        if self._rubber_start and self._rubber_end:
            self._draw_rubber(painter)

        # 8. 力度编辑条（固定在底部）
        self._draw_velocity_lane(painter)

        # 9. 左上角遮罩（盖住键盘与标尺交叉区域）
        painter.fillRect(0, 0, PIANO_KEY_WIDTH, HEADER_HEIGHT, QColor(COLOR_BG_HEADER))

    def _draw_grid(self, painter: QPainter) -> None:
        vw = self.viewport().width()
        vh = self.viewport().height()
        h_off = self.horizontalScrollBar().value()
        v_off = self.verticalScrollBar().value()

        if not self._project:
            return

        beats_per_measure = self._project.beats_per_measure()

        # ── 水平线（半音分隔）────────────────────────────────
        for pitch in range(MIDI_MIN_PITCH, MIDI_MAX_PITCH + 1):
            y = self._pitch_to_y(pitch)
            if y < HEADER_HEIGHT or y > vh:
                continue
            if is_black_key(pitch):
                painter.fillRect(PIANO_KEY_WIDTH, int(y), vw - PIANO_KEY_WIDTH, int(self._note_h), QColor(28, 28, 38))
            painter.setPen(QPen(QColor(COLOR_GRID_SEMI), 1))
            painter.drawLine(PIANO_KEY_WIDTH, int(y + self._note_h - 1),
                             vw, int(y + self._note_h - 1))

        # ── 高低音分界线（中央 C = pitch 60）────────────────────
        c4_y = self._pitch_to_y(60)
        if HEADER_HEIGHT < c4_y < vh:
            painter.setPen(QPen(QColor(180, 140, 60, 55), 1, Qt.PenStyle.DashLine))
            painter.drawLine(PIANO_KEY_WIDTH, int(c4_y), vw, int(c4_y))

        # ── 垂直线（小节 / 拍 / 细分）────────────────────────
        total_beats = (self._project.total_beats() + 8) if self._project else 32
        step = self._snap_grid   # 每格 = 当前吸附精度
        beat = 0.0
        while beat <= total_beats:
            x = self._beat_to_x(beat)
            if x < PIANO_KEY_WIDTH:
                beat += step
                continue
            if x > vw:
                break

            is_bar  = abs(beat % beats_per_measure) < 1e-9
            is_beat = abs(beat % 1.0) < 1e-9  # 整拍

            if is_bar:
                color = COLOR_GRID_BAR    # 小节线：最亮
            elif is_beat:
                color = COLOR_GRID_BEAT   # 拍线：中亮
            else:
                color = COLOR_GRID_SEMI   # 细分线：最暗
            painter.setPen(QPen(QColor(color), 1))
            painter.drawLine(int(x), HEADER_HEIGHT, int(x), vh)

            beat = round((beat + step) / step) * step  # 避免浮点误差

    def _draw_keyboard(self, painter: QPainter) -> None:
        vh = self.viewport().height()

        for pitch in range(MIDI_MIN_PITCH, MIDI_MAX_PITCH + 1):
            y = self._pitch_to_y(pitch)
            if y + self._note_h < HEADER_HEIGHT or y > vh:
                continue

            bk    = is_black_key(pitch)
            name  = pitch_to_name(pitch)
            nh    = int(self._note_h)

            if bk:
                kw    = int(PIANO_KEY_WIDTH * 0.60)
                kx    = PIANO_KEY_WIDTH - kw
                color = QColor(25, 25, 35)
            else:
                kx    = 0
                kw    = PIANO_KEY_WIDTH - 1
                color = QColor(200, 200, 210) if not name.startswith('C') else QColor(220, 200, 170)

            painter.fillRect(kx, int(y), kw, nh, color)

            if not bk:
                painter.setPen(QPen(QColor(130, 130, 140), 1))
                painter.drawLine(0, int(y + self._note_h - 1), PIANO_KEY_WIDTH - 1, int(y + self._note_h - 1))

            if not bk and name.startswith('C'):
                painter.setPen(QPen(QColor(80, 80, 90), 1))
                font = QFont("Arial", max(6, int(self._note_h) - 4))
                painter.setFont(font)
                painter.drawText(2, int(y), PIANO_KEY_WIDTH - 4, nh,
                                 Qt.AlignmentFlag.AlignVCenter, name)

        # 高低音分界标记（中央 C = pitch 60）：在键盘右侧画一道琥珀色细线
        c4_y = self._pitch_to_y(60)
        if HEADER_HEIGHT < c4_y < vh:
            painter.setPen(QPen(QColor(200, 160, 60, 160), 2))
            painter.drawLine(0, int(c4_y), PIANO_KEY_WIDTH - 1, int(c4_y))

        # 右边分隔线
        painter.setPen(QPen(QColor(60, 60, 80), 1))
        painter.drawLine(PIANO_KEY_WIDTH - 1, HEADER_HEIGHT, PIANO_KEY_WIDTH - 1, vh)

    def _draw_header(self, painter: QPainter) -> None:
        vw = self.viewport().width()
        painter.fillRect(PIANO_KEY_WIDTH, 0, vw, HEADER_HEIGHT, QColor(COLOR_BG_HEADER))

        if not self._project:
            return

        beats_per_measure = self._project.beats_per_measure()
        total_beats       = self._project.total_beats() + 8
        font              = QFont("Arial", 8)
        painter.setFont(font)
        painter.setPen(QPen(QColor(160, 160, 180), 1))

        beat    = 0.0
        measure = 1
        step    = self._snap_grid
        while beat <= total_beats:
            x = self._beat_to_x(beat)
            if x >= PIANO_KEY_WIDTH:
                is_bar = abs(beat % beats_per_measure) < 1e-9
                tick_h = 8 if is_bar else 4
                painter.drawLine(int(x), HEADER_HEIGHT - tick_h, int(x), HEADER_HEIGHT)
                if is_bar:
                    painter.drawText(int(x) + 2, 0, 40, HEADER_HEIGHT - 4,
                                     Qt.AlignmentFlag.AlignBottom, str(measure))
                    measure += 1
            beat = round((beat + step) / step) * step

        # ── BPM 变速标记（顶部条带）────────────────────────────
        if self._project and self._project.tempo_changes:
            from PyQt6.QtGui import QPolygonF
            for tc in self._project.tempo_changes:
                bx = self._beat_to_x(tc.beat)
                if bx < PIANO_KEY_WIDTH or bx > vw:
                    continue
                # 向下的小三角
                painter.setBrush(QBrush(QColor(255, 190, 50)))
                painter.setPen(Qt.PenStyle.NoPen)
                pts = [QPointF(bx, _TEMPO_STRIP_H - 1),
                       QPointF(bx - 5, _TEMPO_STRIP_H - 9),
                       QPointF(bx + 5, _TEMPO_STRIP_H - 9)]
                painter.drawPolygon(QPolygonF(pts))
                # BPM 数字
                painter.setPen(QPen(QColor(255, 220, 100)))
                painter.setFont(QFont("Arial", 7))
                painter.drawText(int(bx) + 6, 0, 50, _TEMPO_STRIP_H,
                                 Qt.AlignmentFlag.AlignVCenter, f"{tc.bpm}")

        # 底部分隔线
        painter.setPen(QPen(QColor(60, 60, 80), 1))
        painter.drawLine(PIANO_KEY_WIDTH, HEADER_HEIGHT - 1, vw, HEADER_HEIGHT - 1)

    def _draw_notes(self, painter: QPainter) -> None:
        if not self._project:
            return
        vw = self.viewport().width()
        vh = self.viewport().height()

        for track in self._project.tracks:
            base_color = QColor(track.color)
            is_muted   = track.muted

            for note in track.notes:
                x = self._beat_to_x(note.start_beat)
                y = self._pitch_to_y(note.pitch)
                w = max(note.duration_beats * self._ppb - 1, 2.0)
                h = self._note_h - 2

                # 可见性裁剪
                if x + w < PIANO_KEY_WIDTH or x > vw:
                    continue
                if y + h < HEADER_HEIGHT or y > vh:
                    continue

                rect = QRectF(x, y + 1, w, h)

                if is_muted:
                    # 静音：暗灰色，半透明
                    fill = QColor(60, 60, 70, 140)
                    painter.setBrush(QBrush(fill))
                    painter.setPen(QPen(QColor(80, 80, 90, 160), 1))
                    painter.drawRoundedRect(rect, 2.0, 2.0)
                    continue

                selected    = id(note) in self._selected
                is_dragging = (self._drag_op != DragOp.NONE and selected)

                # 填充：拖拽中保持原色；普通选中略微变亮
                if is_dragging:
                    fill = base_color
                elif selected:
                    fill = base_color.lighter(140)
                else:
                    fill = base_color
                painter.setBrush(QBrush(fill))

                # 边框：选中/拖拽都用白色，普通用暗色
                if selected:
                    pen_w = 2.0 if is_dragging else 1.5
                    painter.setPen(QPen(QColor(COLOR_SELECTION), pen_w))
                else:
                    painter.setPen(QPen(base_color.darker(140), 1))

                painter.drawRoundedRect(rect, 2.0, 2.0)

                # 音符名称（宽度足够时显示）
                if w > 20:
                    painter.setPen(QPen(QColor(255, 255, 255, 180), 1))
                    font = QFont("Arial", 7)
                    painter.setFont(font)
                    painter.drawText(
                        QRectF(x + 2, y + 1, w - 4, h),
                        Qt.AlignmentFlag.AlignVCenter,
                        note.name
                    )

    def _draw_hover(self, painter: QPainter) -> None:
        """用白色虚线框标出光标所在位置。
        若悬停在已有音符上，则框住整个音符；否则显示一个网格单元大小的预览框。
        拖拽时不显示。
        """
        if self._hover_beat is None or self._hover_pitch is None:
            return
        if self._drag_op != DragOp.NONE:
            return

        # 在内容区中查找光标下的音符（如有）
        hover_note = None
        if self._project:
            hx = self._beat_to_x(self._hover_beat) + 1
            hy = self._pitch_to_y(self._hover_pitch) + self._note_h / 2
            note, _, _ = self._note_at(hx, hy)
            hover_note = note

        if hover_note is not None:
            # 悬停在已有音符上：框住整个音符
            x = self._beat_to_x(hover_note.start_beat)
            y = self._pitch_to_y(hover_note.pitch)
            w = max(hover_note.duration_beats * self._ppb - 1, 2.0)
            h = self._note_h - 1
        else:
            # 空白位置：显示一个网格单元预览框
            x = self._beat_to_x(self._hover_beat)
            y = self._pitch_to_y(self._hover_pitch)
            w = self._min_duration() * self._ppb
            h = self._note_h - 1

        painter.setPen(QPen(QColor(255, 255, 255, 180), 1, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(x, y + 1, w, h))

    def _draw_cursor(self, painter: QPainter) -> None:
        x  = self._beat_to_x(self._cursor_beat)
        vw = self.viewport().width()
        vh = self.viewport().height()
        if x < PIANO_KEY_WIDTH or x > vw:
            return

        color = QColor(COLOR_CURSOR)
        from PyQt6.QtGui import QPolygonF

        # ── 顶部三角（与 BPM 标记条等高，指向内容区）──────────
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        pts = [QPointF(x - 6, 0), QPointF(x + 6, 0), QPointF(x, _TEMPO_STRIP_H)]
        painter.drawPolygon(QPolygonF(pts))

        # ── 标尺区细线（三角尖端 → 内容区顶部）─────────────────
        painter.setPen(QPen(color, 1))
        painter.drawLine(int(x), _TEMPO_STRIP_H, int(x), HEADER_HEIGHT)

        # ── 内容区主光标线 ────────────────────────────────────
        painter.setPen(QPen(color, 2))
        painter.drawLine(int(x), HEADER_HEIGHT, int(x), vh)

    def _draw_input_cursor(self, painter: QPainter) -> None:
        """绘制键盘输入光标（绿色竖线 + 顶部三角）。"""
        x  = self._beat_to_x(self._input_beat)
        vw = self.viewport().width()
        vh = self.viewport().height()
        if x < PIANO_KEY_WIDTH or x > vw:
            return

        from PyQt6.QtGui import QPolygonF
        color = QColor(80, 220, 120)   # 绿色

        # 顶部三角（朝下）
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        pts = [QPointF(x - 5, 0), QPointF(x + 5, 0), QPointF(x, _TEMPO_STRIP_H)]
        painter.drawPolygon(QPolygonF(pts))

        # 标尺细线
        painter.setPen(QPen(color, 1))
        painter.drawLine(int(x), _TEMPO_STRIP_H, int(x), HEADER_HEIGHT)

        # 内容区主线（虚线）
        pen = QPen(color, 2, Qt.PenStyle.DashLine)
        pen.setDashPattern([6, 4])
        painter.setPen(pen)
        painter.drawLine(int(x), HEADER_HEIGHT, int(x), vh - _VEL_LANE_H)

    def _draw_rubber(self, painter: QPainter) -> None:
        if not (self._rubber_start and self._rubber_end):
            return
        x1, y1 = self._rubber_start.x(), self._rubber_start.y()
        x2, y2 = self._rubber_end.x(),   self._rubber_end.y()
        rect = QRectF(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
        painter.setPen(QPen(QColor(120, 180, 255), 1, Qt.PenStyle.DashLine))
        painter.setBrush(QBrush(QColor(100, 160, 255, 60)))
        painter.drawRect(rect)

    # ── 音符查找 ──────────────────────────────────────────────

    def _note_at(self, x: float, y: float) -> Tuple[Optional[Note], Optional[Track], bool]:
        """
        查找给定坐标处的音符
        返回 (note, track, is_resize_zone)
        is_resize_zone=True 表示鼠标在音符右边缘（调整时值区）
        """
        if not self._project:
            return None, None, False
        for track in self._project.tracks:
            if track.muted:
                continue
            for note in track.notes:
                nx  = self._beat_to_x(note.start_beat)
                ny  = self._pitch_to_y(note.pitch)
                nw  = note.duration_beats * self._ppb
                if nx <= x <= nx + nw and ny <= y <= ny + self._note_h:
                    in_resize = (x >= nx + nw - RESIZE_ZONE)
                    return note, track, in_resize
        return None, None, False

    def _notes_in_rect(self, rect: QRectF) -> List[Tuple[Note, Track]]:
        """返回矩形框内的所有音符"""
        result = []
        if not self._project:
            return result
        beat_l = self._x_to_beat(rect.left())
        beat_r = self._x_to_beat(rect.right())
        pit_t  = self._y_to_pitch(rect.top())
        pit_b  = self._y_to_pitch(rect.bottom())
        pitch_lo, pitch_hi = min(pit_t, pit_b), max(pit_t, pit_b)
        for track in self._project.tracks:
            if track.muted:
                continue
            for note in track.notes:
                if (note.start_beat < beat_r and note.end_beat > beat_l
                        and pitch_lo <= note.pitch <= pitch_hi):
                    result.append((note, track))
        return result

    # ── 拖拽辅助 ──────────────────────────────────────────────

    def _start_drag(self, note: Note, track, is_resize: bool, pos: QPointF) -> None:
        """初始化拖拽状态，保存所有选中音符的原始位置以支持多音符同步移动"""
        self._drag_op        = DragOp.RESIZE if is_resize else DragOp.MOVE
        self._drag_note      = note
        self._drag_track     = track
        self._drag_start     = pos
        self._drag_orig_beat = note.start_beat
        self._drag_orig_pit  = note.pitch
        self._drag_orig_dur  = note.duration_beats
        # 保存所有选中音符的原始位置（用于多音符同步移动）
        if self._drag_op == DragOp.MOVE and self._project:
            self._drag_orig_all = {
                id(n): (n.start_beat, n.pitch, n.duration_beats)
                for t in self._project.tracks
                for n in t.notes
                if id(n) in self._selected
            }
        else:
            self._drag_orig_all = {}

    def _create_note_at(self, x: float, y: float, pos: QPointF, start_drag: bool = False) -> None:
        """在指定坐标处创建新音符，可选择立即进入 CREATE 拖拽"""
        if not self._project or not self._project.tracks:
            return
        beat  = self._snap_beat(self._x_to_beat(x))
        pitch = self._y_to_pitch(y)
        target_track = self._project.tracks[0]
        self.pre_change.emit()
        new_note = Note(
            pitch          = pitch,
            start_beat     = beat,
            duration_beats = DEFAULT_NOTE_DURATION,  # 固定 1/4 音符，不受吸附影响
        )
        target_track.add_note(new_note)
        self.note_preview.emit(pitch)
        if start_drag:
            self._drag_op        = DragOp.CREATE
            self._drag_note      = new_note
            self._drag_track     = target_track
            self._drag_orig_beat = beat
            self._drag_start     = pos
            self._drag_orig_dur  = new_note.duration_beats
        self.notes_changed.emit()

    # ── 鼠标事件 ──────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        x, y = pos.x(), pos.y()

        # 点击时间标尺
        if y < HEADER_HEIGHT and x > PIANO_KEY_WIDTH:
            if event.button() == Qt.MouseButton.RightButton:
                # 右键：添加/编辑 BPM 标记
                snapped = self._snap_beat(self._x_to_beat(x))
                self.tempo_change_requested.emit(snapped)
            elif event.button() == Qt.MouseButton.LeftButton:
                # 左键：记录起点，松开时判断是单击（设点位）还是拖动（平移）
                self._ruler_drag_x      = x
                self._ruler_drag_scroll = self.horizontalScrollBar().value()
                self.viewport().setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            return

        # 点击钢琴键盘区 -> 预览该音高
        if x < PIANO_KEY_WIDTH:
            if y > HEADER_HEIGHT and event.button() == Qt.MouseButton.LeftButton:
                pitch = self._y_to_pitch(y)
                self.note_preview.emit(pitch)
            return

        # 点击力度编辑条（视口底部 _VEL_LANE_H 像素内）
        vh = self.viewport().height()
        if y >= vh - _VEL_LANE_H and x >= PIANO_KEY_WIDTH:
            if event.button() == Qt.MouseButton.LeftButton:
                self._start_vel_drag(x, y)
            return

        btn = event.button()

        if self._mode == EditMode.PEN:
            if btn == Qt.MouseButton.LeftButton:
                note, track, is_resize = self._note_at(x, y)
                if note:
                    # 点到已有音符 -> 选中并准备拖拽
                    if id(note) not in self._selected:
                        if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                            self._selected.clear()
                        self._selected.add(id(note))
                    self.note_preview.emit(note.pitch)
                    self.pre_change.emit()
                    self._start_drag(note, track, is_resize, pos)
                else:
                    # 空白处 -> 新建音符
                    self._create_note_at(x, y, pos, start_drag=True)
            elif btn == Qt.MouseButton.RightButton:
                # 右键 -> 删除
                note, track, _ = self._note_at(x, y)
                if note and track:
                    self.pre_change.emit()
                    track.remove_note(note)
                    self._selected.discard(id(note))
                    self.notes_changed.emit()

        elif self._mode == EditMode.SELECT:
            if btn == Qt.MouseButton.LeftButton:
                note, track, is_resize = self._note_at(x, y)
                if note:
                    if id(note) not in self._selected:
                        if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                            self._selected.clear()
                        self._selected.add(id(note))
                    self.note_preview.emit(note.pitch)
                    self.pre_change.emit()
                    self._start_drag(note, track, is_resize, pos)
                else:
                    # 空白处 -> 记录位置，短按=创建音符，长拖=框选
                    if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                        self._selected.clear()
                    self._blank_press_pos = pos

        elif self._mode == EditMode.ERASE:
            if btn == Qt.MouseButton.LeftButton:
                note, track, _ = self._note_at(x, y)
                if note and track:
                    self.pre_change.emit()
                    track.remove_note(note)
                    self._selected.discard(id(note))
                    self.notes_changed.emit()
                else:
                    # 空白处 -> 记录位置，短按=创建音符
                    self._blank_press_pos = pos

        self.viewport().update()

    def leaveEvent(self, event) -> None:
        self._hover_beat  = None
        self._hover_pitch = None
        self.viewport().update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        x, y = pos.x(), pos.y()

        # ── 标尺拖动横向平移 ──────────────────────────────────
        if self._ruler_drag_x is not None:
            dx = self._ruler_drag_x - x
            self.horizontalScrollBar().setValue(self._ruler_drag_scroll + int(dx))
            self.viewport().update()
            return

        # 标尺悬停：显示手形光标提示可拖动
        if y < HEADER_HEIGHT and x > PIANO_KEY_WIDTH and self._drag_op == DragOp.NONE:
            self.viewport().setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
        elif self._drag_op == DragOp.NONE:
            # 离开标尺区后恢复当前模式的光标
            if self._mode == EditMode.SELECT:
                self.viewport().setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            elif self._mode == EditMode.PEN:
                self.viewport().setCursor(QCursor(Qt.CursorShape.CrossCursor))
            elif self._mode == EditMode.ERASE:
                self.viewport().setCursor(QCursor(Qt.CursorShape.ForbiddenCursor))

        # 更新悬停位置（仅在 Piano Roll 内容区：不含力度条）
        _vh = self.viewport().height()
        if x > PIANO_KEY_WIDTH and y > HEADER_HEIGHT and y < _vh - _VEL_LANE_H:
            self._hover_beat  = self._snap_beat(self._x_to_beat(x))
            self._hover_pitch = self._y_to_pitch(y)
            from core.note_model import pitch_to_name
            beats_per_measure = self._project.beats_per_measure() if self._project else 4
            measure = int(self._hover_beat // beats_per_measure) + 1
            beat_in = self._hover_beat % beats_per_measure + 1
            self.hover_info.emit(
                f"{pitch_to_name(self._hover_pitch)}  |  第 {measure} 小节 第 {beat_in:.1f} 拍"
            )
        else:
            self._hover_beat  = None
            self._hover_pitch = None
            self.hover_info.emit("")

        # 更新光标形状
        if self._mode == EditMode.SELECT and self._drag_op == DragOp.NONE:
            note, _, is_resize = self._note_at(x, y)
            if note and is_resize:
                self.viewport().setCursor(QCursor(Qt.CursorShape.SizeHorCursor))
            elif note:
                self.viewport().setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            else:
                self.viewport().setCursor(QCursor(Qt.CursorShape.ArrowCursor))

        # 力度条拖拽：更新力度
        if self._vel_drag_note is not None:
            self._vel_drag_note.velocity = self._vel_from_y(y)
            self.viewport().update()
            return

        if self._drag_op == DragOp.CREATE and self._drag_note:
            # 拖拽调整新建音符时值（原地修改，无闪烁）
            cur_beat = self._snap_beat(self._x_to_beat(x))
            dur = max(self._min_duration(), cur_beat - self._drag_note.start_beat + self._min_duration())
            self._drag_note.duration_beats = dur

        elif self._drag_op == DragOp.MOVE and self._drag_note:
            # 多音符同步移动（原地修改，无闪烁）
            dx_beat  = (x - self._drag_start.x()) / self._ppb
            dy_pitch = -int((y - self._drag_start.y()) / self._note_h)
            if self._drag_orig_all and self._project:
                for track in self._project.tracks:
                    for note in track.notes:
                        orig = self._drag_orig_all.get(id(note))
                        if orig:
                            ob, op, od = orig
                            note.start_beat = self._snap_beat(max(0.0, ob + dx_beat))
                            note.pitch = max(MIDI_MIN_PITCH, min(MIDI_MAX_PITCH, op + dy_pitch))
            else:
                # 单音符后备
                new_beat = self._snap_beat(max(0.0, self._drag_orig_beat + dx_beat))
                new_pit  = max(MIDI_MIN_PITCH, min(MIDI_MAX_PITCH, self._drag_orig_pit + dy_pitch))
                self._drag_note.start_beat = new_beat
                self._drag_note.pitch      = new_pit

        elif self._drag_op == DragOp.RESIZE and self._drag_note:
            dx_beat = (x - self._drag_start.x()) / self._ppb
            new_dur = max(self._min_duration(), self._drag_orig_dur + dx_beat)
            new_dur = self._snap_beat(new_dur) if self._snap_enabled else new_dur
            self._drag_note.duration_beats = new_dur

        elif self._rubber_start:
            self._rubber_end = pos

        # 空白区域拖拽阈值：超过 8px 则转为框选
        elif self._blank_press_pos is not None:
            dist = ((pos.x() - self._blank_press_pos.x()) ** 2 +
                    (pos.y() - self._blank_press_pos.y()) ** 2) ** 0.5
            if dist > 8:
                self._rubber_start    = self._blank_press_pos
                self._rubber_end      = pos
                self._blank_press_pos = None

        self.viewport().update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        x   = pos.x()

        # ── 标尺拖动结束 ──────────────────────────────────────
        if self._ruler_drag_x is not None:
            dx = abs(x - self._ruler_drag_x)
            if dx < 5:
                # 短按（未明显位移）→ 设置播放位置
                beat = self._x_to_beat(self._ruler_drag_x)
                self.beat_clicked.emit(beat)
            self._ruler_drag_x = None
            # 恢复模式光标
            if self._mode == EditMode.SELECT:
                self.viewport().setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            elif self._mode == EditMode.PEN:
                self.viewport().setCursor(QCursor(Qt.CursorShape.CrossCursor))
            elif self._mode == EditMode.ERASE:
                self.viewport().setCursor(QCursor(Qt.CursorShape.ForbiddenCursor))
            self.viewport().update()
            return

        # 力度条拖拽结束
        if self._vel_drag_note is not None:
            self.pre_change.emit()   # 记录撤销快照（在 drag 结束时补充）
            self._vel_drag_note = None
            self.notes_changed.emit()
            self.viewport().update()
            return

        if self._drag_op != DragOp.NONE:
            if self._drag_op in (DragOp.MOVE, DragOp.RESIZE, DragOp.CREATE):
                self.notes_changed.emit()
            self._drag_op        = DragOp.NONE
            self._drag_note      = None
            self._drag_track     = None
            self._drag_orig_all  = {}

        # 完成框选
        if self._rubber_start and self._rubber_end:
            rect = QRectF(self._rubber_start, self._rubber_end).normalized()
            for note, _ in self._notes_in_rect(rect):
                self._selected.add(id(note))
            self._rubber_start = None
            self._rubber_end   = None

        # 短按空白区域 -> 创建音符
        if self._blank_press_pos is not None:
            px, py = self._blank_press_pos.x(), self._blank_press_pos.y()
            if py > HEADER_HEIGHT and px > PIANO_KEY_WIDTH:
                self._create_note_at(px, py, self._blank_press_pos, start_drag=False)
            self._blank_press_pos = None

        self.viewport().update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        mods  = event.modifiers()
        mouse_x = event.position().x()

        if mods & Qt.KeyboardModifier.ControlModifier:
            if mouse_x < PIANO_KEY_WIDTH:
                # Ctrl + 滚轮（在钢琴键盘上）：垂直缩放音符高度
                self._set_note_height(self._note_h * (1.15 if delta > 0 else 1 / 1.15))
            else:
                # Ctrl + 滚轮（在内容区）：水平缩放
                factor = 1.15 if delta > 0 else 1 / 1.15
                self.set_pixels_per_beat(self._ppb * factor)
        elif mods & Qt.KeyboardModifier.ShiftModifier:
            # Shift + 滚轮：水平滚动
            hbar = self.horizontalScrollBar()
            hbar.setValue(hbar.value() - delta)
        else:
            # 垂直滚动
            vbar = self.verticalScrollBar()
            vbar.setValue(vbar.value() - delta // 3)

    def _set_note_height(self, new_h: float) -> None:
        """调整垂直音符高度（4~32 像素），保持视图中心音高不变"""
        _MIN_NOTE_H = 4.0
        _MAX_NOTE_H = 32.0
        new_h = max(_MIN_NOTE_H, min(_MAX_NOTE_H, new_h))
        if abs(new_h - self._note_h) < 0.01:
            return

        # 保持视图中心的音高不变
        vbar = self.verticalScrollBar()
        vh   = self.viewport().height()
        center_y_canvas = vbar.value() + (vh - HEADER_HEIGHT) / 2
        center_pitch    = MIDI_MAX_PITCH - center_y_canvas / self._note_h

        self._note_h = new_h
        self._update_scrollbars()

        new_center_y = (MIDI_MAX_PITCH - center_pitch) * self._note_h
        vbar.setValue(int(max(0, min(vbar.maximum(), new_center_y - (vh - HEADER_HEIGHT) / 2))))
        self.viewport().update()

    # ── 力度编辑条 ────────────────────────────────────────────

    def _vel_from_y(self, y: float) -> int:
        """将视口 y 坐标转换为 MIDI 力度值（0-127）"""
        vh   = self.viewport().height()
        lane_top = vh - _VEL_LANE_H
        rel  = max(0.0, min(1.0, (vh - 4 - y) / max(_VEL_LANE_H - 8, 1)))
        return max(1, min(127, int(rel * 127 + 0.5)))

    def _start_vel_drag(self, x: float, y: float) -> None:
        """开始力度条拖拽：找到 x 处的音符并开始修改其力度"""
        if not self._project:
            return
        # 找最近的音符（按 x 中心最近）
        best_note = None
        best_dist = float('inf')
        for track in self._project.tracks:
            if track.muted:
                continue
            for note in track.notes:
                nx  = self._beat_to_x(note.start_beat)
                nw  = note.duration_beats * self._ppb
                if nx <= x <= nx + nw:
                    dist = abs(x - (nx + nw / 2))
                    if dist < best_dist:
                        best_dist = dist
                        best_note = note
        if best_note is not None:
            self._vel_drag_note = best_note
            self._vel_drag_v0   = best_note.velocity
            best_note.velocity  = self._vel_from_y(y)
            self.viewport().update()

    def _draw_velocity_lane(self, painter: QPainter) -> None:
        """在视口底部绘制力度编辑条"""
        vw = self.viewport().width()
        vh = self.viewport().height()
        vy = vh - _VEL_LANE_H   # 力度条顶部 y

        # 背景
        painter.fillRect(PIANO_KEY_WIDTH, vy, vw - PIANO_KEY_WIDTH, _VEL_LANE_H,
                         QColor(20, 20, 30))
        # 左侧标签区
        painter.fillRect(0, vy, PIANO_KEY_WIDTH, _VEL_LANE_H, QColor(22, 22, 32))
        painter.setPen(QPen(QColor(100, 100, 130)))
        painter.setFont(QFont("Arial", 8))
        painter.drawText(0, vy, PIANO_KEY_WIDTH, _VEL_LANE_H,
                         Qt.AlignmentFlag.AlignCenter, "VEL")
        # 分隔线
        painter.setPen(QPen(QColor(50, 50, 70)))
        painter.drawLine(0, vy, vw, vy)
        # 50% 参考线（虚线）
        mid_y = vy + _VEL_LANE_H // 2
        painter.setPen(QPen(QColor(45, 45, 65), 1, Qt.PenStyle.DotLine))
        painter.drawLine(PIANO_KEY_WIDTH, mid_y, vw, mid_y)

        if not self._project:
            return

        painter.setClipRect(PIANO_KEY_WIDTH, vy, vw - PIANO_KEY_WIDTH, _VEL_LANE_H)
        for track in self._project.tracks:
            if track.muted:
                continue
            base_color = QColor(track.color)
            for note in track.notes:
                nx = self._beat_to_x(note.start_beat)
                nw = max(note.duration_beats * self._ppb - 2, 2.0)
                if nx + nw < PIANO_KEY_WIDTH or nx > vw:
                    continue
                bar_h   = max(2, int(note.velocity / 127 * (_VEL_LANE_H - 8)))
                bar_x   = max(nx, float(PIANO_KEY_WIDTH))
                bar_y   = float(vh - 4 - bar_h)
                bar_w   = nw - max(0.0, PIANO_KEY_WIDTH - nx)
                selected = id(note) in self._selected
                fill     = base_color.lighter(150) if selected else base_color
                painter.fillRect(QRectF(bar_x, bar_y, max(bar_w, 1.0), float(bar_h)), fill)
                # 顶部亮条（手柄）
                painter.fillRect(QRectF(bar_x, bar_y, max(bar_w, 1.0), 2.0),
                                  fill.lighter(130))
        painter.setClipping(False)

    # ── 选中音符批量操作 ──────────────────────────────────────

    def _selected_notes(self) -> List[Tuple[Note, Track]]:
        if not self._project:
            return []
        return [
            (n, t) for t in self._project.tracks
            for n in t.notes if id(n) in self._selected
        ]

    def move_selected_pitch(self, delta: int) -> None:
        """上下移动音高，delta=+1 升半音，-1 降半音"""
        pairs = self._selected_notes()
        if not pairs:
            return
        # 边界检查
        if any(not (MIDI_MIN_PITCH <= n.pitch + delta <= MIDI_MAX_PITCH) for n, _ in pairs):
            return
        self.pre_change.emit()
        new_ids = set()
        for note, track in pairs:
            idx = track.notes.index(note)
            track.notes[idx] = Note(note.pitch + delta, note.start_beat,
                                    note.duration_beats, note.velocity)
            new_ids.add(id(track.notes[idx]))
        self._selected = new_ids
        self.viewport().update()
        self.notes_changed.emit()

    def move_selected_beat(self, delta: float) -> None:
        """左右移动时间位置"""
        pairs = self._selected_notes()
        if not pairs:
            return
        if any(n.start_beat + delta < 0 for n, _ in pairs):
            return
        self.pre_change.emit()
        new_ids = set()
        for note, track in pairs:
            idx = track.notes.index(note)
            track.notes[idx] = Note(note.pitch, note.start_beat + delta,
                                    note.duration_beats, note.velocity)
            new_ids.add(id(track.notes[idx]))
        self._selected = new_ids
        self.viewport().update()
        self.notes_changed.emit()

    def change_selected_duration(self, factor: float) -> None:
        """缩放选中音符时值，factor=2.0 加倍，0.5 减半"""
        pairs = self._selected_notes()
        if not pairs:
            return
        self.pre_change.emit()
        new_ids = set()
        for note, track in pairs:
            idx = track.notes.index(note)
            new_dur = max(0.25, note.duration_beats * factor)
            track.notes[idx] = Note(note.pitch, note.start_beat, new_dur, note.velocity)
            new_ids.add(id(track.notes[idx]))
        self._selected = new_ids
        self.viewport().update()
        self.notes_changed.emit()

    def duplicate_selected(self) -> None:
        """在选中音符末尾复制一份（Ctrl+D）"""
        pairs = self._selected_notes()
        if not pairs or not self._project:
            return
        max_end = max(n.end_beat for n, _ in pairs)
        self.pre_change.emit()
        new_ids = set()
        for note, track in pairs:
            offset   = max_end - min(n.start_beat for n, _ in pairs)
            new_note = Note(note.pitch, note.start_beat + offset,
                            note.duration_beats, note.velocity)
            track.add_note(new_note)
            new_ids.add(id(new_note))
        self._selected = new_ids
        self.viewport().update()
        self.notes_changed.emit()

    def quantize_selected(self, grid: Optional[float] = None) -> None:
        """将选中音符的起始拍和时值量化到指定网格（默认使用当前吸附精度）"""
        pairs = self._selected_notes()
        if not pairs:
            return
        q = grid if grid is not None else self._snap_grid
        self.pre_change.emit()
        new_ids: Set[int] = set()
        for note, track in pairs:
            idx = track.notes.index(note)
            new_start = round(note.start_beat / q) * q
            new_dur   = max(q, round(note.duration_beats / q) * q)
            track.notes[idx] = Note(note.pitch, new_start, new_dur, note.velocity)
            new_ids.add(id(track.notes[idx]))
        self._selected = new_ids
        self.viewport().update()
        self.notes_changed.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        # 键盘弹奏模式：放行所有按键让其冒泡到 MainWindow.keyPressEvent
        if self._input_active:
            event.ignore()
            return

        key  = event.key()
        mods = event.modifiers()
        ctrl  = bool(mods & Qt.KeyboardModifier.ControlModifier)

        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
        elif key == Qt.Key.Key_Up:
            delta = 12 if ctrl else 1
            self.move_selected_pitch(delta)
        elif key == Qt.Key.Key_Down:
            delta = 12 if ctrl else 1
            self.move_selected_pitch(-delta)
        elif key == Qt.Key.Key_Left:
            self.move_selected_beat(-self._snap_beat(0.5))
        elif key == Qt.Key.Key_Right:
            self.move_selected_beat(self._snap_beat(0.5))
        elif key == Qt.Key.Key_W:
            self.change_selected_duration(2.0)
        elif key == Qt.Key.Key_Q:
            self.change_selected_duration(0.5)
        else:
            super().keyPressEvent(event)
