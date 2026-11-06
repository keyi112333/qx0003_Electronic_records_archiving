#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模块三：合并 PDF。

职责：对 OA 状态为“确认已完成归集”的单据，读取 OA 现有附件，
按组织配置顺序合并 PDF，回写 OA 状态并输出 PdfResult。
"""
import shutil
from datetime import datetime
from pathlib import Path
from typing import List

from core.errors import BusinessPending
from core.retry import Verification
from core.task_base import TaskBase
from log.loggingConfig import logger
from workflows.qx0003_archive.models import (
    STATUS_PDF,
    Attachment,
    Declaration,
    OrganizationRules,
    OaSyncResult,
    PdfResult,
)
from workflows.qx0003_archive.subtask_collect import required_kinds
from workflows.qx0003_archive.pdf_builder import merge


class MergePdfSubtask(TaskBase):
    """子任务：合并 PDF。"""

    def __init__(self, context, declaration: Declaration,
                 sync: OaSyncResult, rules: OrganizationRules):
        super().__init__("合并PDF", context)
        self.oa = context.get("oa")
        self.config = context.config
        self.audit = context.get("audit")
        self.run_id = str(context.get("run_id") or "")
        self.declaration = declaration
        self.sync = sync
        self.rules = rules
        self.pdf_result: PdfResult | None = None
        self.temp_dir = Path(self.config.get_path("temp")) / self.run_id / "pdf"
        self.output_root = Path(self.config.get_path("output"))

    def execute(self) -> bool:
        if self.oa is None:
            raise ValueError("合并PDF子任务缺少OA适配器")
        self.pdf_result = self.run(self.declaration, self.sync, self.rules)
        self.context.set(f"pdf:{self.declaration.key}", self.pdf_result)
        return True

    def run(self, declaration: Declaration, sync: OaSyncResult,
            rules: OrganizationRules) -> PdfResult:
        if not rules.pdf_order:
            raise BusinessPending(f"OA打印顺序为空，不能合并PDF: {declaration.key}")

        snapshot = self.oa.read_attachments(declaration)
        if not snapshot.complete:
            raise BusinessPending("OA附件快照不完整，不能确认归集范围")
        current = str(sync.status or "").strip()
        if current not in ("确认已完成归集", STATUS_PDF):
            raise BusinessPending(f"OA状态不是“确认已完成归集/已归集为PDF”，当前: {sync.status}")

        required = required_kinds(rules, declaration)
        present = snapshot.kinds
        missing = [kind for kind in required if kind not in present]
        if missing:
            raise BusinessPending("必要附件缺失，不能生成PDF: " + "、".join(missing))

        folder = self.temp_dir / declaration.company / declaration.declaration_no
        files = self.oa.fetch_pdf_inputs(declaration, snapshot, folder)
        ordered = self._order(files, rules)
        if not ordered:
            raise BusinessPending("按配置顺序没有可合并的附件")

        output = self._output_path(declaration)
        self._archive_existing(output)
        page_count, manifest = merge([(item.kind, item.path) for item in ordered], output)

        uploaded = False
        if self.config.get_bool("OA配置.上传归集PDF", False):
            self.oa.upload_collected_pdf(declaration, output, manifest)
            uploaded = True

        merged_at = datetime.now().replace(microsecond=0)
        self.oa.upload_pdf(declaration, output, merged_at, manifest)
        verification, receipt = self.oa.verify_pdf(declaration, output, merged_at, manifest)
        if verification != Verification.COMPLETED:
            raise BusinessPending("OA状态回写后回查未通过，PDF已生成但状态未确认")

        # 状态变更后重新读取修改时间，结果表与 OA 保持同源。
        found = self.oa.find_form(declaration)
        if found:
            declaration.status = found.status or STATUS_PDF
            if found.modified_at:
                merged_at = found.modified_at.replace(microsecond=0)
        declaration.collected_pdf_path = str(output)
        declaration.collected_pdf_time = merged_at

        if self.audit:
            self.audit.write("pdf", declaration.key, "merge", "成功", {
                "path": str(output),
                "pages": page_count,
                "uploaded": uploaded,
                "status": declaration.status,
                "merged_at": merged_at.strftime("%Y-%m-%d %H:%M:%S"),
            })
            self.audit.save_pdf_manifest(declaration.declaration_no, {
                "declaration": declaration.to_dict(),
                "output": str(output),
                "pages": page_count,
                "merged_at": merged_at.strftime("%Y-%m-%d %H:%M:%S"),
                "uploaded": uploaded,
                "files": manifest,
            })
        logger.info("PDF归集完成: %s -> %s（%d页，上传OA=%s）",
                    declaration.key, output, page_count, uploaded)
        return PdfResult(
            path=str(output),
            page_count=page_count,
            merged_at=merged_at,
            uploaded=uploaded,
            status_verified=True,
        )

    def _order(self, files: List[Attachment], rules: OrganizationRules) -> List[Attachment]:
        by_kind = {}
        for item in files:
            if item.path is None:
                continue
            by_kind.setdefault(item.kind, []).append(item)
        for items in by_kind.values():
            items.sort(key=lambda item: str(item.effective_updated_at))

        ordered: List[Attachment] = []
        for kind in rules.pdf_order:
            ordered.extend(by_kind.pop(kind, []))
        for kind, items in by_kind.items():
            if kind in rules.required:
                logger.warning("附件类型不在打印顺序中，已追加到末尾: %s", kind)
                ordered.extend(items)
            else:
                logger.info("附件类型未配置归集，跳过: %s", kind)
        return ordered

    def _output_path(self, declaration: Declaration) -> Path:
        year = declaration.export_date.year if declaration.export_date else datetime.now().year
        return (self.output_root / declaration.company / str(year) / "PDF"
                / f"{declaration.declaration_no}.pdf")

    def _archive_existing(self, output: Path) -> None:
        if not output.is_file():
            return
        archive = output.parent / "archive" / self.run_id / output.name
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(output), str(archive))
        logger.info("旧归集PDF已归档: %s", archive)
