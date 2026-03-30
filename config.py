"""
全局配置文件
修改此处的参数以自定义应用行为
"""
import os

# ─── 路径 ────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# SoundFont 音色文件路径（需手动下载并放置）
SOUNDFONT_PATH = os.path.join(BASE_DIR, "GeneralUser-GS.sf2")

# Tesseract OCR 可执行文件路径（Windows 需手动指定）
TESSERACT_CMD = r"D:\download\tesseract\tesseract.exe"

# Audiveris 五线谱识别引擎路径
# 下载地址：https://github.com/Audiveris/audiveris/releases
# Windows 安装后通常在 C:\Program Files\Audiveris\bin\Audiveris.bat
# 也可以解压 zip，指向 bin\Audiveris.bat
AUDIVERIS_PATH = r"D:\download\Audiveris\Audiveris.exe"

# MuseScore 路径（用于五线谱图片/PDF 识别，精度优于 oemer）
# 常见路径：C:\Program Files\MuseScore 4\bin\MuseScore4.exe
#           C:\Program Files\MuseScore 3\bin\MuseScore3.exe
MUSESCORE_PATH = r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe"

# ─── Piano Roll 外观 ────────────────────────────────────
PIANO_KEY_WIDTH   = 80    # 左侧钢琴键盘宽度（像素）
NOTE_HEIGHT       = 12    # 每个半音的高度（像素）
HEADER_HEIGHT     = 30    # 顶部时间标尺高度（像素）
DEFAULT_PPB       = 80    # 默认每拍像素数（可缩放）
MIN_PPB           = 20    # 最小每拍像素数
MAX_PPB           = 400   # 最大每拍像素数

# MIDI 音符范围（0=C-1, 127=G9）
MIDI_MIN_PITCH = 0
MIDI_MAX_PITCH = 127

# ─── 编辑默认值 ──────────────────────────────────────────
DEFAULT_NOTE_DURATION = 1.0   # 新建音符的默认时值（拍，1.0=四分音符）
DEFAULT_VELOCITY      = 80    # 默认力度

# ─── 吸附设置 ────────────────────────────────────────────
SNAP_ENABLED = True   # 是否开启吸附
SNAP_GRID    = 1.0    # 吸附精度（拍，1.0=四分音符/一格）

# ─── 播放器 ──────────────────────────────────────────────
DEFAULT_TEMPO = 120  # 默认 BPM

# ─── 轨道颜色池 ──────────────────────────────────────────
TRACK_COLORS = [
    "#4A9EFF",  # 蓝
    "#FF6B6B",  # 红
    "#4ECDC4",  # 青
    "#FFE66D",  # 黄
    "#A8E6CF",  # 绿
    "#FF8B94",  # 粉
    "#C3B1E1",  # 紫
    "#FFD3A5",  # 橙
]

# ─── 主题色 ──────────────────────────────────────────────
COLOR_BG_DARK       = "#1E1E2E"   # 主背景
COLOR_BG_MID        = "#2A2A3E"   # 次背景
COLOR_BG_HEADER     = "#16162A"   # 标题栏背景
COLOR_GRID_BAR      = "#3A3A5A"   # 小节线
COLOR_GRID_BEAT     = "#2A2A4A"   # 拍线
COLOR_GRID_SEMI     = "#222233"   # 半音网格线
COLOR_WHITE_KEY_BG  = "#F5F5F5"   # 白键颜色
COLOR_BLACK_KEY_BG  = "#1A1A1A"   # 黑键颜色
COLOR_C_KEY_MARK    = "#E8D5B7"   # C键高亮色
COLOR_CURSOR        = "#FF4444"   # 播放光标颜色
COLOR_SELECTION     = "#FFFFFF"   # 选中音符边框色
