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


def _cell_text(cell) -> str:
    """取导出单元格显示值；下拉字段的枚举ID仅作回退。"""
    if not isinstance(cell, dict):
        return ""
    return str(cell.get("showValue") or cell.get("value") or "").strip()


def export_payload_to_xlsx(payload, target) -> Path:
    """把OA REST批量导出的JSON写成临时xlsx，供与结果表/规则表比对。"""
    try:
        import openpyxl
    except ImportError as exc:
        raise BusinessPending("写入OA导出临时Excel需要 openpyxl") from exc
    body = payload.get("data") or {}
    if str(body.get("success")) != "1":
        raise BusinessPending("OA导出未成功: " + str(body.get("message") or body.get("code") or ""))
    inner = body.get("data") or {}
    fields = (inner.get("definition") or {}).get("fields") or []
    if not fields:
        raise BusinessPending("OA导出缺少字段定义")
    workbook = openpyxl.Workbook()
    try:
        sheet = workbook.active
        sheet.title = "导出数据"
        sheet.append([str(field.get("display") or field.get("name") or "").strip()
                      for field in fields])
        for row in inner.get("data") or []:
            master = row.get("masterData") or {}
            sheet.append([_cell_text(master.get(field.get("name"))) for field in fields])
        target_path = Path(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        workbook.save(target_path)
        return target_path
    finally:
        workbook.close()


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
