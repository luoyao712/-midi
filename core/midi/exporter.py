"""
MIDI 导出模块
将 Project 数据结构导出为标准 .mid 文件
"""
from __future__ import annotations
import mido
from mido import MidiFile, MidiTrack, Message, MetaMessage

from core.note_model import Project, Track, Note


# MIDI Ticks per beat（分辨率）
TICKS_PER_BEAT = 480


def _beats_to_ticks(beats: float) -> int:
    return int(beats * TICKS_PER_BEAT)


def _bpm_to_tempo(bpm: int) -> int:
    """BPM -> MIDI tempo（微秒/拍）"""
    return int(60_000_000 / bpm)


def _midi_text(text: str) -> str:
    """
    将文本编码为 MIDI MetaMessage 可接受的 latin-1 字符串。
    对于含中文等非 latin-1 字符，先编码为 UTF-8 字节再以 latin-1 解读，
    使 UTF-8 字节原样写入 MIDI 文件。大多数现代 DAW 均能正确识别。
    """
    try:
        text.encode('latin-1')
        return text            # 纯 ASCII / latin-1，直接用
    except UnicodeEncodeError:
        return text.encode('utf-8').decode('latin-1')


def export_midi(project: Project, output_path: str) -> None:
    """
    将 Project 导出为 MIDI 文件

    Parameters
    ----------
    project     : Project  要导出的项目
    output_path : str      输出文件路径（.mid）
    """
    midi_file = MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT)

    # ── 全局轨道（Tempo / 拍号 / 歌曲名 / 变速点）───────────
    global_track = MidiTrack()
    midi_file.tracks.append(global_track)

    # 收集所有全局事件（绝对 tick），统一排序后转 delta
    global_events: list[tuple[int, MetaMessage]] = []
    global_events.append((0, MetaMessage('set_tempo',      tempo=_bpm_to_tempo(project.tempo), time=0)))
    global_events.append((0, MetaMessage('time_signature', numerator=project.time_sig.numerator,
                                          denominator=project.time_sig.denominator, time=0)))
    global_events.append((0, MetaMessage('track_name',     name=_midi_text(project.title), time=0)))

    # 分段变速点
    for tc in sorted(project.tempo_changes, key=lambda t: t.beat):
        tick = _beats_to_ticks(tc.beat)
        global_events.append((tick, MetaMessage('set_tempo', tempo=_bpm_to_tempo(tc.bpm), time=0)))

    global_events.sort(key=lambda e: e[0])
    prev_tick = 0
    for abs_tick, msg in global_events:
        global_track.append(msg.copy(time=abs_tick - prev_tick))
        prev_tick = abs_tick
    global_track.append(MetaMessage('end_of_track', time=0))

    # ── 每个轨道 ─────────────────────────────────────────────
    # 跳过 MIDI channel 9（GM 鼓组），非打击乐轨道不应分配到该通道
    _used_channels: list[int] = []

    def _next_channel() -> int:
        for ch in range(16):
            if ch == 9:
                continue   # 保留给鼓组
            if ch not in _used_channels:
                _used_channels.append(ch)
                return ch
        # 超过 15 条非鼓轨道时循环复用（避免崩溃）
        ch = _used_channels[-1] % 16
        _used_channels.append(ch)
        return ch

    for track in project.tracks:
        if not track.notes:
            continue

        channel = _next_channel()

        midi_track = MidiTrack()
        midi_file.tracks.append(midi_track)

        # 轨道名称
        midi_track.append(MetaMessage('track_name', name=_midi_text(track.name), time=0))

        # 乐器（Program Change）
        midi_track.append(Message(
            'program_change',
            program = track.instrument,
            channel = channel,
            time    = 0
        ))

        # 轨道音量（CC7），将 0-100% 映射到 0-127
        cc7_val = max(0, min(127, int(getattr(track, 'volume', 100) * 127 / 100)))
        midi_track.append(Message('control_change', control=7, value=cc7_val,
                                  channel=channel, time=0))

        # 将音符转换为绝对 tick 的 note_on/note_off 事件
        events: list[tuple[int, Message]] = []
        for note in track.notes:
            on_tick  = _beats_to_ticks(note.start_beat)
            off_tick = _beats_to_ticks(note.end_beat)
            velocity = max(0, min(127, note.velocity))   # 限幅防止 mido 异常
            events.append((on_tick,  Message('note_on',  note=note.pitch, velocity=velocity, channel=channel, time=0)))
            events.append((off_tick, Message('note_off', note=note.pitch, velocity=0,        channel=channel, time=0)))

        # 按绝对 tick 排序（同 tick 下 note_off 先于 note_on，避免同音高连续音符丢失）
        events.sort(key=lambda e: (e[0], 0 if e[1].type == 'note_off' else 1))

        # 转换为相对 tick（delta time）
        prev_tick = 0
        for abs_tick, msg in events:
            delta = abs_tick - prev_tick
            midi_track.append(msg.copy(time=delta))
            prev_tick = abs_tick

        # End of Track
        midi_track.append(MetaMessage('end_of_track', time=0))

    midi_file.save(output_path)
    print(f"[exporter] MIDI 已保存：{output_path}")
