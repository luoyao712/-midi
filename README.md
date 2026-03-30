# 音乐编辑器 Music Editor

基于 PyQt6 的桌面音乐编辑软件，支持钢琴卷帘编辑、简谱/五线谱识别导入、MIDI 导入导出、WAV 导出等功能。

---

## 功能特性

- **钢琴卷帘编辑**：多轨道音符编辑，支持绘制、选择、删除、移动、缩放
- **键盘弹奏输入**：MuseScore 风格，使用 `d r m f s l x` 按调号输入音符
- **力度编辑**：底部力度泳道，可拖拽调整每个音符的力度
- **量化**：选中音符一键对齐到网格
- **MIDI 导入 / 导出**：支持标准 `.mid` 文件
- **项目存档**：保存为 `.mep`（JSON 格式），随时恢复编辑状态
- **WAV 导出**：通过 FluidSynth 渲染为音频文件
- **乐谱识别（OMR）**：
  - 简谱图片识别（PaddleOCR）
  - 五线谱图片/PDF 识别（oemer / Audiveris / MuseScore）

---

## 环境要求

- Python 3.11+
- Windows 10/11（部分功能仅限 Windows）

### 依赖安装

```bash
pip install -r requirements.txt
```

### 额外依赖（可选）

| 功能 | 工具 | 说明 |
|------|------|------|
| WAV 导出 / 实时预览 | [FluidSynth](https://github.com/FluidSynth/fluidsynth/releases) | 将 DLL 放入 `piano/` 目录 |
| 五线谱识别（精度高） | [Audiveris](https://github.com/Audiveris/audiveris/releases) | 配置 `config.py` 中路径 |
| 五线谱转换 | [MuseScore 4](https://musescore.org/) | 配置 `config.py` 中路径 |
| 简谱识别 OCR | [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) | 配置 `config.py` 中路径 |

### 音色文件

WAV 导出和实时预览需要 SoundFont 文件：

1. 下载 [GeneralUser GS](https://schristiancollins.com/generaluser.php)（免费）
2. 将 `GeneralUser-GS.sf2` 放到项目根目录

---

## 启动

```bash
python main.py
```

---

## 项目结构

```
music_project/
├── main.py                  # 入口
├── config.py                # 全局配置（路径、外观参数）
├── requirements.txt
├── core/
│   ├── note_model.py        # 数据模型（Project / Track / Note）
│   ├── project_io.py        # 项目存档读写（.mep）
│   ├── midi/
│   │   ├── importer.py      # MIDI 导入
│   │   └── exporter.py      # MIDI 导出
│   └── omr/
│       ├── jianpu_omr.py    # 简谱识别
│       └── staff_omr.py     # 五线谱识别
├── ui/
│   ├── main_window.py       # 主窗口
│   ├── piano_roll.py        # 钢琴卷帘组件
│   ├── track_panel.py       # 轨道列表面板
│   ├── keyboard_input.py    # 键盘弹奏输入面板
│   ├── piano_keyboard.py    # 钢琴键盘预览组件
│   └── omr_preview_dialog.py# OMR 识别预览对话框
├── utils/
│   └── audio_player.py      # 音频播放器（FluidSynth）
└── piano/                   # FluidSynth 运行时 DLL（不纳入版本控制）
```

---

## 键盘快捷键

### 编辑模式

| 按键 | 功能 |
|------|------|
| `1` | 绘制模式 |
| `2` / `S` | 选择模式 |
| `3` | 橡皮擦模式 |
| `Del` | 删除选中音符 |
| `↑` / `↓` | 选中音符升/降半音 |
| `Ctrl+A` | 全选 |
| `Ctrl+Z` | 撤销 |
| `Ctrl+Shift+Q` | 量化选中音符 |
| `Space` | 播放 / 暂停 |

### 键盘弹奏模式（点击工具栏 ⌨ 弹奏 开启）

| 按键 | 功能 |
|------|------|
| `d r m f s l x` | 输入 do re mi fa sol la xi |
| `1` `2` `3` `4` `5` | 时值：十六 / 八分 / 四分 / 二分 / 全音符 |
| `.` | 切换附点 |
| `↑` / `↓` | 升/降八度 |
| `Z` | 输入休止符 |
| `Esc` | 退出弹奏模式 |

---

## 配置

编辑 `config.py` 可自定义：

- 外部工具路径（Tesseract、Audiveris、MuseScore）
- 钢琴卷帘外观参数（音符高度、每拍像素数等）
- 默认 BPM、音符时值、力度

---

## License

MIT
