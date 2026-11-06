#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BIP 附件适配器。

BIP 的正式接口尚未提供（见开发文档 O03），当前支持两种可落地方式：
1. 读取 BIP 导出的查询结果文件（xlsx/csv），并按配置的附件目录取文件；
2. 直接扫描 `BIP配置.附件目录/{报关单号}` 下的附件。
两者都未配置时抛 BusinessPending，由模块一记录“BIP待补”。
"""
import csv
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.errors import BusinessPending
from web.qx0003.adapters import SourceAdapter
from workflows.qx0003_archive.models import Attachment, Snapshot, normalize_type

HEADER_ALIASES = {
    "报关单号": "declaration_no",
    "发票附件id": "source_id",
    "发票附件ID": "source_id",
    "附件id": "source_id",
    "附件ID": "source_id",
    "bip附件更新时间": "updated_at",
    "BIP附件更新时间": "updated_at",
    "更新时间": "updated_at",
    "附件路径": "path",
    "文件路径": "path",
    "附件文件名": "filename",
    "文件名": "filename",
    "发票单号": "invoice_no",
    "是否存在销售发票": "has_invoice",
    "是否存在国内运费形式发票": "has_freight",
    "附件类型": "kind",
}
ATTACHMENT_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}


@dataclass
class BipRecord:
    declaration_no: str
    source_id: str = ""
    updated_at: str = ""
    path: Optional[Path] = None
    kind: str = "销售发票"
    has_invoice: str = ""
    has_freight: str = ""


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    result = {}
    for key, value in row.items():
        name = str(key or "").strip()
        result[HEADER_ALIASES.get(name, name)] = value
    return result


def _read_rows(path: Path) -> List[Dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            return [_normalize_row(row) for row in csv.DictReader(stream)]
    try:
        import openpyxl
    except ImportError as exc:
        raise BusinessPending("读取BIP结果文件需要 openpyxl") from exc
    workbook = openpyxl.load_workbook(path, data_only=True)
    try:
        sheet = workbook.active
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(value or "").strip() for value in rows[0]]
        return [_normalize_row(dict(zip(headers, values))) for values in rows[1:]]
    finally:
        workbook.close()


class BipAdapter(SourceAdapter):
    """BIP 销售发票与运输发票附件来源。"""

    name = "BIP"
    api_based = True

    def __init__(self, config=None, browser=None):
        super().__init__(browser)
        self.config = config

    def _attachment_dir(self) -> Optional[Path]:
        value = self.config.get("BIP配置.附件目录") if self.config else None
        return Path(str(value)) if value else None

    def _query_file(self) -> Optional[Path]:
        if not self.config:
            return None
        value = (self.config.get("BIP配置.查询结果文件")
                 or self.config.get("BIP配置.报表路径"))
        return Path(str(value)) if value else None

    def _record_from_file(self, path: Path, kind: str) -> BipRecord:
        return BipRecord(
            declaration_no="",
            source_id=_hash_file(path),
            updated_at=datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            path=path,
            kind=normalize_type(kind),
        )

    def _records_from_query(self, declaration) -> List[BipRecord]:
        query_file = self._query_file()
        if not query_file or not query_file.is_file():
            return []
        attachment_dir = self._attachment_dir()
        records = []
        for row in _read_rows(query_file):
            number = str(row.get("declaration_no") or "").strip()
            if number != declaration.declaration_no:
                continue
            has_invoice = str(row.get("has_invoice") or "").strip()
            has_freight = str(row.get("has_freight") or "").strip()
            raw_path = str(row.get("path") or "").strip()
            filename = str(row.get("filename") or "").strip()
            source_id = str(row.get("source_id") or "").strip()
            kind_text = str(row.get("kind") or "").strip()
            if not kind_text:
                lowered = filename.lower()
                if "运费" in filename or "freight" in lowered:
                    kind_text = "国内运费形式发票"
                elif has_freight == "是" and has_invoice != "是":
                    kind_text = "国内运费形式发票"
                else:
                    kind_text = "销售发票"
            if has_invoice == "否" and "运费" not in kind_text:
                continue
            if has_freight == "否" and "运费" in kind_text:
                continue
            path = Path(raw_path) if raw_path else None
            if path is None and filename and attachment_dir:
                path = attachment_dir / filename
            if path is None or not path.is_file():
                continue
            records.append(BipRecord(
                declaration_no=number,
                source_id=source_id or _hash_file(path),
                updated_at=str(row.get("updated_at") or "").strip()
                or datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                path=path,
                kind=normalize_type(kind_text),
                has_invoice=has_invoice,
                has_freight=has_freight,
            ))
        return records

    def _records_from_folder(self, declaration) -> List[BipRecord]:
        attachment_dir = self._attachment_dir()
        if not attachment_dir or not attachment_dir.is_dir():
            return []
        folder = attachment_dir / declaration.declaration_no
        files = []
        if folder.is_dir():
            files = [path for path in folder.iterdir() if path.is_file()]
        else:
            files = [path for path in attachment_dir.glob(f"{declaration.declaration_no}*")
                     if path.is_file()]
        records = []
        for path in files:
            if path.suffix.lower() not in ATTACHMENT_SUFFIXES:
                continue
            kind = "国内运费形式发票" if "运费" in path.stem else "销售发票"
            record = self._record_from_file(path, kind)
            record.declaration_no = declaration.declaration_no
            records.append(record)
        return records

    def snapshot(self, declaration) -> Snapshot:
        query_file = self._query_file()
        if query_file:
            if not query_file.is_file():
                raise BusinessPending(f"BIP查询结果文件不存在: {query_file}")
            records = self._records_from_query(declaration)
        else:
            records = self._records_from_folder(declaration)
            if not records and not self._attachment_dir():
                raise BusinessPending(
                    "BIP未配置查询结果文件或附件目录，无法获取销售发票；"
                    "请在config.xlsx补充BIP配置.查询结果文件或BIP配置.附件目录"
                )
        items = [
            Attachment(
                kind=record.kind,
                source=self.name,
                source_id=record.source_id,
                updated_at=record.updated_at,
                path=record.path,
                robot_managed=True,
                content_hash=record.source_id,
            )
            for record in records
            if record.path
        ]
        return Snapshot(items, complete=True, scope=declaration.key, source=self.name)
