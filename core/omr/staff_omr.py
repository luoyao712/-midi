"""
五线谱 OMR 识别模块
引擎优先级：Audiveris（高精度）> MuseScore（中高精度）> oemer（兜底）
识别流程：图片/PDF -> 预处理(CLAHE[+Otsu]) -> OMR引擎 -> MusicXML -> music21 解析
         -> 去重/过滤/量化/小节校验 -> Track/Note 列表

PDF 多页支持：用 PyMuPDF 将每页渲染为图片，逐页识别后按顺序合并轨道。
"""
from __future__ import annotations
import os
import subprocess
import tempfile
from typing import List, Tuple

from core.note_model import Note, Track, TimeSignature


# ─── 主识别函数 ──────────────────────────────────────────────

def recognize_staff(image_path: str,
                    page_from: int = 1,
                    page_to: int = -1) -> Tuple[List[Track], TimeSignature, int, int]:
    """
    识别五线谱图片或 PDF，返回 (轨道列表, 拍号, BPM, 调号升降号数)

    page_from / page_to: 1-indexed 页范围，page_to=-1 表示最后一页（仅 PDF 生效）。
    PDF：拆页后逐张识别（Audiveris 直接处理整个 PDF 时会因 Tesseract 报错失败）。
    单张图片：优先 Audiveris，不可用时降级到 oemer。
    """
    ext = os.path.splitext(image_path)[1].lower()

    # PDF 始终走拆页路径，每页再由 _recognize_single_image 自动选引擎
    if ext == ".pdf":
        print("[staff_omr] PDF 文件，拆页后逐张识别")
        return _recognize_pdf(image_path, page_from=page_from, page_to=page_to)

    # 单张图片：优先 Audiveris
    if _audiveris_available():
        print("[staff_omr] 使用 Audiveris 引擎识别")
        with tempfile.TemporaryDirectory() as tmpdir:
            xml_path = _run_audiveris(image_path, tmpdir)
            if xml_path:
                return _parse_xml(xml_path)
        print("[staff_omr] Audiveris 识别失败，降级到 oemer")

    print("[staff_omr] 使用 oemer 引擎识别")
    return _recognize_with_oemer(image_path)


# ─── PDF 多页处理 ─────────────────────────────────────────────

def _recognize_pdf(pdf_path: str,
                   page_from: int = 1,
                   page_to: int = -1) -> Tuple[List[Track], TimeSignature, int, int]:
    """将 PDF 指定页范围渲染为图片，逐页识别后合并轨道"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError("请运行 pip install pymupdf 以支持 PDF 导入")

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    # 转换为 0-indexed 区间
    p_from = max(0, page_from - 1)
    p_to   = total_pages - 1 if page_to == -1 else min(total_pages - 1, page_to - 1)
    n_pages = p_to - p_from + 1
    print(f"[staff_omr] PDF 共 {total_pages} 页，识别第 {p_from+1}~{p_to+1} 页（共 {n_pages} 页）")

    all_tracks: List[Track] = []
    time_sig   = TimeSignature(4, 4)
    bpm        = 120
    key_sharps = 0
    # 记录每条轨道当前已有的最大结束拍，用于拼接下一页
    track_offsets: List[float] = []

    page_errors: list[str] = []
    first_page = True

    # 根据可用引擎选择渲染分辨率：Audiveris 推荐 300 DPI，oemer 推荐 150 DPI
    render_dpi = 300 if _audiveris_available() else 150
    print(f"[staff_omr] 渲染分辨率：{render_dpi} DPI"
          f"（引擎：{'Audiveris' if render_dpi == 300 else 'oemer'}）")

    with tempfile.TemporaryDirectory() as tmpdir:
        for page_idx in range(p_from, p_to + 1):
            page = doc[page_idx]
            print(f"[staff_omr] 识别第 {page_idx + 1}/{total_pages} 页...")

            mat  = fitz.Matrix(render_dpi / 72, render_dpi / 72)
            pix  = page.get_pixmap(matrix=mat, alpha=False)
            img_path = os.path.join(tmpdir, f"page_{page_idx:03d}.png")
            pix.save(img_path)

            try:
                page_tracks, page_ts, page_bpm, page_ks = _recognize_single_image(img_path)
            except Exception as e:
                err_msg = f"第 {page_idx + 1} 页：{type(e).__name__}: {e}"
                print(f"[staff_omr] 识别失败，跳过：{err_msg}")
                page_errors.append(err_msg)
                continue

            # 范围内第一页确定全局拍号、BPM 和调号
            if first_page:
                first_page = False
                time_sig   = page_ts
                bpm        = page_bpm
                key_sharps = page_ks
                all_tracks = page_tracks
                track_offsets = [
                    max((n.start_beat + n.duration_beats for n in t.notes), default=0.0)
                    for t in all_tracks
                ]
            else:
                _merge_page(all_tracks, track_offsets, page_tracks)

    doc.close()

    if not all_tracks:
        detail = "\n".join(page_errors) if page_errors else "未知原因"
        raise RuntimeError(f"PDF 所有页面识别均失败，未获得任何音符。\n\n详细错误：\n{detail}")

    return all_tracks, time_sig, bpm, key_sharps


def _merge_page(
    all_tracks: List[Track],
    track_offsets: List[float],
    page_tracks: List[Track],
) -> None:
    """
    将新一页的轨道追加到已有轨道列表中。
    按轨道顺序匹配（高音谱→轨道0，低音谱→轨道1），超出数量则新建。
    """
    for i, pt in enumerate(page_tracks):
        if i < len(all_tracks):
            offset = track_offsets[i]
            for note in pt.notes:
                all_tracks[i].add_note(Note(
                    pitch          = note.pitch,
                    start_beat     = note.start_beat + offset,
                    duration_beats = note.duration_beats,
                    velocity       = note.velocity,
                ))
            # 更新该轨道的偏移量
            track_offsets[i] = max(
                (n.start_beat + n.duration_beats for n in all_tracks[i].notes),
                default=track_offsets[i],
            )
        else:
            # 新轨道：把音符整体往后移
            offset = max(track_offsets, default=0.0)
            new_track = Track(name=pt.name, instrument=pt.instrument)
            for note in pt.notes:
                new_track.add_note(Note(
                    pitch          = note.pitch,
                    start_beat     = note.start_beat + offset,
                    duration_beats = note.duration_beats,
                    velocity       = note.velocity,
                ))
            all_tracks.append(new_track)
            track_offsets.append(
                max((n.start_beat + n.duration_beats for n in new_track.notes), default=offset)
            )


def _preprocess_staff_image(image_path: str, output_path: str,
                             binarize: bool = False) -> str:
    """
    对五线谱图像做预处理：
    1. CLAHE 对比度增强（LAB 色彩空间，适用于所有引擎）
    2. Otsu 自适应二值化（binarize=True，专为 oemer 神经网络路径启用）
    失败时返回原始 image_path。
    """
    try:
        import cv2
        img = cv2.imread(image_path)
        if img is None:
            return image_path
        # CLAHE
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8))
        l_eq  = clahe.apply(l)
        enhanced = cv2.cvtColor(cv2.merge([l_eq, a, b]), cv2.COLOR_LAB2BGR)
        if binarize:
            # Otsu 自适应阈值 → 黑白，消除灰色背景干扰
            gray = cv2.cvtColor(enhanced, cv2.COLOR_BGR2GRAY)
            _, binary = cv2.threshold(gray, 0, 255,
                                      cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            enhanced = cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)
        cv2.imwrite(output_path, enhanced)
        tag = "CLAHE+Otsu" if binarize else "CLAHE"
        print(f"[staff_omr] {tag} 预处理完成 -> {output_path}")
        return output_path
    except Exception as e:
        print(f"[staff_omr] 图像预处理跳过（{e}），使用原图")
        return image_path


def _recognize_single_image(image_path: str) -> Tuple[List[Track], TimeSignature, int, int]:
    """
    识别单张图片。引擎优先级：Audiveris > MuseScore > oemer
    - Audiveris / MuseScore 使用 CLAHE 彩色图（引擎对灰度/彩色更友好）
    - oemer 额外做 Otsu 二值化（神经网络对黑白对比更敏感）
    """
    import tempfile as _tf, os as _os
    with _tf.TemporaryDirectory() as pre_dir:
        p_color  = _os.path.join(pre_dir, "enhanced.png")
        p_binary = _os.path.join(pre_dir, "binary.png")
        proc_color = _preprocess_staff_image(image_path, p_color, binarize=False)

        # ── Audiveris ──────────────────────────────────────────
        if _audiveris_available():
            print("[staff_omr] 使用 Audiveris 引擎")
            with _tf.TemporaryDirectory() as tmpdir:
                xml_path = _run_audiveris(proc_color, tmpdir)
                if xml_path:
                    return _parse_xml(xml_path)
            print("[staff_omr] Audiveris 失败，尝试下一引擎")

        # ── MuseScore ──────────────────────────────────────────
        if _musescore_available():
            print("[staff_omr] 使用 MuseScore 引擎")
            with _tf.TemporaryDirectory() as tmpdir:
                xml_path = _run_musescore(proc_color, tmpdir)
                if xml_path:
                    return _parse_xml(xml_path)
            print("[staff_omr] MuseScore 失败，降级到 oemer")
        elif not _audiveris_available():
            print("[staff_omr] Audiveris/MuseScore 均未配置，使用 oemer（精度较低）")

        # ── oemer（兜底）──────────────────────────────────────
        proc_binary = _preprocess_staff_image(image_path, p_binary, binarize=True)
        return _recognize_with_oemer(proc_binary)


# ─── Audiveris 引擎 ───────────────────────────────────────────

def _audiveris_available() -> bool:
    """检查 Audiveris 可执行文件是否存在"""
    try:
        from config import AUDIVERIS_PATH
        return os.path.isfile(AUDIVERIS_PATH)
    except Exception:
        return False


def _run_audiveris(image_path: str, output_dir: str) -> str | None:
    """
    调用 Audiveris CLI 识别图片，返回 MusicXML/.mxl 路径
    命令：Audiveris.exe -batch -export -output <dir> -- <image>
    """
    from config import AUDIVERIS_PATH

    cmd = [AUDIVERIS_PATH, "-batch", "-export", "-output", output_dir, "--", image_path]
    print(f"[staff_omr] 执行命令：{' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1800,   # 最多等 30 分钟（多页 PDF 耗时长）
            encoding="utf-8",
            errors="replace",
        )
        if result.stdout:
            print("[Audiveris]", result.stdout[-500:])
        if result.returncode != 0:
            print(f"[staff_omr] Audiveris 返回非零退出码：{result.returncode}")
            if result.stderr:
                print("[Audiveris stderr]", result.stderr[-300:])
    except subprocess.TimeoutExpired:
        raise RuntimeError("Audiveris 识别超时（>5分钟），请检查图片是否过大")
    except FileNotFoundError:
        raise RuntimeError(f"找不到 Audiveris：{AUDIVERIS_PATH}\n请检查 config.py 中的 AUDIVERIS_PATH")

    for fname in os.listdir(output_dir):
        if fname.endswith(".mxl") or fname.endswith(".xml") or fname.endswith(".musicxml"):
            return os.path.join(output_dir, fname)
    return None


# ─── MuseScore 引擎 ───────────────────────────────────────────

def _musescore_available() -> bool:
    """检查 MuseScore 可执行文件是否存在"""
    try:
        from config import MUSESCORE_PATH
        return os.path.isfile(MUSESCORE_PATH)
    except Exception:
        return False


def _run_musescore(image_path: str, output_dir: str) -> str | None:
    """
    调用 MuseScore CLI 将图片/PDF 转换为 MusicXML。
    命令：MuseScore4.exe -o <output.mxl> <input>
    MuseScore 4 支持直接打开图片并通过内置 OMR 导出乐谱。
    """
    from config import MUSESCORE_PATH
    out_path = os.path.join(output_dir, "score.mxl")
    cmd = [MUSESCORE_PATH, "-o", out_path, image_path]
    print(f"[staff_omr] MuseScore 命令：{' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            encoding="utf-8",
            errors="replace",
        )
        if result.stdout:
            print("[MuseScore]", result.stdout[-400:])
        if result.returncode != 0:
            print(f"[staff_omr] MuseScore 返回非零：{result.returncode}")
            if result.stderr:
                print("[MuseScore stderr]", result.stderr[-300:])
    except subprocess.TimeoutExpired:
        raise RuntimeError("MuseScore 处理超时（>5分钟）")
    except FileNotFoundError:
        raise RuntimeError(f"找不到 MuseScore：{MUSESCORE_PATH}\n请检查 config.py 中的 MUSESCORE_PATH")

    for fname in os.listdir(output_dir):
        if fname.endswith((".mxl", ".xml", ".musicxml")):
            return os.path.join(output_dir, fname)
    return None


# ─── oemer 引擎（兜底）────────────────────────────────────────

def _recognize_with_oemer(image_path: str) -> Tuple[List[Track], TimeSignature, int]:
    try:
        import oemer  # noqa: F401
        import music21  # noqa: F401
    except ImportError as e:
        raise ImportError(
            f"Audiveris 未配置，oemer 也缺少依赖：{e}\n"
            "请安装 Audiveris（推荐）或运行 pip install oemer music21"
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        xml_path = _run_oemer(image_path, tmpdir)
        if xml_path is None:
            raise RuntimeError("oemer 识别失败，未生成 MusicXML 文件")
        return _parse_xml(xml_path)


def _run_oemer(image_path: str, output_dir: str) -> str | None:
    """调用 oemer Python API"""
    from argparse import Namespace
    from oemer.ete import extract, clear_data, CHECKPOINTS_URL, MODULE_PATH, download_file

    for title, url in CHECKPOINTS_URL.items():
        save_dir = "unet_big" if title.startswith("1st") else "seg_net"
        save_dir = os.path.join(MODULE_PATH, "checkpoints", save_dir)
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, title.split("_")[1])
        if not os.path.exists(save_path):
            print(f"[staff_omr] 下载缺失的模型文件：{title}")
            download_file(title, url, save_path)

    clear_data()
    args = Namespace(
        img_path       = image_path,
        output_path    = output_dir,
        use_tf         = False,
        save_cache     = False,
        without_deskew = False,
    )
    mxl_path = extract(args)
    if mxl_path and os.path.exists(mxl_path):
        return mxl_path

    for fname in os.listdir(output_dir):
        if fname.endswith(".xml") or fname.endswith(".musicxml"):
            return os.path.join(output_dir, fname)
    return None


# ─── MusicXML 解析（两个引擎共用）───────────────────────────

def _parse_xml(xml_path: str) -> Tuple[List[Track], TimeSignature, int, int]:
    """music21 解析 MusicXML / MXL 文件，返回 (tracks, time_sig, bpm, key_sharps)"""
    import music21.converter
    score = music21.converter.parse(xml_path)
    return _parse_score(score)


def _clef_sign_to_name(clef_obj) -> str:
    """music21 Clef 对象 -> 'treble' | 'bass' | 'alto' | 'tenor'"""
    sign = getattr(clef_obj, 'sign', 'G') or 'G'
    if sign == 'G':
        return 'treble'
    if sign == 'F':
        return 'bass'
    if sign == 'C':
        return 'alto' if getattr(clef_obj, 'line', 3) == 3 else 'tenor'
    return 'auto'


def _parse_score(score) -> Tuple[List[Track], TimeSignature, int, int]:
    """将 music21 Score 转换为 Track 列表，返回 (tracks, time_sig, bpm, key_sharps)

    支持：
      - 多声部（每个 Part 独立轨道）
      - 多行谱（同一 Part 内含 staff 1 / staff 2，如钢琴大谱表）→ 拆成两条轨道
      - 谱号识别（高音 G / 低音 F / 中音 C）
      - 变谱号检测（轨道内发生谱号变化时在名称后追加标注）
    """
    from music21 import note as m21note, chord as m21chord, tempo as m21tempo, meter
    from music21 import key as m21key, clef as m21clef

    tracks: List[Track] = []
    bpm        = 120
    time_sig   = TimeSignature(4, 4)
    key_sharps = 0

    # ── 全局拍号 ──────────────────────────────────────────────
    ts_objs = score.flatten().getElementsByClass(meter.TimeSignature)
    if ts_objs:
        ts = ts_objs[0]
        time_sig = TimeSignature(ts.numerator, ts.denominator)

    # ── 全局速度 ──────────────────────────────────────────────
    mm_objs = score.flatten().getElementsByClass(m21tempo.MetronomeMark)
    if mm_objs:
        bpm = int(mm_objs[0].number or 120)
    else:
        import re as _re
        from music21 import expressions as m21expr
        for te in score.flatten().getElementsByClass(m21expr.TextExpression):
            txt = str(te.content)
            m = _re.search(r'[=＝]\s*(\d{2,3})', txt)
            if m:
                bpm = int(m.group(1))
                print(f"[staff_omr] 从文字标记中提取 BPM={bpm}：{txt!r}")
                break

    # ── 调号 ──────────────────────────────────────────────────
    ks_objs = score.flatten().getElementsByClass(m21key.KeySignature)
    if ks_objs:
        key_sharps = ks_objs[0].sharps
        print(f"[staff_omr] 检测到调号：{key_sharps:+d} 个升降号")

    # ── 逐 Part 解析 ──────────────────────────────────────────
    for part_idx, part in enumerate(score.parts):
        part_name = part.partName or f"轨道 {part_idx + 1}"

        # 1) 收集谱号（按 staff 编号记录第一个谱号 + 是否发生变化）
        staff_first_clef:   dict[int, str]  = {}   # staff_num -> clef_name
        staff_clef_changed: dict[int, bool] = {}   # staff_num -> 是否有变谱号
        for clef_obj in part.flatten().getElementsByClass(m21clef.Clef):
            # music21 把 staff 编号存在 editorial 里
            editorial = getattr(clef_obj, 'editorial', None)
            sn = getattr(editorial, 'staffNumber', None)
            if sn is None:
                sn = getattr(clef_obj, 'staff', None)
            sn = int(sn) if sn is not None else 1
            name = _clef_sign_to_name(clef_obj)
            if sn not in staff_first_clef:
                staff_first_clef[sn] = name
            elif staff_first_clef[sn] != name:
                staff_clef_changed[sn] = True

        # 2) 收集音符（按 staff 编号分组，保留绝对拍位）
        #    先尝试从 Measure 层遍历以保留 staff 信息；否则回退到 flatten
        staves: dict[int, list] = {}   # staff_num -> [{type, pitch/pitches, offset, duration, velocity}]

        try:
            from music21 import stream as m21stream
            for measure in part.getElementsByClass(m21stream.Measure):
                m_offset = float(measure.offset)
                # flatten() 穿透 Voice 层，取出所有声部的音符
                for elem in measure.flatten().notesAndRests:
                    duration_beats = float(elem.duration.quarterLength)
                    if duration_beats == 0:
                        continue  # 跳过装饰音（时值为0）

                    ed = getattr(elem, 'editorial', None)
                    sn = getattr(ed, 'staffNumber', None)
                    if sn is None:
                        sn = getattr(elem, 'staff', None)
                    sn = int(sn) if sn is not None else 1

                    note_offset = m_offset + float(elem.offset)

                    if isinstance(elem, m21note.Note):
                        staves.setdefault(sn, []).append({
                            'type':     'note',
                            'pitch':    elem.pitch.midi,
                            'offset':   note_offset,
                            'duration': duration_beats,
                            'velocity': int(elem.volume.velocity or 80) if elem.volume.velocity else 80,
                        })
                    elif isinstance(elem, m21chord.Chord):
                        staves.setdefault(sn, []).append({
                            'type':     'chord',
                            'pitches':  [p.midi for p in elem.pitches],
                            'offset':   note_offset,
                            'duration': duration_beats,
                        })
        except Exception:
            # 回退：flatten 整个 Part，不区分 staff
            for element in part.flatten().notesAndRests:
                d = float(element.duration.quarterLength)
                if d == 0:
                    continue  # 跳过装饰音
                if isinstance(element, m21note.Note):
                    staves.setdefault(1, []).append({
                        'type': 'note', 'pitch': element.pitch.midi,
                        'offset': float(element.offset), 'duration': d,
                        'velocity': int(element.volume.velocity or 80) if element.volume.velocity else 80,
                    })
                elif isinstance(element, m21chord.Chord):
                    staves.setdefault(1, []).append({
                        'type': 'chord', 'pitches': [p.midi for p in element.pitches],
                        'offset': float(element.offset), 'duration': d,
                    })

        if not staves:
            continue

        multi = len(staves) > 1   # 本 Part 含多行谱（如钢琴大谱表）

        # 3) 为每个 staff 创建独立轨道
        for staff_num, note_data_list in sorted(staves.items()):
            if multi:
                label     = '高音' if staff_num == 1 else '低音'
                tname     = f"{part_name} - {label}"
                default_c = 'treble' if staff_num == 1 else 'bass'
            else:
                tname     = part_name
                default_c = 'auto'

            clef_name = staff_first_clef.get(staff_num, default_c)

            track = Track(name=tname, instrument=0, clef=clef_name)

            if staff_clef_changed.get(staff_num):
                track.name += "（变谱号）"

            for nd in note_data_list:
                if nd['type'] == 'note':
                    track.add_note(Note(
                        pitch=nd['pitch'], start_beat=nd['offset'],
                        duration_beats=nd['duration'], velocity=nd['velocity'],
                    ))
                else:
                    for p in nd['pitches']:
                        track.add_note(Note(
                            pitch=p, start_beat=nd['offset'],
                            duration_beats=nd['duration'], velocity=80,
                        ))

            if track.notes:
                # 去重：同拍位 + 同音高的音符只保留一个（oemer 常见输出）
                seen: set[tuple[float, int]] = set()
                deduped: List[Note] = []
                dup_count = 0
                for n in track.notes:
                    key = (round(n.start_beat, 6), n.pitch)
                    if key not in seen:
                        seen.add(key)
                        deduped.append(n)
                    else:
                        dup_count += 1
                if dup_count:
                    print(f"[staff_omr] 去除重复音符 {dup_count} 个")
                track.notes = deduped

                # 后处理过滤：移除明显异常的音符
                before = len(track.notes)
                track.notes = [
                    n for n in track.notes
                    if 21 <= n.pitch <= 108           # 标准钢琴音域 A0-C8
                    and n.duration_beats >= 0.05      # 过短（32分音符以下）视为 OCR 噪声
                    and n.duration_beats <= 16.0      # 超过 4 小节的时值基本是解析错误
                ]
                filtered = before - len(track.notes)
                if filtered:
                    print(f"[staff_omr] 过滤异常音符 {filtered} 个")

                # A. 节拍量化：将 start_beat/duration_beats 对齐到最近的 1/16 拍格
                #    修正 OCR 产生的 0.997 → 1.0 之类的漂移
                _Q = 1.0 / 16
                q_count = 0
                for n in track.notes:
                    old_s = n.start_beat
                    old_d = n.duration_beats
                    n.start_beat     = round(n.start_beat     / _Q) * _Q
                    n.duration_beats = max(_Q, round(n.duration_beats / _Q) * _Q)
                    if abs(n.start_beat - old_s) > 1e-6 or abs(n.duration_beats - old_d) > 1e-6:
                        q_count += 1
                if q_count:
                    print(f"[staff_omr] 量化修正 {q_count} 个音符时值")

                # B. 小节完整性：防止音符跨越小节线（oemer/Audiveris 解析错误常见）
                bpm_num = time_sig.numerator
                clip_count = 0
                for n in track.notes:
                    measure_idx = int(n.start_beat / bpm_num)
                    measure_end = (measure_idx + 1) * bpm_num
                    if n.start_beat + n.duration_beats > measure_end + _Q:
                        n.duration_beats = max(_Q, round((measure_end - n.start_beat) / _Q) * _Q)
                        clip_count += 1
                if clip_count:
                    print(f"[staff_omr] 截断跨小节音符 {clip_count} 个")

                max_b = max(n.start_beat + n.duration_beats for n in track.notes)
                print(f"[staff_omr] Part {part_idx} Staff {staff_num}: "
                      f"{len(track.notes)} 个音符，谱号={track.clef}，"
                      f"最大拍位 {max_b:.1f}（≈第 {int(max_b/time_sig.numerator)+1} 小节）")
                tracks.append(track)

    return tracks, time_sig, bpm, key_sharps
