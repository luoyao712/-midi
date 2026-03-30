"""
MIDI 导入模块
将 .mid 文件解析为 Project 数据结构
"""
from __future__ import annotations
import os
import mido
from mido import MidiFile

from core.note_model import Project, Track, Note, TimeSignature

TICKS_PER_BEAT_FALLBACK = 480


def import_midi(file_path: str) -> Project:
    """
    读取 MIDI 文件，返回 Project 对象

    Parameters
    ----------
    file_path : str  .mid 文件路径
    """
    midi_file    = MidiFile(file_path)
    tpb          = midi_file.ticks_per_beat or TICKS_PER_BEAT_FALLBACK
    project_name = os.path.splitext(os.path.basename(file_path))[0]
    project      = Project(title=project_name)

    bpm      = 120
    time_sig = TimeSignature(4, 4)
    # (tick, bpm) 全局变速点，稍后转换为拍位
    _raw_tempos: list[tuple[int, int]] = []

    for i, midi_track in enumerate(midi_file.tracks):
        # ── 解析全局信息（通常在第一个轨道）──────────────────
        abs_tick = 0
        for msg in midi_track:
            abs_tick += msg.time
            if msg.type == 'set_tempo':
                tick_bpm = int(60_000_000 / msg.tempo)
                if not _raw_tempos:
                    bpm = tick_bpm          # 第一个 tempo → 全局基准
                _raw_tempos.append((abs_tick, tick_bpm))
            elif msg.type == 'time_signature':
                time_sig = TimeSignature(msg.numerator, msg.denominator)

        # ── 按通道分组音符 ────────────────────────────────────
        notes_by_channel = _extract_notes_by_channel(midi_track, tpb)
        track_name       = _get_track_name(midi_track) or f"轨道 {i + 1}"
        multi_channel    = len(notes_by_channel) > 1

        for channel, notes in sorted(notes_by_channel.items()):
            if not notes:
                continue
            name       = f"{track_name} Ch{channel + 1}" if multi_channel else track_name
            instrument = _get_instrument_for_channel(midi_track, channel)
            track            = project.add_track(name)
            track.notes      = notes
            track.instrument = instrument

    project.tempo    = bpm
    project.time_sig = time_sig

    # ── 将 tick-based tempo 事件转换为 beat-based tempo_changes ──
    # 需要把 tick 转成拍（tick / tpb），但速度本身会影响时间流逝。
    # MIDI tempo_changes 是基于 tick 的，而 Project 用拍位，直接换算即可。
    if len(_raw_tempos) > 1:
        for tick, t_bpm in _raw_tempos[1:]:   # 跳过第一个（已作为全局 bpm）
            beat = tick / tpb
            project.add_tempo_change(beat, t_bpm)

    return project


def _extract_notes_by_channel(midi_track, tpb: int) -> dict[int, list[Note]]:
    """将 MIDI 轨道中的 note_on/note_off 事件按通道分组，转换为 Note 列表"""
    # (pitch, channel) -> (on_tick, velocity)
    active: dict[tuple[int, int], tuple[int, int]] = {}
    channels_notes: dict[int, list[Note]] = {}

    abs_tick = 0
    for msg in midi_track:
        abs_tick += msg.time
        if msg.type == 'note_on' and msg.velocity > 0:
            active[(msg.note, msg.channel)] = (abs_tick, msg.velocity)
        elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
            key = (msg.note, msg.channel)
            if key in active:
                on_tick, velocity  = active.pop(key)
                start_beat         = on_tick / tpb
                duration_beats     = (abs_tick - on_tick) / tpb
                if duration_beats > 0:
                    ch = msg.channel
                    if ch not in channels_notes:
                        channels_notes[ch] = []
                    channels_notes[ch].append(Note(
                        pitch          = msg.note,
                        start_beat     = start_beat,
                        duration_beats = duration_beats,
                        velocity       = velocity,
                    ))

    # 处理文件末尾未收到 note_off 的孤立 note_on（给予 0.5 拍默认时长）
    for (pitch, channel), (on_tick, velocity) in active.items():
        ch = channel
        if ch not in channels_notes:
            channels_notes[ch] = []
        channels_notes[ch].append(Note(
            pitch          = pitch,
            start_beat     = on_tick / tpb,
            duration_beats = 0.5,
            velocity       = velocity,
        ))

    for notes in channels_notes.values():
        notes.sort(key=lambda n: n.start_beat)

    return channels_notes


def _get_track_name(midi_track) -> str:
    for msg in midi_track:
        if msg.type == 'track_name':
            return msg.name
    return ""


def _get_instrument_for_channel(midi_track, channel: int) -> int:
    for msg in midi_track:
        if msg.type == 'program_change' and msg.channel == channel:
            return msg.program
    return 0
