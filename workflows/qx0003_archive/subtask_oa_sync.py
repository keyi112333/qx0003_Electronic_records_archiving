#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模块二（同步历史）：OA 已有单据的读取与附件同步。

职责：OA 列表中已存在该单据时沿用主记录，读取状态；机器人新增的历史单据
按源ID比对并新增/替换/删除附件，历史或人工单据只读。
本模块不新建主表、不重新下载源附件、不生成 PDF。
"""
from typing import Dict, List, Optional

from core.retry import Verification
from core.task_base import TaskBase
from log.loggingConfig import logger
from workflows.qx0003_archive.models import (
    Attachment,
    CollectionPackage,
    Declaration,
    OaSyncResult,
    Snapshot,
    next_action,
)


class OaHistorySyncSubtask(TaskBase):
    """子任务：OA 同步历史（只读或维护既有单据附件）。"""

    def __init__(self, context, declaration: Declaration, existing: Declaration,
                 package: Optional[CollectionPackage] = None):
        super().__init__("OA同步历史", context)
        self.oa = context.get("oa")
        self.config = context.config
        self.audit = context.get("audit")
        self.declaration = declaration
        self.existing = existing
        self.package = package
        self.sync_result: Optional[OaSyncResult] = None
        self.robot_name = str(
            (self.config.get("OA配置.机器人创建人") if self.config else None) or "RPA"
        ).strip() or "RPA"

    def execute(self) -> bool:
        if self.oa is None:
            raise ValueError("OA同步历史子任务缺少OA适配器")
        if self.existing is None:
            raise ValueError("OA同步历史子任务缺少已存在的OA主记录")
        self.sync_result = self.sync(self.declaration, self.existing)
        self.context.set(f"sync:{self.declaration.key}", self.sync_result)
        return True

    def _apply_found(self, declaration: Declaration, found: Declaration) -> None:
        if found.created_by:
            declaration.created_by = found.created_by
        if found.status:
            declaration.status = found.status
        if found.created_at:
            declaration.created_at = found.created_at
        if found.modified_at:
            declaration.modified_at = found.modified_at
        if found.oa_form_id:
            declaration.oa_form_id = found.oa_form_id
        if found.collected_pdf_path:
            declaration.collected_pdf_path = found.collected_pdf_path
        if found.collected_pdf_time:
            declaration.collected_pdf_time = found.collected_pdf_time
        declaration.exists_in_oa = True
        declaration.origin = (
            "robot_created" if declaration.created_by == self.robot_name else "manual_or_history"
        )

    def sync(self, declaration: Declaration, found: Declaration) -> OaSyncResult:
        """沿用已有主表并同步附件；历史/人工单据只读。"""
        self._apply_found(declaration, found)
        logger.info("OA主记录已存在: %s，创建人=%s，状态=%s",
                    declaration.key, declaration.created_by, declaration.status)

        if not declaration.robot_owned:
            snapshot = self.oa.read_attachments(declaration)
            if any(item.robot_managed for item in snapshot.items):
                # 列表不显示创建人时，用明细“归集人=RPA”判定机器人历史单据。
                declaration.origin = "robot_created"
                declaration.created_by = declaration.created_by or self.robot_name
            else:
                if self.audit:
                    self.audit.write("sync", declaration.key, "read_only", "跳过",
                                     {"reason": "历史/人工单据不维护源附件"})
                return OaSyncResult(
                    declaration=declaration,
                    exists=True,
                    robot_owned=False,
                    status=declaration.status,
                    snapshot=snapshot,
                    next_action=next_action(declaration),
                )

        snapshot = self.oa.read_attachments(declaration)
        result = OaSyncResult(
            declaration=declaration,
            exists=True,
            robot_owned=True,
            status=declaration.status,
            snapshot=snapshot,
        )
        if self.package is not None:
            self._apply_diff(result, declaration, self.package, snapshot)
            self._execute(result, declaration)
            snapshot = self.oa.read_attachments(declaration)
            result.snapshot = snapshot
            result.unresolved = self._unresolved(self.package, snapshot)
        result.next_action = next_action(declaration)
        if self.audit:
            self.audit.write("sync", declaration.key, "sync", "成功", {
                "added": len(result.added),
                "updated": len(result.updated),
                "deleted": len(result.deleted),
                "unresolved": result.unresolved,
                "status": declaration.status,
                "next_action": result.next_action,
            })
        logger.info("OA历史同步完成: %s，新增%d，更新%d，删除%d，未解决%d，下一步=%s",
                    declaration.key, len(result.added), len(result.updated),
                    len(result.deleted), len(result.unresolved), result.next_action)
        return result

    def _apply_diff(self, result: OaSyncResult, declaration: Declaration,
                    package: CollectionPackage, snapshot: Snapshot) -> None:
        oa_map: Dict[tuple, Attachment] = {
            (item.kind, item.source_id): item for item in snapshot.items if item.source_id
        }
        package_map: Dict[tuple, Attachment] = {
            (item.kind, item.source_id): item for item in package.items if item.source_id
        }
        result.added = [item for key, item in package_map.items() if key not in oa_map]
        result.updated = [
            item for key, item in package_map.items()
            if key in oa_map and self._source_changed(oa_map[key], item)
        ]
        if package.complete:
            result.deleted = [
                item for key, item in oa_map.items()
                if item.robot_managed and key not in package_map
            ]
        else:
            result.deleted = []
            logger.info("收集不完整，跳过删除同步: %s", declaration.key)

    @staticmethod
    def _source_changed(existing: Attachment, incoming: Attachment) -> bool:
        """仅 BIP 使用同一附件ID+更新时间判定更新。

        一卡通每次上传会生成新的附件ID，变更表现为新增+删除，不能比较 OA 侧
        自动写入的附件更新时间，避免每轮重复上传。
        """
        if existing.source != "BIP" and incoming.source != "BIP":
            return False
        return (str(existing.updated_at or "") != str(incoming.updated_at or "")
                and bool(existing.updated_at) and bool(incoming.updated_at))

    def _execute(self, result: OaSyncResult, declaration: Declaration) -> None:
        for item in result.added:
            self.oa.upload_attachment(declaration, item)
            verification, _ = self.oa.verify_attachment(declaration, item)
            if verification != Verification.COMPLETED:
                result.unresolved.append(f"新增未回查: {item.kind}/{item.source_id}")
        for item in result.updated:
            existing = next(
                (old for old in (result.snapshot.items if result.snapshot else [])
                 if old.key == item.key), None)
            if existing is None:
                result.unresolved.append(f"更新缺少旧附件: {item.kind}/{item.source_id}")
                continue
            self.oa.delete_attachment(declaration, existing)
            self.oa.upload_attachment(declaration, item)
            verification, _ = self.oa.verify_attachment(declaration, item)
            if verification != Verification.COMPLETED:
                result.unresolved.append(f"更新未回查: {item.kind}/{item.source_id}")
        for item in result.deleted:
            self.oa.delete_attachment(declaration, item)
            verification, _ = self.oa.verify_deletion(declaration, item)
            if verification != Verification.COMPLETED:
                result.unresolved.append(f"删除未回查: {item.kind}/{item.source_id}")

    @staticmethod
    def _unresolved(package: CollectionPackage, snapshot: Snapshot) -> List[str]:
        present = {(item.kind, item.source_id) for item in snapshot.items}
        return [
            f"未在OA确认: {item.kind}/{item.source_id}"
            for item in package.items
            if (item.kind, item.source_id) not in present
        ]
