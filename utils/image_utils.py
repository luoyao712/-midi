"""
图片 / PDF 预处理工具
支持 PNG、JPG、PDF 格式输入
PDF 自动拆分为逐页 PNG，再交给 OMR 模块识别
"""
from __future__ import annotations
import os
import tempfile
from typing import List

from PIL import Image


# ─── 格式检测 ────────────────────────────────────────────────

def get_file_type(path: str) -> str:
    """返回文件类型：'pdf' / 'image' / 'unknown'"""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.pdf':
        return 'pdf'
    if ext in ('.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.webp'):
        return 'image'
    return 'unknown'


# ─── PDF -> PNG 转换 ──────────────────────────────────────────

def pdf_to_images(pdf_path: str, dpi: int = 200) -> List[str]:
    """
    将 PDF 每页转换为 PNG 图片，存入临时目录
    返回 PNG 路径列表（调用方负责删除临时文件）

    Parameters
    ----------
    pdf_path : str  PDF 文件路径
    dpi      : int  渲染分辨率（越高越清晰，200 dpi 对 OMR 足够）
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError("缺少依赖 PyMuPDF，请运行：pip install PyMuPDF")

    doc      = fitz.open(pdf_path)
    tmpdir   = tempfile.mkdtemp(prefix="music_omr_")
    paths: List[str] = []

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        mat  = fitz.Matrix(dpi / 72, dpi / 72)  # 72 DPI 是 PDF 默认
        pix  = page.get_pixmap(matrix=mat, alpha=False)
        out  = os.path.join(tmpdir, f"page_{page_idx + 1:03d}.png")
        pix.save(out)
        paths.append(out)

    doc.close()
    return paths


# ─── 图片预处理（提升 OMR 精度）─────────────────────────────

def preprocess_image(image_path: str) -> str:
    """
    对乐谱图片进行预处理，返回处理后图片的临时路径
    处理步骤：灰度化 -> 对比度增强 -> 二值化（Otsu）

    五线谱图片保留灰度（oemer 需要灰度输入）
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        # 如果没有 opencv，直接返回原路径
        return image_path

    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return image_path

    # 自适应二值化（比全局 Otsu 更适合光照不均的图片）
    binary = cv2.adaptiveThreshold(
        img, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=25, C=10
    )

    # 保存到临时文件
    tmpdir = tempfile.mkdtemp(prefix="music_preproc_")
    out    = os.path.join(tmpdir, "preprocessed.png")
    cv2.imwrite(out, binary)
    return out


def load_image_for_display(path: str) -> Image.Image:
    """加载图片为 PIL Image（用于 UI 预览）"""
    return Image.open(path).convert("RGB")


# ─── 批量处理入口 ─────────────────────────────────────────────

def prepare_images(file_path: str, preprocess: bool = True) -> List[str]:
    """
    统一入口：接受任意格式，返回可直接送入 OMR 的图片路径列表
    """
    ftype = get_file_type(file_path)

    if ftype == 'pdf':
        pages = pdf_to_images(file_path)
    elif ftype == 'image':
        pages = [file_path]
    else:
        raise ValueError(f"不支持的文件格式：{file_path}")

    if preprocess:
        pages = [preprocess_image(p) for p in pages]

    return pages
