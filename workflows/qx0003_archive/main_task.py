#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QX0003 主任务：附件收集 -> OA同步 -> 合并PDF。"""
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from config.credentials import get_credential
from core.context import TaskContext
from core.errors import BusinessPending, ConfigError, ImplementationRequired, SystemFailure
from core.retry import call_with_retry
from core.task_base import TaskBase
from log.loggingConfig import logger
from utils.screenshot import take_error_screenshot
from web.browser import BrowserManager
from web.oa.pages import ArchiveOaAdapter
from workflows.qx0003_archive.models import (
    NEXT_MERGE_PDF,
    Declaration,
    OrganizationRules,
    RunItem,
)
from workflows.qx0003_archive.subtask_collect import CollectSubtask
from workflows.qx0003_archive.subtask_merge_pdf import MergePdfSubtask
from workflows.qx0003_archive.subtask_oa_create import OaCreateSubtask
from workflows.qx0003_archive.subtask_oa_sync import OaHistorySyncSubtask
from workflows.qx0003_archive.notify import Notifier
from workflows.qx0003_archive.lock import SingleInstance
from workflows.qx0003_archive.params import RunParams
from workflows.qx0003_archive.result_workbook import ResultWorkbook
from workflows.qx0003_archive.run_state import AuditLog, RunState


class Qx0003MainTask(TaskBase):
    """电子档案管理（单证归集与打印）主任务。"""

    def __init__(self, config):
        context = TaskContext()
        context.config = config
        super().__init__("电子档案管理（单证归集与打印）", context)
        self.config = config
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.context.set("run_id", self.run_id)
        self.items: List[RunItem] = []
        self.workbooks: Dict[str, List[Path]] = {}
        self.notifier = Notifier(config)
        self.audit: Optional[AuditLog] = None

    def execute(self) -> bool:
        params = RunParams.from_config(self.config)
        self.config.ensure_directories()
        log_dir = Path(self.config.get_path("log"))
        lock = SingleInstance(log_dir / "qx0003.lock")
        if not lock.acquire():
            logger.error("已有机器人实例在运行，本次退出")
            self.notifier.notify_error("单实例检查", "", RuntimeError("已有实例运行"))
            return False
        state = None
        try:
            state = RunState.load(log_dir)
            self.audit = AuditLog(log_dir, self.run_id)
            self.context.set("audit", self.audit)
            self.audit.write("run", "", "start", "开始", {"params": params.summary()})
            self.notifier.notify_start(self.run_id, params.summary())
            self._cleanup_temp()

            # 收集阶段只处理本地目录/接口来源，不启动OA浏览器；OA在同步阶段懒启动。
            scanner = CollectSubtask(self.context)
            self.context.set("shared_source", scanner.shared)
            companies = self._resolve_companies(params)
            declarations = scanner.discover(params, companies)
            if not declarations:
                logger.info("没有待处理单据，流程结束")
                self._notify_end()
                if state:
                    state.save(self.run_id, self._per_org(), success=True)
                return True

            result_workbook = ResultWorkbook(self.config, self.run_id)

            for declaration in declarations:
                self._process(declaration, result_workbook, params.stage)

            self._notify_end()
            if state:
                state.save(self.run_id, self._per_org(), success=True)
            self.audit.write("run", "", "end", "完成", {
                "total": len(self.items),
                "pdf": sum(1 for item in self.items if item.pdf),
                "failed": sum(1 for item in self.items if item.status == "失败"),
            })
            return True
        except ConfigError as exc:
            logger.error("配置错误，流程终止: %s", exc)
            self.notifier.notify_error("初始化", "", exc)
            return False
        except Exception as exc:  # 兜底：记录后终止，避免无提示退出
            logger.exception("流程异常终止: %s", exc)
            self.notifier.notify_error("主流程", "", exc)
            return False
        finally:
            self._cleanup()
            lock.release()

    def _process(self, declaration: Declaration,
                 result_workbook: ResultWorkbook, stage: str) -> None:
        item = RunItem(declaration=declaration, start_time=datetime.now())
        declaration.start_time = item.start_time
        audit_key = declaration.key
        self.audit.write("declaration", audit_key, "start", "开始", {
            "export_date": declaration.export_date.isoformat() if declaration.export_date else "",
            "contract_no": declaration.contract_no,
        })
        package = None
        sync = None
        pdf = None
        rules = None
        try:
            if not declaration.declaration_no:
                raise BusinessPending("缺少报关单号")
            if not declaration.export_date:
                raise BusinessPending("出口日期无法确定，不写入结果表")

            # 模块一：先完成离线收集；OA阶段拿到组织规则后再判定必要附件完整性。
            collect_task = CollectSubtask(self.context, declaration, None)
            package = self._run_collect(collect_task, declaration, stage)

            if stage != "collect":
                oa = self._ensure_oa()
                rules = self._rules_for(oa, declaration)
                collect_task.apply_rules(package, declaration, rules)
                # 模块二：OA 有记录走同步历史，无记录走新增（填表+上传附件+保存一次）。
                sync = self._run_oa(oa, declaration, package)

                if sync.next_action == NEXT_MERGE_PDF and stage in ("all", "pdf"):
                    if rules is None:
                        raise BusinessPending("缺少组织打印顺序配置，不能合并PDF")
                    pdf = self._run_pdf(declaration, sync, rules)

            item.package = package
            item.sync = sync
            item.pdf = pdf
            item.status = "成功"
            # 历史/人工单据不维护源附件，收集完整性不参与其状态判定。
            owns_sources = sync is None or sync.robot_owned
            notes = []
            if package and not package.complete and owns_sources:
                notes.extend(package.notes)
                notes.extend(f"缺失: {missing.get('kind', '')}" for missing in package.missing)
            if sync and sync.unresolved:
                notes.extend(sync.unresolved)
            if sync and sync.error_message:
                notes.append(sync.error_message)
            if notes:
                item.error_message = "；".join(notes)[:500]
            if package and not package.complete and owns_sources:
                item.status = "待人工"
            if sync and (sync.error_message or sync.unresolved):
                item.status = "待人工"
            declaration.run_status = item.status
            logger.info("单据处理完成: %s，状态=%s", declaration.key, item.status)
        except BusinessPending as exc:
            item.status = "待人工"
            item.error_message = str(exc)
            declaration.run_status = item.status
            logger.warning("单据转人工: %s - %s", declaration.key, exc)
        except ImplementationRequired as exc:
            item.status = "失败"
            item.error_message = str(exc)
            declaration.run_status = item.status
            screenshot = self._screenshot(declaration, "页面结构")
            self.notifier.notify_error("页面结构", declaration.key, exc, screenshot)
            logger.error("页面结构需补充: %s - %s", declaration.key, exc)
        except SystemFailure as exc:
            item.status = "失败"
            item.error_message = str(exc)
            declaration.run_status = item.status
            screenshot = self._screenshot(declaration, "系统异常")
            self.notifier.notify_error("系统异常", declaration.key, exc, screenshot)
            logger.error("系统异常: %s - %s", declaration.key, exc)
        except ConfigError:
            raise
        except Exception as exc:
            item.status = "失败"
            item.error_message = f"{type(exc).__name__}: {exc}"
            declaration.run_status = item.status
            screenshot = self._screenshot(declaration, "未分类异常")
            self.notifier.notify_error("未分类异常", declaration.key, exc, screenshot)
            logger.exception("单据处理异常: %s", declaration.key)
        finally:
            item.end_time = datetime.now()
            declaration.end_time = item.end_time
            item.declaration = declaration
            self.items.append(item)
            self.audit.write("declaration", audit_key, "end", item.status,
                             {"error": item.error_message})
            self._save_result(result_workbook, declaration, item, sync, pdf)

    def _run_collect(self, task: CollectSubtask, declaration: Declaration, stage: str):
        if stage in ("sync", "pdf"):
            cached = task.load_package(declaration)
            if cached:
                logger.info("复用上一轮收集清单: %s", declaration.key)
                task.package = cached
                return cached
        if stage == "pdf":
            raise BusinessPending("仅执行PDF阶段，但缺少收集清单")
        attempts = self.config.get_int("基础配置.重试次数", 3)
        call_with_retry(
            task.execute,
            attempts=attempts,
            delay=self.config.get_float("基础配置.重试间隔（秒）", 2.0),
            description=f"附件收集 {declaration.key}",
        )
        return task.package

    def _ensure_oa(self):
        """OA在同步阶段才启动；同一轮只启动并登录一次。"""
        oa = self.context.get("oa")
        if oa is not None:
            return oa
        browser = self._start_browser("OA配置")
        self.context.set_browser("oa", browser)
        credential = get_credential(str(self.config.require("OA配置.凭据名")))
        oa = ArchiveOaAdapter(browser, config=self.config)
        self.context.set("oa", oa)
        call_with_retry(lambda: oa.login(credential),
                        attempts=self.config.get_int("基础配置.重试次数", 3),
                        delay=self.config.get_float("基础配置.重试间隔（秒）", 5.0),
                        description="OA登录")
        # 规则表只读一次；失败终止，避免缺失必要附件清单时继续写OA。
        self._read_rules(oa)
        return oa

    def _run_oa(self, oa, declaration: Declaration, package):
        existing = oa.find_form(declaration)
        if existing is not None:
            task = OaHistorySyncSubtask(self.context, declaration, existing, package)
            call_with_retry(
                task.execute,
                attempts=self.config.get_int("基础配置.重试次数", 3),
                delay=self.config.get_float("基础配置.重试间隔（秒）", 2.0),
                description=f"OA同步历史 {declaration.key}",
            )
            return task.sync_result
        # 新增只保存一次并回查；不自动重试，避免重复建单。
        task = OaCreateSubtask(self.context, declaration, package)
        task.execute()
        return task.sync_result

    def _run_pdf(self, declaration: Declaration, sync, rules: OrganizationRules):
        task = MergePdfSubtask(self.context, declaration, sync, rules)
        attempts = self.config.get_int("基础配置.重试次数", 3)
        call_with_retry(
            task.execute,
            attempts=attempts,
            delay=self.config.get_float("基础配置.重试间隔（秒）", 2.0),
            description=f"合并PDF {declaration.key}",
        )
        return task.pdf_result

    def _rules_for(self, oa, declaration: Declaration) -> Optional[OrganizationRules]:
        try:
            return oa.read_rules(declaration)
        except BusinessPending as exc:
            logger.warning("组织规则不可用，转人工: %s - %s", declaration.key, exc)
            self.audit.write("sync", declaration.key, "rules", "待人工", {"error": str(exc)})
            return None

    def _read_rules(self, oa):
        try:
            return call_with_retry(
                oa.read_all_rules,
                attempts=self.config.get_int("基础配置.重试次数", 3),
                delay=self.config.get_float("基础配置.重试间隔（秒）", 5.0),
                description="OA配置表读取",
            )
        except (BusinessPending, SystemFailure) as exc:
            raise ConfigError(f"OA配置表读取失败，无法确定必要附件与打印顺序: {exc}") from exc

    def _resolve_companies(self, params: RunParams) -> List[str]:
        if params.companies:
            return params.companies
        root = self.config.get("路径配置.报关单证存放路径")
        shared = []
        if root and Path(root).is_dir():
            shared = [item.name for item in Path(root).iterdir() if item.is_dir()]
        if shared:
            return shared
        raise ConfigError("没有可运行的公司：入参为空且共享目录无公司")

    def _start_browser(self, section: str) -> BrowserManager:
        browser = BrowserManager(
            headless=self.config.get_bool(f"{section}.无头模式", False),
            browser_type=str(self.config.get(f"{section}.浏览器类型") or "chromium").lower(),
            executable_path=str(self.config.get(f"{section}.浏览器路径") or ""),
            maximized=self.config.get_bool(f"{section}.窗口最大化", False),
        )
        browser.start()
        return browser

    def _save_result(self, result_workbook: ResultWorkbook, declaration: Declaration,
                     item: RunItem, sync, pdf) -> None:
        try:
            path = result_workbook.save(declaration, item, sync, pdf)
            self.workbooks.setdefault(declaration.company, [])
            if path not in self.workbooks[declaration.company]:
                self.workbooks[declaration.company].append(path)
        except ValueError as exc:
            logger.info("跳过结果表写入: %s - %s", declaration.key, exc)
        except Exception as exc:
            logger.error("结果表更新失败: %s - %s", declaration.key, exc)
            item.error_message = (item.error_message + f"；结果表保存失败: {exc}")[:500]
            self.notifier.notify_error("结果表", declaration.key, exc)

    def _screenshot(self, declaration: Declaration, tag: str) -> str:
        name = f"{self.run_id}_{declaration.declaration_no}_{tag}"
        try:
            return take_error_screenshot(str(self.config.get_path("screenshot")), name) or ""
        except Exception as exc:
            logger.warning("截图失败: %s", exc)
            return ""

    def _per_org(self) -> Dict[str, str]:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {org: now for org in {item.declaration.company for item in self.items}}

    def _notify_end(self) -> None:
        try:
            self.notifier.notify_end(self.items, self.workbooks)
        except Exception as exc:
            logger.warning("结果通知失败: %s", exc)

    def _cleanup(self) -> None:
        try:
            self.context.close_all_browsers()
        except Exception:
            pass

    def _cleanup_temp(self) -> None:
        """清理7天前的临时运行目录，只匹配 run_id 命名的目录。"""
        root = Path(self.config.get_path("temp"))
        if not root.is_dir():
            return
        cutoff = time.time() - 7 * 24 * 60 * 60
        for child in root.iterdir():
            if not child.is_dir() or not re.match(r"^\d{8}_\d{6}$", child.name):
                continue
            try:
                if child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
                    logger.info("已清理临时目录: %s", child)
            except OSError:
                continue
