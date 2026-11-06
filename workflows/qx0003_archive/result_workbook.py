#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运行结果表读写。

按组织、出口日期所属年度维护“明细”和“打印”两个页签。
模板样式沿用 docs 下的 xlsm；数据从第 3 行开始写入，第 2 行保留为模板示例。
"""
import os
import shutil
import time
from copy import copy
from pathlib import Path
from typing import Any, Iterable, List, Optional

from log.loggingConfig import logger
from workflows.qx0003_archive.models import (
    STATUS_PDF,
    Attachment,
    Declaration,
    OaSyncResult,
    PdfResult,
    RunItem,
    parse_datetime,
)

DETAIL_SHEET = "明细"
PRINT_SHEET = "打印"
DATA_START_ROW = 3

DETAIL_HEADERS = [
    "组织", "报关单号", "合同号", "出口日期", "创建人", "创建时间", "状态",
    "归集的PDF路径", "归集的PDF时间", "是否打印", "打印时间",
    "报关单", "报关单路径", "报关单归集时间",
    "放行单", "放行单路径", "放行单归集时间",
    "合同", "合同路径", "合同归集时间时间",
    "提单", "提单路径", "提单归集时间",
    "一卡通上的其它附件", "一卡通上的其它附件路径", "一卡通上的其它附件归集归集时间",
    "销售发票", "销售发票路径", "销售发票归集时间",
    "BIP上的其他附件", "BIP上的其他附件路径", "BIP上的其他附件归集时间",
    "运行状态", "异常信息", "开始时间", "结束时间",
]

PRINT_HEADERS = [
    "组织", "报关单号", "出口日期", "创建人", "创建时间", "状态",
    "归集的PDF路径", "归集PDF时间", "是否打印", "打印时间", "打印后再次归集PDF？",
]

# 类型 -> (存在标识列, 路径列, 归集时间列)；每列给出别名，兼容模板表头错别字。
ATTACHMENT_COLUMNS = {
    "报关单": (("报关单",), ("报关单路径",), ("报关单归集时间",)),
    "放行通知单": (("放行单",), ("放行单路径",), ("放行单归集时间",)),
    "合同": (("合同",), ("合同路径",), ("合同归集时间时间", "合同归集时间")),
    "提单": (("提单",), ("提单路径",), ("提单归集时间",)),
    "一卡通其他附件": (
        ("一卡通上的其它附件", "一卡通上的其他附件"),
        ("一卡通上的其它附件路径", "一卡通上的其他附件路径"),
        ("一卡通上的其它附件归集归集时间", "一卡通上的其它附件归集时间"),
    ),
    "销售发票": (("销售发票",), ("销售发票路径",), ("销售发票归集时间",)),
    "BIP其他附件": (
        ("BIP上的其他附件",), ("BIP上的其他附件路径",), ("BIP上的其他附件归集时间",),
    ),
    "电子委托协议": (
        ("电子委托协议",), ("电子委托协议路径",), ("电子委托协议归集时间",),
    ),
    "发票": (("发票",), ("发票路径",), ("发票归集时间",)),
    "装箱单": (("装箱单",), ("装箱单路径",), ("装箱单归集时间",)),
    "保税核注清单": (
        ("保税核注清单", "报税核注清单"),
        ("保税核注清单路径", "报税核注清单路径"),
        ("保税核注清单归集时间", "报税核注清单归集时间"),
    ),
    "代理报关发票": (
        ("代理报关发票",), ("代理报关发票路径",), ("代理报关发票归集时间",),
    ),
    "国内运输发票": (
        ("国内运输发票",), ("国内运输发票路径",), ("国内运输发票归集时间",),
    ),
    "CIF保险单": (("CIF保险单",), ("CIF保险单路径",), ("CIF保险单归集时间",)),
    "国内运费形式发票": (
        ("国内运费形式发票",), ("国内运费形式发票路径",), ("国内运费形式发票归集时间",),
    ),
    "其他附件": (("其他附件",), ("其他附件路径",), ("其他附件归集时间",)),
}


class ResultWorkbook:
    """结果表管理器。"""

    def __init__(self, config, run_id: str = ""):
        self.config = config
        self.run_id = run_id
        root = config.get("路径配置.结果表根目录")
        self.root = Path(str(root)) if root else Path(config.get_path("log"))
        self.template = self._find_template(config)

    @staticmethod
    def _find_template(config) -> Path:
        configured = config.get("路径配置.结果表模板")
        if configured and Path(str(configured)).is_file():
            return Path(str(configured))
        candidates = [
            Path.cwd() / "docs",
            Path(__file__).resolve().parents[2] / "docs",
        ]
        for folder in candidates:
            if not folder.is_dir():
                continue
            matches = sorted(folder.glob("*运行结果表*.xlsm"))
            if matches:
                return matches[0]
        raise FileNotFoundError("未找到运行结果表模板（docs/*运行结果表*.xlsm）")

    def workbook_path(self, declaration: Declaration) -> Path:
        year = declaration.export_date.year if declaration.export_date else 0
        if not year:
            raise ValueError("出口日期为空，无法确定结果表年度")
        return (self.root / declaration.company / str(year)
                / f"{declaration.company}-电子档案管理（单证归集与打印）-运行结果表-{year}.xlsm")

    def _ensure_workbook(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            if not self.template.is_file():
                raise FileNotFoundError(f"结果表模板不存在: {self.template}")
            shutil.copy2(self.template, path)
            logger.info("结果表已创建: %s", path)
        return path

    def save(self, declaration: Declaration, item: RunItem,
             sync: Optional[OaSyncResult] = None,
             pdf: Optional[PdfResult] = None) -> Path:
        path = self._ensure_workbook(self.workbook_path(declaration))
        workbook = None
        try:
            import openpyxl
            workbook = openpyxl.load_workbook(path, keep_vba=True)
            detail = workbook[DETAIL_SHEET]
            headers = self._headers(detail)
            self._validate_detail(headers)
            headers = self._ensure_attachment_columns(detail, headers, sync, item)
            row = self._upsert_row(detail, headers, declaration)
            self._write_detail(detail, headers, row, declaration, item, sync, pdf)

            if pdf or str(declaration.status or "").strip() == STATUS_PDF:
                print_sheet = workbook[PRINT_SHEET]
                print_headers = self._headers(print_sheet)
                print_row = self._upsert_row(print_sheet, print_headers, declaration)
                self._write_print(print_sheet, print_headers, print_row, declaration, pdf)

            self._save_with_retry(workbook, path)
            logger.info("结果表已更新: %s（%s）", path, declaration.key)
            return path
        finally:
            if workbook is not None:
                workbook.close()

    @staticmethod
    def _headers(sheet) -> List[str]:
        return [str(cell.value or "").strip() for cell in sheet[1]]

    @staticmethod
    def _validate_detail(headers: List[str]) -> None:
        required = ["组织", "报关单号", "运行状态", "异常信息"]
        missing = [name for name in required if name not in headers]
        if missing:
            raise ValueError("结果表“明细”页签缺少列: " + "、".join(missing))

    @staticmethod
    def _column(headers: List[str], names: Iterable[str]) -> Optional[int]:
        for name in names:
            if name in headers:
                return headers.index(name)
        return None

    def _ensure_attachment_columns(self, sheet, headers: List[str],
                                   sync: Optional[OaSyncResult],
                                   item: RunItem) -> List[str]:
        """按实际附件类型扩展三列（存在/路径/时间），插入到“运行状态”之前。"""
        present = set()
        if sync and sync.snapshot:
            present.update(att.kind for att in sync.snapshot.items)
        if item.package:
            present.update(att.kind for att in item.package.items)
        if not present:
            return headers
        from openpyxl.utils import get_column_letter
        insert_at = headers.index("运行状态") + 1 if "运行状态" in headers else len(headers) + 1
        for kind, columns in ATTACHMENT_COLUMNS.items():
            if kind == "国内运费形式发票" and any(
                    name in headers for name in ATTACHMENT_COLUMNS["BIP其他附件"][0]):
                continue
            if kind not in present:
                continue
            if any(name in headers for names in columns for name in names):
                continue
            sheet.insert_cols(insert_at, 3)
            style_source = sheet.cell(row=1, column=max(1, insert_at - 1))
            for offset, name in enumerate((columns[0][0], columns[1][0], columns[2][0])):
                cell = sheet.cell(row=1, column=insert_at + offset)
                cell.value = name
                cell.font = copy(style_source.font)
                cell.fill = copy(style_source.fill)
                cell.border = copy(style_source.border)
                cell.alignment = copy(style_source.alignment)
                sheet.column_dimensions[get_column_letter(insert_at + offset)].width = 14
            headers = self._headers(sheet)
            insert_at += 3
        return headers

    def _upsert_row(self, sheet, headers: List[str], declaration: Declaration) -> int:
        company_col = self._column(headers, ("组织",))
        number_col = self._column(headers, ("报关单号",))
        if company_col is None or number_col is None:
            raise ValueError("结果表缺少组织或报关单号列")
        for row in range(2, sheet.max_row + 1):
            company = str(sheet.cell(row=row, column=company_col + 1).value or "").strip()
            number = str(sheet.cell(row=row, column=number_col + 1).value or "").strip()
            if company == declaration.company and number == declaration.declaration_no:
                return row
        return max(sheet.max_row + 1, DATA_START_ROW)

    @staticmethod
    def _set(sheet, row: int, headers: List[str], names: Iterable[str],
             value: Any, number_format: str = "") -> None:
        for name in names:
            if name in headers:
                cell = sheet.cell(row=row, column=headers.index(name) + 1)
                cell.value = value
                if number_format:
                    cell.number_format = number_format
                return

    def _set_link(self, sheet, row: int, headers: List[str],
                  names: Iterable[str], value: Any) -> None:
        for name in names:
            if name in headers:
                cell = sheet.cell(row=row, column=headers.index(name) + 1)
                text = str(value or "").strip()
                cell.value = text
                if text:
                    cell.hyperlink = text
                    cell.style = "Hyperlink"
                else:
                    cell.hyperlink = None
                return

    def _write_detail(self, sheet, headers: List[str], row: int,
                      declaration: Declaration, item: RunItem,
                      sync: Optional[OaSyncResult], pdf: Optional[PdfResult]) -> None:
        self._set(sheet, row, headers, ("组织",), declaration.company)
        self._set(sheet, row, headers, ("报关单号",), declaration.declaration_no)
        self._set(sheet, row, headers, ("合同号",), declaration.contract_no)
        self._set(sheet, row, headers, ("出口日期",), declaration.export_date,
                  "yyyy-mm-dd")
        self._set(sheet, row, headers, ("创建人",), declaration.created_by)
        self._set(sheet, row, headers, ("创建时间",), declaration.created_at,
                  "yyyy-mm-dd hh:mm:ss")
        self._set(sheet, row, headers, ("状态",), declaration.status)

        pdf_path = pdf.path if pdf else declaration.collected_pdf_path
        pdf_time = pdf.merged_at if pdf else declaration.collected_pdf_time
        self._set_link(sheet, row, headers, ("归集的PDF路径",), pdf_path)
        self._set(sheet, row, headers, ("归集的PDF时间",), pdf_time,
                  "yyyy-mm-dd hh:mm:ss")

        for kind, columns in ATTACHMENT_COLUMNS.items():
            attachment = self._pick_attachment(kind, sync, item)
            exists_col, path_cols, time_cols = columns
            if not any(name in headers for names in columns for name in names):
                continue
            if not attachment:
                self._set(sheet, row, headers, exists_col, "否")
                self._set_link(sheet, row, headers, path_cols, "")
                self._set(sheet, row, headers, time_cols, None, "yyyy-mm-dd hh:mm:ss")
                continue
            self._set(sheet, row, headers, exists_col, "是")
            self._set_link(sheet, row, headers, path_cols, attachment.path or "")
            self._set(sheet, row, headers, time_cols,
                      parse_datetime(attachment.effective_updated_at),
                      "yyyy-mm-dd hh:mm:ss")

        self._set(sheet, row, headers, ("运行状态",), item.status)
        self._set(sheet, row, headers, ("异常信息",), (item.error_message or "")[:500])
        self._set(sheet, row, headers, ("开始时间",), item.start_time,
                  "yyyy-mm-dd hh:mm:ss")
        self._set(sheet, row, headers, ("结束时间",), item.end_time,
                  "yyyy-mm-dd hh:mm:ss")

    @staticmethod
    def _pick_attachment(kind: str, sync: Optional[OaSyncResult],
                         item: RunItem) -> Optional[Attachment]:
        kinds = [kind]
        if kind == "BIP其他附件":
            kinds.append("国内运费形式发票")
        candidates: List[Attachment] = []
        if sync and sync.snapshot:
            candidates.extend([att for att in sync.snapshot.items if att.kind in kinds])
        if item.package:
            candidates.extend([att for att in item.package.items if att.kind in kinds])
        if not candidates:
            return None
        candidates.sort(key=lambda att: str(att.effective_updated_at), reverse=True)
        selected = candidates[0]
        if not selected.path and item.package:
            local = next((att for att in item.package.items
                          if att.key == selected.key and att.path), None)
            if local:
                selected = Attachment(
                    kind=selected.kind,
                    source=selected.source,
                    source_id=selected.source_id,
                    updated_at=selected.updated_at,
                    path=local.path,
                    robot_managed=selected.robot_managed,
                    content_hash=selected.content_hash,
                    oa_updated_at=selected.oa_updated_at,
                )
        return selected

    def _write_print(self, sheet, headers: List[str], row: int,
                     declaration: Declaration, pdf: Optional[PdfResult]) -> None:
        self._set(sheet, row, headers, ("组织",), declaration.company)
        self._set(sheet, row, headers, ("报关单号",), declaration.declaration_no)
        self._set(sheet, row, headers, ("出口日期",), declaration.export_date,
                  "yyyy-mm-dd")
        self._set(sheet, row, headers, ("创建人",), declaration.created_by)
        self._set(sheet, row, headers, ("创建时间",), declaration.created_at,
                  "yyyy-mm-dd hh:mm:ss")
        self._set(sheet, row, headers, ("状态",), STATUS_PDF)
        self._set_link(sheet, row, headers, ("归集的PDF路径",),
                       pdf.path if pdf else declaration.collected_pdf_path)
        self._set(sheet, row, headers, ("归集PDF时间",),
                  pdf.merged_at if pdf else declaration.collected_pdf_time,
                  "yyyy-mm-dd hh:mm:ss")
        if "打印后再次归集PDF？" in headers:
            col = headers.index("打印后再次归集PDF？") + 1
            sheet.cell(row=row, column=col).value = (
                f'=IF((H{row}-J{row})>0,"是","否")')

    @staticmethod
    def _save_with_retry(workbook, path: Path, attempts: int = 5, delay: float = 2.0) -> None:
        last_error = None
        for index in range(attempts):
            pending = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            try:
                workbook.save(pending)
                os.replace(pending, path)
                return
            except PermissionError as exc:
                last_error = exc
                if pending.exists():
                    try:
                        pending.unlink()
                    except OSError:
                        pass
                if index < attempts - 1:
                    logger.warning("结果表被占用，%.0f秒后重试(%d/%d): %s",
                                   delay, index + 1, attempts, path)
                    time.sleep(delay)
            finally:
                if pending.exists():
                    try:
                        pending.unlink()
                    except OSError:
                        pass
        raise PermissionError(f"结果表保存失败，可能被用户打开: {path}") from last_error
