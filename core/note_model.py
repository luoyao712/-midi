"""
音符数据模型
整个项目的核心数据结构，OMR识别、Piano Roll编辑、MIDI导出均基于此
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional
import uuid
import copy

from config import TRACK_COLORS, DEFAULT_VELOCITY


# ─── 音符名称工具函数 ────────────────────────────────────────

_NOTE_NAMES     = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
_NOTE_NAME_MAP  = {name: i for i, name in enumerate(_NOTE_NAMES)}


def pitch_to_name(pitch: int) -> str:
    """MIDI音高 -> 音符名称，如 60 -> 'C4'"""
    octave = (pitch // 12) - 1
    name   = _NOTE_NAMES[pitch % 12]
    return f"{name}{octave}"


def name_to_pitch(name: str) -> int:
    """音符名称 -> MIDI音高，如 'C4' -> 60"""
    # 解析末尾的八度数字（支持负数，如 C-1）
    i = len(name) - 1
    while i >= 0 and (name[i].isdigit() or name[i] == '-'):
        i -= 1
    note_part   = name[:i + 1]
    octave_part = name[i + 1:]
    note_idx    = _NOTE_NAME_MAP.get(note_part, 0)
    octave      = int(octave_part) if octave_part else 4
    return (octave + 1) * 12 + note_idx


def is_black_key(pitch: int) -> bool:
    """判断该音高是否为黑键"""
    return _NOTE_NAMES[pitch % 12].endswith('#')


# ─── 核心数据类 ──────────────────────────────────────────────

@dataclass
class Note:
    """单个音符（不可变时间单位：拍）"""
    pitch:          int    # MIDI音高 0-127（60 = 中央C / C4）
    start_beat:     float  # 开始时间（拍）
    duration_beats: float  # 持续时间（拍，1.0 = 四分音符）
    velocity:       int = DEFAULT_VELOCITY  # 力度 0-127

    @property
    def end_beat(self) -> float:
        return self.start_beat + self.duration_beats

    @property
    def name(self) -> str:
        return pitch_to_name(self.pitch)

    def clone(self) -> Note:
        return copy.copy(self)


@dataclass
class TimeSignature:
    """拍号"""
    numerator:   int = 4  # 分子（每小节拍数）
    denominator: int = 4  # 分母（以几分音符为一拍）


@dataclass
class TempoChange:
    """速度变化点（精确到拍）"""
    beat: float  # 生效拍位（拍，从 0 开始）
    bpm:  int    # 该拍位之后的速度（BPM）


@dataclass
class Track:
    """单个轨道，包含若干音符"""
    track_id:   str        = field(default_factory=lambda: str(uuid.uuid4()))
    name:       str        = "新轨道"
    notes:      List[Note] = field(default_factory=list)
    color:      str        = "#4A9EFF"
    instrument: int        = 0     # MIDI程序号（0 = 钢琴）
    muted:      bool       = False
    volume:     int        = 100   # 轨道音量 0-100（百分比）
    clef:       str        = 'auto'  # 'treble' | 'bass' | 'alto' | 'tenor' | 'auto'

    # ── 音符管理 ──────────────────────────────────────────

    def add_note(self, note: Note) -> None:
        self.notes.append(note)
        self.notes.sort(key=lambda n: n.start_beat)

    def remove_note(self, note: Note) -> None:
        if note in self.notes:
            self.notes.remove(note)

    def notes_in_range(self, start_beat: float, end_beat: float) -> List[Note]:
        """返回与给定范围有交叠的音符"""
        return [n for n in self.notes
                if n.end_beat > start_beat and n.start_beat < end_beat]

    def total_beats(self) -> float:
        if not self.notes:
            return 0.0
        return max(n.end_beat for n in self.notes)


def sharps_to_key_name(sharps: int) -> str:
    """将升降号数转换为调名（默认大调）"""
    _NAMES = {-7:"Cb",-6:"Gb",-5:"Db",-4:"Ab",-3:"Eb",-2:"Bb",-1:"F",
               0:"C", 1:"G",  2:"D",  3:"A",  4:"E",  5:"B",  6:"F#", 7:"C#"}
    return _NAMES.get(sharps, "C") + " 大调"


@dataclass
class Project:
    """整个项目，包含所有轨道和全局设置"""
    title:          str                  = "新建项目"
    tempo:          int                  = 120          # BPM（全局基准，被 tempo_changes 覆盖）
    time_sig:       TimeSignature        = field(default_factory=TimeSignature)
    key_sig:        int                  = 0            # 升降号数（-7~+7，0=C大调）
    tracks:         List[Track]          = field(default_factory=list)
    tempo_changes:  List[TempoChange]    = field(default_factory=list)  # 段落变速点，按 beat 有序

    # ── 变速管理 ──────────────────────────────────────────

    def tempo_at_beat(self, beat: float) -> int:
        """返回指定拍位处的 BPM（取小于等于该拍位的最近变速点）"""
        applicable = [tc for tc in self.tempo_changes if tc.beat <= beat]
        if applicable:
            return max(applicable, key=lambda tc: tc.beat).bpm
        return self.tempo

    def add_tempo_change(self, beat: float, bpm: int) -> None:
        """添加或更新变速点，保持列表有序"""
        self.tempo_changes = [tc for tc in self.tempo_changes if abs(tc.beat - beat) > 1e-6]
        self.tempo_changes.append(TempoChange(beat=beat, bpm=bpm))
        self.tempo_changes.sort(key=lambda tc: tc.beat)

    def remove_tempo_change(self, beat: float) -> None:
        """删除指定拍位的变速点"""
        self.tempo_changes = [tc for tc in self.tempo_changes if abs(tc.beat - beat) > 1e-6]

    # ── 轨道管理 ──────────────────────────────────────────

    def add_track(self, name: Optional[str] = None) -> Track:
        idx   = len(self.tracks)
        color = TRACK_COLORS[idx % len(TRACK_COLORS)]
        track = Track(
            name  = name or f"轨道 {idx + 1}",
            color = color,
        )
        self.tracks.append(track)
        return track

    def remove_track(self, track: Track) -> None:
        if track in self.tracks:
            self.tracks.remove(track)

    def track_by_id(self, track_id: str) -> Optional[Track]:
        for t in self.tracks:
            if t.track_id == track_id:
                return t
        return None

    # ── 项目属性 ──────────────────────────────────────────

    def total_beats(self) -> float:
        """项目总拍数（至少16拍）"""
        if not self.tracks:
            return 16.0
        max_beat = max((t.total_beats() for t in self.tracks), default=0.0)
        return max(max_beat + 4.0, 16.0)

    def beats_per_measure(self) -> int:
        return self.time_sig.numerator

    def is_empty(self) -> bool:
        return all(len(t.notes) == 0 for t in self.tracks)
