#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模块二（新增）：OA 新建主记录并在保存前上传附件。

职责：OA 列表中不存在该单据时，新建主表 -> 填写组织/出口日期/报关单号/
合同编号 -> 在当前表单内上传本轮收集的附件 -> 保存一次 -> 回查创建结果。
本模块不重新下载源附件、不生成 PDF；保存失败时不重复建单。
"""
from typing import List

from core.errors import BusinessPending
from core.retry import Verification
from core.task_base import TaskBase
from log.loggingConfig import logger
from workflows.qx0003_archive.models import (
    NEXT_MANUAL,
    Attachment,
    CollectionPackage,
    Declaration,
    OaSyncResult,
    next_action,
)


class OaCreateSubtask(TaskBase):
    """子任务：OA 新增（填表 + 上传附件 + 保存一次）。"""

    def __init__(self, context, declaration: Declaration, package: CollectionPackage):
        super().__init__("OA新增", context)
        self.oa = context.get("oa")
        self.config = context.config
        self.audit = context.get("audit")
        self.declaration = declaration
        self.package = package
        self.sync_result = None
        self.robot_name = str(
            (self.config.get("OA配置.机器人创建人") if self.config else None) or "RPA"
        ).strip() or "RPA"

    def execute(self) -> bool:
        if self.oa is None:
            raise ValueError("OA新增子任务缺少OA适配器")
        if self.package is None:
            raise BusinessPending("OA新增缺少收集结果，不能在保存前上传附件")
        self.sync_result = self.create(self.declaration, self.package)
        self.context.set(f"sync:{self.declaration.key}", self.sync_result)
        return True

    def create(self, declaration: Declaration, package: CollectionPackage) -> OaSyncResult:
        attachments: List[Attachment] = [item for item in package.items if item.path]
        self.oa.create_form(declaration, run_id=declaration.key, attachments=attachments)
        verification, created = self.oa.verify_form(declaration, run_id=declaration.key)
        if verification != Verification.COMPLETED or created is None:
            if self.audit:
                self.audit.write("sync", declaration.key, "create", verification.value,
                                 {"created_by": getattr(created, "created_by", "")})
            return OaSyncResult(
                declaration=declaration,
                exists=False,
                status=declaration.status,
                next_action=NEXT_MANUAL,
                error_message="OA新建后回查未完成，禁止重复保存",
            )

        declaration.exists_in_oa = True
        declaration.created_by = created.created_by or self.robot_name
        declaration.status = created.status
        declaration.created_at = created.created_at
        declaration.modified_at = created.modified_at
        declaration.oa_form_id = created.oa_form_id or declaration.oa_form_id
        declaration.origin = "robot_created"

        snapshot = self.oa.read_attachments(declaration)
        present = {(item.kind, item.source_id) for item in snapshot.items}
        unresolved = [f"未在OA确认: {item.kind}/{item.source_id}"
                      for item in attachments if (item.kind, item.source_id) not in present]
        result = OaSyncResult(
            declaration=declaration,
            exists=True,
            robot_owned=True,
            status=declaration.status,
            added=list(attachments),
            unresolved=unresolved,
            snapshot=snapshot,
            next_action=next_action(declaration),
        )
        if self.audit:
            self.audit.write("sync", declaration.key, "create", "成功", {
                "attachments": len(attachments),
                "unresolved": unresolved,
                "status": declaration.status,
                "next_action": result.next_action,
            })
        logger.info("OA新增完成: %s，保存前上传附件%d个，未确认%d个，下一步=%s",
                    declaration.key, len(attachments), len(unresolved), result.next_action)
        return result
