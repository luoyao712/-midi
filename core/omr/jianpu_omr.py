"""
简谱 OMR 识别模块（图像版）
针对 EveryonePiano 格式简谱图片，使用 PaddleOCR + OpenCV 进行图像分析。

识别流程：
  1. 去除 EOP 蓝色水印
  2. 解析图片头部（调号、拍号、BPM）
  3. 分割系统行（每个系统行含上/下两条声部带）
  4. 对每条声部带用 PaddleOCR 逐字符识别
  5. 分析八度点（高音/低音）和时值横线
  6. 下声部按 x 坐标聚类构建和弦
  7. 构建 Note/Track 对象输出

简谱字符约定：
  1-7  : 音符（do~si）
  0    : 休止符
  i    : 高八度 1（EveryonePiano 字体将「1上加点」渲染为 i）
  —    : 延音线（加一拍）
  .    : 附点（时值×1.5，紧跟在数字右侧）
  点位 : 字符上方小点=高八度，字符下方小点=低八度
  横线 : 字符下方横线数=时值细分（0=四分，1=八分，2=十六分）
"""
from __future__ import annotations

import os
import re
import sys
from typing import List, Tuple, Optional

import cv2
import numpy as np

from core.note_model import Note, Track, TimeSignature

# ─── 调号映射 ────────────────────────────────────────────────

# 每个大调中，「1」（do）对应的半音偏移（相对 C=0）
_KEY_SEMITONE: dict[str, int] = {
    "C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11,
    "#C": 1, "#D": 3, "#F": 6, "#G": 8, "#A": 10,
    "bD": 1, "bE": 3, "bG": 6, "bA": 8, "bB": 10,
    "Db": 1, "Eb": 3, "Gb": 6, "Ab": 8, "Bb": 10,
}

# 调号升降号数（用于返回给上层）
_KEY_SHARPS: dict[str, int] = {
    "Cb": -7, "Gb": -6, "Db": -5, "Ab": -4, "Eb": -3, "Bb": -2, "F": -1,
    "C":   0, "G":   1, "D":   2, "A":   3, "E":   4, "B":   5, "F#":  6, "C#":  7,
}

# 大调音程偏移：1=0, 2=2, 3=4, 4=5, 5=7, 6=9, 7=11（半音）
_DEGREE_OFFSET = [0, 2, 4, 5, 7, 9, 11]

# 只保留这些字符的 OCR 结果（其余视为噪声）
_VALID_CHARS = set("01234567i—-.")

# ─── PaddleOCR 单例 ───────────────────────────────────────────

_ocr_engine = None


def reset_ocr_engine() -> None:
    """强制释放 PaddleOCR 单例，下次识别时重新初始化（用于出错后恢复）"""
    global _ocr_engine
    _ocr_engine = None
    print("[jianpu_omr] OCR 引擎已重置")


def _enhance_for_ocr(img_bgr: np.ndarray) -> np.ndarray:
    """
    对图像做 CLAHE 对比度增强，改善扫描件/拍照版简谱的识别率。
    返回 BGR 格式图像（与输入格式相同，供直接替换 img_bgr 使用）。
    亮度均匀的数字版简谱也无害（CLAHE 不改变已高对比度区域）。
    """
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_eq  = clahe.apply(l)
    lab_eq = cv2.merge([l_eq, a, b])
    return cv2.cvtColor(lab_eq, cv2.COLOR_LAB2BGR)


def _get_ocr():
    """懒加载 PaddleOCR 实例（只初始化一次，兼容 2.x / 3.x API）"""
    global _ocr_engine
    if _ocr_engine is None:
        import os
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        # 禁用 oneDNN：PaddlePaddle 3.x 在 Windows CPU 上 oneDNN 有 bug，
        # 通过 monkey-patch pp_option 模块让 get_default_run_mode() 不选 mkldnn
        try:
            import paddlex.inference.utils.pp_option as _pp_opt
            _pp_opt.is_mkldnn_available = lambda: False
        except Exception:
            pass
        from paddleocr import PaddleOCR
        print("[jianpu_omr] 初始化 PaddleOCR（首次运行会下载模型，请稍候）...")
        try:
            # PaddleOCR 3.x 参数名，使用 mobile 模型（体积小、下载快）
            _ocr_engine = PaddleOCR(
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                text_detection_model_name="PP-OCRv5_mobile_det",
                text_recognition_model_name="PP-OCRv5_mobile_rec",
                lang="en",
            )
        except TypeError:
            # 降级到 2.x 参数名
            _ocr_engine = PaddleOCR(use_angle_cls=False, lang="en")
        print("[jianpu_omr] PaddleOCR 初始化完成")
    return _ocr_engine


def _run_ocr(ocr_engine, img_array) -> list:
    """
    统一调用 PaddleOCR，兼容 2.x / 3.x 返回格式。

    2.x 返回：[ [bbox, (text, conf)], ... ]
    3.x 返回：list of OCRResult（dict-like）:
        { 'rec_texts': [...], 'rec_scores': [...], 'rec_polys': [...] }

    统一返回：[ [bbox, (text, conf)], ... ]
    """
    result = ocr_engine.ocr(img_array)
    if not result:
        return []

    first = result[0]

    # PaddleOCR 3.x：OCRResult 是 dict-like 对象
    if hasattr(first, 'get') or (isinstance(first, dict)):
        texts  = first.get('rec_texts',  []) or []
        scores = first.get('rec_scores', []) or []
        polys  = first.get('rec_polys',  []) or []
        return [[bbox, (txt, float(sc))] for bbox, txt, sc in zip(polys, texts, scores)]

    # PaddleOCR 3.x 旧式列表包装（外层多一页面维度）
    if isinstance(first, list):
        return first or []

    # PaddleOCR 2.x：result 本身就是 [ [bbox, (text, conf)], ... ]
    return result


# ─── 音高计算 ─────────────────────────────────────────────────

def _degree_to_midi(degree: int, octave_offset: int, key_semitone: int,
                    base_octave: int = 4) -> int:
    """
    将简谱音级转换为 MIDI 音高。

    Parameters
    ----------
    degree       : 1-7，简谱音级
    octave_offset: 八度偏移（+1=高八度，-1=低八度，0=本位）
    key_semitone : 调号偏移（1=C 时为0，1=G 时为7，...）
    base_octave  : 基准八度（4=C4=MIDI60，上声部用4，下声部用3）

    Returns
    -------
    MIDI 音高（0-127）
    """
    # C4 = MIDI 60，base_octave=4 对应 1=C 的情形
    base_midi = (base_octave + 1) * 12 + key_semitone + _DEGREE_OFFSET[degree - 1]
    return max(0, min(127, base_midi + octave_offset * 12))


# ─── 图像预处理 ───────────────────────────────────────────────

def _remove_eop_watermark(img_bgr: np.ndarray) -> np.ndarray:
    """
    去除 EveryonePiano 的斜向蓝色 EOP 水印。
    原理：EOP 水印色调偏蓝（B 通道明显高于 R/G），将其替换为白色。

    Parameters
    ----------
    img_bgr : BGR 格式 numpy 数组

    Returns
    -------
    去水印后的 BGR 图像
    """
    img = img_bgr.copy()
    b, g, r = img[:, :, 0], img[:, :, 1], img[:, :, 2]

    # 蓝色水印条件：B 通道显著高于 R 和 G，且不是纯白/纯黑区域
    blue_dominant = (b.astype(int) - r.astype(int) > 30) & \
                    (b.astype(int) - g.astype(int) > 30) & \
                    (b > 80)

    # 将蓝色区域设为白色
    img[blue_dominant] = [255, 255, 255]
    print(f"[jianpu_omr] 水印像素去除：{blue_dominant.sum()} px")
    return img


def _to_gray_binary(img_bgr: np.ndarray) -> np.ndarray:
    """BGR 图像 -> 二值化灰度图（OTSU 自适应阈值）"""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


# ─── 头部解析 ─────────────────────────────────────────────────

def _parse_header_region(img_bgr: np.ndarray,
                          header_h: int = -1) -> Tuple[str, TimeSignature, int, int]:
    """
    识别图片顶部约 15% 区域，提取调号/拍号/BPM。

    Parameters
    ----------
    header_h : 明确指定头部高度（px）；-1 = 自动计算

    Returns
    -------
    (key_name, TimeSignature, bpm, key_sharps)
    """
    h, w = img_bgr.shape[:2]
    if header_h <= 0:
        # 头部高度：小图用 15%，大图（PDF 渲染）上限 250px，避免把音符行误作头部
        header_h = min(250, max(80, int(h * 0.15)))
    header_img = img_bgr[:header_h, :]

    ocr  = _get_ocr()
    items = _run_ocr(ocr, header_img)

    lines: list[str] = []
    for item in items:
        if not item or len(item) < 2:
            continue
        text, conf = item[1][0], float(item[1][1])
        if conf >= 0.3:  # 头部信息允许低置信度
            lines.append(text)

    full_text = " ".join(lines)
    safe_text = full_text.encode('gbk', errors='replace').decode('gbk')
    print(f"[jianpu_omr] 头部 OCR 文本：{safe_text!r}")

    # 解析调号：1=G, 1=Bb, 1=C 等（兼容 OCR 返回小写字母）
    key_name   = "C"
    key_sharps = 0
    m = re.search(r'1\s*[=＝]\s*([#b]?[A-Ga-g][b#]?)', full_text)
    if m:
        raw_key = m.group(1).strip()
        # 规范化：首字母大写，处理孤立 'b' → 'Bb'
        if len(raw_key) == 1 and raw_key.lower() == 'b':
            raw_key = 'Bb'
        else:
            raw_key = raw_key[0].upper() + raw_key[1:]
        key_name = raw_key
        key_sharps = _KEY_SHARPS.get(raw_key, 0)
        print(f"[jianpu_omr] 调号：1={key_name}，升降号={key_sharps:+d}")
    else:
        print("[jianpu_omr] 未检测到调号，默认 1=C")

    # 解析拍号：2/4, 3/4, 4/4, 6/8 等
    time_sig = TimeSignature(4, 4)
    m = re.search(r'(\d)\s*/\s*(\d)', full_text)
    if m:
        time_sig = TimeSignature(int(m.group(1)), int(m.group(2)))
        print(f"[jianpu_omr] 拍号：{time_sig.numerator}/{time_sig.denominator}")
    else:
        print("[jianpu_omr] 未检测到拍号，默认 4/4")

    # 解析 BPM：♩=113 或 =113（兼容 OCR 截断，如 "13" 实为 "130"）
    bpm = 120
    m = re.search(r'[♩Jj=＝]\s*(\d{2,3})', full_text)
    if m:
        bpm = int(m.group(1))
        # OCR 有时将末位 '0' 误读为字母导致截断，如 130→13；若 BPM<40 则尝试×10 恢复
        if bpm < 40:
            candidate = bpm * 10
            if candidate <= 250:
                bpm = candidate
        print(f"[jianpu_omr] BPM：{bpm}")
    else:
        print("[jianpu_omr] 未检测到 BPM，默认 120")

    return key_name, time_sig, bpm, key_sharps


# ─── 系统行分割 ───────────────────────────────────────────────

def _find_system_rows(img_bgr: np.ndarray,
                       header_h: int = -1) -> List[Tuple[int, int]]:
    """
    找到图片中所有「系统行」的 y 坐标范围（每个系统行包含上下两条声部带）。

    原理：
    1. 将图像二值化，对每行统计黑色像素数
    2. 黑色像素多的区域 = 有内容；少的区域 = 空白分隔带
    3. 聚合连续有内容的行为一个区块，再过滤掉过矮的噪声区块

    Parameters
    ----------
    header_h : 跳过顶部多少像素（与 _parse_header_region 保持一致）；-1 = 自动

    Returns
    -------
    [(y_start, y_end), ...] 各系统行的像素范围，已按 y 排序
    """
    h, w = img_bgr.shape[:2]

    if header_h <= 0:
        # 跳过顶部标题区域，与 _parse_header_region 使用相同公式保持一致
        header_h = min(250, max(80, int(h * 0.15)))

    binary = _to_gray_binary(img_bgr)
    # 每行黑色像素数（值=0为黑）
    row_black = np.sum(binary[header_h:, :] == 0, axis=1)

    # 阈值：每行黑色像素超过宽度 1% 视为「内容行」
    threshold = max(5, int(w * 0.01))
    content_mask = row_black > threshold

    # 找连续内容区块
    blocks: List[Tuple[int, int]] = []
    in_block = False
    y_start  = 0
    for rel_y, is_content in enumerate(content_mask):
        abs_y = rel_y + header_h
        if is_content and not in_block:
            in_block = True
            y_start  = abs_y
        elif not is_content and in_block:
            in_block = False
            blocks.append((y_start, abs_y))
    if in_block:
        blocks.append((y_start, h))

    # 合并临近区块：间隙 < 图片高度 3% 的两个块视为同一系统行
    # （简谱字符之间的空白行会把单行内容切成多个小块）
    max_gap = max(20, int(h * 0.03))
    merged: List[Tuple[int, int]] = []
    for ys, ye in blocks:
        if merged and (ys - merged[-1][1]) <= max_gap:
            merged[-1] = (merged[-1][0], ye)
        else:
            merged.append((ys, ye))
    blocks = merged

    # 过滤掉过矮的噪声区块（高度 < 图片高度的 3%）
    min_height = max(30, int(h * 0.03))
    blocks = [(ys, ye) for ys, ye in blocks if (ye - ys) >= min_height]

    print(f"[jianpu_omr] 检测到 {len(blocks)} 个系统行")
    return blocks


def _split_system_to_parts(img_bgr: np.ndarray,
                            y_start: int, y_end: int) -> Tuple[Optional[Tuple[int, int]],
                                                                 Optional[Tuple[int, int]]]:
    """
    将一个系统行（y_start ~ y_end）分割为上声部和下声部的 y 范围。

    原理：
    在系统行内部寻找水平空白带（两条声部之间的分隔），
    空白带将系统行分为上半（旋律）和下半（伴奏）。

    Returns
    -------
    (upper_y_range, lower_y_range) 各自的 (y_start, y_end)，
    找不到分隔时返回 (None, None)
    """
    h_sys = y_end - y_start
    if h_sys < 40:
        return None, None

    binary = _to_gray_binary(img_bgr)
    region = binary[y_start:y_end, :]

    # 忽略左侧 8%（系统编号标注区域）
    w = img_bgr.shape[1]
    skip_left = int(w * 0.08)
    region_trimmed = region[:, skip_left:]

    row_black = np.sum(region_trimmed == 0, axis=1)

    # 空白带阈值（每行黑色像素 < 宽度 0.5%）
    blank_threshold = max(2, int((w - skip_left) * 0.005))
    blank_mask = row_black <= blank_threshold

    # 寻找系统行中部的最长连续空白带
    # 只在中间 30%~70% 的高度范围内搜索分隔带
    search_top    = int(h_sys * 0.25)
    search_bottom = int(h_sys * 0.75)

    best_gap_start = -1
    best_gap_len   = 0
    cur_start      = -1
    cur_len        = 0

    for rel_y in range(search_top, search_bottom):
        if blank_mask[rel_y]:
            if cur_start == -1:
                cur_start = rel_y
            cur_len += 1
        else:
            if cur_len > best_gap_len:
                best_gap_len   = cur_len
                best_gap_start = cur_start
            cur_start = -1
            cur_len   = 0
    if cur_len > best_gap_len:
        best_gap_len   = cur_len
        best_gap_start = cur_start

    # 空白带需要至少 4 行像素
    if best_gap_start == -1 or best_gap_len < 4:
        # 无明显分隔：按中线对半分
        mid = h_sys // 2
        print(f"[jianpu_omr] 系统行 {y_start}-{y_end} 未找到分隔带，按中线对半分")
        return (y_start, y_start + mid), (y_start + mid, y_end)

    gap_abs_start = y_start + best_gap_start
    gap_abs_end   = gap_abs_start + best_gap_len
    upper_range   = (y_start, gap_abs_start)
    lower_range   = (gap_abs_end, y_end)

    # 歌词行过滤：若下声部带高度 < 上声部带高度 35%，认为是歌词行而非伴奏，
    # 返回 (upper_range, None) 只识别上声部。
    upper_h = gap_abs_start - y_start
    lower_h = y_end - gap_abs_end
    if upper_h > 0 and lower_h < upper_h * 0.35:
        print(f"[jianpu_omr] 系统行 {y_start}-{y_end} 下方为歌词行（高度比 "
              f"{lower_h}/{upper_h}={lower_h/upper_h:.2f}），跳过伴奏识别")
        return upper_range, None

    return upper_range, lower_range


# ─── OCR 识别声部带 ───────────────────────────────────────────

def _ocr_strip(img_bgr: np.ndarray,
               y_start: int, y_end: int) -> Tuple[List[dict], List[dict]]:
    """
    对图像中 [y_start, y_end] 区域（一条声部带）执行 PaddleOCR 识别。

    过滤规则：
    - 置信度 < 0.6 丢弃（音符 token）
    - 只保留包含 0-7, i, —, - , . 的有效字符作为音符 token
    - 跳过图像左侧 8%（系统编号区域）
    - 同时收集所有非音符 OCR 检测作为「八度标记候选」

    Returns
    -------
    (note_tokens, oct_markers)

    note_tokens : List[dict]  音符 token 列表（同原格式）
    oct_markers : List[dict]  八度标记候选，每条：
        { 'x': int,    # 中心 x（全图）
          'y': int,    # 中心 y（全图）
          'is_high': bool,   # y 明显低于音符层 → 在音符上方 → 高八度
          'is_low':  bool,   # y 明显高于音符层 → 在音符下方 → 低八度
        }
    """
    h_img, w_img = img_bgr.shape[:2]
    skip_left = int(w_img * 0.08)

    # 裁剪声部带区域
    strip = img_bgr[y_start:y_end, skip_left:]
    if strip.shape[0] < 8 or strip.shape[1] < 8:
        return [], []

    ocr   = _get_ocr()
    items = _run_ocr(ocr, strip)

    tokens: List[dict] = []
    raw_all: List[dict] = []   # 所有通过置信度过滤的 OCR 项（含非音符）

    if not items:
        return tokens, []

    for item in items:
        if not item or len(item) < 2:
            continue
        box  = item[0]
        text = item[1][0]
        conf = float(item[1][1])

        if conf < 0.4:          # 非音符标记放宽置信度
            continue

        # 计算 bounding box（相对于 strip 左上角）
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        bx0, bx1 = int(min(xs)), int(max(xs))
        by0, by1 = int(min(ys)), int(max(ys))

        abs_x  = (bx0 + bx1) // 2 + skip_left
        abs_y  = (by0 + by1) // 2 + y_start
        abs_x0 = bx0 + skip_left
        abs_x1 = bx1 + skip_left
        abs_y0 = by0 + y_start
        abs_y1 = by1 + y_start

        raw_all.append({'x': abs_x, 'y': abs_y, 'text': text})

        if conf < 0.6:          # 音符 token 保持严格阈值
            continue

        # 将识别文本过滤为有效字符
        clean = _filter_valid_chars(text)
        if not clean:
            continue

        # 对 OCR 识别的词进行逐字符拆分
        sub_tokens = _split_word_to_chars(clean, abs_x0, abs_x1, abs_y0, abs_y1, conf)
        tokens.extend(sub_tokens)

    # 按 x 坐标排序
    tokens.sort(key=lambda t: t['x'])

    # ── 建立八度标记候选 ──────────────────────────────────────
    # 用音符 token 的中位 y 作为「音符层」基准
    oct_markers: List[dict] = []
    if tokens:
        note_ys = [t['y'] for t in tokens if t['text'].isdigit() or t['text'] == 'i']
        if note_ys:
            note_ys.sort()
            median_note_y = note_ys[len(note_ys) // 2]
            # 容差：取音符平均高度
            avg_h = sum(t['h'] for t in tokens) / max(1, len(tokens))
            hi_thresh = median_note_y - avg_h * 1.5   # 在此 y 以上 → 高八度标记
            lo_thresh = median_note_y + avg_h * 1.5   # 在此 y 以下 → 低八度标记

            for r in raw_all:
                # 跳过本身就是音符数字的检测
                clean_r = _filter_valid_chars(r['text'])
                if clean_r and any(c.isdigit() or c == 'i' for c in clean_r):
                    continue
                is_high = r['y'] < hi_thresh
                is_low  = r['y'] > lo_thresh
                if is_high or is_low:
                    oct_markers.append({
                        'x':       r['x'],
                        'y':       r['y'],
                        'is_high': is_high,
                        'is_low':  is_low,
                    })

    return tokens, oct_markers


def _filter_valid_chars(text: str) -> str:
    """
    将 OCR 识别的文本过滤，只保留简谱有效字符（0-7, i, —, -, .）。
    同时做常见误识别修正：
      - 字母 l/L → 1（在简谱字体中常见）
      - 字母 O/o → 0
      - 全角数字 → 半角
    """
    # 全角数字
    for full, half in zip("０１２３４５６７", "01234567"):
        text = text.replace(full, half)

    # 常见误识别
    replacements = {
        'l': '1', 'L': '1', 'I': '1',
        'O': '0', 'o': '0',
        '—': '—',  # 全角破折号保留
        '-': '—',  # 普通连字符当延音线处理（有时 OCR 输出 -）
    }
    result = []
    for ch in text:
        ch = replacements.get(ch, ch)
        if ch in _VALID_CHARS:
            result.append(ch)
    return "".join(result)


def _split_word_to_chars(text: str,
                          x0: int, x1: int,
                          y0: int, y1: int,
                          conf: float) -> List[dict]:
    """
    将多字符 OCR 词均匀拆分为单个字符 token，每个字符分配等宽 x 区间。
    """
    if not text:
        return []
    n = len(text)
    w_total = x1 - x0
    char_w  = max(1, w_total // n)
    h       = y1 - y0

    tokens = []
    for i, ch in enumerate(text):
        cx0 = x0 + i * char_w
        cx1 = cx0 + char_w
        tokens.append({
            'text': ch,
            'conf': conf,
            'x'   : (cx0 + cx1) // 2,
            'y'   : (y0  + y1 ) // 2,
            'x0'  : cx0,
            'x1'  : cx1,
            'y0'  : y0,
            'y1'  : y1,
            'w'   : char_w,
            'h'   : h,
        })
    return tokens


# ─── 八度与时值分析 ───────────────────────────────────────────

def _analyze_octave_dots(img_bgr: np.ndarray,
                          token: dict,
                          strip_y_start: int,
                          strip_y_end: int,
                          oct_markers: Optional[List[dict]] = None,
                          baseline_y: int = -1) -> int:
    """
    确定音符的八度偏移（0 / +1 / -1）。

    EveryonePiano 简谱约定：
    - 八度点紧贴音符数字（上方 3-20px 或下方 3-20px 内的小圆点）
    - 八分音符连音横梁 (∇/V) 出现在音符顶部上方 40-80px 处，不是八度标记

    检测方法：在音符上/下方 3~25px 的窄带内做像素密度检测。

    Returns
    -------
    0 / +1 / -1
    """
    x0, x1 = token['x0'], token['x1']
    y0, y1 = token['y0'], token['y1']
    char_h  = max(1, y1 - y0)

    # 若有基准线且 OCR bounding box 明显偏大，用基准线修正字符底部
    if baseline_y > 0 and baseline_y < y1 - char_h * 0.3:
        char_h = max(1, min(char_h, baseline_y - y0))
        y1 = baseline_y

    # 八度点通常与音符数字间隙不超过 1.5 倍字符高度
    dot_scan = max(8, int(char_h * 0.8))

    h_img, w_img = img_bgr.shape[:2]
    x0c = max(0, x0)
    x1c = min(w_img, x1)

    def has_dot_pixels(region_y0: int, region_y1: int) -> bool:
        if region_y0 >= region_y1 or x0c >= x1c:
            return False
        roi = img_bgr[region_y0:region_y1, x0c:x1c]
        if roi.size == 0:
            return False
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        _, b = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY_INV)
        black_px = int(np.sum(b > 0))
        total_px = roi.shape[0] * roi.shape[1]
        return (black_px / max(1, total_px)) > 0.12   # 12% → 只检测紧贴的点

    # 高八度：音符正上方窄带（跳过最近 2px 避开字符本身）
    up_y0 = max(strip_y_start, y0 - dot_scan - 2)
    up_y1 = max(strip_y_start, y0 - 2)
    dot_above = has_dot_pixels(up_y0, up_y1)

    # 'i' 是 EveryonePiano 字体对「高八度 1」的渲染，字形本身含顶部小点。
    # 若像素检测确认上方有点 → 合法高八度 1，返回 +1。
    # 若无点（OCR 把普通 1 误读为 i） → 当普通 1 处理，返回 0。
    if token['text'] == 'i':
        return 1 if dot_above else 0

    if dot_above:
        return 1

    # 低八度：音符正下方窄带（跳过最近 2px 以避开时值横线起始）
    # 时值横线紧贴音符底部；低八度点在横线之下，距离稍远一点
    dn_y0 = min(strip_y_end, y1 + int(char_h * 0.3))
    dn_y1 = min(strip_y_end, y1 + dot_scan + int(char_h * 0.3))
    if has_dot_pixels(dn_y0, dn_y1):
        return -1

    return 0


def _analyze_underlines(img_bgr: np.ndarray,
                          token: dict,
                          strip_y_end: int,
                          baseline_y: int = -1) -> int:
    """
    分析字符下方横线数量以确定时值细分。

    EveryonePiano 简谱：
      0 条横线 → 四分音符（1.0 拍）
      1 条横线 → 八分音符（0.5 拍）
      2 条横线 → 十六分音符（0.25 拍）

    检测原理：在字符正下方约 1.5 倍字符高度内，
    按行统计黑色像素数，找连续「黑行」簇的数量。

    Returns
    -------
    0 / 1 / 2
    """
    x0, x1 = token['x0'], token['x1']
    y1      = token['y1']
    h_img, w_img = img_bgr.shape[:2]

    # 对多字符 OCR 结果拆分后的 token，bounding box 高度包含音符杆/连音梁，
    # 远大于实际字符高度，导致 y1 接近声部带底部，扫描区域几乎为零。
    # 用字符宽度估算实际字高（简谱数字近似正方形），并取二者较小值。
    char_w_est = max(1, x1 - x0)
    char_h  = max(1, min(token['h'], char_w_est * 3))   # 上限为 3×宽，约 45px

    # 搜索区域：字符下方 2 ~ (2 + 1.5*char_h) 像素
    # 若有基准线且在合理范围内，用基准线作为字符底部（比 OCR bounding box 更精确）
    if baseline_y > 0 and token['y0'] < baseline_y < token['y1']:
        char_bottom = baseline_y
    else:
        # 注意：y1 可能来自大 bounding box，使用估算后的 char_h 反推字符底部
        char_bottom = min(token['y1'], token['y0'] + char_h)
    search_y0 = min(strip_y_end, char_bottom + 2)
    search_y1 = min(strip_y_end, char_bottom + int(char_h * 1.6))

    if search_y0 >= search_y1:
        return 0

    x0c = max(0, x0)
    x1c = min(w_img, x1)
    if x0c >= x1c:
        return 0

    roi  = img_bgr[search_y0:search_y1, x0c:x1c]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    _, bin_roi = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY_INV)

    # 每行黑色像素数
    row_black = np.sum(bin_roi > 0, axis=1)
    line_threshold = max(2, int((x1c - x0c) * 0.3))  # 至少 30% 的列有黑色

    # 统计连续「黑行」簇（每簇对应一条横线）
    line_count  = 0
    in_line_row = False
    for px_cnt in row_black:
        if px_cnt >= line_threshold:
            if not in_line_row:
                in_line_row = True
                line_count  += 1
        else:
            in_line_row = False

    return min(line_count, 2)


def _has_dot_after(tokens: List[dict], idx: int) -> bool:
    """
    检查 tokens[idx] 后面紧跟着的是否是附点（.）。
    条件：下一个 token 的文本为 '.'，且 x 坐标紧邻（差 < 2 倍字符宽度）。
    """
    if idx + 1 >= len(tokens):
        return False
    nxt = tokens[idx + 1]
    if nxt['text'] != '.':
        return False
    cur = tokens[idx]
    gap = nxt['x'] - cur['x1']
    return gap < cur['w'] * 2


# ─── 下声部和弦聚类 ───────────────────────────────────────────

def _cluster_bass_tokens(tokens: List[dict]) -> List[List[dict]]:
    """
    将下声部的 OCR 结果按 x 坐标聚类，同一时间位置的多个音符（和弦）聚为一组。

    聚类条件：x 坐标差 ≤ 20px（紧密阈值）。
    - 和弦音：同一垂直列，x 几乎相同（< 5px），可合并
    - 连续音符：相邻 x 至少相差一个字符宽（≥ 30px），不合并
    使用极小阈值区分两者；同时以 cluster[0].x 为锚点防止滑动累积误差。

    Returns
    -------
    List[List[dict]]：每个子列表是同一时间位置的 token 组（和弦）
    """
    if not tokens:
        return []

    # 超紧密阈值：只合并视觉上完全同列（x ≤ 5px）的音符
    # - 和弦音：印刷上列对齐，x 差通常 0~3px → 合并
    # - 连续音符（多字符 OCR 拆分）：相邻 x 差 ≥ 15px  → 不合并
    # - 独立 OCR token 的顺序音符：x 差 ≥ 30px  → 不合并
    cluster_threshold = 5

    clusters: List[List[dict]] = []
    cur_cluster: List[dict]    = [tokens[0]]

    for tok in tokens[1:]:
        ref_x = cur_cluster[0]['x']   # 锚定到 cluster 第一个元素，防止滑动累积
        x_gap = tok['x'] - ref_x
        if x_gap <= cluster_threshold:
            cur_cluster.append(tok)
        else:
            clusters.append(cur_cluster)
            cur_cluster = [tok]
    clusters.append(cur_cluster)

    return clusters


# ─── 轮廓分析与小节线辅助 ────────────────────────────────────


def _detect_strip_baseline(tokens: List[dict]) -> int:
    """
    从音符 token 的底部边缘中位数估算声部带基准线 y（音符字符坐落其上的横线）。
    不足 2 个数字 token 时返回 -1。
    """
    ys = [t['y1'] for t in tokens if t['text'].isdigit() or t['text'] == 'i']
    if len(ys) < 2:
        return -1
    ys.sort()
    return ys[len(ys) // 2]


def _supplement_dash_tokens(img_bgr: np.ndarray,
                              strip_y_start: int,
                              strip_y_end: int,
                              skip_left: int,
                              existing_tokens: List[dict]) -> List[dict]:
    """
    用轮廓（连通域）分析补充 PaddleOCR 漏检的延音线「—」token。

    延音线特征：
    - 宽高比 > 2.5（扁平横向线段）
    - 宽度 ≥ 0.4 × char_h（有一定长度，不是小噪点）
    - 高度 ≤ 0.55 × char_h（细，不是数字字符）
    - 竖向位置在带高 15%~85%（排除八度点/下划线区域）

    Returns
    -------
    合成的延音线 token 列表（已与 existing_tokens 去重）
    """
    h_strip = strip_y_end - strip_y_start
    if h_strip < 8:
        return []

    # 用现有数字 token 估算字符高度
    digit_hs = [min(t['h'], max(1, t['w']) * 3)
                for t in existing_tokens
                if t['text'].isdigit() or t['text'] == 'i']
    char_h = float(np.median(digit_hs)) if digit_hs else 20.0
    char_h = max(6.0, min(char_h, 60.0))

    strip_img = img_bgr[strip_y_start:strip_y_end, skip_left:]
    gray = cv2.cvtColor(strip_img, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY_INV)

    n_labels, _labels, stats, centroids = cv2.connectedComponentsWithStats(
        binary, connectivity=8)

    existing_xs = [t['x'] for t in existing_tokens]

    synthetic: List[dict] = []
    for lbl in range(1, n_labels):
        bx, by, bw, bh, _area = stats[lbl]
        if bw < char_h * 0.4:          # 太窄
            continue
        if bh > char_h * 0.55:         # 太高（数字或其他元素）
            continue
        if bw / max(1, bh) < 2.5:      # 宽高比不够
            continue
        cy = float(centroids[lbl][1])
        if cy < h_strip * 0.15 or cy > h_strip * 0.85:   # 排除顶/底边缘
            continue

        abs_x = int(centroids[lbl][0]) + skip_left
        abs_y = int(cy) + strip_y_start

        # 不与已有 token 重叠（x 距离 < char_h 视为同位置）
        if any(abs(abs_x - ex) < char_h for ex in existing_xs):
            continue

        synthetic.append({
            'text': '—',
            'conf': 0.8,
            'x':  abs_x,
            'y':  abs_y,
            'x0': bx + skip_left,
            'x1': bx + bw + skip_left,
            'y0': by + strip_y_start,
            'y1': by + bh + strip_y_start,
            'w':  bw,
            'h':  bh,
        })

    if synthetic:
        print(f"[jianpu_omr]   轮廓补充延音线 {len(synthetic)} 个")
    return synthetic


def _detect_left_margin(img_bgr: np.ndarray, header_h: int = 0) -> int:
    """
    检测图像左侧的系统括号 / 首条小节线位置，作为动态 skip_left。

    原理：在跳过头部之后的图像中，统计每列的黑色像素数。
    最左侧黑色列密集的区域（列黑像素 > 阈值）结束之处即为音符内容起始列。

    Returns
    -------
    skip_left : int  应跳过的左侧像素列数（保底 = 4% 宽，上限 = 20% 宽）
    """
    h, w = img_bgr.shape[:2]
    min_skip = max(10, int(w * 0.04))
    max_skip = int(w * 0.20)

    binary = _to_gray_binary(img_bgr)
    content = binary[header_h:, :]
    col_black = np.sum(content == 0, axis=0)

    # 左侧密集黑列阈值：内容行数的 15%
    n_rows   = content.shape[0]
    threshold = max(5, int(n_rows * 0.15))

    # 从左向右扫，找到第一个低于阈值的列
    skip = min_skip
    for x in range(min_skip, max_skip):
        if col_black[x] < threshold:
            skip = x
            break

    print(f"[jianpu_omr] 动态左边距 skip_left={skip}px（图宽 {w}px 的 {skip/w:.1%}）")
    return skip


def _detect_barlines(img_bgr: np.ndarray,
                      strip_y_start: int,
                      strip_y_end: int,
                      skip_left: int) -> List[int]:
    """
    检测声部带内的小节线 x 坐标（全图坐标）。

    小节线特征：高度 ≥ 50% 带高、宽度 ≤ 6px 的竖向黑色线段。

    Returns
    -------
    排序去重后的 x 坐标列表
    """
    h_strip = strip_y_end - strip_y_start
    min_h   = max(10, int(h_strip * 0.5))

    strip_img = img_bgr[strip_y_start:strip_y_end, skip_left:]
    gray = cv2.cvtColor(strip_img, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY_INV)

    n_labels, _labels, stats, centroids = cv2.connectedComponentsWithStats(
        binary, connectivity=8)

    xs: List[int] = []
    for lbl in range(1, n_labels):
        bx, by, bw, bh, _area = stats[lbl]
        if bh >= min_h and bw <= 6:
            xs.append(int(centroids[lbl][0]) + skip_left)

    xs.sort()
    merged: List[int] = []
    for x in xs:
        if not merged or x - merged[-1] > 10:
            merged.append(x)
    return merged


def _verify_beats_with_barlines(notes: List[Note],
                                  barline_xs: List[int],
                                  first_note_x: int,
                                  px_per_beat: float,
                                  beats_per_bar: int) -> List[Note]:
    """
    利用小节线位置校正下声部末尾音符时值。

    策略：
    - 若有 px_per_beat + 小节线：把 x 坐标转成拍位，逐小节检查末音符是否过短
    - 无 px_per_beat：按拍号做简单小节计数
    末音符比预期短 ≥ 0.5 拍 且时值 ≥ 1.0 拍时执行延长。
    """
    if not notes or beats_per_bar <= 0:
        return notes

    corrected = list(notes)

    if px_per_beat > 0 and barline_xs and first_note_x >= 0:
        # 小节线 x → 拍位（相对于第一个音符 x）
        bar_beats = sorted(
            (bx - first_note_x) / px_per_beat
            for bx in barline_xs
            if bx > first_note_x + 5
        )
        boundaries = [0.0] + [b for b in bar_beats if b > 0.5]

        for seg_i in range(len(boundaries) - 1):
            seg_start = boundaries[seg_i]
            seg_end   = boundaries[seg_i + 1]
            seg_idx   = [k for k, n in enumerate(corrected)
                          if seg_start - 0.05 <= n.start_beat < seg_end - 0.05]
            if not seg_idx:
                continue
            last_k = seg_idx[-1]
            last   = corrected[last_k]
            gap    = seg_end - (last.start_beat + last.duration_beats)
            if gap >= 0.5 and last.duration_beats >= 1.0:
                corrected[last_k] = Note(
                    pitch          = last.pitch,
                    start_beat     = last.start_beat,
                    duration_beats = last.duration_beats + gap,
                    velocity       = last.velocity,
                )
    else:
        # fallback：按拍号小节计数
        i         = 0
        n         = len(corrected)
        bar_start = 0.0
        while i < n:
            bar_end = bar_start + beats_per_bar
            j = i
            while j < n and corrected[j].start_beat < bar_end - 0.05:
                j += 1
            if j > i:
                last_k = j - 1
                last   = corrected[last_k]
                gap    = bar_end - (last.start_beat + last.duration_beats)
                if gap >= 0.5 and last.duration_beats >= 1.0:
                    corrected[last_k] = Note(
                        pitch          = last.pitch,
                        start_beat     = last.start_beat,
                        duration_beats = last.duration_beats + gap,
                        velocity       = last.velocity,
                    )
            bar_start = bar_end
            i = j if j > i else j + 1

    return corrected


# ─── 声部序列构建 ─────────────────────────────────────────────

def _tokens_to_notes(tokens: List[dict],
                      img_bgr: np.ndarray,
                      strip_y_start: int,
                      strip_y_end: int,
                      key_semitone: int,
                      base_octave: int,
                      oct_markers: Optional[List[dict]] = None) -> List[Note]:
    """
    将上声部 token 列表转换为 Note 列表（单声部，按序排列）。

    Parameters
    ----------
    tokens       : OCR token 列表（已按 x 排序）
    img_bgr      : 原始 BGR 图像（用于八度点/横线检测）
    strip_y_start: 声部带上边界（全图坐标）
    strip_y_end  : 声部带下边界（全图坐标）
    key_semitone : 调号半音偏移
    base_octave  : 基准八度（上声部=4，下声部=3）

    Returns
    -------
    List[Note] 按 start_beat 有序
    """
    # 用 token 底部中位数估算基准线，供 _analyze_underlines/_analyze_octave_dots 使用
    baseline_y = _detect_strip_baseline(tokens)

    notes: List[Note] = []
    beat = 0.0
    i    = 0

    while i < len(tokens):
        tok  = tokens[i]
        text = tok['text']

        # ── 延音线 ──────────────────────────────────────────
        if text in ('—', '-'):
            if notes:
                last = notes[-1]
                notes[-1] = Note(
                    pitch          = last.pitch,
                    start_beat     = last.start_beat,
                    duration_beats = last.duration_beats + 1.0,
                    velocity       = last.velocity,
                )
            beat += 1.0
            i    += 1
            continue

        # ── 附点（跳过，已在音符分析中处理）─────────────────
        if text == '.':
            i += 1
            continue

        # ── 音符或休止符 ─────────────────────────────────────
        if text.isdigit() or text == 'i':
            digit = 1 if text == 'i' else int(text)

            # 时值：分析下方横线（传入基准线以提高精度）
            underlines = _analyze_underlines(img_bgr, tok, strip_y_end, baseline_y)
            duration   = {0: 1.0, 1: 0.5, 2: 0.25}[underlines]

            # 附点
            if _has_dot_after(tokens, i):
                duration *= 1.5
                i        += 1  # 跳过附点 token

            # 休止符：不发声，只推进时间
            if digit == 0:
                beat += duration
                i    += 1
                continue

            # 八度偏移（传入基准线）
            oct_off = _analyze_octave_dots(
                img_bgr, tok, strip_y_start, strip_y_end, oct_markers, baseline_y)

            midi = _degree_to_midi(digit, oct_off, key_semitone, base_octave)
            notes.append(Note(
                pitch          = midi,
                start_beat     = beat,
                duration_beats = duration,
            ))
            beat += duration
            i    += 1
            continue

        # 其他字符跳过
        i += 1

    return notes


def _bass_clusters_to_notes(clusters: List[List[dict]],
                              img_bgr: np.ndarray,
                              strip_y_start: int,
                              strip_y_end: int,
                              key_semitone: int,
                              base_octave: int,
                              oct_markers: Optional[List[dict]] = None,
                              px_per_beat: float = 0.0) -> List[Note]:
    """
    将下声部聚类后的 token 组转换为 Note 列表（支持和弦）。

    每个 cluster 代表一个时间位置，cluster 内多个音符同时发声（和弦）。
    时值取 cluster 中第一个有效音符决定的时值（若 cluster 内全是和弦音则均用同一时值）。

    Returns
    -------
    List[Note] 按 start_beat 有序
    """
    # 用所有 cluster token 底部中位数估算基准线
    all_cluster_tokens = [t for c in clusters for t in c]
    baseline_y = _detect_strip_baseline(all_cluster_tokens)

    notes: List[Note] = []
    beat  = 0.0

    for i_cluster, cluster in enumerate(clusters):
        # 取 cluster 中第一个音符 token 确定时值
        ref_tok  = None
        duration = 1.0
        has_dot  = False

        # 找第一个「音符」类 token 以确定时值
        for tok in cluster:
            t = tok['text']
            if t.isdigit() or t == 'i':
                ref_tok = tok
                break

        if ref_tok is None:
            # cluster 全是延音线或附点等，跳过
            # 延音线处理：对已有最后一个音符加一拍
            for tok in cluster:
                if tok['text'] in ('—', '-'):
                    if notes:
                        last = notes[-1]
                        notes[-1] = Note(
                            pitch=last.pitch,
                            start_beat=last.start_beat,
                            duration_beats=last.duration_beats + 1.0,
                            velocity=last.velocity,
                        )
                    beat += 1.0
                    break
            continue

        # 分析时值（传入基准线以提高精度）
        underlines = _analyze_underlines(img_bgr, ref_tok, strip_y_end, baseline_y)
        duration   = {0: 1.0, 1: 0.5, 2: 0.25}[underlines]

        # 检查 cluster 内是否有附点
        texts_in_cluster = [t['text'] for t in cluster]
        if '.' in texts_in_cluster:
            duration *= 1.5

        # ── x 间距推断延长号（补偿 OCR 漏检的 '-' 延音线）──────
        # 仅对四分音符（duration≥1.0）应用，避免对八分音符产生误延长。
        # 条件：x 推断拍数 ≥ 当前时值 + 1.0（即至少漏了一个延音线）。
        if px_per_beat > 0 and duration >= 1.0 and i_cluster + 1 < len(clusters):
            cur_x  = cluster[0]['x']
            nxt_x  = clusters[i_cluster + 1][0]['x']
            x_gap  = max(0, nxt_x - cur_x)
            x_implied = x_gap / px_per_beat
            # 四舍五入到最近 0.5 拍，范围限 [1.0, 8.0]
            x_implied_rounded = min(8.0, max(1.0, round(x_implied * 2) / 2))
            # 仅当 x 推断比横线检测多出 ≥ 1.0 拍时才采用
            if x_implied_rounded >= duration + 1.0:
                duration = x_implied_rounded

        # 构建本时间位置的所有音符（和弦）
        chord_added = False
        for tok in cluster:
            text  = tok['text']
            if text in ('—', '-', '.'):
                continue
            if text.isdigit() or text == 'i':
                digit = 1 if text == 'i' else int(text)
                if digit == 0:
                    # 休止符：和弦内出现0，该时间位置不发声
                    chord_added = True  # 仍要推进时间
                    continue
                oct_off = _analyze_octave_dots(
                    img_bgr, tok, strip_y_start, strip_y_end, oct_markers, baseline_y)
                midi    = _degree_to_midi(digit, oct_off, key_semitone, base_octave)
                notes.append(Note(
                    pitch          = midi,
                    start_beat     = beat,
                    duration_beats = duration,
                ))
                chord_added = True

        if chord_added:
            beat += duration

    return notes


# ─── 主识别函数 ───────────────────────────────────────────────

def recognize_jianpu(image_path: str,
                     page_from: int = 1,
                     page_to: int = -1) -> Tuple[List[Track], TimeSignature, int, int]:
    """
    识别 EveryonePiano 格式简谱图片或 PDF，返回两条 Track（上声部旋律 + 下声部伴奏）。

    Parameters
    ----------
    image_path : str
        简谱图片路径（PNG/JPG/BMP）或 PDF 路径
    page_from  : int
        PDF 起始页（1-indexed），非 PDF 时忽略
    page_to    : int
        PDF 结束页（1-indexed），-1 = 最后一页，非 PDF 时忽略

    Returns
    -------
    tracks     : List[Track]
        [melody_track, bass_track]
    time_sig   : TimeSignature
    bpm        : int
    key_sharps : int
    """
    # ── PDF 多页路径 ─────────────────────────────────────────────
    if image_path.lower().endswith('.pdf'):
        return _recognize_jianpu_pdf(image_path, page_from, page_to)

    print(f"[jianpu_omr] 开始识别：{image_path}")

    # ── 1. 读取图片（用 numpy 方式支持中文路径）─────────────────
    buf     = np.fromfile(image_path, dtype=np.uint8)
    img_bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img_bgr is None:
        raise FileNotFoundError(f"无法读取图片：{image_path}")

    h, w = img_bgr.shape[:2]
    print(f"[jianpu_omr] 图片尺寸：{w}×{h}")

    # ── 2. 去除 EOP 蓝色水印 ───────────────────────────────────
    img_bgr = _remove_eop_watermark(img_bgr)

    # ── 2b. CLAHE 对比度增强（扫描件/拍照版受益，数字版无害）────
    img_bgr = _enhance_for_ocr(img_bgr)

    # ── 3. 计算统一头部高度（parse 与 find_rows 共用，避免音符被归入头部）
    header_h = min(250, max(80, int(h * 0.15)))

    # ── 4. 解析头部（调号/拍号/BPM）─────────────────────────
    key_name, time_sig, bpm, key_sharps = _parse_header_region(img_bgr, header_h)
    key_semitone = _KEY_SEMITONE.get(key_name, 0)
    print(f"[jianpu_omr] 调号={key_name}（semitone={key_semitone}），"
          f"拍号={time_sig.numerator}/{time_sig.denominator}，BPM={bpm}")

    # ── 5. 找系统行（使用相同 header_h，保证不重叠）──────────
    system_rows = _find_system_rows(img_bgr, header_h)
    if not system_rows:
        print("[jianpu_omr] 未检测到系统行，返回空轨道")
        melody = Track(name="上声部旋律", instrument=0, clef="treble")
        bass   = Track(name="下声部伴奏", instrument=0, clef="bass")
        return [melody, bass], time_sig, bpm, key_sharps

    # ── 6. 逐系统行识别音符 ────────────────────────────────────
    melody_notes: List[Note] = []
    bass_notes:   List[Note] = []

    # 全局拍位累加器（每个系统行的音符拼接到上一行末尾）
    melody_beat = 0.0
    bass_beat   = 0.0

    # 动态左边距：检测图像中最靠左的竖向长线（系统括号/第一小节线），
    # 以其 x 坐标作为跳过宽度；若检测失败则回退到固定 8%。
    skip_left = _detect_left_margin(img_bgr, header_h)

    for sys_idx, (sys_y0, sys_y1) in enumerate(system_rows):
        print(f"[jianpu_omr] 处理系统行 {sys_idx + 1}/{len(system_rows)}"
              f"（y={sys_y0}~{sys_y1}）")

        # 6.1 分割上下声部（lower_range=None 表示下方是歌词行）
        upper_range, lower_range = _split_system_to_parts(img_bgr, sys_y0, sys_y1)
        if upper_range is None:
            print(f"[jianpu_omr]   系统行 {sys_idx + 1} 分割失败，跳过")
            continue

        up_y0, up_y1 = upper_range
        has_bass = lower_range is not None
        lo_y0 = lo_y1 = 0
        if has_bass:
            lo_y0, lo_y1 = lower_range

        # 6.2 OCR 上声部带
        upper_tokens, upper_oct = _ocr_strip(img_bgr, up_y0, up_y1)
        print(f"[jianpu_omr]   上声部 OCR token 数：{len(upper_tokens)}")

        # 6.3 OCR 下声部带 + 轮廓补充延音线（歌词行时跳过）
        lower_tokens: List[dict] = []
        lower_oct:   List[dict] = []
        if has_bass:
            lower_tokens, lower_oct = _ocr_strip(img_bgr, lo_y0, lo_y1)
            print(f"[jianpu_omr]   下声部 OCR token 数：{len(lower_tokens)}")
            dash_extra = _supplement_dash_tokens(
                img_bgr, lo_y0, lo_y1, skip_left, lower_tokens)
            if dash_extra:
                lower_tokens = sorted(lower_tokens + dash_extra, key=lambda t: t['x'])

        # 6.4 检测小节线（从上声部带提取，适用于两个声部）
        barline_xs = _detect_barlines(img_bgr, up_y0, up_y1, skip_left)
        if barline_xs:
            print(f"[jianpu_omr]   检测到小节线 {len(barline_xs)} 条")

        # 6.5 上声部转 Note（单声部）
        row_melody = _tokens_to_notes(
            upper_tokens, img_bgr,
            up_y0, up_y1,
            key_semitone, base_octave=4,
            oct_markers=upper_oct,
        )

        # 将本行音符的 start_beat 偏移到全局拍位
        for n in row_melody:
            melody_notes.append(Note(
                pitch          = n.pitch,
                start_beat     = n.start_beat + melody_beat,
                duration_beats = n.duration_beats,
                velocity       = n.velocity,
            ))

        # 更新全局旋律拍位
        if row_melody:
            row_end_beat = max(n.start_beat + n.duration_beats for n in row_melody)
            melody_beat += row_end_beat
        print(f"[jianpu_omr]   上声部本行音符数：{len(row_melody)}，"
              f"累计拍位：{melody_beat:.1f}")

        # 用旋律音符的 x 坐标 + 拍数 → 估算 px_per_beat
        px_per_beat = 0.0
        mel_note_toks = [t for t in upper_tokens if t['text'].isdigit() or t['text'] == 'i']
        if mel_note_toks and row_melody:
            mel_row_beats = max(n.start_beat + n.duration_beats for n in row_melody)
            x_span = mel_note_toks[-1]['x'] - mel_note_toks[0]['x']
            if mel_row_beats > 1 and x_span > 50:
                px_per_beat = x_span / mel_row_beats

        # 6.6 下声部聚类处理和弦（仅当有伴奏声部时）
        row_bass: List[Note] = []
        if has_bass and lower_tokens:
            bass_clusters = _cluster_bass_tokens(lower_tokens)
            print(f"[jianpu_omr]   下声部聚类组数：{len(bass_clusters)}")

            row_bass = _bass_clusters_to_notes(
                bass_clusters, img_bgr,
                lo_y0, lo_y1,
                key_semitone, base_octave=3,
                oct_markers=lower_oct,
                px_per_beat=px_per_beat,
            )

            # 6.7 小节线校正
            if row_bass and barline_xs and px_per_beat > 0:
                first_x = lower_tokens[0]['x'] if lower_tokens else (
                          upper_tokens[0]['x'] if upper_tokens else 0)
                row_bass = _verify_beats_with_barlines(
                    row_bass, barline_xs, first_x, px_per_beat, time_sig.numerator)

            # 6.8 行末修正
            if row_bass and row_melody:
                mel_row_end  = max(n.start_beat + n.duration_beats for n in row_melody)
                bass_row_end = max(n.start_beat + n.duration_beats for n in row_bass)
                gap = mel_row_end - bass_row_end
                last_bn = row_bass[-1]
                if gap >= 2.0 and last_bn.duration_beats >= 1.0:
                    row_bass[-1] = Note(
                        pitch          = last_bn.pitch,
                        start_beat     = last_bn.start_beat,
                        duration_beats = last_bn.duration_beats + gap,
                        velocity       = last_bn.velocity,
                    )

        for n in row_bass:
            bass_notes.append(Note(
                pitch          = n.pitch,
                start_beat     = n.start_beat + bass_beat,
                duration_beats = n.duration_beats,
                velocity       = n.velocity,
            ))

        if row_bass:
            row_end_beat = max(n.start_beat + n.duration_beats for n in row_bass)
            bass_beat   += row_end_beat
        print(f"[jianpu_omr]   下声部本行音符数：{len(row_bass)}，"
              f"累计拍位：{bass_beat:.1f}")

    # ── 7. 构建轨道对象 ────────────────────────────────────────
    melody_track       = Track(name="上声部旋律", instrument=0, clef="treble")
    melody_track.notes = sorted(melody_notes, key=lambda n: n.start_beat)

    bass_track       = Track(name="下声部伴奏", instrument=0, clef="bass")
    bass_track.notes = sorted(bass_notes,   key=lambda n: n.start_beat)

    print(f"[jianpu_omr] 识别完成：旋律 {len(melody_notes)} 个音符，"
          f"伴奏 {len(bass_notes)} 个音符")

    return [melody_track, bass_track], time_sig, bpm, key_sharps


# ─── PDF 多页简谱识别 ─────────────────────────────────────────

def _recognize_jianpu_pdf(pdf_path: str,
                           page_from: int = 1,
                           page_to: int = -1) -> Tuple[List[Track], TimeSignature, int, int]:
    """将 PDF 指定页范围逐页识别简谱并合并轨道"""
    import os
    import tempfile
    try:
        import fitz
    except ImportError:
        raise ImportError("请运行 pip install pymupdf 以支持 PDF 导入")

    doc         = fitz.open(pdf_path)
    total_pages = len(doc)
    p_from = max(0, page_from - 1)
    p_to   = total_pages - 1 if page_to == -1 else min(total_pages - 1, page_to - 1)
    n_pages = p_to - p_from + 1
    print(f"[jianpu_omr] PDF 共 {total_pages} 页，识别第 {p_from+1}~{p_to+1} 页（共 {n_pages} 页）")

    all_mel:  List[Note]  = []
    all_bass: List[Note]  = []
    time_sig   = TimeSignature(4, 4)
    bpm        = 120
    key_sharps = 0
    mel_offset  = 0.0
    bass_offset = 0.0
    first_page  = True

    with tempfile.TemporaryDirectory() as tmpdir:
        for page_idx in range(p_from, p_to + 1):
            page = doc[page_idx]
            print(f"[jianpu_omr] 识别第 {page_idx + 1}/{total_pages} 页...")
            mat = fitz.Matrix(200 / 72, 200 / 72)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img_path = os.path.join(tmpdir, f"page_{page_idx:03d}.png")
            pix.save(img_path)

            try:
                page_tracks, page_ts, page_bpm, page_ks = recognize_jianpu(img_path)
            except Exception as e:
                print(f"[jianpu_omr] 第 {page_idx+1} 页识别失败，跳过：{e}")
                continue

            # 范围内第一页确定全局参数
            if first_page:
                first_page = False
                time_sig   = page_ts
                bpm        = page_bpm
                key_sharps = page_ks

            mel_notes  = page_tracks[0].notes if page_tracks else []
            bass_notes = page_tracks[1].notes if len(page_tracks) > 1 else []

            for n in mel_notes:
                all_mel.append(Note(
                    pitch=n.pitch,
                    start_beat=n.start_beat + mel_offset,
                    duration_beats=n.duration_beats,
                    velocity=n.velocity,
                ))
            for n in bass_notes:
                all_bass.append(Note(
                    pitch=n.pitch,
                    start_beat=n.start_beat + bass_offset,
                    duration_beats=n.duration_beats,
                    velocity=n.velocity,
                ))

            mel_offset  += max((n.start_beat + n.duration_beats for n in mel_notes),  default=0.0)
            bass_offset += max((n.start_beat + n.duration_beats for n in bass_notes), default=0.0)

    doc.close()

    mel_track        = Track(name="上声部旋律", instrument=0, clef="treble")
    mel_track.notes  = sorted(all_mel,  key=lambda n: n.start_beat)
    bass_track       = Track(name="下声部伴奏", instrument=0, clef="bass")
    bass_track.notes = sorted(all_bass, key=lambda n: n.start_beat)

    return [mel_track, bass_track], time_sig, bpm, key_sharps


# ─── 兼容旧接口（供已有调用代码过渡）─────────────────────────

def ocr_jianpu_text(image_path: str) -> str:
    """
    兼容旧版文本接口：对图片执行 PaddleOCR 并返回原始文本（仅供调试）。
    新代码请直接使用 recognize_jianpu()。
    """
    print("[jianpu_omr] ocr_jianpu_text() 为兼容接口，建议改用 recognize_jianpu()")
    img_bgr = cv2.imread(image_path)
    if img_bgr is None:
        raise FileNotFoundError(f"无法读取图片：{image_path}")
    img_bgr = _remove_eop_watermark(img_bgr)

    ocr   = _get_ocr()
    items = _run_ocr(ocr, img_bgr)
    lines: list[str] = []
    items_sorted = sorted(items, key=lambda x: x[0][0][1]) if items else []
    for item in items_sorted:
        if item and len(item) >= 2 and float(item[1][1]) >= 0.3:
            lines.append(item[1][0])
    return "\n".join(lines)


def parse_jianpu_text(text: str) -> Tuple[List[Track], TimeSignature, int]:
    """
    兼容旧版：将简谱文本解析为轨道（单轨道，无图像信息）。
    新代码请直接使用 recognize_jianpu()。
    """
    import re as _re

    _KEY_MAP_LOCAL: dict[str, int] = {
        "C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11,
        "Db": 1, "Eb": 3, "Gb": 6, "Ab": 8, "Bb": 10,
        "#C": 1, "#D": 3, "#F": 6, "#G": 8, "#A": 10,
        "bD": 1, "bE": 3, "bG": 6, "bA": 8, "bB": 10,
    }

    key_off  = 0
    time_sig = TimeSignature(4, 4)
    body     = text

    m = _re.search(r'1\s*=\s*([#b]?[A-G][b#]?)', body)
    if m:
        key_off = _KEY_MAP_LOCAL.get(m.group(1).strip(), 0)
        body    = body[:m.start()] + body[m.end():]

    m = _re.search(r'(\d+)\s*/\s*(\d+)', body)
    if m:
        time_sig = TimeSignature(int(m.group(1)), int(m.group(2)))
        body     = body[:m.start()] + body[m.end():]

    bpm = 120
    m   = _re.search(r'[♩Jj]\s*=\s*(\d{2,3})|=\s*(\d{2,3})', body)
    if m:
        bpm  = int(m.group(1) or m.group(2))
        body = body[:m.start()] + body[m.end():]  # 删除 BPM 文本，避免数字被当音符

    # 逐字符解析（支持 0-7 数字、i 高八度、— 延音、. 附点、| 小节线忽略）
    notes: List[Note] = []
    beat = 0.0
    chars = list(body)
    ci = 0
    while ci < len(chars):
        ch = chars[ci]
        if ch in '1234567' or ch == 'i':
            d       = 1 if ch == 'i' else int(ch)
            oct_off = 1 if ch == 'i' else 0
            dur     = 1.0
            # 下一个字符是附点？
            if ci + 1 < len(chars) and chars[ci + 1] == '.':
                dur  = 1.5
                ci  += 1  # 跳过附点
            midi = _degree_to_midi(d, oct_off, key_off, 4)
            notes.append(Note(pitch=midi, start_beat=beat, duration_beats=dur))
            beat += dur
        elif ch == '0':
            beat += 1.0
        elif ch in ('—', '-'):
            if notes:
                last = notes[-1]
                notes[-1] = Note(pitch=last.pitch, start_beat=last.start_beat,
                                 duration_beats=last.duration_beats + 1.0,
                                 velocity=last.velocity)
            beat += 1.0
        # | 小节线、空格、换行等均忽略
        ci += 1

    track       = Track(name="简谱旋律", instrument=0)
    track.notes = notes
    return [track], time_sig, bpm
