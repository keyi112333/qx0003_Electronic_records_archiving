#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OA 导出文件校验。

OA 导出可能返回 xlsx 二进制、HTML 登录页或旧版 xls。本模块只接受
真正的 xlsx，避免把登录页当附件写入磁盘。
"""
from pathlib import Path

from core.errors import BusinessPending

ZIP_MAGIC = b"PK\x03\x04"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def normalize_export_bytes(data: bytes, target) -> Path:
    """校验导出内容并写入目标路径，返回 Path。"""
    target_path = Path(target)
    if not data:
        raise BusinessPending("OA导出内容为空")
    if data.startswith(OLE_MAGIC):
        raise BusinessPending("OA导出为旧版xls，请改为xlsx格式")
    if not data.startswith(ZIP_MAGIC):
        head = data[:512].lower()
        if b"<html" in head or b"<!doctype" in head or b"<script" in head:
            raise BusinessPending("OA导出返回HTML页面，可能登录已过期")
        raise BusinessPending("OA导出内容不是xlsx")
    if b"xl/workbook.xml" not in data:
        raise BusinessPending("OA导出压缩包缺少xl/workbook.xml")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(data)
    return target_path
