"""
音频播放模块
优先使用 FluidSynth（真实钢琴音色），降级到 pygame MIDI（系统合成器）

使用方式：
    player = AudioPlayer()
    player.load_project(project)
    player.play()
    player.pause()
    player.stop()
    player.seek(beat=4.0)
"""
from __future__ import annotations
import math
import threading
import time
from typing import Callable, Optional, List, Tuple

from core.note_model import Project, Note
from config import SOUNDFONT_PATH, DEFAULT_TEMPO

# 快速衰减乐器（钢琴族 0-7、半音打击乐 8-15、吉他族 24-31）：
# 超过此拍数截断 note_off，避免 SoundFont 已自然衰减完但音符仍"开着"造成静音期
_FAST_DECAY_PROGRAMS  = frozenset(range(0, 16)) | frozenset(range(24, 32))
_FAST_DECAY_MAX_BEATS = 4.0   # 钢琴/吉他音符最长发音拍数（约 2s @ 120BPM）


# ─── 变速辅助 ────────────────────────────────────────────────

def _elapsed_to_beat(elapsed: float, start_beat: float, base_tempo: int, tempo_changes: list) -> float:
    """
    将从 start_beat 出发、已经过 elapsed 秒转换为当前拍位。
    考虑 tempo_changes 列表中的分段变速（每个元素有 .beat 和 .bpm 属性）。
    """
    # 确定 start_beat 时刻的初始 BPM
    current_bpm = base_tempo
    for tc in sorted(tempo_changes, key=lambda t: t.beat):
        if tc.beat <= start_beat:
            current_bpm = tc.bpm

    # start_beat 之后的变速点（升序）
    later = sorted([tc for tc in tempo_changes if tc.beat > start_beat], key=lambda t: t.beat)

    current_beat = start_beat
    remaining    = elapsed

    for tc in later:
        bps          = current_bpm / 60.0
        seg_duration = (tc.beat - current_beat) / bps
        if remaining <= seg_duration:
            return current_beat + remaining * bps
        remaining    -= seg_duration
        current_beat  = tc.beat
        current_bpm   = tc.bpm

    return current_beat + remaining * (current_bpm / 60.0)


# ─── 后端枚举 ────────────────────────────────────────────────

class Backend:
    FLUIDSYNTH = "fluidsynth"
    PYGAME     = "pygame"


# ─── 统一播放接口 ─────────────────────────────────────────────

class AudioPlayer:
    """
    音频播放器
    Signals（回调）:
        on_beat_changed(beat: float)   当前播放位置更新
        on_playback_stopped()          播放结束
    """

    def __init__(self):
        self._project: Optional[Project] = None
        self._tempo: int                 = DEFAULT_TEMPO
        self._backend: str               = self._detect_backend()

        self._playing:     bool           = False
        self._current_beat: float         = 0.0
        self._thread:       Optional[threading.Thread] = None
        self._lock          = threading.Lock()
        self._midi_lock     = threading.Lock()   # 保护 FluidSynth/MIDI 并发调用

        # 节拍器
        self.metronome_enabled: bool = False

        # 回调
        self.on_beat_changed:      Optional[Callable[[float], None]]  = None
        # on_playback_stopped(ended_naturally: bool)
        # ended_naturally=True 表示播到结尾；False 表示外部暂停/停止
        self.on_playback_stopped:  Optional[Callable[[bool], None]]   = None

        # FluidSynth 实例
        self._fs   = None
        self._sfid = None

        self._init_backend()

    # ── 后端检测 ──────────────────────────────────────────────

    def _detect_backend(self) -> str:
        # 先把 piano/ 和项目根目录加入 DLL 搜索路径
        try:
            import os as _os
            _base = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
            for _sub in ("", "piano"):
                _dir = _os.path.join(_base, _sub) if _sub else _base
                if _os.path.isdir(_dir):
                    try:
                        _os.add_dll_directory(_dir)
                    except (AttributeError, OSError):
                        pass
        except Exception:
            pass
        try:
            import fluidsynth
            return Backend.FLUIDSYNTH
        except (ImportError, FileNotFoundError, OSError):
            pass
        try:
            import pygame.midi
            return Backend.PYGAME
        except (ImportError, Exception):
            pass
        return "none"

    def _init_backend(self) -> None:
        if self._backend == Backend.FLUIDSYNTH:
            self._init_fluidsynth()
        elif self._backend == Backend.PYGAME:
            self._init_pygame()
        else:
            print("[AudioPlayer] 警告：未找到可用音频后端，播放功能不可用")
            print("  请运行：pip install pyfluidsynth  或  pip install pygame")

    def _init_fluidsynth(self) -> None:
        try:
            import fluidsynth
            import os as _os
            # 将根目录和 piano 子目录都加入 DLL 搜索路径
            _base = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
            for _sub in ("", "piano"):
                _dir = _os.path.join(_base, _sub) if _sub else _base
                if _os.path.isdir(_dir):
                    try:
                        _os.add_dll_directory(_dir)
                    except (AttributeError, OSError):
                        pass
            self._fs = fluidsynth.Synth(gain=0.8, samplerate=44100.0)
            # 减小缓冲区以降低播放延迟（period-size 256 ≈ 5.8ms @ 44100Hz）
            try:
                self._fs.setting('audio.period-size', 256)
                self._fs.setting('audio.periods', 2)
            except Exception:
                pass
            # wasapi 跟随 Windows 默认音频设备（含蓝牙耳机），dsound 不支持
            for _drv in ("wasapi", "dsound", "winmm"):
                try:
                    self._fs.start(driver=_drv)
                    print(f"[AudioPlayer] FluidSynth 音频驱动：{_drv}")
                    break
                except Exception:
                    continue
            if SOUNDFONT_PATH and __import__("os").path.exists(SOUNDFONT_PATH):
                self._sfid = self._fs.sfload(SOUNDFONT_PATH)
                self._fs.program_select(0, self._sfid, 0, 0)
                print(f"[AudioPlayer] FluidSynth 初始化成功，音色：{SOUNDFONT_PATH}")
            else:
                print(f"[AudioPlayer] SoundFont 未找到：{SOUNDFONT_PATH}")
                print("  请下载 SoundFont 并在 config.py 中设置 SOUNDFONT_PATH")
        except Exception as e:
            print(f"[AudioPlayer] FluidSynth 初始化失败：{e}，降级到 pygame")
            self._backend = Backend.PYGAME
            self._init_pygame()

    def _init_pygame(self) -> None:
        try:
            import pygame
            import pygame.midi
            pygame.init()
            pygame.midi.init()
            default_id = pygame.midi.get_default_output_id()
            if default_id == -1:
                raise RuntimeError("未找到 MIDI 输出设备")
            self._midi_out = pygame.midi.Output(default_id)
            self._midi_out.set_instrument(0)  # 钢琴
            print(f"[AudioPlayer] pygame MIDI 初始化成功，设备 ID={default_id}")
        except Exception as e:
            print(f"[AudioPlayer] pygame MIDI 初始化失败：{e}")
            self._backend = "none"

    # ── 公开接口 ──────────────────────────────────────────────

    def preview_note(self, pitch: int, velocity: int = 80, duration_ms: int = 400) -> None:
        """
        立即发出单个音符预览音，duration_ms 后自动停止
        在后台线程中执行，不阻塞 UI
        """
        if self._backend == "none":
            return
        def _play():
            self._note_on(pitch, velocity)
            time.sleep(duration_ms / 1000)
            self._note_off(pitch)
        threading.Thread(target=_play, daemon=True).start()

    def load_project(self, project: Project) -> None:
        """加载项目（播放前调用）。
        只停止播放线程，不触发 on_beat_changed/on_playback_stopped 回调，
        避免 UI 意外滚回开头（调用方若需要重置游标，应自行处理）。
        """
        self._playing = False
        self._all_notes_off()
        with self._lock:
            self._project = project
            self._tempo   = project.tempo
        self._current_beat = 0.0

    def play(self) -> None:
        """从当前位置开始播放"""
        if self._project is None:
            return
        if self._playing:
            return
        # 等待上一个播放线程退出，避免新旧线程并发发送 MIDI 事件互相干扰
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.15)
        self._playing = True
        self._thread  = threading.Thread(target=self._playback_thread, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        """暂停播放，保留当前位置"""
        self._playing = False
        self._all_notes_off()

    def stop(self) -> None:
        """停止并回到开头"""
        self._playing       = False
        self._current_beat  = 0.0
        self._all_notes_off()
        if self.on_beat_changed:
            self.on_beat_changed(0.0)

    def seek(self, beat: float) -> None:
        """跳转到指定拍位"""
        was_playing       = self._playing
        self._playing     = False
        self._all_notes_off()
        with self._lock:
            self._current_beat = max(0.0, beat)
        if was_playing:
            self.play()

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def current_beat(self) -> float:
        return self._current_beat

    # ── 播放线程 ──────────────────────────────────────────────

    def _playback_thread(self) -> None:
        """
        实时播放：预排列所有 note_on/note_off 事件，
        按时间戳精确触发（支持段落变速、同音高重叠引用计数）
        """
        project = self._project
        if project is None:
            return

        start_beat      = self._current_beat
        start_real_time = time.perf_counter()
        tempo_changes   = list(project.tempo_changes)   # 快照，播放中不受外部修改影响

        total_beats = project.total_beats()

        # 构建事件列表 (beat, event_type, pitch, velocity, channel, track)
        # 包含所有轨道（静音状态在触发时实时判断，支持播放中动态静音）
        events = []
        for ch_idx, track in enumerate(project.tracks):
            channel = ch_idx % 16  # MIDI 只有 16 个 channel
            for note in track.notes:
                if note.end_beat <= start_beat:
                    continue
                on_beat  = max(note.start_beat, start_beat)
                off_beat = note.end_beat
                # 快速衰减乐器：截断过长音符，防止 SoundFont 已衰减完但 note_off 未发造成静音期
                if track.instrument in _FAST_DECAY_PROGRAMS:
                    off_beat = min(off_beat, on_beat + _FAST_DECAY_MAX_BEATS)
                events.append((on_beat,  'on',  note.pitch, note.velocity, channel, track))
                events.append((off_beat, 'off', note.pitch, 0,             channel, track))

        # 节拍器：每整数拍插入一个打击音（MIDI 通道 9 = GM 打击乐）
        # 强拍（每小节第 1 拍）用 High Wood Block(76) 强力度，弱拍用 Side Stick(37) 低力度
        if self.metronome_enabled:
            bpm_per_measure = project.beats_per_measure()
            first_click = math.ceil(start_beat - 1e-6)  # 第一个 >= start_beat 的整数拍
            click_beat  = float(first_click)
            while click_beat <= total_beats:
                is_accent = (round(click_beat) % bpm_per_measure == 0)
                if is_accent:
                    pitch_c, vel_c = 76, 100   # High Wood Block，强拍
                else:
                    pitch_c, vel_c = 37, 65    # Side Stick，弱拍
                events.append((click_beat,        'click_on',  pitch_c, vel_c, 9, None))
                events.append((click_beat + 0.08, 'click_off', pitch_c, 0,     9, None))
                click_beat += 1.0

        # 排序：同拍位时 off/click_off 先于 on/click_on（防止同拍位 note_off 晚于 note_on）
        _ORDER = {'off': 0, 'click_off': 0, 'on': 1, 'click_on': 1}
        events.sort(key=lambda e: (e[0], _ORDER.get(e[1], 9)))

        event_idx       = 0
        ended_naturally = False

        # 同（音高, 通道）引用计数：防止重叠音符互相切断
        note_counts: dict = {}     # (pitch, channel) -> int

        while self._playing:
            now_real = time.perf_counter()
            elapsed  = now_real - start_real_time
            now_beat = _elapsed_to_beat(elapsed, start_beat, project.tempo, tempo_changes)

            with self._lock:
                self._current_beat = now_beat

            # ① 先触发 MIDI 事件（时间敏感，不允许被 UI 回调延迟）
            while event_idx < len(events) and events[event_idx][0] <= now_beat:
                ev_beat, etype, pitch, velocity, channel, track = events[event_idx]
                key = (pitch, channel)
                if etype == 'on':
                    if not track.muted:
                        cnt = note_counts.get(key, 0)
                        note_counts[key] = cnt + 1
                        if cnt == 0:            # 只在第一个 note_on 时发声
                            vol = max(0, min(100, getattr(track, 'volume', 100)))
                            scaled_vel = max(1, int(velocity * vol / 100))
                            self._note_on(pitch, scaled_vel, channel)
                elif etype == 'off':
                    cnt = note_counts.get(key, 0)
                    if cnt > 0:
                        note_counts[key] = cnt - 1
                        if cnt == 1:            # 引用数归零才真正 note_off
                            self._note_off(pitch, channel)
                elif etype == 'click_on':
                    self._note_on(pitch, velocity, channel)
                elif etype == 'click_off':
                    self._note_off(pitch, channel)
                event_idx += 1

            # 播放到结尾
            if now_beat >= total_beats:
                ended_naturally = True
                break

            # ③ 再通知 UI（不影响 MIDI 时序）
            if self.on_beat_changed:
                self.on_beat_changed(now_beat)

            time.sleep(0.005)  # 5ms 精度

        self._playing = False
        # 自然播完才重置位置；暂停时保留 _current_beat 供继续播放用
        if ended_naturally:
            self._current_beat = 0.0
        self._all_notes_off()
        if self.on_playback_stopped:
            self.on_playback_stopped(ended_naturally)

    # ── MIDI 发音 ──────────────────────────────────────────────

    def _set_expression(self, channel: int, value: int) -> None:
        """设置 MIDI CC11 Expression（0-127），用于渐弱效果"""
        with self._midi_lock:
            try:
                if self._backend == Backend.FLUIDSYNTH and self._fs:
                    self._fs.cc(channel, 11, value)
                elif self._backend == Backend.PYGAME:
                    self._midi_out.write_short(0xB0 | (channel & 0x0F), 11, value)
            except Exception:
                pass

    def _note_on(self, pitch: int, velocity: int, channel: int = 0) -> None:
        with self._midi_lock:
            try:
                if self._backend == Backend.FLUIDSYNTH and self._fs:
                    self._fs.noteon(channel, pitch, velocity)
                elif self._backend == Backend.PYGAME:
                    self._midi_out.note_on(pitch, velocity, channel)
            except Exception:
                pass

    def _note_off(self, pitch: int, channel: int = 0) -> None:
        with self._midi_lock:
            try:
                if self._backend == Backend.FLUIDSYNTH and self._fs:
                    self._fs.noteoff(channel, pitch)
                elif self._backend == Backend.PYGAME:
                    self._midi_out.note_off(pitch, 0, channel)
            except Exception:
                pass

    def silence_channel(self, channel: int) -> None:
        """立即停止指定 MIDI 通道的所有发音（播放中动态静音时调用）"""
        with self._midi_lock:
            try:
                if self._backend == Backend.FLUIDSYNTH and self._fs:
                    self._fs.cc(channel, 123, 0)   # All Notes Off
                elif self._backend == Backend.PYGAME:
                    for pitch in range(128):
                        self._midi_out.note_off(pitch, 0, channel)
            except Exception:
                pass

    def _all_notes_off(self) -> None:
        """发送 All Notes Off 消息，清除所有延音，并还原 CC11 Expression"""
        with self._midi_lock:
            try:
                if self._backend == Backend.FLUIDSYNTH and self._fs:
                    for ch in range(16):
                        self._fs.cc(ch, 123, 0)   # All Notes Off
                        self._fs.cc(ch, 11, 127)  # 还原 Expression（渐弱后重置）
                elif self._backend == Backend.PYGAME:
                    for pitch in range(128):
                        for ch in range(16):
                            self._midi_out.note_off(pitch, 0, ch)
                    for ch in range(16):
                        try:
                            self._midi_out.write_short(0xB0 | ch, 11, 127)
                        except Exception:
                            pass
            except Exception:
                pass

    def close(self) -> None:
        """释放资源（程序退出时调用）"""
        self.stop()
        try:
            if self._backend == Backend.FLUIDSYNTH and self._fs:
                self._fs.delete()
            elif self._backend == Backend.PYGAME:
                import pygame.midi
                self._midi_out.close()
                pygame.midi.quit()
        except Exception:
            pass
