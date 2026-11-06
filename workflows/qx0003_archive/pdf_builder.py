#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF 合并与核验。"""
import io
from pathlib import Path
from typing import List, Tuple

from core.errors import BusinessPending
from utils.atomic import atomic_target

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}


def _import_pdf():
    try:
        from pypdf import PdfReader, PdfWriter
        return PdfReader, PdfWriter
    except ImportError as exc:
        raise BusinessPending("需要安装 pypdf: pip install pypdf") from exc


def _image_to_pdf(path: Path) -> bytes:
    try:
        from PIL import Image
    except ImportError as exc:
        raise BusinessPending("合并图片附件需要 Pillow") from exc
    with Image.open(path) as image:
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="PDF")
        return buffer.getvalue()


def merge(files: List[Tuple[str, Path]], output) -> Tuple[int, List[dict]]:
    """按传入顺序合并 PDF/图片，返回 (页数, 清单)。

    files 为 [(类型, 路径)]，调用方负责排序。
    """
    PdfReader, PdfWriter = _import_pdf()
    writer = PdfWriter()
    manifest = []
    total_pages = 0
    for kind, path in files:
        path = Path(path)
        if not path.is_file() or path.stat().st_size == 0:
            raise BusinessPending(f"待合并附件不存在或为空: {path}")
        suffix = path.suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            reader = PdfReader(io.BytesIO(_image_to_pdf(path)))
        elif suffix == ".pdf":
            reader = PdfReader(str(path))
        else:
            raise BusinessPending(f"附件格式不支持合并: {path.name}")
        if getattr(reader, "is_encrypted", False):
            raise BusinessPending(f"附件PDF已加密，无法合并: {path.name}")
        pages = len(reader.pages)
        if pages <= 0:
            raise BusinessPending(f"附件PDF没有页面: {path.name}")
        start_page = total_pages
        for page in reader.pages:
            writer.add_page(page)
        total_pages += pages
        try:
            writer.add_outline_item(f"{len(manifest) + 1}.{kind}", start_page)
        except Exception:
            pass
        manifest.append({
            "index": len(manifest) + 1,
            "kind": kind,
            "path": str(path),
            "pages": pages,
            "start_page": start_page + 1,
        })
    if total_pages <= 0:
        raise BusinessPending("没有可合并的附件页面")
    output_path = Path(output)
    with atomic_target(output_path) as pending:
        with pending.open("wb") as stream:
            writer.write(stream)
    # 重新打开核验，避免写出损坏文件。
    verify = PdfReader(str(output_path))
    if len(verify.pages) != total_pages:
        raise BusinessPending("合并后PDF页数与预期不一致")
    return total_pages, manifest
