"""
轨道面板
显示在 Piano Roll 左侧，列出所有轨道，提供静音、颜色、添加/删除操作
"""
from __future__ import annotations
from typing import Callable, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QFrame, QColorDialog,
    QInputDialog, QSizePolicy, QSlider,
)
from PyQt6.QtCore  import Qt, pyqtSignal
from PyQt6.QtGui   import QColor, QPalette

from core.note_model import Project, Track


class TrackRow(QFrame):
    """单行轨道控件"""

    mute_toggled    = pyqtSignal(object)   # Track
    solo_toggled    = pyqtSignal(object)   # Track
    color_changed   = pyqtSignal(object)   # Track
    delete_clicked  = pyqtSignal(object)   # Track
    name_changed    = pyqtSignal(object)   # Track
    volume_changed  = pyqtSignal(object)   # Track
    selected        = pyqtSignal(object)   # Track

    def __init__(self, track: Track, parent=None):
        super().__init__(parent)
        self.track        = track
        self._is_selected = False
        self._build_ui()
        self._update_style()

    # ── 谱号辅助 ──────────────────────────────────────────────

    _CLEF_BADGE = {
        'treble': ('G', '#4488FF', '高音谱号 (G 谱号)'),
        'bass':   ('F', '#FF7744', '低音谱号 (F 谱号)'),
        'alto':   ('C', '#9966FF', '中音谱号 (C 谱号)'),
        'tenor':  ('C', '#44AAFF', '次中音谱号 (C 谱号)'),
    }

    def _resolved_clef(self) -> str:
        """若 clef == 'auto'，从音符音域推断；否则直接返回"""
        clef = self.track.clef
        if clef != 'auto':
            return clef
        if self.track.notes:
            avg = sum(n.pitch for n in self.track.notes) / len(self.track.notes)
            return 'treble' if avg >= 60 else 'bass'
        return ''

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        # 颜色块（点击改色）
        self.color_btn = QPushButton()
        self.color_btn.setFixedSize(14, 14)
        self.color_btn.setToolTip("点击更改颜色")
        self.color_btn.clicked.connect(self._on_color_click)
        layout.addWidget(self.color_btn)

        # 谱号徽章
        self.clef_badge = QLabel()
        self.clef_badge.setFixedSize(18, 18)
        self.clef_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.clef_badge)
        self._refresh_clef_badge()

        # 轨道名（双击改名）
        self.name_label = QLabel(self.track.name)
        self.name_label.setFixedWidth(82)
        self.name_label.setToolTip("双击重命名")
        self.name_label.mouseDoubleClickEvent = lambda _: self._on_rename()
        layout.addWidget(self.name_label)

        # 音量滑条
        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(getattr(self.track, 'volume', 100))
        self.vol_slider.setFixedWidth(52)
        self.vol_slider.setToolTip(f"音量：{self.vol_slider.value()}%")
        self.vol_slider.valueChanged.connect(self._on_volume_changed)
        layout.addWidget(self.vol_slider)

        layout.addSpacing(2)

        # 独奏按钮
        self.solo_btn = QPushButton("S")
        self.solo_btn.setFixedSize(22, 22)
        self.solo_btn.setCheckable(True)
        self.solo_btn.setToolTip("独奏（只播放本轨道）")
        self.solo_btn.clicked.connect(self._on_solo)
        layout.addWidget(self.solo_btn)

        # 静音按钮
        self.mute_btn = QPushButton("M")
        self.mute_btn.setFixedSize(22, 22)
        self.mute_btn.setCheckable(True)
        self.mute_btn.setChecked(self.track.muted)
        self.mute_btn.setToolTip("静音 (M)")
        self.mute_btn.clicked.connect(self._on_mute)
        layout.addWidget(self.mute_btn)

        # 删除按钮
        del_btn = QPushButton("✕")
        del_btn.setFixedSize(22, 22)
        del_btn.setToolTip("删除轨道")
        del_btn.clicked.connect(lambda: self.delete_clicked.emit(self.track))
        layout.addWidget(del_btn)

        self._refresh_color_btn()

    def mousePressEvent(self, event) -> None:
        self.selected.emit(self.track)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self._is_selected = selected
        self._update_style()

    def _update_style(self) -> None:
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("TrackRow")
        muted    = self.track.muted
        sel      = self._is_selected
        if sel:
            bg     = "#1E3560"
            border = "#3366CC"
            hover  = "#2A4070"
        elif muted:
            bg     = "#1C1C2C"
            border = "#2A2A3A"
            hover  = "#222232"
        else:
            bg     = "#2A2A3E"
            border = "#3A3A5A"
            hover  = "#333350"
        label_color = "#888899" if muted else "#CCCCDD"
        self.setStyleSheet(f"""
            TrackRow {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 4px;
            }}
            TrackRow:hover {{ background: {hover}; }}
            QLabel {{ color: {label_color}; font-size: 12px; }}
            QPushButton {{ color: #AAAACC; border: 1px solid #4A4A6A;
                          border-radius: 3px; background: #222235; }}
            QPushButton:hover   {{ background: #333355; }}
            QPushButton[checked="true"]#mute {{ background: #884444; color: #FFAAAA; }}
            QPushButton:checked {{ background: #884444; color: #FFAAAA; }}
            QPushButton#solo:checked {{ background: #AA7700; color: #FFE066; }}
            QSlider::groove:horizontal {{
                height: 4px; background: #3A3A5A; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                width: 10px; height: 10px; margin: -3px 0;
                background: #5588CC; border-radius: 5px;
            }}
            QSlider::sub-page:horizontal {{ background: #4477AA; border-radius: 2px; }}
        """)

    def _refresh_clef_badge(self) -> None:
        clef = self._resolved_clef()
        info = self._CLEF_BADGE.get(clef)
        if info:
            symbol, color, tip = info
            self.clef_badge.setText(symbol)
            self.clef_badge.setToolTip(tip)
            self.clef_badge.setStyleSheet(
                f"color: #FFFFFF; background: {color}; border-radius: 3px;"
                f" font-size: 10px; font-weight: bold;"
            )
        else:
            self.clef_badge.setText("")
            self.clef_badge.setToolTip("")
            self.clef_badge.setStyleSheet("background: transparent;")

    def _refresh_color_btn(self) -> None:
        self.color_btn.setStyleSheet(
            f"background: {self.track.color}; border: none; border-radius: 2px;"
        )

    def _on_color_click(self) -> None:
        color = QColorDialog.getColor(
            QColor(self.track.color), self, "选择轨道颜色"
        )
        if color.isValid():
            self.track.color = color.name()
            self._refresh_color_btn()
            self.color_changed.emit(self.track)

    def _on_solo(self) -> None:
        self.solo_toggled.emit(self.track)

    def _on_volume_changed(self, value: int) -> None:
        self.track.volume = value
        self.vol_slider.setToolTip(f"音量：{value}%")
        self.volume_changed.emit(self.track)

    def _on_mute(self) -> None:
        self.track.muted = self.mute_btn.isChecked()
        self._update_style()
        self.mute_toggled.emit(self.track)

    def _on_rename(self) -> None:
        text, ok = QInputDialog.getText(self, "重命名轨道", "新名称：",
                                        text=self.track.name)
        if ok and text.strip():
            self.track.name      = text.strip()
            self.name_label.setText(self.track.name)
            self.name_changed.emit(self.track)

    def refresh(self) -> None:
        """外部调用：刷新显示"""
        self.name_label.setText(self.track.name)
        self.mute_btn.setChecked(self.track.muted)
        self.vol_slider.setValue(getattr(self.track, 'volume', 100))
        self._refresh_color_btn()
        self._refresh_clef_badge()
        self._update_style()


class TrackPanel(QWidget):
    """
    轨道面板容器
    信号:
        track_added()
        track_deleted(track)
        track_changed(track)   任何属性变化
    """

    track_added    = pyqtSignal()
    track_deleted  = pyqtSignal(object)
    track_changed  = pyqtSignal(object)
    track_selected = pyqtSignal(object)   # 用户点击选中某条轨道
    solo_changed   = pyqtSignal()         # 任意轨道 solo 状态改变

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project:      Optional[Project]  = None
        self._rows:         list[TrackRow]     = []
        self._selected_row: Optional[TrackRow] = None
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 顶部工具栏
        toolbar = QWidget()
        toolbar.setStyleSheet("background: #16162A;")
        tbar_layout = QHBoxLayout(toolbar)
        tbar_layout.setContentsMargins(6, 4, 6, 4)

        title = QLabel("轨道")
        title.setStyleSheet("color: #8888AA; font-size: 11px; font-weight: bold;")
        tbar_layout.addWidget(title)
        tbar_layout.addStretch()

        add_btn = QPushButton("+ 新建轨道")
        add_btn.setFixedHeight(24)
        add_btn.setStyleSheet("""
            QPushButton { color: #88AAFF; border: 1px solid #4455AA;
                          border-radius: 3px; background: #1E2040;
                          font-size: 11px; padding: 0 8px; }
            QPushButton:hover { background: #2A306A; }
        """)
        add_btn.clicked.connect(self._on_add_track)
        tbar_layout.addWidget(add_btn)
        outer.addWidget(toolbar)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("border: 1px solid #2A2A4A;")
        outer.addWidget(sep)

        # 轨道列表（可滚动）
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("QScrollArea { border: none; background: #1E1E2E; }")
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._list_widget = QWidget()
        self._list_widget.setStyleSheet("background: #1E1E2E;")
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(4, 4, 4, 4)
        self._list_layout.setSpacing(3)
        self._list_layout.addStretch()

        self._scroll.setWidget(self._list_widget)
        outer.addWidget(self._scroll)

    # ── 公开接口 ──────────────────────────────────────────────

    def set_project(self, project: Project) -> None:
        self._project = project
        self._rebuild()

    def refresh(self) -> None:
        """外部调用：重新构建列表"""
        self._rebuild()

    # ── 内部 ──────────────────────────────────────────────────

    def _rebuild(self) -> None:
        # 清空旧行
        for row in self._rows:
            self._list_layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()
        self._selected_row = None

        if not self._project:
            return

        for track in self._project.tracks:
            self._add_row(track)

    def mute_selected(self) -> None:
        """切换当前选中轨道的静音状态（M 键调用）"""
        if self._selected_row is None:
            # 若没有选中行则默认选第一条
            if self._rows:
                self._on_row_selected(self._rows[0])
            return
        row = self._selected_row
        row.track.muted = not row.track.muted
        row.mute_btn.setChecked(row.track.muted)
        row._update_style()
        self.track_changed.emit(row.track)

    def _on_row_selected(self, track: Track) -> None:
        """高亮选中行，取消之前的高亮，并通知外部"""
        target_row = None
        for row in self._rows:
            if row.track is track:
                target_row = row
                break
        if target_row is None:
            return
        if self._selected_row and self._selected_row is not target_row:
            self._selected_row.set_selected(False)
        self._selected_row = target_row
        target_row.set_selected(True)
        self.track_selected.emit(track)

    def _apply_solo(self, solo_track: 'Track') -> None:
        """
        独奏逻辑：若 solo_track 的 solo 按钮选中，则其他轨道暂时静音；
        若取消选中，则恢复所有轨道为 track.muted 原值。
        """
        solo_row = next((r for r in self._rows if r.track is solo_track), None)
        if solo_row is None:
            return
        is_solo = solo_row.solo_btn.isChecked()

        for row in self._rows:
            if row.track is solo_track:
                continue
            # 取消其他行的 solo（同时只能一条独奏）
            row.solo_btn.setChecked(False)
            if is_solo:
                row.track.muted = True
            else:
                row.track.muted = row.mute_btn.isChecked()
            row._update_style()
            self.track_changed.emit(row.track)

        self.solo_changed.emit()

    def _add_row(self, track: Track) -> None:
        row = TrackRow(track)
        row.mute_toggled.connect(lambda t: self.track_changed.emit(t))
        row.solo_toggled.connect(self._apply_solo)
        row.volume_changed.connect(lambda t: self.track_changed.emit(t))
        row.color_changed.connect(lambda t: self.track_changed.emit(t))
        row.name_changed.connect(lambda t: self.track_changed.emit(t))
        row.delete_clicked.connect(self._on_delete_track)
        row.selected.connect(self._on_row_selected)

        # 插入到 stretch 之前
        self._list_layout.insertWidget(len(self._rows), row)
        self._rows.append(row)

    def _on_add_track(self) -> None:
        if not self._project:
            return
        track = self._project.add_track()
        self._add_row(track)
        self.track_added.emit()

    def _on_delete_track(self, track: Track) -> None:
        if not self._project:
            return
        self._project.remove_track(track)
        self._rebuild()
        self.track_deleted.emit(track)
